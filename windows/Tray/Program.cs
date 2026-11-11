using System.Diagnostics;
using System.Security.Principal;
using Microsoft.Win32;
using OrderManager.Core;

namespace OrderManager.Tray;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        ApplicationConfiguration.Initialize();
        var identity = WindowsIdentity.GetCurrent().User!.Value;
        var name = "Local\\OrderManager-" + identity;
        using var shutdown = new EventWaitHandle(false, EventResetMode.AutoReset, name + "-shutdown");
        using var mutex = new Mutex(false, name);
        bool acquired;
        var uninstall = args.Contains("--uninstall");
        var exitTray = args.Contains("--exit-tray");
        var requestShutdown = uninstall || exitTray;
        if (requestShutdown) shutdown.Set();
        try { acquired = mutex.WaitOne(requestShutdown ? 30000 : 0); }
        catch (AbandonedMutexException) { acquired = true; }
        if (!acquired)
        {
            // A failed installer attempt must not leave a shutdown queued for
            // when the active setup/update eventually finishes.
            if (exitTray) shutdown.Reset();
            return requestShutdown ? 1 : 0;
        }
        try
        {
            shutdown.Reset();
            // Installer helper: no Docker, configuration, or startup changes.
            if (exitTray) return 0;
            var root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "OrderManager");
            var installation = new Installation(root);
            if (uninstall)
            {
                try
                {
                    var runner = new ProcessRunner(_ => { });
                    var manager = new ServiceManager(installation, runner, new GitHubSource(new HttpClient()), _ => { });
                    Task.Run(manager.Stop).GetAwaiter().GetResult();
                    Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run", true)?.DeleteValue("OrderManager", false);
                    return 0;
                }
                catch (Exception ex)
                {
                    MessageBox.Show("Services could not be stopped. Start Docker Desktop and retry uninstall.\n\n" + ex.Message, "Order Manager uninstall");
                    return 1;
                }
            }
            using var app = new TrayContext(installation, shutdown);
            Application.Run(app);
            return 0;
        }
        catch (Exception ex)
        {
            MessageBox.Show(ex.Message, "Order Manager", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        finally { mutex.ReleaseMutex(); }
    }
}

internal sealed class TrayContext : ApplicationContext
{
    private readonly Installation install;
    private readonly NotifyIcon icon;
    private readonly ToolStripMenuItem stateItem = new("Starting");
    private readonly ToolStripMenuItem extensionItem = new("Browser Extensions");
    private readonly ToolStripMenuItem startupItem = new("Start at Windows sign-in");
    private readonly List<ToolStripMenuItem> actions = [];
    private readonly System.Windows.Forms.Timer timer = new() { Interval = 1000 };
    private readonly EventWaitHandle shutdown;
    private readonly HttpClient http = new() { Timeout = TimeSpan.FromMinutes(15) };
    private readonly ICommandRunner runner;
    private bool active;
    private bool polling;
    private bool awaitingReboot;
    private SetupProgressForm? progressWindow;
    private int ticks;
    private string? notifiedExtension;
    public ServiceManager Manager { get; }
    private const string PrerequisiteHelp = "Docker Desktop must use Linux containers and the WSL 2 backend.\n\n" +
        "Choose Start to check and automatically set up missing Windows features and WSL. Accept the Windows administrator prompt when shown.\n\n" +
        "Restart Windows if prompted, enable virtualization in BIOS/UEFI if required, then launch Docker Desktop and retry Start. " +
        "Setup is saved and will resume at Windows sign-in.";

