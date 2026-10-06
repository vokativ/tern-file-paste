#!/usr/bin/env python3
"""Reject non-GitHub-no-reply identities throughout published Git history."""

import re
import subprocess
import sys


SAFE_EMAIL = re.compile(r"(?:[A-Za-z0-9+_.-]+@users\.noreply\.github\.com|noreply@github\.com)\Z")


def git(*args):
    return subprocess.run(["git", *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def audit(roots):
    if git("rev-parse", "--is-shallow-repository").strip() == b"true":
        raise ValueError("Cannot audit truncated history; fetch the complete repository first")
    commits = set()
    for root in roots:
        commit = git("rev-parse", "--verify", root + "^{commit}").decode().strip()
        commits.update(git("rev-list", commit).decode().splitlines())
        # An annotated tag can expose a tagger email even when its commit is safe.
        obj = root
        while git("cat-file", "-t", obj).strip() == b"tag":
            header = git("cat-file", "tag", obj).split(b"\n\n", 1)[0]
            fields = dict(line.split(b" ", 1) for line in header.splitlines())
            if b"tagger" in fields:
                check_identity(fields[b"tagger"], obj, "tagger")
            obj = fields[b"object"].decode()
    for commit in commits:
        header = git("cat-file", "commit", commit).split(b"\n\n", 1)[0]
        for role in (b"author", b"committer"):
            rows = [line[len(role) + 1:] for line in header.splitlines() if line.startswith(role + b" ")]
            if len(rows) != 1:
                raise ValueError("Invalid commit identity metadata in " + commit)
            check_identity(rows[0], commit, role.decode())
    return len(commits)


def check_identity(identity, oid, role):
    match = re.search(rb"<([^<>]+)> \d+ [+-]\d{4}$", identity)
    if not match or not SAFE_EMAIL.fullmatch(match[1].decode("ascii", errors="replace")):
        # Do not echo the private address into CI or terminal logs.
        raise ValueError(f"Blocked {oid}: {role} must use a GitHub no-reply email")


def main():
    try:
        if sys.argv[1:] == ["--all"]:
            roots = git("for-each-ref", "--format=%(objectname)").decode().splitlines()
            if not roots:
                raise ValueError("No refs to audit")
        elif len(sys.argv) == 3:
            roots = []
            for line in sys.stdin:
                fields = line.split()
                if len(fields) != 4:
                    raise ValueError("Invalid pre-push ref input")
                oid = fields[1]
                if set(oid) == {"0"}:  # Ref deletion does not publish a commit.
                    continue
                if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid):
                    raise ValueError("Invalid pre-push object id")
                roots.append(oid)
            if not roots:
                return 0
        else:
            raise ValueError("Usage: check_commit_privacy.py --all OR REMOTE URL (pre-push stdin)")
        count = audit(roots)
        print(f"Commit privacy: {count} commits checked; no-reply identities only")
        return 0
    except (ValueError, subprocess.CalledProcessError, OSError) as error:
        print("Commit privacy check failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
