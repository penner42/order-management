# Windows installer and tray application

## Summary

Create a Windows 11 x64 installer with a native tray application. It downloads the public GitHub repository, configures Docker, and runs Order Manager at `http://localhost:5173`.

Use the latest `master` commit for updates. Start the tray and services at Windows sign-in by default, with a setting to disable automatic startup.

## Installer and runtime

- Build a self-contained .NET 10 WinForms tray application and package it with Inno Setup. Users need no Git, Node, Python, or .NET installation.
- Install per user under `%LOCALAPPDATA%\OrderManager`, separating application binaries, downloaded source versions, configuration, backups, and logs.
- Detect Docker Desktop, Compose, Linux-container mode, and daemon readiness. If Docker Desktop is missing, offer to download and launch its official installer, verify its Windows signature, and allow its license and elevation prompts.
- Explain missing WSL or virtualization prerequisites and resume setup after any required reboot. Reuse existing Docker installations.
- Resolve `master` to a commit SHA and download that exact GitHub archive without requiring Git.
- Generate a persistent `.env` containing a cryptographically random `SECRET_KEY` and `ADMIN_USERNAME=admin`, `ADMIN_PASSWORD=admin`. Preserve existing values on reinstall and updates. No other variables are required.

## Service management and updates

- Add a standalone Windows deployment Compose configuration. Enable scheduled jobs and buying-group submissions, disable extension signing, and omit Cloudflare.
- Build application images locally in Docker. Serve the built frontend through Nginx, preserving API, tracking, and browser-session WebSocket proxy behavior.
- Bind published ports to loopback and keep PostgreSQL internal. Preserve named volumes for the database, invoices, logs, and browser profiles using a fixed Compose project name.
- Provide tray actions for **Open Order Manager**, **Start**, **Stop**, **Restart**, **Update**, **Browser Extensions**, **View Logs**, startup preferences, and **Exit Tray**.
- Display stopped, starting, running, updating, Docker unavailable, and error states. Serialize operations, prevent duplicate tray instances, and show actionable failures. Stop affects only this application; exiting the tray leaves services running.
- Updates download a new commit into a separate directory and build candidate images before interrupting the running app. Skip unchanged commits.
- Before applying migrations, stop application writers and create a verified PostgreSQL backup. Keep the previous source version and images; switch the recorded active version only after health checks pass.
- If failure occurs before migration, restart the previous version. After migration begins, retain the backup and present explicit recovery instructions rather than automatically running older code against a potentially changed schema.
- Include tray updates from the same source snapshot: build the replacement executable in Docker and use a temporary helper to replace it after exit. Preserve settings.
- Uninstall stops this application and removes its launcher and startup entry while retaining user data, backups, Docker Desktop, and Docker volumes.

## Authentication and browser extensions

- Add `ADMIN_PASSWORD_SYNC_ENABLED`, defaulting to the current behavior for existing deployments. Set it to false in the Windows deployment so `admin/admin` creates the initial account but later restarts preserve password changes.
- Add a stable public Firefox update manifest on GitHub and reference it through `browser_specific_settings.gecko.update_url`. Generate its version, signed XPI link, and SHA-256 hash when publishing extension packages.
- Preserve the Firefox extension ID and existing signing workflow. Signing credentials stay in development; Windows installations consume committed signed packages.
- Require one manual Firefox installation of the first update-enabled package. Subsequent updates follow Firefox’s normal update mechanism. ([Mozilla documentation](https://extensionworkshop.com/documentation/manage/updating-your-extension/))
- Provide a stable unpacked-extension directory for Chrome. Refresh its contents when the installed service version changes, preserving the extension’s identity and browser-managed settings.
- Show a persistent tray reminder when extension packages change, with Firefox installation/check-for-update instructions and Chrome Load Unpacked/Reload instructions. Label acknowledgement as user-confirmed; do not claim to detect every browser profile’s installed version.
- Correct the existing Windows Chrome CRX installation guidance to reflect Chrome’s distribution restrictions. ([Chrome documentation](https://developer.chrome.com/docs/extensions/how-to/distribute))

## Validation and delivery

- Test prerequisite detection, archive extraction, paths with spaces, secret persistence, operation locking, update staging, backup failure, failed migration/health checks, and interrupted-update recovery.
- Test initial admin creation and password preservation with the Windows setting, while retaining existing deployment behavior by default.
- Validate startup with only the generated three-variable `.env`; missing UPS and extension-signing credentials must not prevent startup.
- Run Docker-based backend tests, frontend build, and browser smoke tests against isolated test data and volumes.
- Add containerized build tooling and CI artifacts for the installer. Keep application builds and automated checks inside Docker.
- Manually validate installation, Docker installation prompts, reboot recovery, tray controls, sign-in startup, upgrades, uninstall/reinstall, Chrome reload, and Firefox automatic updates on Windows.
- Assume public GitHub access, Windows 11 x64, and one installation per Windows user. Installer code signing is outside this first version.
- When implementation is approved, first save this plan to `docs/plans/2026-11-09-windows-installer-and-tray.md`.
