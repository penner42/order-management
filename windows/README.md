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
the Docker Desktop installer if offered. Review Docker's license in its installer.
If Windows needs WSL, the tray explains the commands and reboot requirements.
Launch Docker Desktop and retry **Start** after a reboot; setup and credentials
are retained. Hardware virtualization must be enabled. A first installation builds
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
- Existing Docker stopped/running, Linux/Windows-container mode, unavailable daemon.
- A Windows account name/path containing spaces; duplicate launcher prevention.
- Sign-in startup enabled/disabled; Start, Stop, Restart, Exit Tray, and log viewing.
- Change the admin password, restart, update, uninstall/reinstall, and sign in again.
- Update while running/stopped; interrupted download/build, backup failure, migration
  failure, health failure, manual restore, and replacement launcher restart.
- Existing Chrome profile: Load Unpacked, settings persistence, subsequent Reload.
- Existing Firefox profile: one manual update-enabled XPI installation followed by
  an automatic signed update; check the public feed is available after merging.
- Uninstall stops only this app; configuration, Docker, and all data remain.
