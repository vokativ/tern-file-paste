"""Stream regular files over OpenSSH into private, per-operation directories."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import time
import unicodedata
import uuid


class TransferError(Exception):
    """An upload could not be completed; no successful paths are returned."""


_TARGET = re.compile(r"(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9_][A-Za-z0-9_.-]*\Z")


def _basename(name: str) -> str:
    return "".join(
        "".join(f"%{byte:02X}" for byte in os.fsencode(char))
        if char in "\"'" or unicodedata.category(char) in ("Cc", "Cs")
        else char
        for char in name
    )


def _run(
    argv: list[str], deadline: float, description: str, *, stdin=None,
) -> bytes:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TransferError(f"Upload timed out while {description}")
    options = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP
    }
    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL if stdin is None else stdin, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, **options,
        )
    except OSError as exc:
        raise TransferError(f"Cannot start {argv[0]} while {description}: {exc}") from exc
    try:
        stdout, stderr = process.communicate(timeout=remaining)
    except subprocess.TimeoutExpired as exc:
        # Terminate ssh and any local child processes, including proxy commands.
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=0.1, check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
            if process.poll() is None:
                process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        # Discard command output on timeout; do not wait for a descendant to
        # close an inherited pipe after the client itself has been terminated.
        process.stdout.close()
        process.stderr.close()
        process.wait()
        raise TransferError(f"Upload timed out while {description}") from exc
    if process.returncode:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise TransferError(
            f"Failed while {description} (exit {process.returncode})"
            + (f": {detail}" if detail else "")
        )
    return stdout


def upload_files(
    paths: list[str], target: str, *, port: int | None = None,
    identity_file: str | None = None, timeout: int = 300,
) -> dict:
    """Stream complete files over SSH, rolling back this operation on failure.

    Authentication and host-key trust must already be configured in OpenSSH.
    A small part of the total timeout is reserved for failure cleanup.
    """
    started = time.monotonic()
    if not isinstance(target, str) or not _TARGET.fullmatch(target):
        raise TransferError("Target must be an SSH alias or user@hostname, not a URL, address:port, or QUIC address")
    if port is not None and (type(port) is not int or not 1 <= port <= 65535):
        raise TransferError("SSH port must be an integer between 1 and 65535")
    if type(timeout) is not int or timeout <= 0:
        raise TransferError("Upload timeout must be a positive number of seconds")
    if not isinstance(paths, list):
        raise TransferError("File paths must be a list")

    files: list[tuple[str, str]] = []
    seen: set[str] = set()
    total = 0
    for path in paths:
        if not isinstance(path, str) or not path or "\0" in path:
            raise TransferError("Each file path must be a nonempty string without NUL characters")
        source = os.path.abspath(os.path.expanduser(path))
        # Do not case-fold: Windows directories may enable case sensitivity.
        if source in seen:
            continue
        try:
            info = os.stat(source)
        except OSError as exc:
            raise TransferError(f"Cannot access file {path!r}: {exc}") from exc
        if not stat.S_ISREG(info.st_mode):
            raise TransferError(f"Not a regular file (directories and special files cannot be uploaded): {path!r}")
        # Reject unreadable sources before any remote changes, without reading bytes.
        try:
            with open(source, "rb"):
                pass
        except OSError as exc:
            raise TransferError(f"Cannot open file {path!r}: {exc}") from exc
        files.append((source, _basename(os.path.basename(source))))
        seen.add(source)
        total += info.st_size

    if identity_file is not None:
        if not isinstance(identity_file, str) or not identity_file or "\0" in identity_file:
            raise TransferError("SSH identity_file must be a file path")
        identity_file = os.path.abspath(os.path.expanduser(identity_file))
        if not os.path.isfile(identity_file):
            raise TransferError(f"SSH identity file does not exist or is not a regular file: {identity_file!r}")
    if not files:
        return {"paths": [], "directory": "", "bytes": 0}

    ssh = shutil.which("ssh")
    if not ssh:
        raise TransferError("OpenSSH ssh must be installed and available on PATH")
    common = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    if identity_file is not None:
        common += ["-i", identity_file]
    # Never allocate a PTY: terminal processing would corrupt binary contents.
    ssh_args = [ssh, "-T", *common]
    if port is not None:
        ssh_args += ["-p", str(port)]

    deadline = started + timeout
    work_deadline = deadline - min(5.0, timeout / 4)
    operation = "upload-" + uuid.uuid4().hex
    owner = uuid.uuid4().hex
    relative_root = ".cache/tern-file-paste/" + operation
    # The ownership marker allows safe cleanup even if SSH times out after mkdir
    # but before its response arrives. Never delete a pre-existing directory.
    root_expression = '"$HOME"/' + shlex.quote(relative_root)
    setup = (
        "set -eu; umask 077; "
        'case "$HOME" in /*) ;; *) echo "Remote HOME is not absolute" >&2; exit 1;; esac; '
        'mkdir -p -- "$HOME/.cache/tern-file-paste"; '
        f"root={root_expression}; mkdir -- \"$root\"; "
        "trap 'rm -rf -- \"$root\"' 0; "
        f"printf '%s' {shlex.quote(owner)} > \"$root/.owner\"; "
        'printf \'%s\\0\' "$root"; trap - 0'
    )
    cleanup = (
        f"root={root_expression}; "
        f"if [ -f \"$root/.owner\" ] && [ \"$(cat -- \"$root/.owner\")\" = {shlex.quote(owner)} ]; "
        'then rm -rf -- "$root"; '
        'elif [ -e "$root" ]; then echo "Upload directory ownership could not be verified" >&2; exit 1; fi'
    )
    try:
        output = _run([*ssh_args, target, setup], work_deadline, "creating upload directory")
        if not output.endswith(b"\0") or output.count(b"\0") != 1:
            raise TransferError("SSH returned an invalid upload directory response")
        try:
            directory = output[:-1].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TransferError("Remote upload directory is not valid UTF-8") from exc
        if not directory.startswith("/") or not directory.endswith("/" + relative_root):
            raise TransferError("SSH returned an unexpected upload directory")
        remote_paths: list[str] = []
        for index, (source, name) in enumerate(files):
            subdirectory = f"{directory}/{index:06d}"
            remote_path = f"{subdirectory}/{name}"
            _run(
                [*ssh_args, target, f"umask 077; mkdir -- {shlex.quote(subdirectory)}"],
                work_deadline, f"creating directory for file {index + 1}",
            )
            # Streaming a descriptor through ssh avoids copying file contents
            # into Python memory and the legacy SCP filename/newline protocol.
            # It also works with Windows OpenSSH versions predating SFTP scp.
            with open(source, "rb") as stream:
                _run(
                    [*ssh_args, target,
                     f"umask 077; cat > {shlex.quote(remote_path)}"],
                    work_deadline, f"uploading {os.path.basename(source)!r}",
                    stdin=stream,
                )
            remote_paths.append(remote_path)
        return {"paths": remote_paths, "directory": directory, "bytes": total}
    except (TransferError, OSError, ValueError) as exc:
        try:
            _run([*ssh_args, target, cleanup], deadline, "removing failed upload directory")
        except TransferError as cleanup_error:
            raise TransferError(f"{exc}; cleanup failed: {cleanup_error}") from exc
        if isinstance(exc, TransferError):
            raise
        raise TransferError(f"Upload failed: {exc}") from exc
