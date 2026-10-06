"""Read native clipboard file selections without consuming images or plain text.

File-manager cut selections are deliberately treated as copies. This module only
reads the clipboard; it never removes files or updates clipboard ownership.
"""

from __future__ import annotations

import json
import ntpath
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote_to_bytes, urlsplit


class ClipboardError(Exception):
    """The native clipboard could not be read safely."""


_TIMEOUT = 10
_SCRIPTS = Path(__file__).resolve().parent
_FILE_TYPES = ("x-special/gnome-copied-files", "text/uri-list")
_BAD_ESCAPE = re.compile(r"%(?![0-9a-fA-F]{2})")


def _command(argv: list[str], *, empty_errors: tuple[str, ...] = ()) -> str:
    # Stable diagnostics allow only known empty-selection errors to mean [].
    environment = os.environ.copy()
    environment["LC_ALL"] = "C"
    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=_TIMEOUT,
            env=environment,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ClipboardError(f"Clipboard dependency not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ClipboardError(f"Clipboard command timed out after {_TIMEOUT}s: {argv[0]}") from exc
    except (OSError, UnicodeError) as exc:
        raise ClipboardError(f"Cannot read clipboard using {argv[0]}: {exc}") from exc
    if result.returncode:
        diagnostic = result.stderr.decode("utf-8", errors="replace").strip()
        if result.returncode == 1 and diagnostic in empty_errors:
            return ""
        detail = diagnostic or f"exit status {result.returncode}"
        raise ClipboardError(f"Clipboard command {argv[0]} failed: {detail}")
    # Decode on this thread: Windows subprocess text-mode reader threads can
    # swallow UnicodeDecodeError and leave stdout unset.
    try:
        return result.stdout.decode("utf-8")
    except UnicodeError as exc:
        raise ClipboardError(f"Clipboard command {argv[0]} returned invalid UTF-8: {exc}") from exc


def _unique(paths: list[str]) -> list[str]:
    return list(dict.fromkeys(paths))


def _native_json(text: str, *, windows: bool = False) -> list[str]:
    try:
        paths = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ClipboardError("Native clipboard reader did not return a JSON array") from exc
    if not isinstance(paths, list):
        raise ClipboardError("Native clipboard reader did not return a JSON array")
    for path in paths:
        if not isinstance(path, str) or not path or "\0" in path:
            raise ClipboardError("Native clipboard reader returned an invalid file path")
        if windows:
            absolute = ntpath.isabs(path) and bool(ntpath.splitdrive(path)[0])
        else:
            absolute = path.startswith("/")
        if not absolute:
            raise ClipboardError("Native clipboard reader returned a nonabsolute file path")
    return _unique(paths)


def _local_uri(uri: str) -> str:
    # urlsplit silently strips some control characters: reject them first.
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in uri):
        raise ClipboardError(f"Malformed clipboard file URI: {uri!r}")
    if _BAD_ESCAPE.search(uri):
        raise ClipboardError(f"Malformed percent escape in clipboard file URI: {uri!r}")
    try:
        parts = urlsplit(uri)
    except ValueError as exc:
        raise ClipboardError(f"Malformed clipboard file URI: {uri!r}") from exc
    if parts.scheme.lower() != "file" or parts.netloc.lower() not in ("", "localhost"):
        raise ClipboardError(f"Clipboard URI is not a local file: {uri!r}")
    if "?" in uri or "#" in uri or not parts.path.startswith("/"):
        raise ClipboardError(f"Malformed clipboard file URI: {uri!r}")
    path = os.fsdecode(unquote_to_bytes(parts.path))
    if "\0" in path or path.startswith("//"):
        raise ClipboardError(f"Clipboard URI is not a valid local file path: {uri!r}")
    return path


def _uri_files(text: str, mime_type: str) -> list[str]:
    # Split only URI-list's newline delimiter, not Unicode characters in names.
    lines = [line.removesuffix("\r") for line in text.split("\n")]
    if mime_type == "x-special/gnome-copied-files":
        if not lines or lines[0] not in ("copy", "cut"):
            raise ClipboardError("GNOME clipboard file list has no valid copy/cut action header")
        lines = lines[1:]
    return _unique([_local_uri(line) for line in lines if line and not line.startswith("#")])


def _linux_files() -> list[str]:
    wayland = bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE") == "wayland"
    if wayland:
        # Never fall back to XWayland: it may expose a stale, unrelated selection.
        prefix = ["wl-paste"]
        offered = _command(prefix + ["--list-types"], empty_errors=("Nothing is copied",))
        arguments = lambda mime: prefix + ["--no-newline", "--type", mime]
    elif os.environ.get("DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "x11":
        prefix = ["xclip", "-selection", "clipboard", "-out"]
        offered = _command(
            prefix + ["-target", "TARGETS"],
            empty_errors=(
                "xclip: Error: There is no owner for the CLIPBOARD selection",
                # xclip 0.13 uses the same diagnostic for no owner / no TARGETS.
                "Error: target TARGETS not available",
            ),
        )
        arguments = lambda mime: prefix + ["-target", mime]
    else:
        raise ClipboardError("No active Wayland or X11 clipboard session (WAYLAND_DISPLAY/DISPLAY)")
    types = set(offered.splitlines())
    for mime_type in _FILE_TYPES:
        if mime_type in types:
            return _uri_files(_command(arguments(mime_type)), mime_type)
    return []


def read_files(platform: str | None = None) -> list[str]:
    """Return ordered, deduplicated native file paths; text/images return [].

    macOS needs its built-in osascript/AppKit. Linux needs wl-paste on Wayland
    or xclip on X11, in the graphical user's session. Windows needs Windows
    PowerShell with .NET System.Windows.Forms and an interactive desktop.
    """
    platform = sys.platform if platform is None else platform
    if platform == "darwin":
        return _native_json(_command(["osascript", "-l", "JavaScript", str(_SCRIPTS / "macos_files.js")]))
    if platform == "linux":
        return _linux_files()
    if platform == "win32":
        return _native_json(
            _command([
                "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-STA",
                "-ExecutionPolicy", "Bypass", "-File", str(_SCRIPTS / "windows_files.ps1"),
            ]),
            windows=True,
        )
    raise ClipboardError(f"Unsupported clipboard platform: {platform}")
