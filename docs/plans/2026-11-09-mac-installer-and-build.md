# Apple Silicon Mac installer and release build

## Summary

Create a downloadable `OrderManager-Setup-arm64.dmg` containing a Mac menu bar application with setup progress, service controls, updates, browser extension instructions, and database recovery.

Use Docker Desktop. Keep the entire orders application—including PostgreSQL, automation browsers, PDF generation, and tracking—inside Linux ARM64 containers. Only the launcher and installation controls run natively.

Support Apple Silicon only. Distribute initially with ad hoc signing, without paid Developer ID signing or notarization. Document Apple’s [Open Anyway procedure](https://support.apple.com/en-us/102445).

When implementation is approved, save this complete plan to `docs/plans/2026-11-09-mac-installer-and-build.md` before editing implementation files.

## Installer and Mac experience

- Build a Swift/AppKit menu bar application. Bundle a self-contained ARM64 .NET helper that reuses the existing deployment logic; users need no Xcode, Homebrew, .NET, Python, Node, or Git.
- Install per user at `~/Applications/Order Manager.app`. Opening the app from the disk image presents an Install and Open action that copies the complete bundle there and relaunches it. Register startup only from the installed location.
- Store configuration, source versions, backups, logs, and unpacked Chrome extension files under `~/Library/Application Support/OrderManager`. Restrict configuration and secret files to the current user.
- Provide Open Order Manager, Show Setup Progress, Start, Stop, Restart, Update, Browser Extensions, View Logs, Recovery Instructions, Resume After Manual Restore, Launch at Login, Uninstall, and Quit.
- Default launch at login to enabled, using `SMAppService.mainApp`; show the actual registration status and guide users if macOS requires approval. Quit leaves services running. Stop affects only this application’s containers.
- Setup progress shows the current step, elapsed time, recent activity, and download progress when available. Closing the window leaves operations running; reopening restores it. Failures remain visible and retryable.
- Detect installed Docker Desktop in standard system/user application locations. Use its bundled CLI and explicitly target its local Desktop context, without changing the user’s global Docker context or using inherited remote Docker settings.
- If Docker is missing, offer to download the official Apple Silicon disk image, verify the mounted Docker application’s signature and Docker publisher identity, and open it for guided installation. Wait for users to install, launch, and accept Docker’s terms; do not accept terms automatically.
- Check ARM64 hardware, the macOS minimum declared by the release, Compose availability, and Linux daemon readiness. Start Docker when needed. Timeouts explain how to finish setup and retry.
- Uninstall stops this stack and unregisters login startup before removing the installed app. Retain application data, volumes, backups, configuration, extensions, and Docker Desktop. If stopping fails, retain the installation and report the failure.

## Deployment, shared logic, and updates

- Extract the portable Windows deployment core into a shared desktop library. Inject platform settings for installation paths, Compose project/file, source validation, candidate-launcher preparation, and recovery command generation. Keep Windows defaults and behavior intact.
- Use Swift only for Mac UI and OS integration. The bundled helper exposes explicit operation commands and newline-delimited JSON progress/results; process exit codes indicate success or failure. Protect operations with a per-installation process lock, including helper invocations outside the UI.
- Add `docker-compose.macos.yml`, using the existing desktop service configuration with a fixed `order-manager-macos` project and ARM64 builds. Reuse the static frontend/Nginx deployment. Publish only `127.0.0.1:5173`.
- Preserve Windows feature parity: generated secrets, initial admin credentials, password persistence, scheduled jobs, extension packages, and retained named volumes. Keep Chrome’s unpacked path stable and Firefox’s existing signed-update mechanism. Safari support is excluded.
- Mac updates follow successful Mac releases, not raw `master`. Each release supplies the launcher archive, disk image, checksums, and a versioned manifest containing source commit, architecture, minimum macOS version, helper protocol version, and launcher archive hash.
- First installation uses the release bundled with the installer. Update discovers the newest published compatible Mac release by its tag prefix and manifest, independently of GitHub’s global “latest” release.
- Download the exact source commit and matching prebuilt launcher before interrupting services. Verify archive hashes, expected bundle identity, architecture, signatures, and safe extraction paths. Never build Swift or require Apple tools on users’ machines.
- Retain the existing update safeguards: build candidate images before downtime; stop writers; create and verify a copied PostgreSQL backup; persist the migration journal before startup; activate only after all services are healthy.
- Failures before migrations restart the previous version. Failures after migrations may begin stop writers and require manual database recovery. Generate correctly quoted Mac shell recovery commands with the exact Docker context, Compose project, and backup paths.
- Replace the launcher only after service activation succeeds. A detached replacement helper waits for exit, swaps the staged bundle at the installed path, and relaunches it. Keep a separate replacement journal and previous bundle so an interrupted swap is repairable without downgrading the database. Replacement failures leave healthy services running and offer installer-based repair.
- Application APIs and database schema remain unchanged. Add only desktop helper, release manifest, and installation-state interfaces.

## Build and publication

- Run shared .NET tests and helper cross-publishing inside Docker. Produce the self-contained `osx-arm64` helper there.
- Run native ARM64 container deployment checks on an ARM64 Linux CI runner. Verify Chromium PDF generation and Camoufox startup against local fixtures, without live store logins.
- Use a macOS ARM64 CI runner for Swift compilation/tests, bundle assembly, nested ad hoc signing, signature verification, and disk-image packaging. These are the explicit exceptions to the repository’s Docker build rule.
- Publish only after shared tests, ARM deployment checks, and Mac packaging checks pass. Run on every `master` push and manual dispatch; pull requests produce artifacts without releases.
- Use immutable tags `mac-installer-<run>-<attempt>`. Include source commit, build link, download instructions, and signing limitations. Publish without replacing the Windows release’s global “latest” designation.
- Treat the package’s deployment minimum as the higher of the pinned .NET runtime and Docker Desktop requirements, encode that value in the bundle/manifest, and check it before installation.
- Document Docker-only application development, native Mac packaging commands, installation, updates, extension setup, storage, recovery, uninstall, and remaining manual acceptance checks.

## Verification and acceptance

- Extend shared tests for platform configuration, manifest selection, invalid downloads, archive traversal, locking, interrupted updates, backup failure, migration failure, health failure, and launcher replacement recovery. Run existing Windows launcher tests after the extraction.
- Run ARM64 integration tests in disposable Compose projects with temporary credentials and their own volumes. Cover frontend/backend health, admin password persistence, migrations, backup copy/restore, browser launch, and generated PDFs. Never reset or seed shared development data.
- Test Mac UI state and helper event handling with a fake helper, including progress restoration, failure/retry, Docker waiting, startup registration errors, and recovery blocking.
- Validate packaged architecture, bundle contents, executable permissions, signatures, manifest hashes, and launching the bundled helper on macOS CI.
- Perform manual acceptance on a real Apple Silicon Mac: downloaded/quarantined app opening, clean install without Docker, Docker first-run prompts, login startup, paths containing spaces, extension installation, updates, interrupted replacement, uninstall/reinstall, and retained data.
- Record automated results and any unperformed manual checks explicitly. Successful Linux ARM checks establish container compatibility; actual Docker Desktop and Gatekeeper behavior require Mac acceptance testing.
