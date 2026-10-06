"""Clipboard decoding tests; native fixtures never write the user's clipboard."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from helpers import clipboard


class UriListTests(unittest.TestCase):
    def parse(self, text, mime="text/uri-list"):
        return clipboard._uri_files(text, mime)

    def test_local_uri_decoding_order_and_duplicates(self):
        payload = "# copied files\r\nfile:///tmp/a%20b%23%3F.txt\r\nfile://localhost/tmp/%E6%96%87%E4%BB%B6.txt\r\n\r\nfile:///tmp/a%20b%23%3F.txt\r\nfile:/tmp/a+b.txt\r\n"
        self.assertEqual(self.parse(payload), ["/tmp/a b#?.txt", "/tmp/文件.txt", "/tmp/a+b.txt"])

    def test_percent_encoding_decoded_once(self):
        self.assertEqual(self.parse("file:///tmp/%2520.txt"), ["/tmp/%20.txt"])

    def test_encoded_newlines_and_native_byte_names(self):
        self.assertEqual(self.parse("file:///tmp/a%0Ab"), ["/tmp/a\nb"])
        if os.name != "nt":
            self.assertEqual(os.fsencode(self.parse("file:///tmp/%FF")[0]), b"/tmp/\xff")

    def test_empty_and_comment_only_uri_list(self):
        self.assertEqual(self.parse(""), [])
        self.assertEqual(self.parse("# no files\r\n\r\n"), [])

    def test_rejects_malformed_remote_and_plain_path_entries(self):
        invalid = [
            "https://example.com/a", "smb://server/share/a", "file://server/tmp/a",
            "file://localhost:22/tmp/a", "file://user@localhost/tmp/a",
            "file://[broken/tmp/a", "file:relative", "file://localhost",
            "file:///tmp/%", "file:///tmp/%0", "file:///tmp/%XY", "file:///tmp/%00",
            "file:///tmp/a?query", "file:///tmp/a#fragment", "file:///tmp/a?", "file:///tmp/a#",
            "file:////server/share/a", "file:///tmp/a b", "file:///tmp/a\tb", "/tmp/a",
        ]
        for uri in invalid:
            with self.subTest(uri=uri), self.assertRaises(clipboard.ClipboardError):
                self.parse(uri)

    def test_mixed_remote_uri_fails_whole_selection(self):
        with self.assertRaises(clipboard.ClipboardError):
            self.parse("file:///tmp/local\nfile://server/tmp/remote")

    def test_gnome_action_headers(self):
        for action in ("copy", "cut"):
            with self.subTest(action=action):
                self.assertEqual(self.parse(action + "\r\nfile:///tmp/a\r\n", "x-special/gnome-copied-files"), ["/tmp/a"])
                self.assertEqual(self.parse(action + "\n", "x-special/gnome-copied-files"), [])
        for header in ("", "move", "COPY", "file:///tmp/a"):
            with self.subTest(header=header), self.assertRaises(clipboard.ClipboardError):
                self.parse(header + "\nfile:///tmp/a", "x-special/gnome-copied-files")

    @unittest.skipIf(os.name == "nt", "GNOME file URIs refer to a POSIX filesystem")
    def test_copy_and_cut_keep_source_bytes_and_clipboard_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "copy ü file.bin"
            data = b"\x00\xffreal source file\n"
            source.write_bytes(data)
            for action in ("copy", "cut"):
                outputs = ["x-special/gnome-copied-files\ntext/uri-list\nimage/png\n", action + "\n" + source.as_uri()]
                with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=True), patch.object(clipboard, "_command", side_effect=outputs) as command:
                    self.assertEqual(clipboard.read_files("linux"), [str(source)])
                    self.assertEqual(source.read_bytes(), data)


class NativeJsonTests(unittest.TestCase):
    def test_mac_json_arrays_deduplicate_without_reordering(self):
        self.assertEqual(clipboard._native_json('["/tmp/a", "/tmp/文 件", "/tmp/a"]'), ["/tmp/a", "/tmp/文 件"])
        self.assertEqual(clipboard._native_json("[]"), [])
        self.assertEqual(clipboard._native_json('["/tmp/single"]'), ["/tmp/single"])

    def test_windows_drive_and_unc_paths(self):
        paths = ["C:\\Users\\a\\文 件.txt", "\\\\server\\share\\a", "C:\\Users\\a\\文 件.txt"]
        self.assertEqual(clipboard._native_json(json.dumps(paths), windows=True), paths[:2])

    def test_reject_invalid_json_shapes_and_paths(self):
        for data in ("", "not JSON", "null", '"/tmp/a"', "{}", "[null]", "[1]", "[true]", '[{}]', '[[]]', '[""]', '["relative"]', '["/tmp/\\u0000"]'):
            with self.subTest(data=data), self.assertRaises(clipboard.ClipboardError):
                clipboard._native_json(data)
        for path in ("relative", "C:relative", "\\rooted-without-drive"):
            with self.subTest(path=path), self.assertRaises(clipboard.ClipboardError):
                clipboard._native_json(json.dumps([path]), windows=True)

    def test_native_consumers_propagate_shape_errors(self):
        for platform in ("darwin", "win32"):
            with self.subTest(platform=platform), patch.object(clipboard, "_command", return_value='"single path, not an array"'), self.assertRaises(clipboard.ClipboardError):
                clipboard.read_files(platform)



class LinuxSelectionTests(unittest.TestCase):
    def read(self, environment, offered, payload=None):
        replies = [offered] if payload is None else [offered, payload]
        with patch.dict(os.environ, environment, clear=True), patch.object(clipboard, "_command", side_effect=replies) as command:
            result = clipboard.read_files("linux")
            return result, [call.args[0] for call in command.call_args_list]

    def test_wayland_prefers_files_to_icon_previews(self):
        result, commands = self.read({"WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":0"}, "image/png\ntext/plain\ntext/uri-list\n", "file:///tmp/a%20b")
        self.assertEqual(result, ["/tmp/a b"])

    def test_wayland_session_without_display_variable(self):
        result, commands = self.read({"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0"}, "text/uri-list", "file:///tmp/a")
        self.assertEqual(result, ["/tmp/a"])

    def test_x11_uses_clipboard_selection_not_primary(self):
        result, commands = self.read({"DISPLAY": ":0"}, "TARGETS\ntext/uri-list\nimage/png", "file:///tmp/a")
        self.assertEqual(result, ["/tmp/a"])

    def test_text_and_image_only_never_request_payload(self):
        for environment in ({"WAYLAND_DISPLAY": "wayland-0"}, {"DISPLAY": ":0"}):
            for types in ("text/plain\nUTF8_STRING\n", "image/png\nimage/tiff\n", "", "text/plain\nimage/png\n"):
                with self.subTest(environment=environment, types=types):
                    result, commands = self.read(environment, types)
                    self.assertEqual(result, [])
                    self.assertEqual(len(commands), 1)

    def test_gnome_files_have_priority_over_uri_list(self):
        result, commands = self.read({"DISPLAY": ":0"}, "text/uri-list\nx-special/gnome-copied-files", "cut\nfile:///tmp/a")
        self.assertEqual(result, ["/tmp/a"])

    def test_failed_wayland_does_not_fall_back_to_x11(self):
        with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}, clear=True), patch.object(clipboard, "_command", side_effect=clipboard.ClipboardError("Wayland denied")) as command:
            with self.assertRaisesRegex(clipboard.ClipboardError, "Wayland denied"):
                clipboard.read_files("linux")
            self.assertEqual(command.call_count, 1)

    def test_advertised_file_payload_failure_is_not_empty_clipboard(self):
        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True), patch.object(clipboard, "_command", side_effect=["text/uri-list", clipboard.ClipboardError("selection changed")]):
            with self.assertRaisesRegex(clipboard.ClipboardError, "selection changed"):
                clipboard.read_files("linux")

    def test_no_graphical_session_and_unsupported_platform(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(clipboard.ClipboardError):
            clipboard.read_files("linux")
        with self.assertRaisesRegex(clipboard.ClipboardError, "Unsupported"):
            clipboard.read_files("freebsd")


class CommandFailureTests(unittest.TestCase):
    def test_real_subprocess_failure_preserves_diagnostic(self):
        with self.assertRaisesRegex(clipboard.ClipboardError, "native reader failed"):
            clipboard._command([sys.executable, "-c", "import sys; sys.stderr.write('native reader failed'); sys.exit(7)"])

    def test_only_exact_empty_diagnostic_and_exit_one_are_empty(self):
        code = "import sys; sys.stderr.write('Nothing is copied\\n'); sys.exit(1)"
        self.assertEqual(clipboard._command([sys.executable, "-c", code], empty_errors=("Nothing is copied",)), "")
        for changed in (code.replace("exit(1)", "exit(2)"), code.replace("Nothing is copied", "Clipboard access denied")):
            with self.subTest(code=changed), self.assertRaises(clipboard.ClipboardError):
                clipboard._command([sys.executable, "-c", changed], empty_errors=("Nothing is copied",))

    def test_missing_executable_is_actionable(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(clipboard.ClipboardError, "dependency not found"):
            clipboard._command([str(Path(directory) / "nonexistent-clipboard-tool")])

    def test_timeout_and_invalid_encoding_are_clipboard_errors(self):
        with patch.object(clipboard, "_TIMEOUT", 0.05), self.assertRaisesRegex(clipboard.ClipboardError, "timed out"):
            clipboard._command([sys.executable, "-c", "import time; time.sleep(3)"])
        with self.assertRaises(clipboard.ClipboardError):
            clipboard._command([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff')"])



@unittest.skipUnless(sys.platform == "darwin", "JXA/AppKit is available only on macOS")
class MacNativeScriptTests(unittest.TestCase):
    def run_fixture(self, uris):
        # Exercise the shipped script and actual AppKit NSURL/NSArray bridging.
        # Substitute only the pasteboard so the user's clipboard is untouched.
        source = (clipboard._SCRIPTS / "macos_files.js").read_text(encoding="utf-8")
        harness = """
