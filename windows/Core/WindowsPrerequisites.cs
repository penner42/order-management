using System.Text;
using System.Text.Json;

namespace OrderManager.Core;

public sealed class WindowsRebootRequiredException() : Exception(
    "Restart Windows to finish enabling Windows prerequisites. Setup is saved. " +
    "Order Manager will continue at Windows sign-in if automatic startup is enabled; otherwise open Order Manager and choose Start.");

public sealed record WindowsPrerequisiteStatus(string BootTime, bool FeaturesEnabled, bool WslInstalled);

public sealed class WindowsPrerequisites(Installation install, ICommandRunner runner, Func<Task<int>> elevate)
{
    // Fixed, embedded commands: never execute an elevated script from a user-writable file
    // or interpolate installation paths into elevated PowerShell code.
    public const string InspectScript = """
        $ErrorActionPreference = 'Stop'
        try {
            $names = @('VirtualMachinePlatform', 'Microsoft-Windows-Subsystem-Linux')
            $features = @(Get-CimInstance Win32_OptionalFeature | Where-Object { $_.Name -in $names -and $_.InstallState -eq 1 })
            $boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString('O')
            $wsl = Join-Path $env:SystemRoot 'System32\wsl.exe'
            $installed = $false
            if (Test-Path -LiteralPath $wsl) {
                try { & $wsl --version *> $null; $installed = $LASTEXITCODE -eq 0 } catch { $installed = $false }
            }
            @{ BootTime = $boot; FeaturesEnabled = ($features.Count -eq 2); WslInstalled = $installed } | ConvertTo-Json -Compress
            exit 0
        } catch { Write-Error $_ -ErrorAction Continue; exit 1 }
        """;

    public const string SetupScript = """
        $ErrorActionPreference = 'Stop'
        try {
            $restart = $false
            foreach ($name in @('VirtualMachinePlatform', 'Microsoft-Windows-Subsystem-Linux')) {
                Write-Host "Checking $name..."
                $feature = Get-WindowsOptionalFeature -Online -FeatureName $name
                if ($feature.State -in @('EnablePending', 'DisablePending')) { $restart = $true }
                elseif ($feature.State -ne 'Enabled') {
                    Write-Host "Enabling $name..."
                    $result = Enable-WindowsOptionalFeature -Online -FeatureName $name -All -NoRestart
                    if ($result.RestartNeeded) { $restart = $true }
                }
            }
            if ($restart) { exit 3010 }
        } catch { Write-Error $_ -ErrorAction Continue; exit 10 }
        $wsl = Join-Path $env:SystemRoot 'System32\wsl.exe'
        try {
            $installed = $false
            try { & $wsl --version *> $null; $installed = $LASTEXITCODE -eq 0 } catch { }
            if (-not $installed) {
                Write-Host 'Installing WSL (without a Linux distribution)...'
                & $wsl --install --no-distribution --web-download
                if ($LASTEXITCODE -in @(3010, 1641)) { exit 3010 }
                if ($LASTEXITCODE -ne 0) { exit 20 }
            }
        } catch { Write-Error $_ -ErrorAction Continue; exit 20 }
        try {
            Write-Host 'Updating WSL...'
            & $wsl --update --web-download
            if ($LASTEXITCODE -in @(3010, 1641)) { exit 3010 }
            if ($LASTEXITCODE -ne 0) { exit 21 }
            exit 0
        } catch { Write-Error $_ -ErrorAction Continue; exit 21 }
        """;

    public static string[] PowerShellArguments(string script) =>
        ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand",
            Convert.ToBase64String(Encoding.Unicode.GetBytes(script))];

    public async Task<WindowsPrerequisiteStatus> Inspect()
    {
        var result = await runner.Run("powershell.exe", PowerShellArguments(InspectScript), timeout: TimeSpan.FromMinutes(2));
        if (result.ExitCode != 0) throw new IOException("Could not check Windows prerequisites. See launcher logs and retry Start.");
        var status = JsonSerializer.Deserialize<WindowsPrerequisiteStatus>(result.Output.Trim());
        if (status == null || string.IsNullOrWhiteSpace(status.BootTime))
            throw new InvalidDataException("Windows prerequisite detection returned no boot time.");
        return status;
    }

    public void RequireReboot(string bootTime)
    {
        var state = install.Load();
        state.PrerequisiteRebootBootTime = bootTime;
        install.Save(state);
        throw new WindowsRebootRequiredException();
    }

    public async Task Ensure()
    {
        var status = await Inspect();
        var state = install.Load();
        if (state.PrerequisiteRebootBootTime == status.BootTime) throw new WindowsRebootRequiredException();
        if (state.PrerequisiteRebootBootTime != null)
        {
            state.PrerequisiteRebootBootTime = null;
            install.Save(state);
        }
        if (status.FeaturesEnabled && status.WslInstalled) return;
        var code = await elevate();
        if (code == 3010) RequireReboot(status.BootTime);
        if (code != 0)
        {
            var step = code switch { 10 => "enabling Windows features", 20 => "installing WSL", 21 => "updating WSL", _ => "setting up Windows prerequisites" };
            throw new IOException($"Windows setup failed while {step} (code {code}). Retry Start. " +
                "Check your internet connection and Windows Update or organization policies. " +
                "Windows feature servicing details are in C:\\Windows\\Logs\\DISM\\dism.log.");
        }
        var verified = await Inspect();
        if (!verified.FeaturesEnabled || !verified.WslInstalled)
            throw new IOException("Windows prerequisites are still unavailable after setup. Restart Windows and retry Start.");
    }
}
