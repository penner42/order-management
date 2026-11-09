using System.IO.Compression;
using System.Text.Json;
using OrderManager.Core;

// Dependency-free executable tests run in the .NET SDK container. All Docker
// commands and GitHub downloads are faked; these tests cannot touch real volumes.
var tests = new (string Name, Func<Task> Test)[]
{
    ("environment is minimal, random and preserved", async () =>
    {
        using var f = new Fixture();
        f.Install.EnsureEnvironment();
        var original = File.ReadAllText(f.Install.EnvFile);
        Check(original.Split('\n', StringSplitOptions.RemoveEmptyEntries).Length == 3);
        Check(original.Contains("ADMIN_USERNAME=admin\nADMIN_PASSWORD=admin\n"));
        Check(original.Split('\n')[0].Length == 107);
        f.Install.EnsureEnvironment();
        Check(File.ReadAllText(f.Install.EnvFile) == original);
        using var other = new Fixture();
        other.Install.EnsureEnvironment();
        Check(File.ReadAllText(other.Install.EnvFile) != original);
        await Task.CompletedTask;
    }),
    ("archive extraction accepts spaces and rejects unsafe paths", async () =>
    {
        using var f = new Fixture();
        var target = Path.Combine(f.Install.Root, "safe path");
        using (var zip = Archive("root/docker-compose.windows.yml", "root/windows/Dockerfile"))
            Installation.ExtractArchive(zip, target);
        Check(File.Exists(Path.Combine(target, "windows", "Dockerfile")));
        foreach (var name in new[] { "root/../escape", "root/C:/escape", "root/a\\..\\escape", "root/CON.txt", "other/file" })
        {
            using var zip = Archive("root/docker-compose.windows.yml", "root/windows/Dockerfile", name);
            await Throws<InvalidDataException>(() => Task.Run(() => Installation.ExtractArchive(zip, Path.Combine(f.Install.Root, Guid.NewGuid().ToString()))));
        }
        await Throws<InvalidDataException>(() => Task.Run(() => f.Install.Source("../outside")));
    }),
    ("Docker readiness requires daemon, Linux mode and Compose", async () =>
    {
        using var f = new Fixture();
        f.Runner.Os = "windows";
        await Throws<InvalidOperationException>(f.Manager.CheckDocker);
        f.Runner.Os = "linux";
        f.Runner.Fail = a => a.SequenceEqual(new[] { "compose", "version" });
        await Throws<IOException>(f.Manager.CheckDocker);
        f.Runner.Fail = a => a[0] == "info";
        await Throws<IOException>(f.Manager.CheckDocker);
    }),
    ("operation lock rejects concurrent work and releases after failure", async () =>
    {
        using var f = new Fixture();
        var entered = new TaskCompletionSource();
        var release = new TaskCompletionSource();
        var task = f.Manager.Exclusive(async () => { entered.SetResult(); await release.Task; });
        await entered.Task;
        await Throws<InvalidOperationException>(() => f.Manager.Exclusive(() => Task.CompletedTask));
        release.SetResult(); await task;
        await Throws<IOException>(() => f.Manager.Exclusive(() => throw new IOException()));
        await f.Manager.Exclusive(() => Task.CompletedTask);
    }),
    ("first install stages exact commit and extensions without backup", async () =>
    {
        using var f = new Fixture();
        await f.Manager.Start();
        var state = f.Install.Load();
        Check(state.ActiveSha == Fixture.New && state.PendingSha == null && !state.MigrationStarted);
        Check(!f.Runner.Calls.Any(c => c.Contains("pg_dump")));
        Check(File.Exists(Path.Combine(f.Install.Chrome, "manifest.json")));
        var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(f.Install.Chrome, "manifest.json"))).RootElement;
        Check(!manifest.TryGetProperty("browser_specific_settings", out _));
        Check(!manifest.GetProperty("background").TryGetProperty("scripts", out _));
        Check(manifest.GetProperty("key").GetString() == "stable-key");
        Check(state.ExtensionFingerprint != state.AcknowledgedExtension);
    }),
    ("update builds before stopping writers and backs up before migrations", async () =>
    {
        using var f = new Fixture(); f.Existing();
        await f.Manager.Update();
        var calls = f.Runner.Calls;
        int Index(string arg) => calls.FindIndex(c => c.Contains(arg));
        Check(Index("build") < Index("stop"));
        Check(Index("--target") < Index("stop"));
        Check(Index("stop") < Index("pg_dump"));
        Check(Index("pg_restore") < Index("--wait-timeout"));
        var state = f.Install.Load();
        Check(state.ActiveSha == Fixture.New && state.PreviousSha == Fixture.Old);
        Check(File.Exists(state.BackupPath));
        Check(Directory.Exists(f.Install.Source(Fixture.Old)));
        Check(f.Runner.Environments.Any(e => e?.GetValueOrDefault("ORDER_MANAGER_VERSION") == Fixture.New));
    }),
    ("unchanged update does not stop or build", async () =>
    {
        using var f = new Fixture(); f.Existing(); f.Source.Sha = Fixture.Old;
        await f.Manager.Update();
        Check(!f.Runner.Calls.Any(c => c.Contains("stop") || c.Contains("build")));
    }),
    ("candidate build failure leaves old version active", async () =>
    {
        using var f = new Fixture(); f.Existing();
        f.Runner.Fail = a => a.Contains("build");
        await Throws<IOException>(f.Manager.Update);
        Check(f.Install.Load().ActiveSha == Fixture.Old && f.Install.Load().PendingSha == null);
        Check(!f.Runner.Calls.Any(c => c.Contains("stop")));
    }),
    ("backup failure restarts old services without migrating candidate", async () =>
    {
        using var f = new Fixture(); f.Existing();
        f.Runner.Fail = a => a.Contains("pg_dump");
        await Throws<IOException>(f.Manager.Update);
        Check(!f.Install.Load().MigrationStarted && f.Install.Load().PendingSha == null);
        Check(f.Runner.Calls.Last().Contains("--wait-timeout"));
        Check(f.Runner.Environments.Last()!["ORDER_MANAGER_VERSION"] == Fixture.Old);
    }),
    ("copied backup must be nonempty and parseable", async () =>
    {
        using var f = new Fixture(); f.Existing(); f.Runner.EmptyBackup = true;
        await Throws<IOException>(f.Manager.Update);
        Check(!f.Install.Load().MigrationStarted);
        using var g = new Fixture(); g.Existing();
        g.Runner.Fail = a => a.Contains("pg_restore") && a.Last().EndsWith(".verify");
        await Throws<IOException>(g.Manager.Update);
        Check(!g.Install.Load().MigrationStarted);
    }),
    ("migration or health failure blocks automatic rollback and restart", async () =>
    {
        using var f = new Fixture(); f.Existing();
        f.Runner.Fail = a => a.Contains("--remove-orphans");
        await Throws<IOException>(f.Manager.Update);
        var state = f.Install.Load();
        Check(state.RecoveryRequired && state.ActiveSha == Fixture.Old && state.PendingSha == Fixture.New);
        Check(File.Exists(state.BackupPath));
        Check(f.Runner.Calls.Last().Contains("stop"));
        await Throws<InvalidOperationException>(f.Manager.Start);
        await Throws<InvalidOperationException>(f.Manager.Restart);
        await Throws<InvalidOperationException>(f.Manager.Update);
        Check(await f.Manager.GetStatus() == "Recovery required");
        f.Runner.Fail = null;
        await f.Manager.CompleteRecovery();
        Check(!f.Install.Load().RecoveryRequired && f.Install.Load().ActiveSha == Fixture.Old);
    }),
    ("pre-migration interruption safely resumes old version", async () =>
    {
        using var f = new Fixture(); f.Existing();
        var state = f.Install.Load(); state.PendingSha = Fixture.New; f.Install.Save(state);
        await f.Manager.Start();
        Check(f.Install.Load().PendingSha == null);
        Check(!f.Runner.Calls.Any(c => c.Contains("pg_dump") || c.Contains("build")));
    }),
    ("initial migration failure can retry forward", async () =>
    {
        using var f = new Fixture();
        f.Runner.Fail = a => a.Contains("--remove-orphans");
        await Throws<IOException>(f.Manager.Start);
        Check(f.Install.Load().ActiveSha == null);
        f.Runner.Fail = null;
        await f.Manager.Start();
        Check(f.Install.Load().ActiveSha == Fixture.New);
    }),
    ("process arguments are literal and timeouts kill child processes", async () =>
    {
        var runner = new ProcessRunner(_ => { });
        var literal = "a path with spaces; $(printf unexpected) `echo hi`";
        var result = await runner.Run("/usr/bin/printf", ["%s", literal]);
        Check(result.Output.TrimEnd() == literal);
        await Throws<TimeoutException>(() => runner.Run("/bin/sh", ["-c", "sleep 30"], timeout: TimeSpan.FromMilliseconds(50)));
    }),
};
foreach (var (name, test) in tests)
{
    await test();
    Console.WriteLine("PASS " + name);
}
Console.WriteLine($"{tests.Length} launcher tests passed.");

