// web-ext does not read .web-extignore / .webignore.
// Ignore rules must live here (or in --ignore-files).
// During `web-ext sign`, the build uses a temp artifacts dir, so ./dist is
// NOT auto-ignored — exclude it explicitly or prior Chrome CRXs get packed
// into the Firefox XPI (and Firefox may treat the result as unverified).
export default {
  ignoreFiles: [
    "web-ext-config.mjs",
    "package.json",
    "package-lock.json",
    "order.json",
    "README.md",
    "*.md",
    "scripts",
    "scripts/**",
    "dist",
    "dist/**",
    ".keys",
    ".keys/**",
    "*.crx",
    "*.xpi",
    "*.zip",
    "*.log",
  ],
};
