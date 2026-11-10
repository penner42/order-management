using System.IO.Compression;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;
using OrderManager.Core;

namespace OrderManager.Mac;

public sealed record ReleaseManifest(int SchemaVersion, string SourceSha, string Architecture,
    string MinimumMacOS, int HelperProtocolVersion, string ReleaseTag, string LauncherUrl = "", string LauncherSha256 = "")
{
    public static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);
    public void Validate(Version os, bool bootstrap = false)
    {
        Installation.ValidateSha(SourceSha);
        if (SchemaVersion != 1 || HelperProtocolVersion != 1 || Architecture != "arm64" ||
            !Version.TryParse(MinimumMacOS, out var minimum) || minimum > os ||
            !Regex.IsMatch(ReleaseTag, "^mac-installer-[0-9]+-[0-9]+$"))
            throw new InvalidDataException("This Mac release is incompatible. Download a compatible installer.");
        if (!bootstrap && (!Regex.IsMatch(LauncherSha256, "^[0-9a-f]{64}$") ||
            LauncherUrl != $"https://github.com/penner42/order-management/releases/download/{ReleaseTag}/OrderManager-arm64.zip"))
            throw new InvalidDataException("Invalid Mac launcher asset.");
    }
    public static ReleaseManifest Read(string path) => JsonSerializer.Deserialize<ReleaseManifest>(File.ReadAllText(path), Json)
        ?? throw new InvalidDataException("Missing release manifest.");
}

public sealed class MacReleases(HttpClient http, Installation install, ReleaseManifest bundled, Version os,
    bool firstInstall, ICommandRunner runner, Action<string> status) : ISourceProvider, IReleaseSource
{
    private ReleaseManifest? selected;
    public string? ReleaseTag => selected?.ReleaseTag;
    public static (long Run, long Attempt) ReleaseOrder(string tag)
    {
        var parts = tag.Split('-');
        if (parts.Length != 4 || !long.TryParse(parts[2], out var run) || !long.TryParse(parts[3], out var attempt))
            throw new InvalidDataException("Invalid Mac release tag.");
        return (run, attempt);
    }
    public async Task<string> Latest()
    {
        if (firstInstall) { bundled.Validate(os, bootstrap: true); selected = bundled; return bundled.SourceSha; }
        var candidates = new List<(DateTimeOffset Published, JsonElement Release)>();
        for (var page = 1; ; page++)
        {
            var releases = await http.GetFromJsonAsync<JsonElement[]>($"https://api.github.com/repos/penner42/order-management/releases?per_page=100&page={page}") ?? [];
            foreach (var release in releases)
                if (!release.GetProperty("draft").GetBoolean() && !release.GetProperty("prerelease").GetBoolean() &&
                    Regex.IsMatch(release.GetProperty("tag_name").GetString() ?? "", "^mac-installer-[0-9]+-[0-9]+$"))
                    candidates.Add((release.GetProperty("published_at").GetDateTimeOffset(), release));
            if (releases.Length < 100) break;
        }
        var current = install.Load();
        var currentTag = current.MacReleaseTag ?? (current.ActiveSha == bundled.SourceSha ? bundled.ReleaseTag : null);
        foreach (var (_, release) in candidates.OrderByDescending(r => ReleaseOrder(r.Release.GetProperty("tag_name").GetString()!)))
        {
            var tag = release.GetProperty("tag_name").GetString();
            if (currentTag != null && ReleaseOrder(tag!).CompareTo(ReleaseOrder(currentTag)) < 0)
            {
                // A late rerun of an older workflow must never downgrade a migrated database.
                return current.ActiveSha!;
            }
            var url = $"https://github.com/penner42/order-management/releases/download/{tag}/mac-release.json";
            if (!release.GetProperty("assets").EnumerateArray().Any(a => a.GetProperty("browser_download_url").GetString() == url)) continue;
            var manifest = await http.GetFromJsonAsync<ReleaseManifest>(url, ReleaseManifest.Json)
                ?? throw new InvalidDataException("Empty Mac release manifest.");
            if (manifest.ReleaseTag != tag) throw new InvalidDataException("Release tag does not match manifest.");
            try { manifest.Validate(os); }
            catch (InvalidDataException) { continue; }
            selected = manifest;
            return manifest.SourceSha;
        }
        throw new IOException("No compatible published Mac release was found. Existing services have not been changed.");
    }
    public Task Download(string sha, string destination) => new GitHubSource(http, DeploymentSettings.Mac).Download(sha, destination);
    public async Task PrepareLauncher(string sha)
    {
        if (selected == null || selected.SourceSha != sha) throw new InvalidOperationException("No matching release selected.");
        if (firstInstall) return; // This installation is already running the bundled release's launcher.
        var destination = Path.Combine(install.Source(sha), "launcher");
        if (Directory.Exists(destination)) Directory.Delete(destination, true);
        Directory.CreateDirectory(destination);
        var zip = Path.Combine(destination, "download.zip");
        try
        {
            status("Downloading matching Mac launcher");
            await Downloads.Get(http, selected.LauncherUrl, zip, status, 512L * 1024 * 1024);
            using (var stream = File.OpenRead(zip))
                if (Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant() != selected.LauncherSha256)
                    throw new InvalidDataException("Mac launcher checksum mismatch.");
            SafeZip.Extract(zip, destination);
            await VerifyBundle(Path.Combine(destination, "Order Manager.app"), runner, selected.SourceSha);
        }
        catch { Directory.Delete(destination, true); throw; }
        finally { if (File.Exists(zip)) File.Delete(zip); }
    }
    public static async Task VerifyBundle(string bundle, ICommandRunner runner, string? sha = null)
    {
        async Task<string> Check(string executable, params string[] args)
        {
            var result = await runner.Run(executable, args, timeout: TimeSpan.FromMinutes(2));
            if (result.ExitCode != 0) throw new InvalidDataException("Mac application verification failed. Reinstall from a successful release.");
            return result.Output.Trim();
        }
        if (await Check("/usr/libexec/PlistBuddy", "-c", "Print:CFBundleIdentifier", Path.Combine(bundle, "Contents/Info.plist")) != "com.penner42.OrderManager")
            throw new InvalidDataException("Unexpected application identity.");
        await Check("/usr/bin/lipo", "-verify_arch", "arm64", Path.Combine(bundle, "Contents/MacOS/OrderManager"));
        await Check("/usr/bin/lipo", "-verify_arch", "arm64", Path.Combine(bundle, MacBundle.HelperPath));
        await Check("/usr/bin/codesign", "--verify", "--deep", "--strict", bundle);
        var manifest = ReleaseManifest.Read(Path.Combine(bundle, "Contents/Resources/bootstrap.json"));
        if (sha != null && manifest.SourceSha != sha) throw new InvalidDataException("Launcher source commit does not match services.");
    }
}