ObjC.import('AppKit');
function run() {
    var urls = $.NSMutableArray.alloc.init;
    var input = INPUT;
    input.forEach(function (uri) { urls.addObject($.NSURL.URLWithString(uri)); });
    var bridge = {
        NSArray: $.NSArray, NSURL: $.NSURL, NSDictionary: $.NSDictionary,
        NSNumber: $.NSNumber,
        NSPasteboardURLReadingFileURLsOnlyKey: $.NSPasteboardURLReadingFileURLsOnlyKey,
        NSPasteboard: {generalPasteboard: {readObjectsForClassesOptions: function(classes, options) {
            return urls;
        }}}
    };
    return (new Function('ObjC', '$', SOURCE + '\\nreturn run();'))(ObjC, bridge);
}
""".replace("INPUT", json.dumps(uris)).replace("SOURCE", json.dumps(source))
        return clipboard._command(["osascript", "-l", "JavaScript", "-e", harness])

    def test_actual_script_returns_empty_single_and_multiple_json_arrays(self):
        for uris, expected in (([], []), (["file:///tmp/a%20b"], ["/tmp/a b"]), (["file:///tmp/a", "file:///tmp/%E6%96%87"], ["/tmp/a", "/tmp/文"])):
            with self.subTest(uris=uris):
                self.assertEqual(json.loads(self.run_fixture(uris)), expected)

    def test_actual_script_failure_is_not_an_empty_file_list(self):
        for uri in ("https://example.com/a", "file://remote/tmp/a", "file:///tmp/a?query", "file:///tmp/a#fragment"):
            with self.subTest(uri=uri), self.assertRaises(clipboard.ClipboardError):
                self.run_fixture([uri])


@unittest.skipUnless(sys.platform == "win32", "Windows PowerShell/Forms is available only on Windows")
class WindowsNativeScriptTests(unittest.TestCase):
    def test_actual_script_rejects_non_sta_with_nonzero_exit(self):
        with self.assertRaisesRegex(clipboard.ClipboardError, "STA"):
            clipboard._command(["powershell.exe", "-NoProfile", "-NonInteractive", "-MTA", "-ExecutionPolicy", "Bypass", "-File", str(clipboard._SCRIPTS / "windows_files.ps1")])

    @unittest.skipUnless(os.environ.get("CI") == "true", "Clipboard mutation is limited to an isolated CI desktop")
    def test_native_file_selections_and_text_only_clipboard(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / "copied file č.txt", root / "second.txt"]
            for path in paths:
                path.write_bytes(b"clipboard file fixture")
            harness = root / "fixture.ps1"
            harness.write_text(r"""
param([string] $Reader, [string] $Selection)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
try {
    $files = @((Get-Content -Raw -Encoding UTF8 $Selection | ConvertFrom-Json))
    if ($files.Count -eq 0) {
        [System.Windows.Forms.Clipboard]::SetText('C:\not-a-copied-file.txt')
    } else {
        $collection = New-Object System.Collections.Specialized.StringCollection
        foreach ($file in $files) { [void] $collection.Add([string] $file) }
        [System.Windows.Forms.Clipboard]::SetFileDropList($collection)
    }
    & $Reader
} finally {
    [System.Windows.Forms.Clipboard]::Clear()
}
""", encoding="utf-8")
            selection = root / "selection.json"
            for selected in ([], paths[:1], paths):
                selection.write_text(json.dumps([str(path) for path in selected]), encoding="utf-8")
                output = clipboard._command([
                    "powershell.exe", "-NoProfile", "-NonInteractive", "-STA",
                    "-ExecutionPolicy", "Bypass", "-File", str(harness),
                    "-Reader", str(clipboard._SCRIPTS / "windows_files.ps1"),
                    "-Selection", str(selection),
                ])
                self.assertEqual(json.loads(output), [str(path) for path in selected])


if __name__ == "__main__":
    unittest.main()
