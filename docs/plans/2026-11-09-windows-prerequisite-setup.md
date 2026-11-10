# Automatic Windows prerequisite setup

Approved plan:

- Keep Order Manager installed for the current Windows user.
- Add a prerequisite step to first-run setup before Docker installation/startup.
- Detect missing Virtual Machine Platform and Windows Subsystem for Linux features.
- Request Windows administrator access for a helper that enables missing features
  using the documented Windows servicing commands, then installs or updates WSL
  without installing a Linux distribution.
- Handle required reboots explicitly: retain setup, prompt the user to restart
  Windows, and continue setup at sign-in or when Start is retried after restarting.
- Preserve Docker installations, application settings, and all data volumes.
- Explain that BIOS/UEFI hardware virtualization may still need manual enabling.
- Add automated tests for prerequisite orchestration and failure/reboot paths,
  build/package inside Docker, and document actual-Windows acceptance checks.

Additional user requirement:

- Keep a visible progress window open throughout installation/setup, including
  prerequisite changes, Docker download/installation/startup, and application
  download/build/startup. Show the current step and activity; explain reboot or
  failure outcomes clearly.
