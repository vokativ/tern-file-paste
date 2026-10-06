# Changelog

## 0.1.0

- Added a separate Tern shortcut for copying regular files from macOS, Linux Wayland/X11, and Windows clipboards to Ubuntu over SSH.
- Added explicit per-host SSH mappings, asynchronous complete-file streaming, private unique upload directories, and OMP/shell reference formatting.
- Preserved normal paste and avoided submitting prompts, uploading thumbnails, deleting cut sources, overwriting duplicate basenames, or redirecting completed uploads into a different pane.
- Added clipboard, transfer, and routing regression tests plus macOS/Linux/Windows CI.
- Kept clipboard subprocess output as bytes until explicit decoding, so malformed UTF-8 raises a handled error on Windows instead of escaping through a reader thread.
- Verified native macOS clipboard uploads and actual Ubuntu file integrity; interactive Linux/Windows Tern verification remains a device-level check.
- Confirmed the Finder image-file → remote Ubuntu OMP workflow in actual use and documented public installation with explicit per-host configuration.
- Added full-history author/committer/tagger no-reply checks, an opt-in pre-push hook, and non-shallow CI auditing to prevent accidental personal email publication.
