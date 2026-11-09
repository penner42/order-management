# Publish Windows installer downloads

Approved plan: publish the Windows installer as a GitHub Release asset so users
can download it from the public repository without signing into GitHub, without
committing generated executables into Git history.

1. Preserve the existing Docker build, launcher tests, and workflow artifact.
2. Add release publication after successful builds on `master`, including manual
   runs on `master`. Pull requests and other branches must not publish releases.
3. Give only the release job repository write permission. Publish the exact
   installer produced by the build, with a unique tag and source commit recorded.
4. Document the public download location and when releases are published.
5. Validate workflow structure and publication commands without publishing a
   release or changing the shared development database.
