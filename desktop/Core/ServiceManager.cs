using System.Text.Json;

namespace OrderManager.Core;

public sealed class ServiceManager(Installation install, ICommandRunner runner, ISourceProvider source, Action<string> status,
    string docker = "docker", Func<string, Task>? prepareLauncher = null)
{
    private readonly SemaphoreSlim operation = new(1, 1);
    public bool Busy => operation.CurrentCount == 0;
    public async Task Exclusive(Func<Task> action)
    {
        if (!await operation.WaitAsync(0)) throw new InvalidOperationException("Another operation is already running.");
        try { await action(); }
        finally { operation.Release(); }
    }
    private async Task<string> Docker(string[] args, string? sha = null, TimeSpan? timeout = null)
    {
        var result = await runner.Run(docker, args, sha == null ? null : new Dictionary<string, string> { ["ORDER_MANAGER_VERSION"] = sha }, timeout);
        if (result.ExitCode != 0) throw new IOException($"Docker command failed ({result.ExitCode}): {args[0]}. See logs for details.");
        return result.Output;
    }
    private string[] ComposeArgs(string sha, params string[] args) =>
        ["compose", "--project-name", install.Settings.ProjectName, "--project-directory", install.Source(sha),
         "--env-file", install.EnvFile, "-f", Path.Combine(install.Source(sha), install.Settings.ComposeFile), ..args];
    private Task<string> Compose(string sha, params string[] args) => Docker(ComposeArgs(sha, args), sha);
    public async Task CheckDocker()
    {
        var os = await Docker(["info", "--format", "{{.OSType}}"], timeout: TimeSpan.FromSeconds(20));
        if (os.Trim() != "linux") throw new InvalidOperationException("Switch Docker Desktop to Linux containers, then retry Start.");
        await Docker(["compose", "version"], timeout: TimeSpan.FromSeconds(20));
    }
    public async Task<string> GetStatus()
    {
        if (Busy) return "Busy";
        var state = install.Load();
        if (state.RecoveryRequired) return "Recovery required";
        try
        {
            await CheckDocker();
            if (state.ActiveSha == null) return "Stopped";
            var output = await Compose(state.ActiveSha, "ps", "--all", "--format", "json");
            // Compose versions emit either JSON Lines or a JSON array.
            var rows = output.TrimStart().StartsWith('[')
                ? JsonSerializer.Deserialize<JsonElement[]>(output) ?? []
                : output.Split('\n', StringSplitOptions.RemoveEmptyEntries).Select(l => JsonSerializer.Deserialize<JsonElement>(l)).ToArray();
            if (rows.Length == 0 || rows.All(r => r.GetProperty("State").GetString() == "exited")) return "Stopped";
            return rows.Length == 4 && rows.All(r => r.GetProperty("State").GetString() == "running" &&
                r.TryGetProperty("Health", out var h) && h.GetString() == "healthy") ? "Running" : "Error: services unhealthy";
        }
        catch { return "Docker unavailable"; }
    }
    private static void GuardRecovery(InstallationState state)
    {
        if (state.RecoveryRequired) throw new InvalidOperationException("An update was interrupted after migrations could begin. Open recovery instructions before restarting.");
    }
    public Task Start() => Exclusive(async () =>
    {
        status("Starting");
        var state = install.Load();
        GuardRecovery(state);
        await CheckDocker();
        install.EnsureEnvironment();
        if (state.ActiveSha == null) await UpdateCore();
        else
        {
            // A pre-migration interruption can safely resume the recorded version.
            state.PendingSha = null;
            install.Save(state);
            await Compose(state.ActiveSha, "up", "-d", "--wait", "--wait-timeout", "600");
            RefreshExtensions(state);
            install.Save(state);
        }
        status("Running");
    });
    public Task Stop() => Exclusive(async () =>
    {
        var state = install.Load();
        var sha = state.PendingSha ?? state.ActiveSha;
        if (sha != null) await Compose(sha, "stop");
        status("Stopped");
    });
    public Task Restart() => Exclusive(async () =>
    {
        var state = install.Load();
        GuardRecovery(state);
        if (state.ActiveSha == null) { await UpdateCore(); return; }
        status("Starting");
        await CheckDocker();
        await Compose(state.ActiveSha, "stop");
        await Compose(state.ActiveSha, "up", "-d", "--wait", "--wait-timeout", "600");
        status("Running");
    });
    public Task Update() => Exclusive(UpdateCore);

    private async Task UpdateCore()
    {
        status("Updating");
        var state = install.Load();
        GuardRecovery(state);
        await CheckDocker();
        install.EnsureEnvironment();
        status("Checking latest application version");
        var sha = await source.Latest();
        if (state.ActiveSha == sha) { status("Already up to date"); return; }
        var directory = install.Source(sha);
        if (!Directory.Exists(directory))
        {
            status("Downloading application source");
            await source.Download(sha, directory);
        }
        state.PendingSha = sha;
        state.MigrationStarted = false;
        install.Save(state);
        var writersStopped = false;
        try
        {
            // Build all candidates (including our replacement launcher) before downtime.
            status("Building application images — this can take several minutes");
            await Compose(sha, "build");
            status("Preparing updated launcher");
            if (prepareLauncher != null) await prepareLauncher(sha);
            else
            {
                await Docker(["build", "--file", Path.Combine(directory, "windows", "Dockerfile"), "--target", "launcher",
                    "--output", $"type=local,dest={Path.Combine(directory, "launcher")}", directory]);
                if (!File.Exists(Path.Combine(directory, "launcher", "OrderManager.exe")))
                    throw new IOException("Candidate launcher build did not produce OrderManager.exe.");
            }
            if (state.ActiveSha != null)
            {
                // Set before stop: even a partially failed stop must resume the old app.
                writersStopped = true;
                status("Stopping services and backing up database");
                await Compose(state.ActiveSha, "stop", "frontend", "backend", "package-tracking");
                await Compose(state.ActiveSha, "up", "-d", "--wait", "db");
                state.BackupPath = await Backup(state.ActiveSha);
            }
            // Persist before the command: startup applies migrations in the backend entrypoint.
            state.MigrationStarted = true;
            install.Save(state);
            status("Starting services and waiting for health checks");
            await Compose(sha, "up", "-d", "--wait", "--wait-timeout", "600", "--remove-orphans");
            state.PreviousSha = state.ActiveSha;
            state.ActiveSha = sha;
            if (source is IReleaseSource release) state.MacReleaseTag = release.ReleaseTag;
            state.PendingSha = null;
            state.MigrationStarted = false;
            install.Save(state);
        }
        catch
        {
            if (!state.MigrationStarted)
            {
                state.PendingSha = null;
                install.Save(state);
                if (writersStopped && state.ActiveSha != null)
                    await Compose(state.ActiveSha, "up", "-d", "--wait", "--wait-timeout", "600");
            }
            else
            {
                // Do not leave scheduled writers running on a partially failed upgrade.
                try { await Compose(sha, "stop", "frontend", "backend", "package-tracking"); } catch { /* Journal stays authoritative. */ }
            }
            throw;
        }
        RefreshExtensions(state);
        install.Save(state);
        status("Running");
    }

    private async Task<string> Backup(string sha)
    {
        Directory.CreateDirectory(install.Backups);
        var name = $"pre-update-{DateTime.UtcNow:yyyyMMdd-HHmmss}-{Guid.NewGuid():N}.dump";
        var containerPath = "/tmp/" + name;
        var path = Path.Combine(install.Backups, name);
        try
        {
            await Compose(sha, "exec", "-T", "db", "pg_dump", "-U", "postgres", "-d", "order_management", "-Fc", "-f", containerPath);
            await Compose(sha, "exec", "-T", "db", "pg_restore", "--list", containerPath);
            await Compose(sha, "cp", "db:" + containerPath, path);
            if (!File.Exists(path) || new FileInfo(path).Length == 0) throw new IOException("Database backup is empty or missing.");
            // Verify the actual copied backup too, not only the source in the database container.
            await Compose(sha, "cp", path, "db:" + containerPath + ".verify");
            await Compose(sha, "exec", "-T", "db", "pg_restore", "--list", containerPath + ".verify");
            return path;
        }
        finally
        {
            try { await Compose(sha, "exec", "-T", "db", "rm", "-f", containerPath, containerPath + ".verify"); } catch { }
        }
    }

    public void RefreshExtensions(InstallationState state)
    {
        if (state.ActiveSha == null) return;
        var directory = Path.Combine(install.Source(state.ActiveSha), "browser-extension");
        using var release = JsonDocument.Parse(File.ReadAllText(Path.Combine(directory, "release", "release.json")));
        var fingerprint = release.RootElement.GetProperty("fingerprint").GetString();
        if (fingerprint == state.ExtensionFingerprint && File.Exists(Path.Combine(install.Chrome, "manifest.json"))) return;
        var stage = install.Chrome + ".staging";
        if (Directory.Exists(stage)) Directory.Delete(stage, true);
        Directory.CreateDirectory(stage);
        // Copy runtime files only. Never distribute signing credentials, build tools or archives.
        foreach (var name in new[] { "manifest.json", "background.js" }) File.Copy(Path.Combine(directory, name), Path.Combine(stage, name));
        foreach (var name in new[] { "lib", "stores", "buying_groups", "bulk", "options", "popup", "order_manager", "icons" })
        {
            var from = Path.Combine(directory, name);
            if (!Directory.Exists(from)) continue;
            foreach (var file in Directory.EnumerateFiles(from, "*", SearchOption.AllDirectories))
            {
                var target = Path.Combine(stage, name, Path.GetRelativePath(from, file));
                Directory.CreateDirectory(Path.GetDirectoryName(target)!);
                File.Copy(file, target);
            }
        }
        // Chromium requires its own manifest without Firefox's background scripts/settings.
        var manifestPath = Path.Combine(stage, "manifest.json");
        var manifest = System.Text.Json.Nodes.JsonNode.Parse(File.ReadAllText(manifestPath))!.AsObject();
        manifest.Remove("browser_specific_settings");
        manifest["background"]!.AsObject().Remove("scripts");
        File.WriteAllText(manifestPath, manifest.ToJsonString());
        // Preserve the canonical path used by Load Unpacked. Retain a previous copy
        // across crashes so the next Start can reconstruct a complete directory.
        var previous = install.Chrome + ".previous";
        if (Directory.Exists(previous)) Directory.Delete(previous, true);
        if (Directory.Exists(install.Chrome)) Directory.Move(install.Chrome, previous);
        Directory.Move(stage, install.Chrome);
        state.ExtensionFingerprint = fingerprint;
    }

    // Only used after the operator has restored the documented database backup.
    public Task CompleteRecovery() => Exclusive(async () =>
    {
        var state = install.Load();
        if (!state.RecoveryRequired || state.ActiveSha == null) throw new InvalidOperationException("No interrupted update requires recovery.");
        await CheckDocker();
        if (state.PendingSha != null) await Compose(state.PendingSha, "stop", "frontend", "backend", "package-tracking");
        await Compose(state.ActiveSha, "up", "-d", "--wait", "--wait-timeout", "600");
        state.MigrationStarted = false;
        state.PendingSha = null;
        install.Save(state);
        status("Running");
    });
}
