"""Real Linux clipboard tools, restricted to isolated CI display servers."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from helpers.clipboard import read_files


@unittest.skipUnless(sys.platform == "linux" and os.environ.get("CI") == "true", "Isolated Linux CI desktops only")
class LinuxNativeClipboardTests(unittest.TestCase):
    def test_x11_file_list_and_plain_text(self):
        with tempfile.TemporaryDirectory() as directory:
            files = [Path(directory) / "file č with spaces.txt", Path(directory) / "second.txt"]
            for path in files:
                path.write_bytes(b"fixture")
            # xvfb-run creates a separate X server; no user's clipboard is touched.
            script = r'''
import json, subprocess, sys
from helpers.clipboard import read_files
paths = json.loads(sys.argv[1])
payload = "\n".join(__import__('pathlib').Path(p).as_uri() for p in paths)
subprocess.run(["xclip", "-selection", "clipboard", "-in", "-target", "text/uri-list"], input=payload.encode(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
files = read_files("linux")
subprocess.run(["xclip", "-selection", "clipboard", "-in"], input=paths[0].encode(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
text_only = read_files("linux")
print(json.dumps([files, text_only]))
'''
            environment = os.environ.copy()
            environment.pop("WAYLAND_DISPLAY", None)
            environment["XDG_SESSION_TYPE"] = "x11"
            result = subprocess.run(["xvfb-run", "-a", sys.executable, "-c", script, json.dumps([str(p) for p in files])], cwd=Path(__file__).resolve().parents[1], env=environment, capture_output=True, text=True, check=True, timeout=30)
            self.assertEqual(json.loads(result.stdout), [[str(p) for p in files], []])

    def test_wayland_file_list_and_plain_text(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.mkdir(mode=0o700)
            file = root / "file č with spaces.txt"
            file.write_bytes(b"fixture")
            config = root / "sway.conf"
            config.write_text("xwayland disable\n", encoding="utf-8")
            environment = os.environ.copy()
            environment.pop("WAYLAND_DISPLAY", None)
            environment.update(XDG_RUNTIME_DIR=str(runtime), XDG_SESSION_TYPE="wayland", DISPLAY=":999", WLR_BACKENDS="headless", WLR_RENDERER="pixman", WLR_LIBINPUT_NO_DEVICES="1")
            compositor = subprocess.Popen(["sway", "--unsupported-gpu", "--config", str(config)], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 10
                while not any(path.is_socket() for path in runtime.glob("wayland-*")):
                    if compositor.poll() is not None or time.monotonic() > deadline:
                        self.fail("Isolated Wayland compositor did not start")
                    time.sleep(0.05)
                environment["WAYLAND_DISPLAY"] = next(path.name for path in runtime.glob("wayland-*") if path.is_socket())
                subprocess.run(["wl-copy", "--type", "text/uri-list"], input=file.as_uri().encode(), env=environment, check=True, timeout=10)
                with patch.dict(os.environ, environment, clear=True):
                    self.assertEqual(read_files("linux"), [str(file)])
                    subprocess.run(["wl-copy", "--type", "text/plain"], input=str(file).encode(), env=environment, check=True, timeout=10)
                    self.assertEqual(read_files("linux"), [])
            finally:
                compositor.terminate()
                compositor.wait(timeout=10)