static void Check(bool value) { if (!value) throw new Exception("Assertion failed"); }
static async Task Throws<T>(Func<Task> action) where T : Exception
{
    try { await action(); } catch (T) { return; }
    throw new Exception("Expected " + typeof(T).Name);
}
static MemoryStream Archive(params string[] names)
{
    var stream = new MemoryStream();
    using (var zip = new ZipArchive(stream, ZipArchiveMode.Create, true))
        foreach (var name in names) { using var w = new StreamWriter(zip.CreateEntry(name).Open()); w.Write("fixture"); }
    stream.Position = 0;
    return stream;
}

sealed class Fixture : IDisposable
{
    public const string Old = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    public const string New = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    public Installation Install { get; } = new(Path.Combine(Path.GetTempPath(), "Order Manager tests " + Guid.NewGuid()));
    public FakeRunner Runner { get; }
    public FakeSource Source { get; } = new();
    public ServiceManager Manager { get; }
    public Fixture()
    {
        Runner = new FakeRunner();
        Manager = new(Install, Runner, Source, _ => { });
    }
    public void Existing()
    {
        Install.EnsureEnvironment();
        Source.Download(Old, Install.Source(Old)).GetAwaiter().GetResult();
        Install.Save(new() { ActiveSha = Old });
    }
    public void Dispose() { if (Directory.Exists(Install.Root)) Directory.Delete(Install.Root, true); }
}
sealed class FakeSource : ISourceProvider
{
    public string Sha { get; set; } = Fixture.New;
    public Task<string> Latest() => Task.FromResult(Sha);
    public Task Download(string sha, string destination)
    {
        Directory.CreateDirectory(Path.Combine(destination, "browser-extension", "release"));
        File.WriteAllText(Path.Combine(destination, "browser-extension", "release", "release.json"), "{\"fingerprint\":\"" + sha + "\"}");
        File.WriteAllText(Path.Combine(destination, "browser-extension", "manifest.json"), "{\"key\":\"stable-key\",\"background\":{\"scripts\":[],\"service_worker\":\"background.js\"},\"browser_specific_settings\":{}}");
        File.WriteAllText(Path.Combine(destination, "browser-extension", "background.js"), "");
        return Task.CompletedTask;
    }
}
sealed class FakeRunner : ICommandRunner
{
    public List<string[]> Calls { get; } = [];
    public List<Dictionary<string, string>?> Environments { get; } = [];
    public Func<string[], bool>? Fail { get; set; }
    public string Os { get; set; } = "linux";
    public bool EmptyBackup { get; set; }
    public Task<CommandResult> Run(string executable, IReadOnlyList<string> arguments, IReadOnlyDictionary<string, string>? environment = null, TimeSpan? timeout = null)
    {
        var args = arguments.ToArray(); Calls.Add(args); Environments.Add(environment?.ToDictionary());
        if (Fail?.Invoke(args) == true) return Task.FromResult(new CommandResult(1, "failure"));
        if (args.Contains("--target"))
        {
            var path = args[Array.IndexOf(args, "--output") + 1].Split("dest=")[1];
            Directory.CreateDirectory(path); File.WriteAllText(Path.Combine(path, "OrderManager.exe"), "fixture");
        }
        if (args.Contains("cp"))
        {
            var index = Array.IndexOf(args, "cp");
            if (args[index + 1].StartsWith("db:")) File.WriteAllText(args[index + 2], EmptyBackup ? "" : "dump");
        }
        return Task.FromResult(new CommandResult(0, args[0] == "info" ? Os : ""));
    }
}
