using System.Diagnostics;
using System.Text.Json;
using OrderManager.Core;
using OrderManager.Mac;

if (!OperatingSystem.IsMacOS()) { Console.Error.WriteLine("This helper requires macOS."); return 1; }
if (args.FirstOrDefault() == "self-check")
{
    var bootstrap = ReleaseManifest.Read(Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "../Resources/bootstrap.json")));
    var probe = new ProcessRunner(_ => { });
    bootstrap.Validate(await MacRunner.CheckPlatform(probe), bootstrap: true);
    Console.WriteLine("{\"type\":\"complete\",\"data\":true}");
    return 0;
}
var outputLock = new object();
var root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Library/Application Support/OrderManager");
var install = new Installation(root, DeploymentSettings.Mac);
Installation.PrivateDirectory(root);
Installation.PrivateDirectory(install.Logs);
void Emit(string type, object data)
{
    lock (outputLock) Console.WriteLine(JsonSerializer.Serialize(new { type, data }, ReleaseManifest.Json));
}
void Log(string line)
{
    lock (outputLock)
    {
        var path = Path.Combine(install.Logs, $"launcher-{DateTime.UtcNow:yyyy-MM-dd}.log");
        var options = new FileStreamOptions { Mode = FileMode.Append, Access = FileAccess.Write };
        if (!OperatingSystem.IsWindows()) options.UnixCreateMode = UnixFileMode.UserRead | UnixFileMode.UserWrite;
        using (var file = new FileStream(path, options))
        using (var writer = new StreamWriter(file)) writer.WriteLine($"{DateTime.UtcNow:O} {line}");
        Emit("activity", line);
    }
}
var runner = new MacRunner(Log);
void Status(string line) => Emit("progress", line);
try
{
    var version = await MacRunner.CheckPlatform(runner);
    var command = args.FirstOrDefault() ?? "status";
    if (command == "replace")
    {
        await LauncherReplacement.Replace(install, runner, int.Parse(args[1]));
        return 0;
    }
    using var operation = new OperationLock(install.Config);
    var bundle = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "../.."));
    var bundled = ReleaseManifest.Read(Path.Combine(bundle, "Contents/Resources/bootstrap.json"));
    bundled.Validate(version, bootstrap: true);
    using var http = new HttpClient { Timeout = TimeSpan.FromMinutes(30) };
    http.DefaultRequestHeaders.UserAgent.ParseAdd("OrderManager-Mac/1.0");
    var source = new MacReleases(http, install, bundled, version, install.Load().ActiveSha == null, runner, Status);
    var manager = new ServiceManager(install, runner, source, Status, prepareLauncher: source.PrepareLauncher);
    if (command == "update" && File.Exists(LauncherReplacement.Journal(install)))
        throw new IOException("Finish the pending launcher replacement first. Choose Start to retry, or reinstall the Mac installer. Existing services are retained.");
    switch (command)
    {
        case "status":
            var s = install.Load();
            Emit("state", new { status = await manager.GetStatus(), s.AutoStart, s.RecoveryRequired,
                extensionReminder = s.ExtensionFingerprint != s.AcknowledgedExtension, install.Chrome,
                installed = File.Exists(install.EnvFile), replacementPending = File.Exists(LauncherReplacement.Journal(install)),
                replacementFailed = File.Exists(LauncherReplacement.Failure(install)) });
            break;
        case "install-docker": await runner.InstallDocker(http, install, Status); break;
        case "start": await runner.EnsureDocker(manager, Status); await manager.Start(); break;
        case "restart": await runner.EnsureDocker(manager, Status); await manager.Restart(); break;
        case "update": await runner.EnsureDocker(manager, Status); await manager.Update(); break;
        case "stop": await manager.Stop(); break;
        case "recover":
            if (!args.Contains("--confirmed")) throw new IOException("Confirm that the documented backup restore succeeded before resuming.");
            await runner.EnsureDocker(manager, Status); await manager.CompleteRecovery(); break;
        case "recovery":
            var docker = Path.Combine(MacRunner.DockerApp() ?? "/Applications/Docker.app", "Contents/Resources/bin/docker");
            var recovery = Path.Combine(install.Logs, "recovery.txt");
            Installation.PrivateWrite(recovery, MacRecovery.Instructions(install, install.Load(), docker));
            Emit("open", recovery); break;
        case "autostart":
            var auto = install.Load(); auto.AutoStart = args.ElementAtOrDefault(1) == "true"; install.Save(auto); break;
        case "acknowledge-extensions":
            var extensions = install.Load(); extensions.AcknowledgedExtension = extensions.ExtensionFingerprint; install.Save(extensions); break;
        case "stage-replacement":
            var active = install.Load().ActiveSha;
            if (active == null || install.Load().RecoveryRequired) break;
            var candidate = Path.Combine(install.Source(active), "launcher/Order Manager.app");
            if (!Directory.Exists(candidate) && !File.Exists(LauncherReplacement.Journal(install))) break;
            if (File.Exists(LauncherReplacement.Journal(install)))
            {
                LauncherReplacement.Read(install);
                await MacReleases.VerifyBundle(Directory.Exists(candidate) ? candidate : LauncherReplacement.Target, runner, active);
            }
            else
            {
                await MacReleases.VerifyBundle(candidate, runner, active);
                LauncherReplacement.Save(install, new(candidate, LauncherReplacement.Target, LauncherReplacement.Target + ".previous"));
            }
            var parent = int.Parse(args[1]);
            if (parent <= 1) throw new IOException("Invalid launcher process.");
            var replacer = Path.Combine(install.Config, "replacer");
            if (Directory.Exists(replacer)) Directory.Delete(replacer, true);
            Directory.CreateDirectory(replacer);
            foreach (var file in Directory.EnumerateFiles(AppContext.BaseDirectory))
            {
                var target = Path.Combine(replacer, Path.GetFileName(file)); File.Copy(file, target);
                File.SetUnixFileMode(target, File.GetUnixFileMode(file));
            }
            // nohup and detached file descriptors let the replacement outlive the UI/helper.
            var launch = new ProcessStartInfo("/bin/sh") { UseShellExecute = false };
            foreach (var arg in new[] { "-c", "log=$1; shift; exec /usr/bin/nohup \"$@\" </dev/null >>\"$log\" 2>&1", "order-manager-replace", Path.Combine(install.Logs, "launcher-replacement.log"), Path.Combine(replacer, "OrderManager.Mac"), "replace", parent.ToString() }) launch.ArgumentList.Add(arg);
            // The shell redirects every inherited descriptor; no pipe holds the UI operation open.
            _ = Process.Start(launch) ?? throw new IOException("Could not launch replacement helper.");
            Emit("replacement", true);
            break;
        default: throw new ArgumentException("Unknown helper command.");
    }
    Emit("complete", true);
    return 0;
}
catch (Exception ex)
{
    Log(ex.Message);
    Emit("error", ex.Message);
    return 1;
}