    public TrayContext(Installation installation, EventWaitHandle shutdownEvent)
    {
        install = installation;
        shutdown = shutdownEvent;
        Directory.CreateDirectory(install.Logs);
        http.DefaultRequestHeaders.UserAgent.ParseAdd("OrderManager-Windows/1.0");
        runner = new ProcessRunner(Log);
        Manager = new ServiceManager(install, runner, new GitHubSource(http), SetStatus, FindDocker());
        icon = new NotifyIcon { Icon = SystemIcons.Application, Text = "Order Manager", Visible = true };
        var menu = new ContextMenuStrip();
        stateItem.Enabled = false;
        menu.Items.Add(stateItem);
        menu.Items.Add("Open Order Manager", null, (_, _) => Open("http://localhost:5173"));
        menu.Items.Add("Show setup progress", null, (_, _) => ShowProgress());
        AddAction(menu, "Start", async () => { await EnsureDocker(); await Manager.Start(); await OfferLauncherReplacement(); });
        AddAction(menu, "Stop", Manager.Stop);
        AddAction(menu, "Restart", async () => { await EnsureDocker(); await Manager.Restart(); });
        AddAction(menu, "Update", async () => { await EnsureDocker(); await Manager.Update(); await OfferLauncherReplacement(); });
        extensionItem.Click += (_, _) => ShowExtensions();
        menu.Items.Add(extensionItem);
        menu.Items.Add("View Logs", null, (_, _) => Open(install.Logs));
        menu.Items.Add("Recovery instructions", null, (_, _) => ShowRecovery());
        AddAction(menu, "Resume after manual database restore", async () =>
        {
            if (MessageBox.Show("Only continue after restoring the pre-update database backup using Recovery instructions.\n\n" +
                "Have you restored the backup and stopped all application writers?", "Confirm manual restore", MessageBoxButtons.YesNo,
                MessageBoxIcon.Warning) == DialogResult.Yes) await Manager.CompleteRecovery();
        });
        startupItem.Checked = install.Load().AutoStart;
        startupItem.Click += (_, _) =>
        {
            var state = install.Load();
            state.AutoStart = !state.AutoStart;
            install.Save(state);
            ConfigureStartup(state.AutoStart);
            startupItem.Checked = state.AutoStart;
        };
        menu.Items.Add(startupItem);
        menu.Items.Add("Exit Tray", null, (_, _) => { if (!active) ExitThread(); });
        icon.ContextMenuStrip = menu;
        icon.DoubleClick += (_, _) => { if (active || awaitingReboot) ShowProgress(); else Open("http://localhost:5173"); };
        ConfigureStartup(install.Load().AutoStart);
        timer.Tick += async (_, _) =>
        {
            if (!active && shutdown.WaitOne(0)) { ExitThread(); return; }
            if (++ticks % 15 != 1 || polling || active || awaitingReboot) return;
            polling = true;
            try { SetStatus(await Manager.GetStatus()); UpdateNotice(); }
            catch (Exception ex) { Log(ex.Message); SetStatus("Error"); }
            finally { polling = false; }
        };
        timer.Start();
        // Schedule after the UI message loop starts, keeping prerequisite dialogs on the UI thread.
        var firstRun = new System.Windows.Forms.Timer { Interval = 300 };
        firstRun.Tick += async (_, _) =>
        {
            firstRun.Stop(); firstRun.Dispose();
            if (!install.Load().AutoStart && File.Exists(install.EnvFile)) return;
            await Run(async () => { await EnsureDocker(); await Manager.Start(); await OfferLauncherReplacement(); });
        };
        firstRun.Start();
    }
    private void AddAction(ContextMenuStrip menu, string title, Func<Task> action)
    {
        var item = new ToolStripMenuItem(title);
        item.Click += async (_, _) => await Run(action);
        actions.Add(item);
        menu.Items.Add(item);
    }
    private async Task Run(Func<Task> action)
    {
        if (active) return;
        active = true;
        awaitingReboot = false;
        progressWindow?.Dispose();
        progressWindow = new SetupProgressForm();
        progressWindow.SetStep("Preparing Order Manager");
        progressWindow.Show();
        foreach (var item in actions) item.Enabled = false;
        startupItem.Enabled = false;
        try
        {
            while (polling) await Task.Delay(50);
            await action();
            UpdateNotice();
            progressWindow.Complete("Complete", "Order Manager: " + stateItem.Text + ". You can close this window; the tray remains available.");
        }
        catch (WindowsRebootRequiredException ex)
        {
            awaitingReboot = true;
            SetStatus("Restart Windows required");
            progressWindow.Complete("Restart Windows required", ex.Message);
        }
        catch (Exception ex)
        {
            Log(ex.ToString()); SetStatus("Error");
            progressWindow.Complete("Setup could not finish", ex.Message + "\n\nLogs: " + install.Logs +
                (install.Load().RecoveryRequired ? "\nAn interrupted update requires recovery. Open Recovery instructions in the tray menu." : ""));
        }
        finally
        {
            active = false;
            foreach (var item in actions) item.Enabled = true;
            startupItem.Enabled = true;
        }
    }
    private void SetStatus(string status)
    {
        if (active) progressWindow?.SetStep(status);
        stateItem.Text = status;
        icon.Text = ("Order Manager: " + status)[..Math.Min(63, 15 + status.Length)];
        icon.Icon = status == "Running" ? SystemIcons.Application : status.Contains("Error") || status.Contains("Recovery") ? SystemIcons.Error : SystemIcons.Information;
    }
    private void Log(string line)
    {
        progressWindow?.AppendActivity(line);
        // Serialize writes: stdout and stderr are drained concurrently.
        lock (install)
            File.AppendAllText(Path.Combine(install.Logs, $"launcher-{DateTime.UtcNow:yyyy-MM-dd}.log"), $"{DateTime.UtcNow:O} {line}\n");
    }
    private static void Open(string target) => Process.Start(new ProcessStartInfo(target) { UseShellExecute = true });
    private void ShowProgress()
    {
        if (progressWindow == null || progressWindow.IsDisposed) return;
        progressWindow.Show();
        progressWindow.WindowState = FormWindowState.Normal;
        progressWindow.Activate();
    }
    private static string? DesktopPath()
    {
        var candidates = new List<string>
        {
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Docker", "Docker", "Docker Desktop.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "DockerDesktop", "Docker Desktop.exe"),
        };
        foreach (var hive in new[] { Registry.CurrentUser, Registry.LocalMachine })
        {
            using var key = hive.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\Docker Desktop");
            if (key?.GetValue("InstallLocation") is string location)
                candidates.Add(Path.Combine(location, "Docker Desktop.exe"));
        }
        return candidates.FirstOrDefault(File.Exists);
    }
    private static string FindDocker()
    {
        var desktop = DesktopPath();
        return desktop == null ? "docker" : Path.Combine(Path.GetDirectoryName(desktop)!, "resources", "bin", "docker.exe");
    }
    private async Task EnsureDocker()
    {
        SetStatus("Checking Windows prerequisites");
        var probeRunner = new ProcessRunner(line => { if (!line.TrimStart().StartsWith('{')) Log(line); });
        var prerequisites = new WindowsPrerequisites(install, probeRunner, SetupWindowsPrerequisites);
        await prerequisites.Ensure();
        SetStatus("Starting Docker");
        try { await Manager.CheckDocker(); return; } catch { }
        var desktop = DesktopPath();
        if (desktop == null)
        {
            if (MessageBox.Show("Docker Desktop was not found. Download and launch its official installer?\n\n" +
                "You will review Docker's license and any Windows elevation prompts in its installer.", "Install Docker Desktop",
                MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes)
                throw new InvalidOperationException("Install Docker Desktop, then retry Start.\n\n" + PrerequisiteHelp);
            var installer = Path.Combine(install.Root, "DockerDesktopInstaller.exe");
            SetStatus("Downloading Docker Desktop");
            await progressWindow!.DownloadAsync(http,
                "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe", installer);
            SetStatus("Verifying Docker Desktop installer");
            // Pass the path through an environment variable, never interpolate it as PowerShell code.
            var verify = await runner.Run("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command",
                "$s = Get-AuthenticodeSignature -LiteralPath $env:ORDER_MANAGER_INSTALLER; " +
                "if ($s.Status -ne 'Valid' -or $s.SignerCertificate.GetNameInfo(" +
                "[System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName,$false) -notin @('Docker Inc','Docker Inc.')) { exit 1 }"],
                new Dictionary<string, string> { ["ORDER_MANAGER_INSTALLER"] = installer });
            if (verify.ExitCode != 0) throw new InvalidDataException("Docker installer signature verification failed. The download was not executed.");
            SetStatus("Installing Docker Desktop");
            using var process = Process.Start(new ProcessStartInfo(installer) { UseShellExecute = true })!;
            await process.WaitForExitAsync();
            if (process.ExitCode is 3010 or 1641)
                prerequisites.RequireReboot((await prerequisites.Inspect()).BootTime);
            if (process.ExitCode != 0)
                throw new InvalidOperationException($"Docker Desktop installer exited with code {process.ExitCode}.\n\n{PrerequisiteHelp}");
            desktop = DesktopPath();
        }
        if (desktop == null) throw new InvalidOperationException(PrerequisiteHelp);
        SetStatus("Waiting for Docker Desktop to start");
        Open(desktop);
        // Manager was constructed before Docker installation; recreate the process lookup via PATH.
        Environment.SetEnvironmentVariable("PATH", Path.Combine(Path.GetDirectoryName(desktop)!, "resources", "bin") + Path.PathSeparator + Environment.GetEnvironmentVariable("PATH"));
        var deadline = DateTime.UtcNow.AddMinutes(3);
        while (DateTime.UtcNow < deadline)
        {
            try { await Manager.CheckDocker(); return; } catch { await Task.Delay(3000); }
        }
        throw new InvalidOperationException("Docker Desktop is not ready.\n\n" + PrerequisiteHelp);
    }

    private async Task<int> SetupWindowsPrerequisites()
    {
        SetStatus("Setting up Windows features and WSL — administrator access required");
        var info = new ProcessStartInfo(Path.Combine(Environment.SystemDirectory,
            @"WindowsPowerShell\v1.0\powershell.exe")) { UseShellExecute = true, Verb = "runas" };
        foreach (var arg in WindowsPrerequisites.PowerShellArguments(WindowsPrerequisites.SetupScript)) info.ArgumentList.Add(arg);
        try
        {
            using var process = Process.Start(info) ?? throw new IOException("Could not start Windows prerequisite setup.");
            await process.WaitForExitAsync();
            Log($"Windows prerequisite helper exited with code {process.ExitCode}.");
            return process.ExitCode;
        }
        catch (System.ComponentModel.Win32Exception ex) when (ex.NativeErrorCode == 1223)
        {
            throw new InvalidOperationException("Windows prerequisite setup was cancelled at the administrator prompt. Choose Start to retry.", ex);
        }
    }
    private static void ConfigureStartup(bool enabled)
    {
        using var key = Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run");
        if (enabled) key.SetValue("OrderManager", "\"" + Environment.ProcessPath + "\" --startup");
        else key.DeleteValue("OrderManager", false);
    }
    private void UpdateNotice()
    {
        var state = install.Load();
        extensionItem.Text = state.ExtensionFingerprint != state.AcknowledgedExtension ? "Browser Extensions — update reminder" : "Browser Extensions";
        if (state.ExtensionFingerprint != null && state.ExtensionFingerprint != state.AcknowledgedExtension && state.ExtensionFingerprint != notifiedExtension)
        {
            notifiedExtension = state.ExtensionFingerprint;
            icon.ShowBalloonTip(10000, "Browser extension update", "Open Browser Extensions in the tray to update Chrome or check Firefox updates.", ToolTipIcon.Info);
        }
    }
    private void ShowExtensions()
    {
        var state = install.Load();
        if (state.ActiveSha == null) { MessageBox.Show("Start Order Manager to download the browser extensions."); return; }
        Open("http://localhost:5173/settings/extension");
        Open(Path.GetDirectoryName(install.Chrome)!);
        if (MessageBox.Show("Chrome: open chrome://extensions, enable Developer mode and Load Unpacked from:\n" + install.Chrome +
            "\nFor later updates click Reload on this extension.\n\nFirefox: install the signed XPI from Settings → Browser extension once. " +
            "The first package containing the new update address must be installed manually. Later updates are automatic; use about:addons → Check for Updates if needed.\n\n" +
            "Set the extension server URL to http://localhost:5173 and log in.\n\nHave you installed or updated the extensions in every browser profile you use? " +
            "Yes acknowledges this reminder; installed versions are not detected.", "Browser extension update reminder",
            MessageBoxButtons.YesNo, MessageBoxIcon.Information) == DialogResult.Yes)
        {
            // Reload state to avoid overwriting an update that finished while this dialog was open.
            var current = install.Load();
            current.AcknowledgedExtension = state.ExtensionFingerprint;
            install.Save(current);
            UpdateNotice();
        }
    }
    private void ShowRecovery()
    {
        var state = install.Load();
        if (!state.RecoveryRequired || state.ActiveSha == null) { MessageBox.Show("No database recovery is currently required."); return; }
        var text = "An update was interrupted after database migrations could begin. Do not start older code against the changed database.\r\n\r\n" +
            "Back up any newer data you need before restoring. Restoring discards changes after the backup.\r\n" +
            "Pre-update backup: " + state.BackupPath + "\r\n\r\nOpen PowerShell, ensure Docker Desktop is running, and run:\r\n" +
            "$env:ORDER_MANAGER_VERSION = '" + state.ActiveSha + "'\r\n" +
            "$composeArgs = @('compose','-p','order-manager-windows','--env-file','" + Ps(install.EnvFile) + "','-f','" +
            Ps(Path.Combine(install.Source(state.ActiveSha), "docker-compose.windows.yml")) + "')\r\n" +
            "docker @composeArgs stop frontend backend package-tracking\r\n" +
            "docker @composeArgs up -d --wait db\r\n" +
            "docker @composeArgs cp '" + Ps(state.BackupPath ?? "BACKUP_PATH") + "' db:/tmp/recovery.dump\r\n" +
            "docker @composeArgs exec -T db pg_restore --list /tmp/recovery.dump\r\n" +
            "# Continue only if all preceding commands succeed. The next command discards the current schema.\r\n" +
            "docker @composeArgs exec -T db psql -U postgres -d order_management -v ON_ERROR_STOP=1 -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'\r\n" +
            "docker @composeArgs exec -T db pg_restore -U postgres -d order_management --no-owner --exit-on-error --single-transaction /tmp/recovery.dump\r\n\r\n" +
            "Only after restore succeeds, choose Resume after manual database restore in the tray. Keep the backup until you have verified the app.\r\n";
        var path = Path.Combine(install.Logs, "recovery.txt");
        File.WriteAllText(path, text);
        Open(path);
    }
    private static string Ps(string value) => value.Replace("'", "''");
    private async Task OfferLauncherReplacement()
    {
        var state = install.Load();
        if (state.ActiveSha == null) return;
        var replacement = Path.Combine(install.Source(state.ActiveSha), "launcher", "OrderManager.exe");
        var running = Environment.ProcessPath!;
        if (!File.Exists(replacement)) return;
        var candidateHash = await Hash(replacement);
        if (candidateHash == await Hash(running)) return;
        var helper = Path.Combine(install.Config, "replace-launcher.ps1");
        File.WriteAllText(helper,
            "param([int]$ParentId,[string]$Source,[string]$Target)\r\n$ErrorActionPreference = 'Stop'\r\n" +
            "Wait-Process -Id $ParentId -ErrorAction SilentlyContinue\r\n" +
            "for ($i=0; $i -lt 60; $i++) { try { Copy-Item -LiteralPath $Source -Destination ($Target + '.new') -Force; " +
            "Move-Item -LiteralPath ($Target + '.new') -Destination $Target -Force; Start-Process -FilePath $Target; exit 0 } " +
            "catch { Start-Sleep -Seconds 1 } }\r\n" +
            "Add-Content -LiteralPath (Join-Path (Split-Path $Target) 'launcher-update-error.log') 'Launcher replacement failed. Reinstall the Windows installer; data is retained.'\r\n");
        var info = new ProcessStartInfo("powershell.exe") { UseShellExecute = false, CreateNoWindow = true };
        foreach (var arg in new[] { "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", helper, "-ParentId", Environment.ProcessId.ToString(), "-Source", replacement, "-Target", running })
            info.ArgumentList.Add(arg);
        Process.Start(info);
        ExitThread();
    }
    private static async Task<string> Hash(string path)
    {
        await using var stream = File.OpenRead(path);
        return Convert.ToHexString(await System.Security.Cryptography.SHA256.HashDataAsync(stream));
    }
    protected override void Dispose(bool disposing)
    {
        if (disposing) { timer.Dispose(); progressWindow?.Dispose(); icon.Visible = false; icon.Dispose(); http.Dispose(); }
        base.Dispose(disposing);
    }
}