public static class Downloads
{
    public static async Task Get(HttpClient http, string url, string path, Action<string> status, long limit)
    {
        using var response = await http.GetAsync(url, HttpCompletionOption.ResponseHeadersRead);
        response.EnsureSuccessStatusCode();
        var length = response.Content.Headers.ContentLength;
        if (length > limit) throw new InvalidDataException("Download exceeds size limit.");
        await using var input = await response.Content.ReadAsStreamAsync();
        await using var output = File.Create(path);
        var buffer = new byte[128 * 1024];
        long total = 0; var lastReport = DateTime.MinValue;
        int count;
        while ((count = await input.ReadAsync(buffer)) > 0)
        {
            total += count;
            if (total > limit) throw new InvalidDataException("Download exceeds size limit.");
            await output.WriteAsync(buffer.AsMemory(0, count));
            if ((DateTime.UtcNow - lastReport).TotalSeconds >= 1)
            {
                status(length > 0 ? $"Downloaded {total * 100 / length}% ({total / 1048576} / {length / 1048576} MB)" : $"Downloaded {total / 1048576} MB");
                lastReport = DateTime.UtcNow;
            }
        }
        if (length != null && total != length) throw new IOException("Incomplete download.");
    }
}

public static class SafeZip
{
    public static void Extract(string archive, string destination)
    {
        using var zip = ZipFile.OpenRead(archive);
        long total = 0;
        var paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var entry in zip.Entries)
        {
            var parts = entry.FullName.TrimEnd('/').Split('/');
            var kind = (entry.ExternalAttributes >> 16) & 0xF000;
            if (parts[0] != "Order Manager.app" || parts.Any(p => p.Length == 0 || p is "." or ".." || p.Contains('\\') || p.Contains(':')) ||
                (kind != 0 && kind != 0x8000 && kind != 0x4000) || !paths.Add(entry.FullName.TrimEnd('/')))
                throw new InvalidDataException("Unsafe launcher archive.");
            total = checked(total + entry.Length);
            if (total > 1024L * 1024 * 1024) throw new InvalidDataException("Launcher archive exceeds size limit.");
            var target = Path.GetFullPath(Path.Combine(destination, entry.FullName));
            if (!target.StartsWith(Path.GetFullPath(destination) + Path.DirectorySeparatorChar, StringComparison.Ordinal))
                throw new InvalidDataException("Archive escapes destination.");
            if (entry.FullName.EndsWith('/')) Directory.CreateDirectory(target);
            else
            {
                Directory.CreateDirectory(Path.GetDirectoryName(target)!);
                entry.ExtractToFile(target, overwrite: false);
                if (!OperatingSystem.IsWindows()) File.SetUnixFileMode(target,
                    (entry.ExternalAttributes >> 16 & 0x49) != 0 ? UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute : UnixFileMode.UserRead | UnixFileMode.UserWrite);
            }
        }
    }
}
