# Remote File Paste for Tern

Copy files in your desktop file manager, focus a remote [Tern](https://stencil.so/tern) terminal or OMP pane, and upload their complete contents with a separate shortcut.

| Client | Shortcut |
| --- | --- |
| macOS | Command + Option + Shift + V |
| Linux / Windows | Ctrl + ; (semicolon) |

The command palette actions are:
- **Paste local files to remote** (`plugin.file-paste.paste`) — uploads clipboard files and inserts remote paths.
- **Remote File Paste: Open keyboard settings** (`plugin.file-paste.configure`) — opens Tern's Preferences with instructions to select Keyboard and customize or remove shortcuts.

Normal paste is not overridden. Copy alone does not upload anything, and the plugin never presses Enter or submits a prompt.

**Earlier macOS workflow, confirmed in actual use before 0.3:** copy an image **file** in Finder, focus OMP on Ubuntu through Tern, and press **Command + Option + Shift + V**. The updated 0.3 progress panel and composer-readiness path is device-tested on Omarchy, not yet on macOS or Windows. Image-only clipboard paste is not handled by this plugin.

## Quick start

Install on the **client computer running the Tern window**, not on Ubuntu:

```sh
tern plugin install github.com/vokativ/tern-file-paste
```

Or use **Preferences → Plugins → Install…** with the same GitHub URL.

You need **Python 3.10+**, the **OpenSSH client**, a working noninteractive SSH connection to Ubuntu, and the platform clipboard tools listed below. Installation alone does not configure the destination: add a host mapping in the plugin's [configuration file](#configure) before using the shortcut.

| Verification | Status |
| --- | --- |
| macOS → Ubuntu, copied image file → remote OMP | Earlier pre-0.3 workflow confirmed; updated 0.3 GUI path not yet device-tested |
| Linux (Omarchy / Wayland) → Ubuntu, binary / JPEG / empty files | Confirmed in actual use |
| Binary, empty, duplicate-name and Unicode files → Ubuntu | Transfer integrity and permissions verified |
| macOS / Ubuntu / Windows CI | Clipboard/transfer checks run in [GitHub Actions](https://github.com/vokativ/tern-file-paste/actions/workflows/check.yml); this is not desktop GUI verification |


## How it works

A client-side Luau window plugin reads the focused pane's host membership, launches a Python helper asynchronously, and inserts the uploaded paths into the original pane. The helper reads the native clipboard file list and streams each complete file through OpenSSH to Ubuntu. It does not read file contents into Python memory, make staging copies, or require a remote plugin/service.

Tern's QUIC connection identifies the destination and carries pane input; **file bytes use a separate SSH connection**. Configure an SSH mapping explicitly: a Tern port or endpoint ID is not an SSH destination.

In `auto` mode, Tern-recognized agent panes receive OMP `@"/remote/path"` references; other terminal panes receive POSIX-shell-quoted paths. `format: "omp"` or `format: "paths"` can override detection for a host. This uploads bytes regardless of file type; whether OMP can interpret the file is a separate matter.

## Requirements

- Tern desktop **0.6.2 or newer**, with native canvases and composer surface inspection. The connected host must support native canvases; no remote plugin is required.
- Python **3.10 or newer** on the client.
- OpenSSH `ssh` on the client, working noninteractive authentication (keys/agent or Tailscale SSH), and an already verified SSH host key.
- Ubuntu or another POSIX remote host with `sh`, `mkdir`, `cat`, and `rm`; the remote user's home must be absolute and writable.
- An active desktop clipboard on the client:
  - **macOS:** built-in `osascript` / AppKit; no `pngpaste` dependency.
  - **Wayland / Omarchy:** `wl-clipboard` (`wl-paste`). Run Tern in the graphical session.
  - **X11:** `xclip`, reading **CLIPBOARD**, not PRIMARY.
  - **Windows:** built-in Windows PowerShell / System.Windows.Forms; Python's `py -3` launcher by default.

Dependency examples:

```sh
# macOS, if a sufficiently new Python is not already installed
brew install python

# Omarchy / Arch
sudo pacman -S python openssh wl-clipboard
# Add xclip only if using X11:
sudo pacman -S xclip

# Debian / Ubuntu client
sudo apt install python3 openssh-client wl-clipboard xclip
```

On Windows install Python 3.10+ with its `py` launcher and enable the Windows **OpenSSH Client** optional feature if `ssh` is absent. A `python` argv override can select a different installation.

## Install for development

To develop from a checkout instead of installing a copy:

```sh
git clone https://github.com/vokativ/tern-file-paste.git
tern plugin link /absolute/path/to/tern-file-paste
```

The default branch is installable. To update an installed copy, run `tern plugin install github.com/vokativ/tern-file-paste --force`. For a linked checkout, use `git pull`. To pin `v0.1.0`, clone the repository, check out that tag, and install the local directory. Tern does not currently provide a plugin update command.

## Configure

Create `config.json` from `config.example.json` in the plugin's **data directory**. Tern creates the directory when it loads the plugin:

| Platform | Configuration file |
| --- | --- |
| macOS | `~/Library/Application Support/Tern/plugin-data/file-paste/config.json` |
| Linux | `${XDG_STATE_HOME:-~/.local/state}/tern/plugin-data/file-paste/config.json` |
| Windows | `%LOCALAPPDATA%\Tern\plugin-data\file-paste\config.json` |
| With `TERN_CONFIG_DIR` | `<TERN_CONFIG_DIR>/plugin-data/file-paste/config.json` |

For example:

```json
{
  "timeout": 300,
  "hosts": {
    "me@ubuntu-box": {
      "target": "me@ubuntu-box",
      "format": "auto"
    }
  }
}
```

The `hosts` key is the exact Tern host address (preferred) or its displayed name. `target` is an OpenSSH alias or `user@hostname`, without a port. IPv6 destinations can use an SSH config alias. Optional mapping fields: `port` (SSH port), `identity_file` (local private-key path), `timeout` (seconds), and `format` (`auto`, `omp`, `paths`). A top-level `timeout` supplies the default.

Python defaults to `["python3"]` on macOS/Linux and `["py", "-3"]` on Windows. If Tern's GUI PATH finds an old Python or cannot find it, add an explicit top-level override, for example:

```json
"python": ["/opt/homebrew/bin/python3"]
```

On Windows an installation without `py` can use `"python": ["python"]` or the full executable path. Do not include credentials in configuration; OpenSSH manages authentication.

Configuration is read for each invocation. It is kept outside the repository and never uploaded to the remote host.

### Keyboard shortcuts & collision avoidance

The default shortcuts are:
- **macOS:** `Command + Option + Shift + V` (`cmd+alt+shift+v`).
- **Linux / Windows:** `Ctrl + ;` (`ctrl+;`).

#### Why Ctrl + ; on Linux and Windows
This is Fn-free, avoids Alt/AltGr combinations, and does not require the bare Ctrl+Shift pair, which can be configured to switch layouts on Windows. The previous Ctrl+Shift+F8 default required Fn on some laptops.

In Tern 0.6.2, Ctrl+Shift+backslash toggles tab orientation, Ctrl+Shift+semicolon hides pictures-in-picture, Ctrl+Shift+slash inserts a path, and Ctrl+Shift+minus decreases font size. Ctrl+Shift+U is unused by Tern but is a Unicode-entry shortcut in some input methods. Ctrl+semicolon was unassigned in the inspected Tern keymap.

The inspected [Windows shortcut reference](https://support.microsoft.com/en-us/accessibility/windows/keyboard-shortcuts-in-windows), [GNOME defaults](https://help.gnome.org/gnome-help/shell-keyboard-shortcuts.html) used by Ubuntu/Fedora, [KDE common shortcuts](https://docs.kde.org/stable_kf6/en/khelpcenter/fundamentals/kbd.html) relevant to Kubuntu, and installed Omarchy defaults did not show a global Ctrl+semicolon binding. This is evidence about those defaults, not a guarantee for every desktop, preset, IME, or custom binding. Shortcuts in other applications do not conflict unless registered globally.

Known exception: [fcitx/fcitx5's Clipboard addon](https://fcitx-im.org/wiki/Clipboard) uses Ctrl+; by default. Omarchy ships that trigger disabled (`TriggerKey=`). If your input method intercepts the chord, change its clipboard trigger or rebind this plugin in Tern's Keyboard settings.

Punctuation placement varies across layouts. If semicolon is inconvenient or unavailable in your active layout, choose a shortcut using Tern's native Keyboard recorder. Do not change desktop layout-switch bindings just to accommodate this plugin.

#### Command palette fallback
The command palette provides another invocation path: open it using your configured palette shortcut and choose **Paste local files to remote**. Tern's default is Ctrl+Shift+P on Linux/Windows or Cmd+Shift+P on macOS; those keys can also be customized or intercepted.

#### Customizing or unbinding shortcuts
You can customize or remove the shortcut at any time:
1. **Via Tern GUI:** Open the palette (`Ctrl+Shift+P`) and choose **"Remote File Paste: Open keyboard settings"** (or press `Ctrl+,` and select **07 · Keyboard**). Search for `file-paste` and record your preferred shortcut or click `×` to remove.
2. **Via settings.json:** Add an override in Tern's user `keybinds` setting (`~/.config/tern/settings.json`):
```json
{
  "keybinds": {
    "ctrl+;": "plugin.file-paste.paste",
    "ctrl+shift+f8": [],
    "ctrl+alt+shift+v": []
  }
}
```
An empty array `[]` leaves the chord unbound. Merge these entries into your existing `keybinds`; do not replace unrelated bindings. Existing explicit F8 overrides survive plugin updates, so remove or replace yours to use the new default.

## Use

1. Select one or more **regular files** in Finder, your Linux file manager, or Explorer and Copy.
2. Focus a connected remote terminal/OMP pane in Tern.
3. Press the plugin shortcut or choose its palette command.
4. Watch the temporary **File upload** panel. After upload completes and the insertion call succeeds, it closes immediately; only the remote reference remains in the original input. Submit your prompt yourself.

Files are stored in `~/.cache/tern-file-paste/upload-<unique-id>/` on Ubuntu, in separate numbered directories. Duplicate basenames do not overwrite one another. Directories are private (`0700`) and files are owner-only (`0600`). Contents are preserved, but executable permissions, extended attributes, resource forks, and other metadata are not copied.

Normal spaces/Unicode are retained. Quotes, control characters, and non-UTF-8 filename bytes are percent-encoded in the uploaded basename so the result can be represented safely in a prompt. Originals are never renamed or deleted; a file-manager Cut selection is treated as Copy.

Uploads remain available until you remove their remote upload directories. There is no background deletion or synchronization.

### Progress and input safety

An explicit upload opens or reuses a temporary native panel below the destination without taking keyboard focus. While transferring, it shows bytes streamed to SSH versus the total source size in MiB, a progress bar, and connecting/uploading/finalizing states. This counter is not an acknowledgement of remote receipt: upload completion is reported only after all SSH processes succeed.

Successful uploads close the panel immediately after the insertion call succeeds and show no success toast or retained status. Transfer failures, destination changes, an unready input, or insertion-call errors keep the panel open with full diagnostics, remote paths and references. In those cases, **Remote File Paste: Upload status** (`plugin.file-paste.status`) reopens the panel without uploading again, and **Copy references** changes the clipboard only when explicitly clicked.

For native OMP, the plugin waits up to 10 seconds for the original composer to become ready, rechecks the destination, then inserts once and closes the panel. It does not wait for a later surface-tree echo or claim that Tern's void-returning insertion API acknowledges delivery. No post-paste confirmation timer, automatic insertion retry, or Enter key is used.

The helper disables Python bytecode writes before importing its local modules. Without this, a cold first invocation can create `__pycache__` inside Tern's watched plugin directory, trigger a reload, and discard the upload callback even though SSH finishes. This first-run failure was reproduced on Omarchy and fixed without prewarming caches or retransferring files.

## Boundaries and failures

- **Regular files only:** folders and special files are rejected before remote upload. There is no recursive folder transfer.
- **Agent panes need a compatible native composer:** older OMP or other agents without a sendable Tern editor are not auto-inserted. After the readiness timeout, use Upload status → Copy references and insert them manually. Plain-terminal panes still use direct input dispatch.
- **Files, not image-only clipboard data:** image/text-only clipboards produce a clear error and are not uploaded. This deliberately does not replace Tern's native screenshot paste.
- No fallback from a failed Wayland clipboard read to potentially stale XWayland data.
- Password/passphrase/first-contact host-key prompts are not allowed in the background helper. Authenticate and verify the host with `ssh` first; use your SSH agent where necessary.
- A failed upload returns no paths and attempts to remove only its newly created, ownership-marked upload directory. Cleanup failure is reported explicitly.
- Focus changes do not redirect a paste into another pane. If the original pane exits, changes program/agent state, or disconnects, uploaded files remain available in the status panel instead of being typed into another destination.
- Reloading/quitting Tern while a helper runs can leave uploaded files without an inserted path; do not reload during an upload.
- This is a desktop plugin. Browser Tern runs no Luau plugins, and iOS has no subprocess support.

## Verification and development

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
luau tests/workflow.luau
```

Regression tests cover clipboard format precedence, URI decoding, errors, filename collisions, binary/empty files, permissions, progress, cold-package launches, rollback, total deadlines, quoting, composer readiness and stale destination safety. Ordinary local tests do not change the user's clipboard or contact a remote host. CI runs on macOS, Ubuntu, and Windows; isolated CI checks exercise actual X11/Wayland clipboard tools and Windows file-drop lists. The simulated remote-shell transfer tests require POSIX and therefore skip on Windows.

The original pre-0.3 macOS smoke verified clipboard uploads and Ubuntu file integrity. The updated 0.3 desktop workflow was exercised on Omarchy/Wayland, including native OMP insertion, progress, quiet successful dismissal and first-use bytecode-triggered reload failure. Progress, cold-start and composer-readiness regressions run locally. Updated macOS and Windows GUI paths still need device-level verification. No passing Luau-LSP static-typecheck claim is made.

## Commit email privacy

This repository accepts GitHub no-reply email addresses in **author, committer, and annotated-tagger metadata**. GitHub-created commits may use `noreply@github.com`. This does not hide names or remove copies someone already downloaded.

When developing from a checkout, set your own GitHub no-reply address and enable the pre-push guard:

```sh
git config --local user.email "YOUR_GITHUB_NOREPLY_ADDRESS"
git config --local core.hooksPath .githooks
python3 tools/check_commit_privacy.py --all
```

The guard checks every ancestor of every outgoing commit and rejects shallow history rather than silently auditing a truncated clone. CI fetches full history and checks all refs. A local hook can be bypassed with `--no-verify`; CI remains a second check, not a guarantee against every manual/API publication.

Also enable GitHub's **Keep my email addresses private** and **Block command line pushes that expose my email** under account email settings. GitHub's push block checks only the latest commit's author, so it is not a replacement for the full-history guard.

## Feedback

Report plugin problems in [GitHub Issues](https://github.com/vokativ/tern-file-paste/issues). Include the client OS, Tern version, clipboard source, and sanitized error text. Do not attach credentials, private host configuration, or the copied file itself unless it is safe to share.

License: MIT.
