# Changelog

## 0.3.2

- Changed the Linux/Windows default from Ctrl+Shift+F8 to Ctrl+semicolon, avoiding laptop Fn requirements. Existing user overrides are preserved; macOS defaults are unchanged.
- Require Tern 0.6.2 or newer for native progress canvases and composer inspection. Agent panes without a compatible native composer retain uploaded references for manual insertion.
- Prevent Python bytecode writes inside the watched plugin directory, avoiding first-use hot reloads that discard upload callbacks.
- Show focus-preserving source-bytes/total progress and SSH phases in a temporary native panel, with wrapped diagnostics and an explicit Copy references recovery action.
- Close the panel immediately after the insertion call succeeds, with no success notification or retained success status. Errors remain accessible through Upload status.
- Recheck the original destination, wait up to 10 seconds for native composer readiness, and dispatch input once without submitting Enter or awaiting a later surface echo.
- Add progress, deadline/rollback, cold-package launch and composer-readiness regressions. The updated desktop workflow is device-tested on Omarchy/Wayland; macOS and Windows GUI verification remains pending.

## 0.2.0

- Changed default Linux and Windows shortcut from `Ctrl + Alt + Shift + V` to `Ctrl + Shift + F8` to prevent collisions with system-level `Alt + Shift` keyboard layout toggles and international Windows `AltGr` chords.
- Added companion command `Remote File Paste: Open keyboard settings` (`plugin.file-paste.configure`) to jump directly to Tern's native Preferences page (`07 · Keyboard`).
- Added non-intrusive first-run notification toast on `window_start` introducing the shortcut and configuration options without stealing focus.
- Added total upload data size feedback in the paste completion toast.
- Added comprehensive documentation on cross-platform shortcut collisions, Command Palette zero-conflict usage, and user `keybinds` configuration.
- Confirmed interactive Wayland file-manager clipboard transfer and remote SHA-256 verification on Omarchy Linux.

## 0.1.0

- Added a separate Tern shortcut for copying regular files from macOS, Linux Wayland/X11, and Windows clipboards to Ubuntu over SSH.
- Added explicit per-host SSH mappings, asynchronous complete-file streaming, private unique upload directories, and OMP/shell reference formatting.
- Preserved normal paste and avoided submitting prompts, uploading thumbnails, deleting cut sources, overwriting duplicate basenames, or redirecting completed uploads into a different pane.
- Added clipboard, transfer, and routing regression tests plus macOS/Linux/Windows CI.
- Kept clipboard subprocess output as bytes until explicit decoding, so malformed UTF-8 raises a handled error on Windows instead of escaping through a reader thread.
- Verified native macOS clipboard uploads and actual Ubuntu file integrity; interactive Linux/Windows Tern verification remains a device-level check.
- Confirmed the Finder image-file → remote Ubuntu OMP workflow in actual use and documented public installation with explicit per-host configuration.
- Added full-history author/committer/tagger no-reply checks, an opt-in pre-push hook, and non-shallow CI auditing to prevent accidental personal email publication.
