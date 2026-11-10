using System.IO.Compression;
using System.Net;
using System.Text;
using System.Text.Json;
using OrderManager.Core;
using OrderManager.Mac;

var tests = new (string Name, Func<Task> Run)[] {
    ("manifest compatibility and asset binding", () => {
        var manifest = Manifest(); manifest.Validate(new Version(15, 0));
        Reject(() => (manifest with { Architecture = "x64" }).Validate(new Version(15, 0)));
        Reject(() => (manifest with { HelperProtocolVersion = 2 }).Validate(new Version(15, 0)));
        Reject(() => (manifest with { MinimumMacOS = "99.0" }).Validate(new Version(15, 0)));
        Reject(() => (manifest with { LauncherUrl = "https://evil.example/file.zip" }).Validate(new Version(15, 0)));
        Reject(() => (manifest with { SourceSha = "../../etc" }).Validate(new Version(15, 0)));
        return Task.CompletedTask;
    }),
    ("private settings, persistent secret, and process lock", () => {
        WithDirectory(root => {
            var install = new Installation(root, DeploymentSettings.Mac); install.EnsureEnvironment();
            var original = File.ReadAllText(install.EnvFile); install.EnsureEnvironment();
            Check(original == File.ReadAllText(install.EnvFile));
            if (!OperatingSystem.IsWindows()) Check((File.GetUnixFileMode(install.EnvFile) & (UnixFileMode.GroupRead | UnixFileMode.OtherRead)) == 0);
            using (var held = new OperationLock(install.Config)) Reject(() => { using var other = new OperationLock(install.Config); });
            using var released = new OperationLock(install.Config);
        }); return Task.CompletedTask;
    }),
    ("safe launcher extraction rejects traversal and links and preserves executables", () => {
        foreach (var name in new[] { "Order Manager.app/../outside", "/Order Manager.app/Contents/file", "Other.app/file", "Order Manager.app/Contents/link" })
            WithDirectory(root => {
                var zipPath = Path.Combine(root, "test.zip");
                using (var zip = ZipFile.Open(zipPath, ZipArchiveMode.Create)) {
                    var entry = zip.CreateEntry(name); if (name.EndsWith("link")) entry.ExternalAttributes = unchecked((int)0xA1FF0000);
                    using var writer = new StreamWriter(entry.Open()); writer.Write("bad");
                }
                Reject(() => SafeZip.Extract(zipPath, Path.Combine(root, "out")));
            });
        WithDirectory(root => {
            var path = Path.Combine(root, "test.zip");
            using (var zip = ZipFile.Open(path, ZipArchiveMode.Create)) {
                var entry = zip.CreateEntry("Order Manager.app/Contents/MacOS/OrderManager"); entry.ExternalAttributes = unchecked((int)0x81ED0000);
                using var writer = new StreamWriter(entry.Open()); writer.Write("binary");
            }
            SafeZip.Extract(path, Path.Combine(root, "out"));
            if (!OperatingSystem.IsWindows()) Check(File.GetUnixFileMode(Path.Combine(root, "out/Order Manager.app/Contents/MacOS/OrderManager")).HasFlag(UnixFileMode.UserExecute));
        }); return Task.CompletedTask;
    }),
    ("Mac source marker is independent of Windows source marker", () => {
        WithDirectory(root => {
            using var memory = new MemoryStream();
            using (var zip = new ZipArchive(memory, ZipArchiveMode.Create, true)) {
                zip.CreateEntry("repo/docker-compose.macos.yml"); zip.CreateEntry("repo/macos/Helper/OrderManager.Mac.csproj");
            }
            memory.Position = 0; Installation.ExtractArchive(memory, Path.Combine(root, "mac"), DeploymentSettings.Mac);
            memory.Position = 0; Reject(() => Installation.ExtractArchive(memory, Path.Combine(root, "win")));
        }); return Task.CompletedTask;
    }),
    ("release selection ignores Windows latest and incompatible releases, paginates", async () => {
        var handler = new FakeHttp(); using var http = new HttpClient(handler);
        var root = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString());
        var source = new MacReleases(http, new Installation(root, DeploymentSettings.Mac), Manifest(), new Version(15, 0), false, new Runner(), _ => {});
        Check(await source.Latest() == Manifest().SourceSha);
        Check(handler.Requests.Any(u => u.Contains("page=2")));
    }),
    ("an older workflow rerun never downgrades an installed release", async () => {
        var root = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString()); Directory.CreateDirectory(root);
        try {
            var install = new Installation(root, DeploymentSettings.Mac);
            install.Save(new InstallationState { ActiveSha = new string('b',40), MacReleaseTag = "mac-installer-2-1" });
            var handler = new FakeHttp(); using var http = new HttpClient(handler);
            var source = new MacReleases(http,install,Manifest(),new Version(15,0),false,new Runner(),_ => {});
            Check(await source.Latest() == new string('b',40));
            Check(!handler.Requests.Any(url => url.Contains("mac-release.json")));
            Check(MacReleases.ReleaseOrder("mac-installer-10-1").CompareTo(MacReleases.ReleaseOrder("mac-installer-9-99")) > 0);
        } finally { Directory.Delete(root,true); }
    }),
    ("initial install uses bundled commit without a release lookup", async () => {
        using var http = new HttpClient(new RejectHttp());
        var source = new MacReleases(http, new Installation("/tmp/unused", DeploymentSettings.Mac), Manifest(), new Version(15, 0), true, new Runner(), _ => {});
        Check(await source.Latest() == Manifest().SourceSha); await source.PrepareLauncher(Manifest().SourceSha);
    }),
    ("recovery commands bind local Docker and quote paths", () => {
        var install = new Installation("/tmp/person's application", DeploymentSettings.Mac);
        var state = new InstallationState { ActiveSha = new string('a',40), PendingSha = new string('b',40), MigrationStarted = true, BackupPath = "/tmp/backup's file" };
        var text = MacRecovery.Instructions(install, state, "/Applications/Docker.app/Contents/Resources/bin/docker");
        Check(text.Contains("--context desktop-linux")); Check(text.Contains("order-manager-macos"));
        Check(text.Contains("'\"'\"'")); Check(text.Contains("--single-transaction")); Check(text.Contains(new string('b',40)));
        return Task.CompletedTask;
    }),
    ("Mac deployment injects launcher preparation and keeps migration protections", async () => {
        foreach (var failure in new[] { "", "prepare", "pg_dump", "--remove-orphans" }) {
            var root = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString()); Directory.CreateDirectory(root);
            try {
                var install = new Installation(root, DeploymentSettings.Mac);
                var old = new string('a',40); var next = new string('b',40);
                Directory.CreateDirectory(install.Source(old)); install.Save(new InstallationState { ActiveSha = old });
                var runner = new LifecycleRunner { Fail = failure };
                var prepared = false;
                var manager = new ServiceManager(install, runner, new LifecycleSource(next), _ => {}, prepareLauncher: sha => {
                    Check(sha == next); Check(!runner.Calls.Any(a => a.Contains("stop"))); prepared = true;
                    if (failure == "prepare") throw new IOException("Candidate failed"); return Task.CompletedTask;
                });
                if (failure.Length > 0) { try { await manager.Update(); throw new Exception("Expected failure"); } catch (IOException) {} }
                else await manager.Update();
                Check(prepared); Check(runner.Calls.Where(a => a.Contains("--project-name")).All(a => a.Contains("order-manager-macos")));
                Check(!runner.Calls.Any(a => a.Contains("--target")));
                var state = install.Load();
                if (failure == "--remove-orphans") {
                    Check(state.RecoveryRequired && state.ActiveSha == old);
                    try { await manager.Start(); throw new Exception("Recovery was not guarded"); } catch (InvalidOperationException) {}
                } else if (failure.Length > 0) Check(state.ActiveSha == old && !state.MigrationStarted);
                else Check(state.ActiveSha == next && File.Exists(state.BackupPath));
            } finally { Directory.Delete(root,true); }
        }
    }),
    ("failed launcher checksum cannot prepare an executable", async () => {
        var root = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString()); Directory.CreateDirectory(root);
        try {
            var install = new Installation(root, DeploymentSettings.Mac); Directory.CreateDirectory(install.Source(Manifest().SourceSha));
            using var http = new HttpClient(new FakeHttp());
            var source = new MacReleases(http,install,Manifest(),new Version(15,0),false,new Runner(),_ => {});
            var sha = await source.Latest();
            try { await source.PrepareLauncher(sha); throw new Exception("Expected checksum failure"); } catch (InvalidDataException) {}
            Check(!Directory.Exists(Path.Combine(install.Source(sha),"launcher")));
        } finally { Directory.Delete(root,true); }
    }),
    ("Docker commands select local Desktop and remove inherited remote settings", async () => {
        var before = Environment.GetEnvironmentVariable("DOCKER_HOST");
        var beforeCompose = Environment.GetEnvironmentVariable("COMPOSE_FILE");
        try {
            Environment.SetEnvironmentVariable("DOCKER_HOST", "tcp://unrelated.invalid:2376");
            Environment.SetEnvironmentVariable("COMPOSE_FILE", "/tmp/unrelated.yml");
            var captured = new CaptureRunner();
            var runner = new MacRunner(_ => {}, captured, () => "/Applications/Docker.app");
            await runner.Run("docker", ["info"], new Dictionary<string,string> { ["ORDER_MANAGER_VERSION"] = "version" });
            Check(captured.Executable == "/Applications/Docker.app/Contents/Resources/bin/docker");
            Check(captured.Arguments.SequenceEqual(new[] { "--context", "desktop-linux", "info" }));
            Check(captured.Environment!["DOCKER_HOST"] == null && captured.Environment["COMPOSE_FILE"] == null);
            Check(captured.Environment["ORDER_MANAGER_VERSION"] == "version");
        } finally { Environment.SetEnvironmentVariable("DOCKER_HOST", before); Environment.SetEnvironmentVariable("COMPOSE_FILE", beforeCompose); }
    }),
    ("launcher swap resumes before and after either rename without touching service state", async () => {
        foreach (var interruption in new[] { "before", "old-moved", "new-moved", "invalid" }) {
            var root = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString()); Directory.CreateDirectory(root);
            try {
                var state = new ReplacementState(Path.Combine(root,"candidate"),Path.Combine(root,"target"),Path.Combine(root,"previous"));
                void Make(string path, string marker) { Directory.CreateDirectory(path); File.WriteAllText(Path.Combine(path,"version"),marker); }
                if (interruption != "new-moved") Make(state.Candidate,"new");
                if (interruption == "old-moved" || interruption == "new-moved") Make(state.Previous,"old");
                if (interruption != "old-moved") Make(state.Target,interruption == "new-moved" ? "new" : "old");
                Task Verify(string path) {
                    if (interruption == "invalid" || File.ReadAllText(Path.Combine(path,"version")) != "new") throw new InvalidDataException("Invalid candidate");
                    return Task.CompletedTask;
                }
                if (interruption == "invalid") {
                    try { await LauncherReplacement.Apply(state,Verify); throw new Exception("Expected invalid candidate"); } catch (InvalidDataException) {}
                    Check(File.ReadAllText(Path.Combine(state.Target,"version")) == "old");
                } else {
                    await LauncherReplacement.Apply(state,Verify);
                    Check(File.ReadAllText(Path.Combine(state.Target,"version")) == "new");
                    Check(File.ReadAllText(Path.Combine(state.Previous,"version")) == "old");
                }
            } finally { Directory.Delete(root,true); }
        }
    }),
    ("replacement journal is separate and rejects paths outside active release", () => {
        WithDirectory(root => {
            var install = new Installation(root, DeploymentSettings.Mac); var state = new InstallationState { ActiveSha = new string('a',40) }; install.Save(state);
            LauncherReplacement.Save(install, new(Path.Combine(install.Source(state.ActiveSha), "launcher/Order Manager.app"), LauncherReplacement.Target, LauncherReplacement.Target + ".previous"));
            Check(LauncherReplacement.Read(install) != null); Check(!install.Load().MigrationStarted);
            LauncherReplacement.Save(install, new("/tmp/unrelated", LauncherReplacement.Target, LauncherReplacement.Target + ".previous"));
            Reject(() => LauncherReplacement.Read(install));
        }); return Task.CompletedTask;
    }),
};
foreach (var (name, run) in tests) { await run(); Console.WriteLine("PASS " + name); }
Console.WriteLine($"{tests.Length} Mac tests passed");
static void Check(bool value) { if (!value) throw new Exception("Assertion failed"); }
static void Reject(Action action) { try { action(); } catch (Exception ex) when (ex is IOException or InvalidDataException or InvalidOperationException) { return; } throw new Exception("Expected rejection"); }
static void WithDirectory(Action<string> action) { var root = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString()); Directory.CreateDirectory(root); try { action(root); } finally { Directory.Delete(root, true); } }
static ReleaseManifest Manifest() => new(1, new string('a',40), "arm64", "14.0", 1, "mac-installer-1-1", "https://github.com/penner42/order-management/releases/download/mac-installer-1-1/OrderManager-arm64.zip", new string('a',64));
sealed class Runner : ICommandRunner {
    public Task<CommandResult> Run(string executable, IReadOnlyList<string> arguments, IReadOnlyDictionary<string,string>? environment = null, TimeSpan? timeout = null) => Task.FromResult(new CommandResult(0,""));
}
sealed class RejectHttp : HttpMessageHandler {
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) => throw new Exception("Unexpected network request");
}
sealed class FakeHttp : HttpMessageHandler {
    public List<string> Requests { get; } = [];
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) {
        var url = request.RequestUri!.ToString(); Requests.Add(url); object value;
        if (url.Contains("mac-release.json")) value = new ReleaseManifest(1, new string('a',40), "arm64", "14.0", 1,"mac-installer-1-1", "https://github.com/penner42/order-management/releases/download/mac-installer-1-1/OrderManager-arm64.zip", new string('a',64));
        else if (url.EndsWith("&page=1", StringComparison.Ordinal)) value = Enumerable.Range(0,100).Select(i => new { draft=false, prerelease=false, tag_name="windows-installer-" + i, published_at="2026-11-09T00:00:00Z", assets=Array.Empty<object>() }).ToArray();
        else value = new[] { new { draft=false, prerelease=false, tag_name="mac-installer-1-1", published_at="2026-11-08T00:00:00Z", assets=new[] { new { browser_download_url="https://github.com/penner42/order-management/releases/download/mac-installer-1-1/mac-release.json" } } } };
        return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(JsonSerializer.Serialize(value,ReleaseManifest.Json),Encoding.UTF8,"application/json") });
    }
}

