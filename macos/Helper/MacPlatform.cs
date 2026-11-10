using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;
using OrderManager.Core;

namespace OrderManager.Mac;

public sealed class OperationLock : IDisposable
{
    private readonly FileStream stream;
    public OperationLock(string config)
    {
        Installation.PrivateDirectory(config);
        try { stream = new FileStream(Path.Combine(config, "operation.lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None); }
        catch (IOException) { throw new IOException("Another Order Manager operation is already running."); }
    }
    public void Dispose() => stream.Dispose();
}

public sealed class MacRunner(Action<string> log, ICommandRunner? commands = null, Func<string?>? findDocker = null) : ICommandRunner
{
    private readonly ICommandRunner inner = commands ?? new ProcessRunner(log);
    public static string? DockerApp() => new[] { "/Applications/Docker.app", Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Applications/Docker.app") }
        .FirstOrDefault(p => File.Exists(Path.Combine(p, "Contents/Resources/bin/docker")));
    public const string DockerContext = "desktop-linux";
    public Task<CommandResult> Run(string executable, IReadOnlyList<string> arguments,
        IReadOnlyDictionary<string, string>? environment = null, TimeSpan? timeout = null)
    {
        if (executable != "docker") return inner.Run(executable, arguments, environment, timeout);
        var app = (findDocker == null ? DockerApp() : findDocker()) ?? throw new IOException("Install and launch Docker Desktop, then choose Start.");
        // ProcessRunner accepts removals for Docker variables. Never inherit a remote endpoint,
        // TLS configuration, platform override, or Compose override from the launching shell.
        var clean = new Dictionary<string, string>(environment ?? new Dictionary<string, string>());
        foreach (System.Collections.DictionaryEntry entry in Environment.GetEnvironmentVariables())
            if (((string)entry.Key).StartsWith("DOCKER_", StringComparison.Ordinal) || ((string)entry.Key).StartsWith("COMPOSE_", StringComparison.Ordinal))
                clean[(string)entry.Key] = null!;
        return inner.Run(Path.Combine(app, "Contents/Resources/bin/docker"), ["--context", DockerContext, ..arguments], clean, timeout);
    }
    public async Task EnsureDocker(ServiceManager manager, Action<string> status)
    {
        var app = DockerApp() ?? throw new IOException("Docker Desktop is missing. Choose Install Docker Desktop, finish its setup, then choose Start.");
        var os = await CheckPlatform(this);
        var minimum = await Run("/usr/libexec/PlistBuddy", ["-c", "Print:LSMinimumSystemVersion", Path.Combine(app, "Contents/Info.plist")]);
        if (minimum.ExitCode == 0 && Version.TryParse(minimum.Output.Trim(), out var required) && required > os)
            throw new IOException($"This Docker Desktop requires macOS {required} or later.");
        status("Starting Docker Desktop; complete any Docker first-run prompts");
        var launched = await Run("/usr/bin/open", [app]);
        if (launched.ExitCode != 0) throw new IOException("Could not launch Docker Desktop.");
        var deadline = DateTime.UtcNow.AddMinutes(5);
        while (DateTime.UtcNow < deadline)
        {
            try { await manager.CheckDocker(); return; }
            catch { await Task.Delay(3000); }
        }
        throw new IOException("Docker Desktop is not ready. Finish installation and license prompts in Docker, then retry Start. Services have not been changed.");
    }
    public async Task InstallDocker(HttpClient http, Installation install, Action<string> status)
    {
        var dmg = Path.Combine(install.Root, "Docker.dmg");
        status("Downloading Docker Desktop for Apple Silicon");
        await Downloads.Get(http, "https://desktop.docker.com/mac/main/arm64/Docker.dmg", dmg, status, 4L * 1024 * 1024 * 1024);
        var mount = Path.Combine(install.Root, "docker-installer");
        if (Directory.Exists(Path.Combine(mount, "Docker.app")))
        {
            var detach = await Run("/usr/bin/hdiutil", ["detach", mount]);
            if (detach.ExitCode != 0) throw new IOException("Close the previous Docker installer window and retry. Its disk image is still in use.");
        }
        Directory.CreateDirectory(mount);
        var attach = await Run("/usr/bin/hdiutil", ["attach", "-nobrowse", "-readonly", "-mountpoint", mount, dmg]);
        if (attach.ExitCode != 0) throw new IOException("Could not mount the Docker installer. Retry installation.");
        var app = Path.Combine(mount, "Docker.app");
        // Docker Inc's Developer ID team; require Apple's signing chain and exact bundle identity.
        var verify = await Run("/usr/bin/codesign", ["--verify", "--deep", "--strict", "-R=anchor apple generic and identifier \"com.docker.docker\" and certificate leaf[subject.OU] = \"9BNSXJN65R\"", app]);
        if (verify.ExitCode != 0)
        {
            await Run("/usr/bin/hdiutil", ["detach", mount]);
            throw new IOException("Docker publisher signature verification failed. The installer was not opened.");
        }
        var os = await CheckPlatform(this);
        var minimum = await Run("/usr/libexec/PlistBuddy", ["-c", "Print:LSMinimumSystemVersion", Path.Combine(app, "Contents/Info.plist")]);
        if (minimum.ExitCode != 0 || !Version.TryParse(minimum.Output.Trim(), out var required))
            throw new IOException("Could not determine Docker's minimum macOS version. Install a compatible official Docker Desktop release manually.");
        if (required > os) throw new IOException($"The downloaded Docker Desktop requires macOS {required} or later. Upgrade macOS or install a compatible official Docker release.");
        await Run("/usr/bin/open", [mount]);
        status("Drag Docker to Applications, launch it, and accept its terms. Then choose Start in Order Manager. Keep the installer mounted until Docker finishes.");
    }
    public static async Task<Version> CheckPlatform(ICommandRunner runner)
    {
        if (!OperatingSystem.IsMacOS() || RuntimeInformation.OSArchitecture != Architecture.Arm64)
            throw new PlatformNotSupportedException("Order Manager for Mac requires an Apple Silicon Mac.");
        var result = await runner.Run("/usr/bin/sw_vers", ["-productVersion"]);
        if (result.ExitCode != 0 || !Version.TryParse(result.Output.Trim(), out var version)) throw new IOException("Could not determine macOS version.");
        return version;
    }
}

public static class MacRecovery
{
    public static string Quote(string value) => "'" + value.Replace("'", "'\"'\"'") + "'";
    public static string Instructions(Installation install, InstallationState state, string docker)
    {
        if (!state.RecoveryRequired || state.ActiveSha == null || state.BackupPath == null) return "No database recovery is currently required.";
        var compose = $"{Quote(docker)} --context desktop-linux compose --project-name order-manager-macos --project-directory {Quote(install.Source(state.ActiveSha))} --env-file {Quote(install.EnvFile)} -f {Quote(Path.Combine(install.Source(state.ActiveSha), install.Settings.ComposeFile))}";
        var stop = state.PendingSha == null ? compose : compose.Replace(Quote(install.Source(state.ActiveSha)), Quote(install.Source(state.PendingSha))).Replace(Quote(Path.Combine(install.Source(state.ActiveSha), install.Settings.ComposeFile)), Quote(Path.Combine(install.Source(state.PendingSha), install.Settings.ComposeFile)));
        return $"""
            An update was interrupted after migrations may have begun.
            Preserve any newer data before restoring: the restore discards changes after the backup.
            Open Terminal and run each command separately. Stop if ANY command fails.

            export ORDER_MANAGER_VERSION={Quote(state.ActiveSha)}
            {stop} stop frontend backend package-tracking
            {compose} up -d --wait db
            {compose} cp {Quote(state.BackupPath)} db:/tmp/recovery.dump
            {compose} exec -T db pg_restore --list /tmp/recovery.dump
            # DESTRUCTIVE: only continue after preserving newer data and verifying all preceding commands.
            {compose} exec -T db psql -U postgres -d order_management -v ON_ERROR_STOP=1 -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'
            {compose} exec -T db pg_restore -U postgres -d order_management --no-owner --exit-on-error --single-transaction /tmp/recovery.dump

            Only after successful restore choose Resume After Manual Restore. Retain your backup.
            """;
    }
}

public sealed record ReplacementState(string Candidate, string Target, string Previous);
public static class LauncherReplacement
{
    public static string Target => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Applications/Order Manager.app");
    public static string Failure(Installation install) => Path.Combine(install.Config, "launcher-replacement-error.txt");
    public static string Journal(Installation install) => Path.Combine(install.Config, "launcher-replacement.json");
    public static void Save(Installation install, ReplacementState state)
    {
        Installation.PrivateWrite(Journal(install) + ".tmp", JsonSerializer.Serialize(state));
        File.Move(Journal(install) + ".tmp", Journal(install), true);
    }
    public static ReplacementState? Read(Installation install)
    {
        if (!File.Exists(Journal(install))) return null;
        var state = JsonSerializer.Deserialize<ReplacementState>(File.ReadAllText(Journal(install))) ?? throw new IOException("Invalid launcher replacement journal.");
        var active = install.Load().ActiveSha ?? throw new IOException("No active release for launcher replacement.");
        if (state.Target != Target || state.Previous != Target + ".previous" || state.Candidate != Path.Combine(install.Source(active), "launcher/Order Manager.app"))
            throw new IOException("Invalid launcher replacement paths. Reinstall the Mac installer; retain application data.");
        return state;
    }
    public static async Task Apply(ReplacementState state, Func<string, Task> verify)
    {
        try
        {
            if (Directory.Exists(state.Candidate))
            {
                await verify(state.Candidate);
                if (Directory.Exists(state.Target))
                {
                    if (Directory.Exists(state.Previous)) Directory.Delete(state.Previous, true);
                    Directory.Move(state.Target, state.Previous);
                }
                Directory.Move(state.Candidate, state.Target);
            }
            await verify(state.Target);
        }
        catch
        {
            if (!Directory.Exists(state.Target) && Directory.Exists(state.Previous)) Directory.Move(state.Previous, state.Target);
            throw;
        }
    }

    public static async Task Replace(Installation install, ICommandRunner runner, int parent)
    {
        if (parent <= 1) throw new IOException("Invalid parent process.");
        var deadline = DateTime.UtcNow.AddMinutes(2);
        while (DateTime.UtcNow < deadline)
        {
            try { using var process = Process.GetProcessById(parent); if (process.HasExited) break; }
            catch (ArgumentException) { break; }
            await Task.Delay(250);
        }
        try { using var process = Process.GetProcessById(parent); if (!process.HasExited) throw new IOException("Launcher did not exit. Reopen Order Manager to retry."); }
        catch (ArgumentException) { }
        using var operation = new OperationLock(install.Config);
        var state = Read(install) ?? throw new IOException("Missing replacement journal.");
        try
        {
            await Apply(state, path => MacReleases.VerifyBundle(path, runner, install.Load().ActiveSha));
            File.Delete(Journal(install));
            if (File.Exists(Failure(install))) File.Delete(Failure(install));
        }
        catch (Exception ex)
        {
            Installation.PrivateWrite(Failure(install), ex.Message + "\nReinstall the Mac installer if retrying does not repair the launcher. Service data has been retained.");
            throw;
        }
        finally
        {
            if (Directory.Exists(state.Target)) await runner.Run("/usr/bin/open", [state.Target]);
        }
    }
}
