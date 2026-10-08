"""Behavioral transfer tests with real subprocesses and an isolated fake server.

The fake ssh executes the actual generated POSIX commands in a temporary HOME.
File bytes flow through its stdin descriptor into real remote cat processes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import stat
import subprocess
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
    if os.environ.get("PROGRESS_GATE"):
        gate = Path(os.environ["PROGRESS_GATE"])

        def wait_for(name):
            deadline = time.monotonic() + 5
            while not (gate / name).exists():
                if time.monotonic() >= deadline:
                    sys.exit("Progress test release was not received")
                time.sleep(0.01)

        remote = subprocess.Popen(
            ["/bin/sh", "-c", command], cwd=home, env=environment, stdin=subprocess.PIPE,
        )
        first = True
        while True:
            chunk = os.read(0, 65536)
            if not chunk:
                break
            remote.stdin.write(chunk)
            remote.stdin.flush()
            if first:
                (gate / f"consumed-{count}").write_text(str(os.getpid()))
                wait_for(f"release-first-{count}")
                first = False
        remote.stdin.close()
        result = remote.wait()
        (gate / f"remote-finished-{count}").write_text(str(result))
        wait_for(f"release-completion-{count}")
        if count == int(os.environ.get("FAIL_AFTER_READ_NUMBER") or "0"):
            sys.exit("Simulated remote completion failure")
        sys.exit(result)
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
        for key in (
            "FAIL_DIRECTORY", "FAIL_CLEANUP", "CORRUPT_RESPONSE", "FAIL_UPLOAD_NUMBER",
            "UPLOAD_DELAY", "PROGRESS_GATE", "FAIL_AFTER_READ_NUMBER",
        ):
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

    def test_empty_selection_reports_zero_byte_completion_without_ssh(self):
        snapshots = []
        result = transfer.upload_files([], "ubuntu", progress=snapshots.append)
        self.assertEqual(result, {"paths": [], "directory": "", "bytes": 0})
        self.assertEqual(snapshots[-1], {
            "phase": "done", "sent": 0, "total": 0, "files": 0, "file_index": 0,
        })
        self.assertEqual(self.records(), [])
        self.assertEqual(self.uploads(), [])

    def gated_progress(self, *, complete=True, writer=None):
        gate = self.root / "progress-gates"
        gate.mkdir()
        os.environ["PROGRESS_GATE"] = str(gate)
        snapshots = []

        def report(snapshot):
            snapshots.append(snapshot)
            if writer is not None:
                writer(snapshot)
            index = snapshot["file_index"]
            if index and snapshot["phase"] == "uploading" and snapshot["sent"] > 0:
                (gate / f"release-first-{index}").touch()
            if index and snapshot["phase"] == "finalizing" and complete:
                (gate / f"release-completion-{index}").touch()

        return gate, snapshots, report

    def assert_monotonic_progress(self, snapshots, total, files):
        sent = [snapshot["sent"] for snapshot in snapshots]
        self.assertEqual(sent, sorted(sent))
        self.assertTrue(all(0 <= value <= total for value in sent))
        validated = next(index for index, item in enumerate(snapshots) if item["files"] == files)
        for snapshot in snapshots[validated:]:
            self.assertEqual(snapshot["total"], total)
            self.assertEqual(snapshot["files"], files)
            self.assertIs(type(snapshot["sent"]), int)
            self.assertIs(type(snapshot["total"]), int)
            self.assertIs(type(snapshot["file_index"]), int)

    def test_progress_observes_real_shared_offset_and_waits_for_remote_exit(self):
        first = self.file("large", bytes(range(256)) * 1024)
        second = self.file("other", b"next" * 32768)
        empty = self.file("empty", b"")
        status_path = self.root / "status.json"
        gate, snapshots, report = self.gated_progress(writer=transfer.ProgressFile(str(status_path)))
        result = transfer.upload_files(
            [str(first), str(first), str(second), str(empty)], "ubuntu", progress=report,
        )
        total = first.stat().st_size + second.stat().st_size
        self.assert_monotonic_progress(snapshots, total, 3)
        self.assertTrue(any(
            item["phase"] == "uploading" and 0 < item["sent"] < first.stat().st_size
            for item in snapshots
        ))
        finalizing = [item for item in snapshots if item["phase"] == "finalizing"]
        self.assertEqual({item["file_index"] for item in finalizing}, {1, 2, 3})
        self.assertTrue(any(item["sent"] == total for item in finalizing))
        self.assertEqual(snapshots[-1]["phase"], "done")
        self.assertEqual(snapshots[-1]["sent"], total)
        self.assertEqual(sum(item["phase"] == "done" for item in snapshots), 1)
        for index, source in enumerate((first, second, empty), 1):
            self.assertEqual((gate / f"remote-finished-{index}").read_text(), "0")
            self.assertEqual(Path(result["paths"][index - 1]).read_bytes(), source.read_bytes())
        self.assertEqual(len([
            item for item in self.records() if item["args"][1].startswith("umask 077; cat > ")
        ]), 3)
        self.assertEqual(json.loads(status_path.read_text()), snapshots[-1])
        self.assertEqual(stat.S_IMODE(status_path.stat().st_mode), 0o600)
        self.assertEqual(list(self.root.glob(".upload-progress-*")), [])

    def test_all_bytes_consumed_is_not_done_when_remote_completion_fails(self):
        source = self.file("large", b"x" * 131072)
        gate, snapshots, report = self.gated_progress()
        os.environ["FAIL_AFTER_READ_NUMBER"] = "1"
        with self.assertRaisesRegex(transfer.TransferError, "remote completion failure"):
            transfer.upload_files([str(source)], "ubuntu", progress=report)
        self.assert_monotonic_progress(snapshots, source.stat().st_size, 1)
        self.assertTrue(any(
            item["phase"] == "finalizing" and item["sent"] == source.stat().st_size
            for item in snapshots
        ))
        self.assertEqual(snapshots[-1]["phase"], "failed")
        self.assertIn("remote completion failure", snapshots[-1]["error"])
        self.assertEqual(snapshots[-1]["sent"], source.stat().st_size)
        self.assertNotIn("done", [item["phase"] for item in snapshots])
        self.assertEqual((gate / "remote-finished-1").read_text(), "0")
        self.assertEqual(self.uploads(), [])

    def test_finalizing_timeout_keeps_deadline_and_rolls_back(self):
        source = self.file("large", b"x" * 131072)
        gate, snapshots, report = self.gated_progress(complete=False)
        started = time.monotonic()
        with self.assertRaisesRegex(transfer.TransferError, "Upload timed out"):
            transfer.upload_files([str(source)], "ubuntu", timeout=1, progress=report)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assert_monotonic_progress(snapshots, source.stat().st_size, 1)
        self.assertEqual(snapshots[-1]["phase"], "failed")
        self.assertEqual(snapshots[-1]["sent"], source.stat().st_size)
        self.assertNotIn("done", [item["phase"] for item in snapshots])
        with self.assertRaises(ProcessLookupError):
            os.kill(int((gate / "consumed-1").read_text()), 0)
        self.assertEqual(self.uploads(), [])

    def test_progress_failure_includes_cleanup_failure_without_success(self):
        source = self.file()
        os.environ["FAIL_UPLOAD_NUMBER"] = "1"
        os.environ["FAIL_CLEANUP"] = "1"
        snapshots = []
        with self.assertRaisesRegex(transfer.TransferError, "cleanup failed"):
            transfer.upload_files([str(source)], "ubuntu", progress=snapshots.append)
        self.assertEqual(snapshots[-1]["phase"], "failed")
        self.assertIn("cleanup failed", snapshots[-1]["error"])
        self.assertEqual(snapshots[-1]["sent"], 0)
        self.assertNotIn("done", [item["phase"] for item in snapshots])

    def test_unavailable_progress_path_never_repeats_or_aborts_upload(self):
        source = self.file()
        writer = transfer.ProgressFile(str(self.root / "missing-parent" / "status.json"))
        result = transfer.upload_files([str(source)], "ubuntu", progress=writer)
        self.assertEqual(Path(result["paths"][0]).read_bytes(), source.read_bytes())
        self.assertEqual(len([
            item for item in self.records() if item["args"][1].startswith("umask 077; cat > ")
        ]), 1)
        self.assertEqual(list(self.root.glob(".upload-progress-*")), [])

    def test_atomic_owner_only_snapshots_and_failed_replace_cleanup(self):
        destination = self.root / "status.json"
        writer = transfer.ProgressFile(str(destination))
        before = {"phase": "reading", "sent": 0, "total": 0, "files": 0, "file_index": 0}
        after = dict(before, phase="done")
        writer(before)
        with destination.open() as previous:
            writer(after)
            self.assertEqual(json.load(previous), before)
        self.assertEqual(json.loads(destination.read_text()), after)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
        writer(dict(before, phase="failed", error="🐍" * 10000))
        self.assertLess(destination.stat().st_size, 8192)
        self.assertEqual(json.loads(destination.read_text())["error"], "🐍" * 512)
        blocked = self.root / "directory-not-file"
        blocked.mkdir()
        transfer.ProgressFile(str(blocked))(before)
        self.assertTrue(blocked.is_dir())
        self.assertEqual(list(self.root.glob(".upload-progress-*")), [])

    def test_invalid_progress_destination_is_rejected(self):
        for path in ("", "relative.json", "nul\0path", None, 1):
            with self.subTest(path=path), self.assertRaises(ValueError):
                transfer.ProgressFile(path)

    def test_helper_reports_preupload_failure_without_stdout_payload(self):
        destination = self.root / "helper-status.json"
        helper = Path(transfer.__file__).with_name("paste_files.py")
        process = subprocess.run(
            [sys.executable, str(helper)],
            input=json.dumps({"target": "", "progress_file": str(destination)}),
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(process.returncode, 1)
        self.assertEqual(process.stdout, "")
        self.assertIn("Configure an SSH target", process.stderr)
        snapshot = json.loads(destination.read_text())
        self.assertEqual(snapshot["phase"], "failed")
        self.assertEqual(snapshot["sent"], 0)
        self.assertIn("Configure an SSH target", snapshot["error"])
        self.assertEqual(self.records(), [])

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


class HelperLaunchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_cold_helper_launch_never_writes_inside_watched_package(self):
        package = self.root / "cold-plugin" / "helpers"
        shutil.copytree(
            Path(transfer.__file__).parent, package,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )

        def inventory():
            return {
                str(path.relative_to(package)): (
                    path.is_dir(), path.stat().st_mtime_ns,
                    None if path.is_dir() else path.read_bytes(),
                )
                for path in [package, *package.rglob("*")]
            }

        before = inventory()
        destination = self.root / "cold-helper-status.json"
        process = subprocess.run(
            # Ignore inherited Python environment flags so bytecode writing
            # would be enabled without the helper's own pre-import protection.
            [sys.executable, "-E", str(package / "paste_files.py")],
            input=json.dumps({"target": "", "progress_file": str(destination)}),
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(process.returncode, 1)
        self.assertEqual(process.stdout, "")
        self.assertIn("Configure an SSH target", process.stderr)
        self.assertEqual(json.loads(destination.read_text())["phase"], "failed")
        self.assertEqual(list(package.rglob("__pycache__")), [])
        self.assertEqual(inventory(), before)


if __name__ == "__main__":
    unittest.main()