sealed class LifecycleSource(string sha) : ISourceProvider {
    public Task<string> Latest() => Task.FromResult(sha);
    public Task Download(string value, string destination) {
        Directory.CreateDirectory(Path.Combine(destination,"browser-extension/release"));
        File.WriteAllText(Path.Combine(destination,"browser-extension/release/release.json"),"{\"fingerprint\":\"test\"}");
        File.WriteAllText(Path.Combine(destination,"browser-extension/manifest.json"),"{\"background\":{\"scripts\":[],\"service_worker\":\"background.js\"}}");
        File.WriteAllText(Path.Combine(destination,"browser-extension/background.js"),"");
        return Task.CompletedTask;
    }
}
sealed class LifecycleRunner : ICommandRunner {
    public string Fail { get; set; } = "";
    public List<string[]> Calls { get; } = [];
    public Task<CommandResult> Run(string executable, IReadOnlyList<string> arguments, IReadOnlyDictionary<string,string>? environment = null, TimeSpan? timeout = null) {
        var args = arguments.ToArray(); Calls.Add(args);
        if (Fail.Length > 0 && args.Contains(Fail)) return Task.FromResult(new CommandResult(1,""));
        var cp = Array.IndexOf(args,"cp");
        if (cp >= 0 && args[cp+1].StartsWith("db:")) File.WriteAllText(args[cp+2],"dump");
        return Task.FromResult(new CommandResult(0,args[0] == "info" ? "linux" : ""));
    }
}

sealed class CaptureRunner : ICommandRunner {
    public string? Executable;
    public string[] Arguments = [];
    public IReadOnlyDictionary<string,string>? Environment;
    public Task<CommandResult> Run(string executable, IReadOnlyList<string> arguments, IReadOnlyDictionary<string,string>? environment = null, TimeSpan? timeout = null) {
        Executable = executable; Arguments = arguments.ToArray(); Environment = environment;
        return Task.FromResult(new CommandResult(0,""));
    }
}
