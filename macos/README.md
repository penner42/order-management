# Order Manager for Apple Silicon Macs

Download **OrderManager-Setup-arm64.dmg** from the newest **Mac installer** entry on
[GitHub Releases](https://github.com/penner42/order-management/releases).
The global latest release may be the Windows installer. Public release downloads
need no GitHub sign-in. The first Mac download becomes available after the Mac
workflow succeeds on `master`; source changes alone do not publish a disk image.

Requires an Apple Silicon Mac and macOS 15 or later. Intel Macs are unsupported.
Docker Desktop runs the entire orders application in native Linux ARM64 containers;
only setup and the menu bar launcher run on the Mac. No Xcode, Homebrew, Git, Node,
Python, PostgreSQL, or .NET installation is needed on the user's Mac. Installation
and updates require internet access, several minutes, and substantial disk space.

Open the disk image, open **Order Manager**, and choose **Install and Open**.
The app installs for your account at `~/Applications/Order Manager.app`.
This release uses ad hoc signatures, without paid Developer ID signing or Apple
notarization. After a blocked launch, use **System Settings → Privacy & Security →
Open Anyway**. The installed copy or a later release may need approval again.
Managed Macs may prohibit this exception. Do not disable Gatekeeper globally.
See [Apple's instructions](https://support.apple.com/en-us/102445).

Follow **Show Setup Progress** from the menu bar. If Docker is missing, approve its
download, drag Docker to Applications, launch it, and review Docker's license and
permission prompts yourself. Keep its disk image mounted until Docker finishes.
Then choose **Start** in Order Manager. Docker startup has a five-minute readiness
limit; finish its prompts and retry if needed. Setup does not bypass permissions
or accept licenses. Existing Docker Desktop installations are reused.

Open http://localhost:5173 and sign in with **admin / admin**. Change the password
in the app; restarts and updates preserve it. Optional UPS credentials can be added
to the retained configuration. Scheduled jobs and configured buying-group
submissions run as in the Windows desktop deployment. No tunnel is installed.
Only localhost port 5173 is published.

## Controls and data

The menu offers Start, Stop, Restart, Update, progress, logs, browser extensions,
database recovery, launch at login, uninstall, and Quit. Closing the progress
window keeps work running. **Quit leaves services running**; Stop stops only this
application's containers. Docker Desktop itself is never stopped or uninstalled.
Only one Mac account should run the fixed project against a given Docker daemon.

Launch at login is enabled by default. Its checkmark shows macOS's actual service
registration status. Approve it in Login Items when requested. Disabling it leaves
the menu bar application available for manual Start. Uninstall through the menu
stops the stack and disables login startup before moving the app to Trash; a stop
failure retains the installation. Quit any existing launcher before reinstalling.

Files are under `~/Library/Application Support/OrderManager`:

| Directory | Contents |
| --- | --- |
| `config` | Private `.env`, service state, process lock, launcher replacement journal/helper |
| `versions/<commit>` | Exact release source and staged replacement launcher |
| `extensions/chrome` | Stable Chrome Load Unpacked directory |
| `backups` | Verified PostgreSQL dumps before updates |
| `logs` | Launcher logs and generated recovery instructions |

Database, invoices, application logs, and browser profiles use named Docker volumes
under **order-manager-macos**, separate from development and Windows projects.
Never delete these volumes in Docker Desktop. Uninstall retains them and all
support files; reinstall reuses data and credentials. Settings/secrets are private
to the current user. The launcher explicitly selects Docker Desktop's local
`desktop-linux` context and does not change the shell's selected Docker context.

## Updates and recovery

First setup uses the installer's bundled source commit. Update selects the newest
published compatible Mac release, independently of Windows releases and unbuilt
`master` commits. Workflow run/attempt numbers order releases, so rerunning an older
build cannot downgrade the installed release. It downloads the exact source and matching prebuilt ARM64 launcher,
checks the archive hash, bundle identity, architecture, and signatures, and builds
candidate service images before downtime. Updates require no native build tools.

Writers stop before PostgreSQL backup. The copied backup is checked before
migrations may start. All four services must be healthy before the new source
becomes active. Failures before migrations resume the old services. After
migrations may have begun, automatic rollback and Start/Restart/Update are blocked;
application writers are stopped. Open **Recovery Instructions**, preserve any newer
data you need, and run the generated Terminal commands one at a time. Restoring the
backup discards later database changes. Choose **Resume After Manual Restore** only
after every restore command succeeds. Do not delete or edit service state to bypass
this guard. A failed first install can retry forward with Start.

Launcher replacement happens after healthy activation. Its journal is separate
from database recovery; interrupted swaps retain the previous app and can be retried
on launch. A failed swap is reported without repeatedly quitting/reopening the launcher;
Start retries it, and another service update waits until replacement is resolved.
A launcher replacement failure does not roll back the database or stop
healthy services. View `logs/launcher-replacement.log`; reinstall a compatible Mac
installer if the launcher cannot reopen. Previous source versions, backups, images,
and the previous app bundle are retained; cleanup is manual.

## Browser extensions

Choose **Browser Extensions** for instructions and the local directory.
Chrome: enable Developer mode at `chrome://extensions`, Load Unpacked from the
stable `extensions/chrome` directory, and click Reload after updates. Firefox:
install the signed XPI from Settings → Browser extension once, then use its built-in
automatic updater. Configure the extension server URL as `http://localhost:5173`.
The reminder is acknowledged by the user after updating every profile; it does not
detect installed extension versions. Safari is not supported. No signing credentials
are needed on desktop installations.

## Build and verification

Application development, shared launcher tests, helper cross-publishing, and ARM
integration tests run in Docker. The explicit native-tool exception is only for
Mac launcher compilation/tests, signing, metadata, and disk-image packaging.

On the Docker development host:

```sh
sh macos/build-helper.sh
docker build -f macos/App/Dockerfile -t order-manager-mac-protocol-tests macos/App
```

On a native ARM64 Docker host:

```sh
sh macos/test-deployment.sh
```

This uses a unique project, temporary configuration, and disposable volumes, disables
third-party scheduled work, and validates startup, backend units, password persistence,
PostgreSQL backup/restore, Chromium PDFs, Camoufox fixture automation, and browser tests
through Nginx. It never reads the development `.env` or resets shared data.

Transfer `macos/build/helper` to an Apple Silicon Mac with Xcode command-line tools:

```sh
SOURCE_COMMIT=<full-40-character-commit> RELEASE_TAG=mac-installer-1-1 bash macos/package.sh
```

Output is `macos/build/output/`: disk image, launcher ZIP, versioned release manifest,
and SHA256SUMS. Packaging tests Swift, checks all signatures/architectures, and launches
the bundled helper's self-check without creating application state or starting Docker.
Release minimums are recorded in `release-settings.json`; packaging uses the higher
.NET/Docker minimum for the bundle and manifest. Recheck these baselines when updating
runtime dependencies. Docker's own installed minimum is also checked at startup.

The **Mac installer** workflow runs shared/helper checks on Ubuntu, native application
checks on ARM64 Ubuntu, and native packaging on ARM64 macOS. Successful `master`
builds publish immutable `mac-installer-<run>-<attempt>` releases without taking over
Windows's global latest designation. Pull requests upload artifacts only. The updater
manifest has schema/protocol version 1 and binds the source SHA, ARM64 architecture,
minimum macOS, immutable release tag, launcher URL, and SHA-256 hash.

## Implementation validation (2026-11-09)

Passed in the Linux Docker development environment: 19 existing Windows launcher
checks, 13 Mac helper checks, 4 portable Swift protocol/progress tests, release
metadata generation checks, and self-contained Windows x64/Mac ARM64 helper
cross-builds. Compose/workflow YAML and shell syntax checks also passed.

Not run in this checkout: native AppKit compilation/tests, ad hoc signing and DMG
packaging, native ARM64 application deployment/browser/PDF checks, and interactive
Mac acceptance. CI contains the native build/deployment jobs; a successful release
still needs the interactive checks below before full acceptance.

## Real Mac acceptance checklist

Automated Linux checks do not establish Gatekeeper, Docker Desktop, or Login Items
behavior. Before calling a release fully accepted, record results for:

- Downloaded/quarantined app, Open Anyway, install/reinstall, and paths with spaces.
- Clean setup without Docker, cancelled download, publisher verification failure,
  slow download, Docker license/permission prompts, daemon stopped/unavailable.
- Progress close/minimize/restore, visible error and retry, duplicate launcher,
  Launch at Login enabled/disabled/approval required, Quit and Stop behavior.
- Change admin password, restart, update, uninstall/reinstall, retained data/volumes.
- Update download/hash/build failure, backup failure, migration/health failure,
  manual restore, interrupted launcher swap, repair with the installer.
- Chrome unpacked setup/settings/Reload and Firefox signed installation/updating.

Native AppKit compilation, full native ARM64 deployment checks, and these interactive
acceptance checks must be reported separately from shared unit tests. The Linux
checkout cannot generate or interactively validate the Mac disk image.
