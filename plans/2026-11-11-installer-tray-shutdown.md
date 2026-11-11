# Close the tray before Windows installer replacement

Approved plan:

1. Have installation and reinstallation explicitly request graceful shutdown of
   the current user's existing tray, using its existing named shutdown event.
2. Wait for the tray to release its single-instance mutex before replacing the
   executable. Block installation with a clear retry message if shutdown fails
   or setup/update work remains busy. Do not force termination.
3. Keep Docker containers running during tray replacement and preserve settings,
   data, and the existing post-install launch option.
4. Use a temporary copy of the new launcher for the shutdown request so this
   works with already-installed versions that only expose the shutdown event
   through their uninstall path.
5. Document the behavior and validate launcher tests, Windows cross-compilation,
   and Inno Setup packaging inside Docker. Record Windows-only acceptance checks.
