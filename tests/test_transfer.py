"""Behavioral transfer tests with real subprocesses and an isolated fake server.

The fake ssh executes the actual generated POSIX commands in a temporary HOME.
File bytes flow through its stdin descriptor into real remote cat processes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import socket
import stat
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from helpers import transfer


_FAKE_OPENSSH = r'''
import json
import os
from pathlib import Path
import subprocess
import sys
import time

kind = os.environ["FAKE_KIND"]
args = sys.argv[1:]
options = {}
while args and args[0].startswith("-"):
    flag = args.pop(0)
    if flag == "-T":
        options[flag] = [True]
        continue
    if flag not in ("-o", "-i", "-p") or not args:
        sys.exit("Unexpected OpenSSH option: " + flag)
    value = args.pop(0)
    options.setdefault(flag, []).append(value)
if "-T" not in options:
    sys.exit("PTY allocation must be disabled")
if "BatchMode=yes" not in options.get("-o", []):
    sys.exit("BatchMode is required")
if "ConnectTimeout=10" not in options.get("-o", []):
    sys.exit("ConnectTimeout is required")
if any("StrictHostKeyChecking=no" in option for option in options.get("-o", [])):
    sys.exit("Host key checking must not be disabled")
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"kind": kind, "args": args, "options": options}) + "\n")
home = Path(os.environ["FAKE_HOME"])
if kind != "ssh" or len(args) != 2:
    sys.exit("Expected SSH target and command")
command = args[1]
if os.environ.get("FAIL_DIRECTORY") and command.startswith("umask 077; mkdir -- "):
    sys.exit("Simulated directory failure")
if os.environ.get("FAIL_CLEANUP") and command.startswith("root="):
    sys.exit("Simulated cleanup failure")
environment = dict(os.environ, HOME=str(home))
if command.startswith("umask 077; cat > "):
    count_file = home / "upload-count"
    count = int(count_file.read_text()) + 1 if count_file.exists() else 1
    count_file.write_text(str(count))
    if os.environ.get("UPLOAD_DELAY"):
        time.sleep(float(os.environ["UPLOAD_DELAY"]))
    if count == int(os.environ.get("FAIL_UPLOAD_NUMBER") or "0"):
        partial = command.replace("cat > ", "printf '%s' partial > ", 1)
        subprocess.run(["/bin/sh", "-c", partial], cwd=home, env=environment, check=True)
        sys.exit("Simulated transfer failure after partial write")
result = subprocess.run(["/bin/sh", "-c", command], cwd=home, env=environment)
if os.environ.get("CORRUPT_RESPONSE") and command.startswith("set -eu;"):
    print("unexpected output", flush=True)
sys.exit(result.returncode)
'''


@unittest.skipIf(os.name == "nt", "Fake remote shell harness requires POSIX")
class UploadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "remote home ' \" $literal Δ"
        self.home.mkdir()
        self.local = self.root / "local"
        self.local.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "commands.jsonl"
        for command in ("ssh",):
            program = self.bin / (command + ".py")
            program.write_text(_FAKE_OPENSSH, encoding="utf-8")
            wrapper = self.bin / command
            wrapper.write_text(
                "#!/bin/sh\nFAKE_KIND=" + command + " exec "
                + shlex.quote(sys.executable) + " " + shlex.quote(str(program))
                + ' "$@"\n', encoding="utf-8",
            )
            wrapper.chmod(0o755)
        environment = {
            "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
            "FAKE_HOME": str(self.home), "FAKE_LOG": str(self.log),
        }
        for key in ("FAIL_DIRECTORY", "FAIL_CLEANUP", "CORRUPT_RESPONSE", "FAIL_UPLOAD_NUMBER", "UPLOAD_DELAY"):
            environment[key] = ""
        self.environment = patch.dict(os.environ, environment)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def file(self, name="file.bin", data=b"data"):
        path = self.local / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def records(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def uploads(self):
        base = self.home / ".cache/tern-file-paste"
        return list(base.iterdir()) if base.exists() else []

    def test_bytes_empty_files_order_dedup_and_same_basename(self):
        first = self.file("first/same name 🐍.bin", bytes(range(256)) * 40)
        second = self.file("second/same name 🐍.bin", b"second\0\xff")
        empty = self.file("empty file", b"")
        result = transfer.upload_files([str(first), str(first), str(second), str(empty)], "ubuntu-test")
        self.assertEqual(result["bytes"], 10248)
        self.assertEqual(len(result["paths"]), 3)
        self.assertEqual(len(set(result["paths"])), 3)
        for remote, source in zip(result["paths"], (first, second, empty)):
            self.assertEqual(Path(remote).read_bytes(), source.read_bytes())
            self.assertEqual(Path(remote).name, source.name)
            self.assertEqual(Path(remote).parent.parent, Path(result["directory"]))
        self.assertEqual(stat.S_IMODE(Path(result["directory"]).stat().st_mode), 0o700)
        for path in result["paths"]:
            self.assertEqual(stat.S_IMODE(Path(path).stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(Path(path).parent.stat().st_mode), 0o700)

    def test_hostile_basename_and_normalization_collisions(self):
        names = [
            'a "double" \'single\'\n\t\x7f\x85 Δ;$(touch INJECTED)`touch BACKTICK`\\.bin',
            "x\"", "x%22", "payload", "100% ordinary.txt",
        ]
        sources = [self.file(name, str(index).encode()) for index, name in enumerate(names)]
        result = transfer.upload_files([str(path) for path in sources], "user@ubuntu")
        expected = 'a %22double%22 %27single%27%0A%09%7F%C2%85 Δ;$(touch INJECTED)`touch BACKTICK`\\.bin'
        self.assertEqual(Path(result["paths"][0]).name, expected)
        self.assertEqual(Path(result["paths"][1]).name, "x%22")
        self.assertEqual(Path(result["paths"][2]).name, "x%22")
        self.assertEqual(Path(result["paths"][3]).name, "payload")
        self.assertEqual(Path(result["paths"][4]).name, names[4])
        for index, path in enumerate(result["paths"]):
            self.assertEqual(Path(path).read_bytes(), str(index).encode())
        self.assertFalse((self.home / "INJECTED").exists())
        self.assertFalse((self.home / "BACKTICK").exists())

    @unittest.skipUnless(sys.platform == "linux", "macOS/Windows filesystems do not accept raw non-UTF-8 filename bytes")
    def test_non_utf8_filename_produces_a_representable_remote_path(self):
        source = self.file(os.fsdecode(b"raw-\xff.bin"), b"binary payload")
        result = transfer.upload_files([str(source)], "ubuntu")
        self.assertEqual(Path(result["paths"][0]).name, "raw-%FF.bin")
        self.assertEqual(Path(result["paths"][0]).read_bytes(), b"binary payload")

    def test_relative_option_and_colon_sources_become_absolute(self):
        source = self.file("-host:port ' file")
        old = os.getcwd()
        self.addCleanup(os.chdir, old)
        os.chdir(self.local)
        result = transfer.upload_files([source.name], "ubuntu")
        self.assertEqual(Path(result["paths"][0]).read_bytes(), b"data")
        upload = next(record for record in self.records() if record["args"][1].startswith("umask 077; cat > "))
        self.assertNotIn(str(source), upload["args"][1])

    def test_invalid_sources_are_rejected_before_remote_changes(self):
        valid = self.file()
        directory = self.local / "directory"
        directory.mkdir()
        fifo = self.local / "fifo"
        os.mkfifo(fifo)
        special = self.local / "socket"
        server = socket.socket(socket.AF_UNIX)
        server.bind(str(special))
        self.addCleanup(server.close)
        for invalid in (directory, fifo, special, self.local / "missing"):
            with self.subTest(path=invalid):
                with self.assertRaises(transfer.TransferError):
                    transfer.upload_files([str(valid), str(invalid)], "ubuntu")
                self.assertEqual(self.records(), [])
                self.assertEqual(self.uploads(), [])

    def test_failure_removes_entire_operation_and_preserves_other_uploads(self):
        previous = transfer.upload_files([str(self.file("old", b"old"))], "ubuntu")
        previous_directory = Path(previous["directory"])
        os.environ["FAIL_UPLOAD_NUMBER"] = "3"
        with self.assertRaisesRegex(transfer.TransferError, "Simulated transfer failure"):
            transfer.upload_files([str(self.file("first")), str(self.file("second"))], "ubuntu")
        self.assertEqual(self.uploads(), [previous_directory])
        self.assertEqual(Path(previous["paths"][0]).read_bytes(), b"old")

    def test_file_directory_failure_also_rolls_back(self):
        os.environ["FAIL_DIRECTORY"] = "1"
        with self.assertRaisesRegex(transfer.TransferError, "Simulated directory failure"):
            transfer.upload_files([str(self.file())], "ubuntu")
        self.assertEqual(self.uploads(), [])

    def test_invalid_creation_response_is_cleaned_up(self):
        os.environ["CORRUPT_RESPONSE"] = "1"
        with self.assertRaisesRegex(transfer.TransferError, "invalid upload directory response"):
            transfer.upload_files([str(self.file())], "ubuntu")
        self.assertEqual(self.uploads(), [])

    def test_cleanup_failure_is_explicit(self):
        os.environ["FAIL_UPLOAD_NUMBER"] = "1"
        os.environ["FAIL_CLEANUP"] = "1"
        with self.assertRaisesRegex(transfer.TransferError, "cleanup failed.*Simulated cleanup failure"):
            transfer.upload_files([str(self.file())], "ubuntu")
        self.assertEqual(len(self.uploads()), 1)

    def test_preexisting_directory_is_never_removed(self):
        token = "a" * 32
        existing = self.home / ".cache/tern-file-paste" / ("upload-" + token)
        existing.mkdir(parents=True)
        (existing / ".owner").write_text("someone-else")
        (existing / "keep").write_bytes(b"keep")
        with patch.object(transfer.uuid, "uuid4", return_value=SimpleNamespace(hex=token)):
            with self.assertRaises(transfer.TransferError):
                transfer.upload_files([str(self.file())], "ubuntu")
        self.assertEqual((existing / "keep").read_bytes(), b"keep")
        self.assertEqual((existing / ".owner").read_text(), "someone-else")

    def test_timeout_kills_transfer_and_cleans_operation_within_total_budget(self):
        os.environ["UPLOAD_DELAY"] = "5"
        started = time.monotonic()
        with self.assertRaisesRegex(transfer.TransferError, "Upload timed out"):
            transfer.upload_files([str(self.file())], "ubuntu", timeout=1)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(self.uploads(), [])

    def test_timeout_budget_is_shared_across_files(self):
        os.environ["UPLOAD_DELAY"] = "0.35"
        sources = [str(self.file(f"file-{index}")) for index in range(3)]
        started = time.monotonic()
        with self.assertRaisesRegex(transfer.TransferError, "Upload timed out"):
            transfer.upload_files(sources, "ubuntu", timeout=1)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(self.uploads(), [])

    def test_invalid_configuration_never_contacts_remote(self):
        source = str(self.file())
        for target in ("-evil", "host:1234", "tern://host", "host;touch PWN", "user@host extra", "::1", ""):
            with self.subTest(target=target), self.assertRaises(transfer.TransferError):
                transfer.upload_files([source], target)
        for options in ({"port": 0}, {"port": True}, {"port": 65536}, {"timeout": 0}, {"timeout": True}, {"identity_file": "/missing/key"}):
            with self.subTest(options=options), self.assertRaises(transfer.TransferError):
                transfer.upload_files([source], "ubuntu", **options)
        self.assertEqual(self.records(), [])

    def test_empty_selection_has_no_remote_side_effects(self):
        self.assertEqual(transfer.upload_files([], "ubuntu"), {"paths": [], "directory": "", "bytes": 0})
        self.assertEqual(self.records(), [])
        self.assertEqual(self.uploads(), [])

    def test_missing_openssh_is_explicit_and_does_not_mutate_remote(self):
        with patch.dict(os.environ, {"PATH": str(self.root / "missing-bin")}):
            with self.assertRaisesRegex(transfer.TransferError, "ssh must be installed"):
                transfer.upload_files([str(self.file())], "ubuntu")
        self.assertEqual(self.uploads(), [])


if __name__ == "__main__":
    unittest.main()
