"""Exercise real commits and tags without changing the user's repository."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CHECK = Path(__file__).resolve().parents[1] / "tools/check_commit_privacy.py"
SAFE = "123+test@users.noreply.github.com"


class CommitPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = os.environ.copy()
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_AUTHOR_NAME="Test", GIT_COMMITTER_NAME="Test", GIT_AUTHOR_EMAIL=SAFE, GIT_COMMITTER_EMAIL=SAFE)
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            self.env.pop(key, None)
        self.git("init", "-b", "main")

    def git(self, *args, **kwargs):
        return subprocess.run(["git", *args], cwd=self.root, env=kwargs.pop("env", self.env), capture_output=True, text=True, check=True, **kwargs).stdout.strip()

    def commit(self, author=SAFE, committer=SAFE):
        env = dict(self.env, GIT_AUTHOR_EMAIL=author, GIT_COMMITTER_EMAIL=committer)
        self.git("-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "test", env=env)
        return self.git("rev-parse", "HEAD")

    def check(self, *args, payload=None):
        return subprocess.run([sys.executable, str(CHECK), *args], cwd=self.root, env=self.env, input=payload, capture_output=True, text=True)

    def test_accepts_noreply_author_and_committer(self):
        self.commit(committer="noreply@github.com")
        result = self.check("--all")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1 commits checked", result.stdout)

    def test_rejects_private_author_behind_clean_tip_without_logging_email(self):
        self.commit(author="private@example.invalid")
        head = self.commit()
        result = self.check("origin", "https://example.invalid/repo", payload=f"refs/heads/main {head} refs/heads/main {'0'*40}\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("author must use", result.stderr)
        self.assertNotIn("private@example.invalid", result.stderr)

    def test_rejects_private_committer(self):
        self.commit(committer="private@example.invalid")
        result = self.check("--all")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("committer must use", result.stderr)

    def test_rejects_private_annotated_tagger(self):
        self.commit()
        self.git("-c", "tag.gpgsign=false", "tag", "-a", "v1", "-m", "tag", env=dict(self.env, GIT_COMMITTER_EMAIL="private@example.invalid"))
        result = self.check("--all")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("tagger must use", result.stderr)

    def test_audits_nondefault_branch(self):
        self.commit()
        self.git("checkout", "-b", "topic")
        self.commit(author="private@example.invalid")
        self.git("checkout", "main")
        self.assertNotEqual(self.check("--all").returncode, 0)

    def test_allows_deletion_of_a_tainted_ref(self):
        old = self.commit(author="private@example.invalid")
        result = self.check("origin", "https://example.invalid/repo", payload=f"(delete) {'0'*40} refs/heads/old {old}\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_refuses_shallow_history(self):
        self.commit(author="private@example.invalid")
        self.commit()
        shallow = self.root / "shallow"
        self.git("clone", "--depth=1", self.root.as_uri(), str(shallow))
        result = subprocess.run([sys.executable, str(CHECK), "--all"], cwd=shallow, env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("truncated history", result.stderr)
