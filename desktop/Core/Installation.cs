using System.Diagnostics;
using System.IO.Compression;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace OrderManager.Core;

public sealed class InstallationState
{
    public string? ActiveSha { get; set; }
    public string? PreviousSha { get; set; }
    public string? PendingSha { get; set; }
    public bool MigrationStarted { get; set; }
    public string? BackupPath { get; set; }
    public string? ExtensionFingerprint { get; set; }
    public string? AcknowledgedExtension { get; set; }
    public bool AutoStart { get; set; } = true;
    public string? MacReleaseTag { get; set; }
    public string? PrerequisiteRebootBootTime { get; set; }
    public bool RecoveryRequired => MigrationStarted && ActiveSha != null;
}

public sealed class Installation(string root, DeploymentSettings? settings = null)
{
    public DeploymentSettings Settings { get; } = settings ?? DeploymentSettings.Windows;
    public string Root { get; } = Path.GetFullPath(root);
    public string Config => Path.Combine(Root, "config");
    public string EnvFile => Path.Combine(Config, ".env");
    public string StateFile => Path.Combine(Config, "state.json");
    public string Logs => Path.Combine(Root, "logs");
    public string Backups => Path.Combine(Root, "backups");
    public string Chrome => Path.Combine(Root, "extensions", "chrome");
    public string Source(string sha)
    {
        ValidateSha(sha);
        return Path.Combine(Root, "versions", sha);
    }
    public static void ValidateSha(string sha)
    {
        if (!Regex.IsMatch(sha, "^[0-9a-f]{40}$")) throw new InvalidDataException("Invalid GitHub commit SHA.");
    }
    public InstallationState Load()
    {
        if (!File.Exists(StateFile)) return new();
        var state = JsonSerializer.Deserialize<InstallationState>(File.ReadAllText(StateFile))
            ?? throw new InvalidDataException("Installation state is empty. Preserve it and contact support.");
        foreach (var sha in new[] { state.ActiveSha, state.PreviousSha, state.PendingSha })
            if (sha != null) ValidateSha(sha);
        return state;
    }
    public void Save(InstallationState state)
    {
        PrivateDirectory(Config);
        PrivateWrite(StateFile + ".tmp", JsonSerializer.Serialize(state, new JsonSerializerOptions { WriteIndented = true }));
        File.Move(StateFile + ".tmp", StateFile, true);
    }
    public void EnsureEnvironment()
    {
        PrivateDirectory(Config);
        if (File.Exists(EnvFile)) { ProtectFile(EnvFile); return; }
        var temporary = EnvFile + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            PrivateWrite(temporary, $"SECRET_KEY={Convert.ToHexString(RandomNumberGenerator.GetBytes(48))}\nADMIN_USERNAME=admin\nADMIN_PASSWORD=admin\n");
            File.Move(temporary, EnvFile); // Never overwrite a pre-existing secret.
        }
        finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }

    public static void PrivateDirectory(string path)
    {
        Directory.CreateDirectory(path);
        if (!OperatingSystem.IsWindows()) File.SetUnixFileMode(path, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
    }
    public static void ProtectFile(string path)
    {
        if (!OperatingSystem.IsWindows()) File.SetUnixFileMode(path, UnixFileMode.UserRead | UnixFileMode.UserWrite);
    }
    public static void PrivateWrite(string path, string content)
    {
        var options = new FileStreamOptions { Mode = FileMode.Create, Access = FileAccess.Write };
        if (!OperatingSystem.IsWindows()) options.UnixCreateMode = UnixFileMode.UserRead | UnixFileMode.UserWrite;
        using (var stream = new FileStream(path, options))
        using (var writer = new StreamWriter(stream)) writer.Write(content);
        ProtectFile(path);
    }

    // GitHub archives have one top-level directory. Reject traversal, links and
    // Windows special paths before opening any destination file.
    public static void ExtractArchive(Stream archive, string destination, DeploymentSettings? settings = null)
    {
        using var zip = new ZipArchive(archive, ZipArchiveMode.Read, leaveOpen: true);
        string? prefix = null;
        long total = 0;
        foreach (var entry in zip.Entries)
        {
            var parts = entry.FullName.Split('/');
            prefix ??= parts[0];
            if (parts[0] != prefix || parts.Any(p => p is "." or ".." || p.Contains('\\') || p.Contains(':') ||
                p.EndsWith(' ') || p.EndsWith('.') || Regex.IsMatch(p, "^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(\\.|$)", RegexOptions.IgnoreCase)))
                throw new InvalidDataException("Unsafe path in GitHub archive.");
            if (((entry.ExternalAttributes >> 16) & 0xF000) == 0xA000)
                throw new InvalidDataException("Links are not allowed in source archives.");
            total = checked(total + entry.Length);
            if (total > 2L * 1024 * 1024 * 1024) throw new InvalidDataException("Source archive exceeds 2 GB.");
            var relative = Path.Combine(parts.Skip(1).ToArray());
            if (relative.Length == 0) continue;
            var path = Path.GetFullPath(Path.Combine(destination, relative));
            if (!path.StartsWith(Path.GetFullPath(destination) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Archive path escapes destination.");
            if (entry.FullName.EndsWith('/')) Directory.CreateDirectory(path);
            else
            {
                Directory.CreateDirectory(Path.GetDirectoryName(path)!);
                entry.ExtractToFile(path, overwrite: false);
            }
        }
        (settings ?? DeploymentSettings.Windows).ValidateSource(destination);
    }
}

public record CommandResult(int ExitCode, string Output);
public interface ICommandRunner
{
    Task<CommandResult> Run(string executable, IReadOnlyList<string> arguments,
        IReadOnlyDictionary<string, string>? environment = null, TimeSpan? timeout = null);
}

public sealed class ProcessRunner(Action<string> log) : ICommandRunner
{
    public async Task<CommandResult> Run(string executable, IReadOnlyList<string> arguments,
        IReadOnlyDictionary<string, string>? environment = null, TimeSpan? timeout = null)
    {
        var info = new ProcessStartInfo(executable)
        {
            UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true
        };
        foreach (var arg in arguments) info.ArgumentList.Add(arg);
        if (environment != null) foreach (var (key, value) in environment)
        {
            if (value == null) info.Environment.Remove(key);
            else info.Environment[key] = value;
        }
        // Never log environment variables, Compose config output, or credentials.
        using var process = Process.Start(info) ?? throw new IOException($"Could not start {executable}.");
        var stdout = new System.Text.StringBuilder();
        async Task Drain(StreamReader reader, bool capture)
        {
            while (await reader.ReadLineAsync() is { } line)
            {
                if (capture) stdout.AppendLine(line);
                log(line);
            }
        }
        var output = Drain(process.StandardOutput, true);
        var error = Drain(process.StandardError, false);
        using var deadline = new CancellationTokenSource(timeout ?? TimeSpan.FromMinutes(45));
        try { await process.WaitForExitAsync(deadline.Token); }
        catch (OperationCanceledException)
        {
            process.Kill(entireProcessTree: true);
            await Task.WhenAll(output, error);
            throw new TimeoutException($"{Path.GetFileName(executable)} timed out. See logs for details.");
        }
        await Task.WhenAll(output, error);
        return new(process.ExitCode, stdout.ToString());
    }
}

public interface IReleaseSource
{
    string? ReleaseTag { get; }
}

public interface ISourceProvider
{
    Task<string> Latest();
    Task Download(string sha, string destination);
}

public sealed class GitHubSource(HttpClient http, DeploymentSettings? settings = null) : ISourceProvider
{
    public async Task<string> Latest()
    {
        using var response = await http.GetAsync("https://api.github.com/repos/penner42/order-management/commits/master");
        response.EnsureSuccessStatusCode();
        var data = await response.Content.ReadFromJsonAsync<JsonElement>();
        var sha = data.GetProperty("sha").GetString() ?? "";
        Installation.ValidateSha(sha);
        return sha;
    }
    public async Task Download(string sha, string destination)
    {
        Installation.ValidateSha(sha);
        var temporary = destination + ".download";
        if (Directory.Exists(temporary)) Directory.Delete(temporary, true);
        Directory.CreateDirectory(temporary);
        try
        {
            using var response = await http.GetAsync($"https://codeload.github.com/penner42/order-management/zip/{sha}", HttpCompletionOption.ResponseHeadersRead);
            response.EnsureSuccessStatusCode();
            // ZipArchive requires seeking; download to disk rather than retaining the repository in RAM.
            var archive = temporary + ".zip";
            try
            {
                await using (var file = File.Create(archive)) await response.Content.CopyToAsync(file);
                using var input = File.OpenRead(archive);
                Installation.ExtractArchive(input, temporary, settings);
            }
            finally { if (File.Exists(archive)) File.Delete(archive); }
            Directory.Move(temporary, destination);
        }
        finally { if (Directory.Exists(temporary)) Directory.Delete(temporary, true); }
    }
}
