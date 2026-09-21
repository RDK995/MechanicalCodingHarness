"""Deterministic Git and workspace checks for task lifecycle transitions."""

from __future__ import annotations

import fnmatch
import hashlib
import os
from pathlib import Path
import subprocess

from .state import HarnessError


def project_root(state_path: Path) -> Path:
    if state_path.parent.name == ".harness":
        return state_path.parent.parent
    return state_path.parent


class Repository:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        probe = self._run("rev-parse", "--show-toplevel")
        if probe.returncode:
            raise HarnessError("mechanical task lifecycle requires a Git repository")
        if Path(probe.stdout.strip()).resolve() != self.root:
            raise HarnessError(f"state project root is not the Git root: {self.root}")

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.root), *arguments],
            text=True,
            capture_output=True,
            check=False,
        )

    def head(self) -> str:
        result = self._run("rev-parse", "HEAD")
        if result.returncode:
            raise HarnessError("cannot resolve repository HEAD: " + result.stderr.strip())
        return result.stdout.strip()

    def changed_files(self) -> list[str]:
        tracked = self._run("diff", "--name-only", "-z", "HEAD")
        if tracked.returncode:
            raise HarnessError("cannot inspect tracked changes: " + tracked.stderr.strip())
        untracked = self._run("ls-files", "--others", "--exclude-standard", "-z")
        if untracked.returncode:
            raise HarnessError("cannot inspect untracked files: " + untracked.stderr.strip())
        paths = {
            value
            for output in (tracked.stdout, untracked.stdout)
            for value in output.split("\0")
            if value and value != ".harness" and not value.startswith(".harness/")
        }
        return sorted(paths)

    @staticmethod
    def _allowed(path: str, patterns: list[str]) -> bool:
        for pattern in patterns:
            if pattern.endswith("/") and path.startswith(pattern):
                return True
            if path == pattern or fnmatch.fnmatchcase(path, pattern):
                return True
        return False

    def assert_scope(self, changed: list[str], patterns: list[str]) -> None:
        outside = sorted(path for path in changed if not self._allowed(path, patterns))
        if outside:
            raise HarnessError("task changed paths outside its scope: " + ", ".join(outside))

    def fingerprint(self, paths: list[str]) -> str:
        digest = hashlib.sha256()
        for relative in sorted(paths):
            digest.update(relative.encode())
            digest.update(b"\0")
            path = self.root / relative
            if not path.exists() and not path.is_symlink():
                digest.update(b"DELETED\0")
            elif path.is_symlink():
                digest.update(b"SYMLINK\0")
                digest.update(os.readlink(path).encode())
                digest.update(b"\0")
            elif path.is_file():
                digest.update(b"FILE\0")
                digest.update(path.read_bytes())
                digest.update(b"\0")
            else:
                raise HarnessError(f"task path is not a file: {relative}")
        return digest.hexdigest()

    def snapshot(self, patterns: list[str]) -> dict:
        changed = self.changed_files()
        self.assert_scope(changed, patterns)
        return {
            "base": self.head(),
            "changed_files": changed,
            "snapshot": self.fingerprint(changed),
        }

    def commit_task(self, base: str, paths: list[str], message: str) -> tuple[str, bool]:
        current = self.head()
        if current != base:
            parent = self._run("rev-parse", "HEAD^")
            committed = self._run("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")
            if (
                parent.returncode == 0
                and parent.stdout.strip() == base
                and committed.returncode == 0
                and sorted(committed.stdout.splitlines()) == sorted(paths)
            ):
                return current, False
            raise HarnessError("repository HEAD moved after task verification")

        actual = self.changed_files()
        if actual != sorted(paths):
            raise HarnessError("workspace changed after task verification")
        cached = self._run("diff", "--cached", "--name-only")
        if cached.returncode:
            raise HarnessError("cannot inspect staged paths: " + cached.stderr.strip())
        staged = sorted(filter(None, cached.stdout.splitlines()))
        unexpected = sorted(set(staged) - set(paths))
        if unexpected:
            raise HarnessError("refusing to commit unrelated staged paths: " + ", ".join(unexpected))
        added = self._run("add", "-A", "--", *paths)
        if added.returncode:
            raise HarnessError("cannot stage task paths: " + added.stderr.strip())
        staged_after = self._run("diff", "--cached", "--name-only")
        if staged_after.returncode or sorted(staged_after.stdout.splitlines()) != sorted(paths):
            raise HarnessError("staged paths do not exactly match the verified task")
        committed = self._run("commit", "-m", message)
        if committed.returncode:
            raise HarnessError("task commit failed: " + committed.stderr.strip())
        commit = self.head()
        changed = self._run("diff-tree", "--no-commit-id", "--name-only", "-r", commit)
        if changed.returncode or sorted(changed.stdout.splitlines()) != sorted(paths):
            raise HarnessError("task commit does not contain exactly the verified paths")
        return commit, True

    def harness_changed_files(self) -> list[str]:
        tracked = self._run("diff", "--name-only", "-z", "HEAD", "--", ".harness")
        untracked = self._run(
            "ls-files", "--others", "--exclude-standard", "-z", "--", ".harness"
        )
        if tracked.returncode or untracked.returncode:
            raise HarnessError("cannot inspect harness changes")
        return sorted(
            {
                path
                for output in (tracked.stdout, untracked.stdout)
                for path in output.split("\0")
                if path
            }
        )

    def commit_harness(self, message: str) -> tuple[str, bool]:
        substantive = self.changed_files()
        if substantive:
            raise HarnessError(
                "cannot close milestone with uncommitted substantive files: "
                + ", ".join(substantive)
            )
        paths = self.harness_changed_files()
        if not paths:
            return self.head(), False
        cached = self._run("diff", "--cached", "--name-only")
        if cached.returncode:
            raise HarnessError("cannot inspect staged files: " + cached.stderr.strip())
        staged = sorted(filter(None, cached.stdout.splitlines()))
        outside = [path for path in staged if not path.startswith(".harness/")]
        if outside:
            raise HarnessError("refusing to close with unrelated staged paths: " + ", ".join(outside))
        added = self._run("add", "-A", "--", *paths)
        if added.returncode:
            raise HarnessError("cannot stage harness records: " + added.stderr.strip())
        committed = self._run("commit", "-m", message)
        if committed.returncode:
            raise HarnessError("harness closing commit failed: " + committed.stderr.strip())
        return self.head(), True
