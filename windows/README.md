# Order Manager for Windows

Download **OrderManager-Setup-x64.exe** from the
[latest release](https://github.com/penner42/order-management/releases/latest).
Release downloads from this public repository do not require a GitHub sign-in.
The **Windows installer** workflow publishes a release after each successful
installer build on `master`, including manual runs on `master`. Each release
records the source commit and build link. Pull requests and other branches only
produce workflow artifacts, whose downloads require sign-in.

The installer supports Windows 11 x64 and uses Docker Desktop's Linux containers
with WSL 2. It installs for the current Windows user. Git, Node, Python, and .NET
are not required on the PC. Internet access is needed for installation and updates.

Run `OrderManager-Setup-x64.exe`, leave **Start Order Manager** selected, and follow
the setup progress window. It stays open during Windows prerequisite checks,
Docker download/installation/startup, and application download/build/startup,
showing the current step, elapsed time, and recent activity. You can minimize it
while work continues and restore it using **Show setup progress** in the tray;
close it when setup finishes. Download progress includes
the percentage and size when the server supplies a total size.

Setup checks **Virtual Machine Platform**, **Windows Subsystem for Linux**, and
WSL. If anything is missing, Windows requests administrator access for a helper
that enables the features and installs/updates WSL without a Linux distribution.
The application itself remains installed for the current user. The helper never
restarts Windows automatically. If a restart is required, the progress window
explains how to continue; retrying before a restart keeps that message visible.
Setup continues at Windows sign-in if **Start at Windows sign-in** is enabled;
otherwise reopen Order Manager and choose **Start** after restarting.

Follow the Docker Desktop installer if offered and review Docker's license there.
Setup and credentials are retained across reboots. Hardware virtualization must
be enabled in BIOS/UEFI. A first installation builds
the application and downloads its browser automation dependencies, which can take
several minutes and substantial disk space.

Open http://localhost:5173 and sign in with **admin / admin**. Change the password
in the app; Windows restarts preserve your new password. No other environment
variables are needed. UPS API tracking requires optional UPS credentials, but
missing credentials do not prevent startup or ordinary tracking links.

The installer downloads the public `penner42/order-management` repository's latest
`master` commit. The Windows deployment files must be merged into `master` before
a freshly distributed installer can install the application. The installer does
not bundle the repository. There is no Cloudflare tunnel and only port 5173 is
published, bound to localhost. Scheduled imports and configured buying-group
submissions are enabled, as in production.

## Tray controls and storage

The tray starts Docker and the services at Windows sign-in by default. Disable
**Start at Windows sign-in** to launch the tray manually and click **Start**.
**Stop** stops only this application's containers, including PostgreSQL. **Restart**
restarts the stack. **Exit Tray** leaves services running; **Stop** first if you
want them stopped. Docker Desktop itself is never stopped or uninstalled.

Files are under `%LOCALAPPDATA%\OrderManager`:

| Directory | Contents |
| --- | --- |
| `app` | Tray executable and uninstaller |
| `config` | Persistent `.env`, state, launcher replacement helper |
| `versions/<commit>` | Downloaded source, candidate launcher |
| `extensions/chrome` | Stable Chrome Load Unpacked directory |
| `backups` | Verified pre-update PostgreSQL dumps |
| `logs` | Launcher logs and generated recovery instructions |

The database, invoices, application logs, and automation browser profiles use
Docker named volumes under the fixed `order-manager-windows` project. Do not
delete those volumes in Docker Desktop. Uninstall retains these volumes and all
configuration, backups, extensions and downloaded source; reinstall reuses them.
Only one Windows user should run this installation against the same Docker daemon.

Running the installer again asks the current user's tray to exit before replacing
it, leaving the containers running. Setup waits up to 30 seconds; if the tray is
busy with setup or an update, installation stops with instructions to retry after
that work finishes. The installer does not force termination. Leave **Start Order
Manager** selected to reopen the tray after installation.

## Updating and recovery

Click **Update** to resolve the latest `master` commit. Unchanged commits are
skipped. New service images and the replacement tray executable build before the
running services are interrupted. Settings, secrets, and data volumes persist.
The tray restarts itself when its executable changes. Existing source versions,
images, and database backups are retained; cleanup is manual.

Before migrations, the tray stops application writers and dumps PostgreSQL in
custom format. It verifies the dump, copies it to the backups directory, and
verifies the copied file again. Backup failures restart the previous services.
The active version changes only after every service is healthy.

If an update is interrupted after migrations may have started, **Start**,
**Restart**, and **Update** are blocked to protect the database. Open **Recovery
instructions** in the tray. It writes commands specific to your installation and
backup. Restore that backup only after preserving any newer data you need: the
restore discards database changes made after the backup. Verify every command
succeeds before proceeding. After restoring, choose **Resume after manual database
restore**. This starts the previous version and clears the recovery journal only
after its health checks pass. Do not clear `state.json` or automatically downgrade
a migrated database. Invoices and browser profiles are retained throughout.

For a failed *first* installation, click **Start** again to retry forward; there
is no previous database version to restore. Logs are available through **View Logs**.

## Browser extensions

Choose **Browser Extensions** in the tray to open instructions and the local folder.

- **Chrome:** open `chrome://extensions`, enable Developer mode, and choose **Load
  unpacked** with `%LOCALAPPDATA%\OrderManager\extensions\chrome`. After an update,
  click **Reload** on this extension. The directory and manifest key stay stable
  so browser-managed extension settings persist. Windows Chrome does not generally
  allow self-hosted CRX installation outside enterprise management.
- **Firefox:** download the signed XPI from **Settings → Browser extension** and
  install it using `about:addons` → gear → **Install Add-on From File**. Existing
  installations need one manual update to the first package containing the public
  update URL. Later versions update using Firefox's built-in updater; **Check for
  Updates** can request a check. Firefox may ask for new permissions.

Set the extension server URL to `http://localhost:5173` and log in. When packages
change, the tray shows a persistent reminder. Acknowledge it only after updating
every profile you use. This is user confirmation, not installed-version detection.
Firefox follows the public extension feed independently of your service version.

Developers build/sign the extension in the existing Docker development environment
and commit `browser-extension/release/`, including `firefox-updates.json`. The
publisher checks the XPI signature files, manifest version, and update URL and
generates the feed's SHA-256 hash. Desktop installs never need signing credentials.

## Building and automated validation

From the repository root, with Docker running:

```sh
sh windows/build.sh
```

This runs the isolated launcher tests in the .NET SDK container, cross-compiles a
self-contained Windows executable, and packages it with Inno Setup under Wine.
Output: `windows/installer/output/OrderManager-Setup-x64.exe`. The GitHub Actions
**Windows installer** workflow uploads the same executable as a downloadable
artifact and publishes a release when run on `master`; it can also be run manually.
Release tags use `windows-installer-<run number>-<attempt>` so each published build
has its own download. The installer is currently unsigned.

The workflow pins the host runner to Ubuntu 24.04 to retain the legacy syscall
support used by Wine's 32-bit Inno Setup packaging tools. Those tools package the
self-contained x64 tray application; they do not change its architecture.

Run the deployment checks against a disposable database and unique Compose project:

```sh
sh windows/test-deployment.sh
```

This validates startup with only the three generated variables, builds the static
frontend, runs backend unit tests, verifies admin password persistence across a
restart, restores a copied PostgreSQL backup to a disposable database, and runs
the browser suite through Nginx. It
disables third-party scheduled work for tests and removes only its own containers
and volumes. It does not read the development `.env` or reset shared data.

## Windows acceptance checks

Run on an actual Windows 11 x64 PC; Linux cross-compilation does not validate the UI:

- Clean install with no Docker; license, UAC, WSL, reboot, and retry paths.
- Both Windows features disabled; accept the prerequisite UAC prompt, verify both
  features are enabled, and verify the app waits for a reboot before Docker setup.
- Retry Start before reboot (including exiting/reopening the tray); confirm no
  repeated helper launch. Reboot and verify setup resumes without a Linux distro.
- Features enabled but WSL missing; WSL already ready (no prerequisite UAC);
  cancel UAC; failed WSL download/update; feature enablement blocked by policy.
- Keep the progress window visible through prerequisite setup, slow Docker
  download, Docker installer, daemon startup, source download, image builds, and
  health checks. Verify elapsed time/activity, minimize/restore, completion,
  error details, and retry. A failed step must not look like a successful install.
- Existing Docker stopped/running, Linux/Windows-container mode, unavailable daemon.
- A Windows account name/path containing spaces; duplicate launcher prevention.
- Reinstall with an older tray running: tray exits, executable is replaced, and
  containers stay running. Repeat with no tray running and with setup/update busy;
  a busy tray blocks replacement and installation can be retried when idle.
- Sign-in startup enabled/disabled; Start, Stop, Restart, Exit Tray, and log viewing.
- Change the admin password, restart, update, uninstall/reinstall, and sign in again.
- Update while running/stopped; interrupted download/build, backup failure, migration
  failure, health failure, manual restore, and replacement launcher restart.
- Existing Chrome profile: Load Unpacked, settings persistence, subsequent Reload.
- Existing Firefox profile: one manual update-enabled XPI installation followed by
  an automatic signed update; check the public feed is available after merging.
- Uninstall stops only this app; configuration, Docker, and all data remain.
