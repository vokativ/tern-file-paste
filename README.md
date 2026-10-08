# Remote File Paste for Tern

Copy files in your desktop file manager, focus a remote [Tern](https://stencil.so/tern) terminal or OMP pane, and upload their complete contents with a separate shortcut.

| Client | Shortcut |
| --- | --- |
| macOS | Command + Option + Shift + V |
| Linux / Windows | Ctrl + Shift + F8 |

The command palette actions are:
- **Paste local files to remote** (`plugin.file-paste.paste`) — uploads clipboard files and inserts remote paths.
- **Remote File Paste: Open keyboard settings** (`plugin.file-paste.configure`) — opens Tern's native Preferences page (`07 · Keyboard`) to customize or remove shortcuts.

Normal paste is not overridden. Copy alone does not upload anything, and the plugin never presses Enter or submits a prompt.

**Confirmed workflow:** copy an image **file** in Finder, focus OMP on Ubuntu through Tern, and press **Command + Option + Shift + V**. The complete file uploads and its remote reference is inserted into the composer. This is separate from copying image pixels in a browser/viewer: image-only clipboard paste is not handled by this plugin.

## Quick start

Install on the **client computer running the Tern window**, not on Ubuntu:

```sh
tern plugin install github.com/vokativ/tern-file-paste
```

Or use **Preferences → Plugins → Install…** with the same GitHub URL.

You need **Python 3.10+**, the **OpenSSH client**, a working noninteractive SSH connection to Ubuntu, and the platform clipboard tools listed below. Installation alone does not configure the destination: add a host mapping in the plugin's [configuration file](#configure) before using the shortcut.

| Verification | Status |
| --- | --- |
| macOS → Ubuntu, copied image file → remote OMP | Confirmed in actual use |
| Linux (Omarchy / Wayland) → Ubuntu, binary / JPEG / empty files | Confirmed in actual use |
| Binary, empty, duplicate-name and Unicode files → Ubuntu | Transfer integrity and permissions verified |
| macOS / Ubuntu / Windows CI | Passing, including native Windows and isolated X11/Wayland clipboard checks |


## How it works

A client-side Luau window plugin reads the focused pane's host membership, launches a Python helper asynchronously, and inserts the uploaded paths into the original pane. The helper reads the native clipboard file list and streams each complete file through OpenSSH to Ubuntu. It does not read file contents into Python memory, make staging copies, or require a remote plugin/service.

Tern's QUIC connection identifies the destination and carries pane input; **file bytes use a separate SSH connection**. Configure an SSH mapping explicitly: a Tern port or endpoint ID is not an SSH destination.

In `auto` mode, Tern-recognized agent panes receive OMP `@"/remote/path"` references; other terminal panes receive POSIX-shell-quoted paths. `format: "omp"` or `format: "paths"` can override detection for a host. This uploads bytes regardless of file type; whether OMP can interpret the file is a separate matter.

## Requirements

- Tern desktop with the host/session window APIs shipped in **0.5.0**.
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
- **Linux / Windows:** `Ctrl + Shift + F8` (`ctrl+shift+f8`).

#### Why Ctrl + Shift + F8 on Linux and Windows
Chords combining `Alt + Shift` with a letter (such as `Ctrl + Alt + Shift + V`) are prone to system-level conflicts:
- On Linux (Ubuntu, Fedora, Kubuntu, Omarchy / Hyprland), `Alt + Shift` is a widespread system shortcut for switching keyboard layouts (e.g. XKB `grp:alt_shift_toggle` or desktop input source switchers). The compositor or input layer intercepts `Alt + Shift` immediately, toggling the layout so the subsequent letter key is received as a different keysym or dropped.
- On Windows with multiple input languages, `Left Alt + Left Shift` is the legacy default layout toggle. Furthermore, on European and international Windows layouts, `Ctrl + Alt` synthesizes `AltGr`, modifying letter keys into special symbols.
- `Ctrl + Shift + F8` avoids the `Alt` key entirely, avoids `Super`/`Win`, and uses a physical function key that remains invariant across all keyboard layouts.

#### Command palette fallback
Regardless of your active keyboard layout or desktop environment, the command palette (`Ctrl + Shift + P` / `Cmd + Shift + P` → **"Paste local files to remote"**) is always available with zero risk of collision.

#### Customizing or unbinding shortcuts
You can customize or remove the shortcut at any time:
1. **Via Tern GUI:** Open the palette (`Ctrl+Shift+P`) and choose **"Remote File Paste: Open keyboard settings"** (or press `Ctrl+,` and select **07 · Keyboard**). Search for `file-paste` and record your preferred shortcut or click `×` to remove.
2. **Via settings.json:** Add an override in Tern's user `keybinds` setting (`~/.config/tern/settings.json`):
```json
{
  "keybinds": {
    "ctrl+shift+f8": "plugin.file-paste.paste",
    "ctrl+alt+shift+v": []
  }
}
```
An empty array `[]` leaves the chord unbound.
## Use

1. Select one or more **regular files** in Finder, your Linux file manager, or Explorer and Copy.
2. Focus a connected remote terminal/OMP pane in Tern.
3. Press the plugin shortcut or choose its palette command.
4. Wait for the success notification and the remote paths to appear. Submit your prompt yourself.

Files are stored in `~/.cache/tern-file-paste/upload-<unique-id>/` on Ubuntu, in separate numbered directories. Duplicate basenames do not overwrite one another. Directories are private (`0700`) and files are owner-only (`0600`). Contents are preserved, but executable permissions, extended attributes, resource forks, and other metadata are not copied.

Normal spaces/Unicode are retained. Quotes, control characters, and non-UTF-8 filename bytes are percent-encoded in the uploaded basename so the result can be represented safely in a prompt. Originals are never renamed or deleted; a file-manager Cut selection is treated as Copy.

Uploads remain available until you remove their remote upload directories. There is no background deletion or synchronization.

## Boundaries and failures

- **Regular files only:** folders and special files are rejected before remote upload. There is no recursive folder transfer.
- **Files, not image-only clipboard data:** image/text-only clipboards produce a clear error and are not uploaded. This deliberately does not replace Tern's native screenshot paste.
- No fallback from a failed Wayland clipboard read to potentially stale XWayland data.
- Password/passphrase/first-contact host-key prompts are not allowed in the background helper. Authenticate and verify the host with `ssh` first; use your SSH agent where necessary.
- A failed upload returns no paths and attempts to remove only its newly created, ownership-marked upload directory. Cleanup failure is reported explicitly.
- Focus changes do not redirect a paste into another pane. If the original pane exits, changes program/agent state, or disconnects, uploaded files are retained and the notification gives their directory instead of typing into the wrong destination.
- Reloading/quitting Tern while a helper runs can leave uploaded files without an inserted path; do not reload during an upload.
- This is a desktop plugin. Browser Tern runs no Luau plugins, and iOS has no subprocess support.

## Verification and development

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
luau tests/workflow.luau
```

Regression tests cover clipboard format precedence, URI decoding, errors, filename collisions, binary/empty files, permissions, rollback, total deadlines, quoting, and stale destination safety. Ordinary local tests do not change the user's clipboard or contact a remote host. CI runs on macOS, Ubuntu, and Windows; isolated CI checks exercise actual X11/Wayland clipboard tools and Windows file-drop lists. The simulated remote-shell transfer tests require POSIX and therefore skip on Windows.

The development smoke on macOS invoked the installed Tern plugin with native file-list clipboard data and uploaded actual files to Ubuntu; independent remote SHA-256 checks verified binary, empty, and duplicate-name files. A user subsequently confirmed copying an image file from the Mac and pasting it into remote OMP. Interactive Omarchy/Windows Tern sessions still need device-level checks. Luau-LSP 1.70.1 could not load Tern 0.5.0's shipped SDK declarations (`userdata` type), so no passing static-typecheck claim is made.

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
