"""Immutable, environment-aware validation execution ledger."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys

from .repository import Repository
from .results import file_sha256
from .state import HarnessError, _atomic_write, _lock_path


PURPOSES = {"worker", "verifier", "orchestrator", "milestone", "reviewer"}
CACHEABLE_PURPOSES = {"orchestrator"}
DEFAULT_ENVIRONMENT_KEYS = ("CI", "LANG", "LC_ALL")
DEPENDENCY_FILES = (
    "Cargo.lock",
    "Gemfile.lock",
    "Package.resolved",
    "Pipfile.lock",
    "go.sum",
    "package-lock.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pyproject.toml",
    "requirements.txt",
    "uv.lock",
    "yarn.lock",
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _bounded(text: str, limit: int = 1200) -> str:
    if len(text) <= limit:
        return text
    return "…" + text[-limit:]


class ValidationLedger:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.harness = self.root / ".harness"
        self.path = self.harness / "validation-ledger.json"
        self.evidence = self.harness / "evidence" / "validation"
        self.repository = Repository(self.root)

    def _load(self) -> dict:
        if not self.path.exists():
            return {"schema_version": 1, "entries": {}}
        try:
            value = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise HarnessError(f"cannot read validation ledger: {error}") from error
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise HarnessError("validation ledger must use schema_version 1")
        if not isinstance(value.get("entries"), dict):
            raise HarnessError("validation ledger entries must be an object")
        self._validate_integrity(value)
        return value

    def _validate_integrity(self, ledger: dict) -> None:
        for key, records in ledger.get("entries", {}).items():
            if not isinstance(records, list) or not records:
                raise HarnessError(f"validation ledger key {key} has no executions")
            for sequence, record in enumerate(records, 1):
                if not isinstance(record, dict):
                    raise HarnessError(f"validation ledger key {key} has an invalid record")
                if record.get("key") != key or record.get("execution") != sequence:
                    raise HarnessError(f"validation ledger key {key} has inconsistent sequencing")
                inputs = record.get("inputs")
                if not isinstance(inputs, dict) or _sha256(inputs) != key:
                    raise HarnessError(f"validation ledger key {key} does not match its inputs")
                artifact = self.root / record.get("artifact", "")
                if not artifact.is_file() or file_sha256(artifact) != record.get("sha256"):
                    raise HarnessError(f"validation ledger artifact changed for key {key}")

    def _environment(self, cwd: Path, env_keys: list[str]) -> dict:
        for name in env_keys:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise HarnessError(f"invalid environment key: {name!r}")
        changed = self.repository.changed_files()
        dependencies = {}
        for name in DEPENDENCY_FILES:
            path = self.root / name
            if path.is_file():
                dependencies[name] = file_sha256(path)
        names = sorted(set(DEFAULT_ENVIRONMENT_KEYS) | set(env_keys))
        environment = {
            name: hashlib.sha256(os.environ[name].encode()).hexdigest()
            for name in names
            if name in os.environ
        }
        return {
            "cwd": cwd.relative_to(self.root).as_posix() or ".",
            "executable_resolution": None,
            "platform": {
                "system": platform.system(),
                "machine": platform.machine(),
                "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            },
            "environment": environment,
            "dependencies": dependencies,
            "workspace": {
                "changed_files": changed,
                "sha256": self.repository.fingerprint(changed),
            },
        }

    def _inputs(
        self,
        command: list[str],
        purpose: str,
        cwd: Path,
        env_keys: list[str],
    ) -> dict:
        if purpose not in PURPOSES:
            raise HarnessError(f"unknown validation purpose: {purpose!r}")
        if not command or not all(isinstance(item, str) and item for item in command):
            raise HarnessError("validation command must be a non-empty argv array")
        cwd = cwd.resolve()
        if cwd != self.root and self.root not in cwd.parents:
            raise HarnessError("validation cwd must be inside the project repository")
        if not cwd.is_dir():
            raise HarnessError(f"validation cwd does not exist: {cwd}")
        environment = self._environment(cwd, env_keys)
        environment["executable_resolution"] = shutil.which(command[0])
        return {
            "head": self.repository.head(),
            "command": command,
            "environment_fingerprint": _sha256(environment),
            "environment": environment,
            "purpose": purpose,
        }

    def _record_output(
        self,
        key: str,
        execution: int,
        inputs: dict,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> dict:
        self.evidence.mkdir(parents=True, exist_ok=True)
        artifact = self.evidence / f"{key}-{execution}.log"
        if artifact.exists():
            raise HarnessError(f"refusing to overwrite validation artifact: {artifact}")
        content = (
            json.dumps(
                {
                    "key": key,
                    "execution": execution,
                    "inputs": inputs,
                    "exit_code": exit_code,
                },
                sort_keys=True,
            )
            + "\n--- stdout ---\n"
            + stdout
            + "\n--- stderr ---\n"
            + stderr
        )
        descriptor = os.open(artifact, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        relative = artifact.relative_to(self.root).as_posix()
        return {
            "key": key,
            "execution": execution,
            "inputs": inputs,
            "purpose": inputs["purpose"],
            "command": shlex.join(inputs["command"]),
            "exit_code": exit_code,
            "artifact": relative,
            "sha256": file_sha256(artifact),
            "summary": {
                "stdout": _bounded(stdout),
                "stderr": _bounded(stderr),
            },
        }

    @staticmethod
    def _public(record: dict, reused: bool) -> dict:
        return {
            field: record[field]
            for field in (
                "key",
                "execution",
                "purpose",
                "command",
                "exit_code",
                "artifact",
                "sha256",
                "summary",
            )
        } | {"reused": reused}

    def run(
        self,
        command: list[str],
        purpose: str,
        cwd: Path,
        env_keys: list[str],
        timeout: int,
        force: bool = False,
    ) -> dict:
        if timeout < 1:
            raise HarnessError("validation timeout must be at least one second")
        lock = _lock_path(self.path)
        with lock.open("a+") as handle:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            ledger = self._load()
            inputs = self._inputs(command, purpose, cwd, env_keys)
            key = _sha256(inputs)
            records = ledger["entries"].setdefault(key, [])
            reusable = next(
                (record for record in reversed(records) if record.get("valid", True)),
                None,
            )
            if reusable and purpose in CACHEABLE_PURPOSES and not force:
                return self._public(reusable, reused=True)

            before = inputs["environment"]["workspace"]
            try:
                completed = subprocess.run(
                    command,
                    cwd=cwd,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=timeout,
                    errors="replace",
                )
                exit_code = completed.returncode
                stdout, stderr = completed.stdout, completed.stderr
            except subprocess.TimeoutExpired as error:
                exit_code = 124
                stdout = error.stdout or ""
                stderr = (error.stderr or "") + f"\nvalidation timed out after {timeout}s"
                if isinstance(stdout, bytes):
                    stdout = stdout.decode(errors="replace")
                if isinstance(stderr, bytes):
                    stderr = stderr.decode(errors="replace")
            after_paths = self.repository.changed_files()
            after = {
                "changed_files": after_paths,
                "sha256": self.repository.fingerprint(after_paths),
            }
            execution = len(records) + 1
            record = self._record_output(
                key, execution, inputs, exit_code, stdout, stderr
            )
            record["valid"] = after == before
            records.append(record)
            _atomic_write(self.path, json.dumps(ledger, indent=2) + "\n")
            if after != before:
                raise HarnessError(
                    "validation command changed substantive workspace files; "
                    f"inspect {record['artifact']}"
                )
            return self._public(record, reused=False)

    def assert_reference(self, reference: dict, purpose: str) -> None:
        ledger = self._load()
        key = reference.get("ledger_key")
        execution = reference.get("execution")
        records = ledger["entries"].get(key)
        if not isinstance(records, list) or not isinstance(execution, int):
            raise HarnessError("validation does not reference a ledger execution")
        if execution < 1 or execution > len(records):
            raise HarnessError("validation references an unknown ledger execution")
        record = records[execution - 1]
        if not record.get("valid", True):
            raise HarnessError("validation references an invalid ledger execution")
        if record.get("purpose") != purpose:
            raise HarnessError(
                f"validation purpose must be {purpose!r}, found {record.get('purpose')!r}"
            )
        expected = {
            "command": record["command"],
            "exit_code": record["exit_code"],
            "artifact": record["artifact"],
            "sha256": record["sha256"],
        }
        for field, value in expected.items():
            if reference.get(field) != value:
                raise HarnessError(f"validation {field} does not match its ledger record")

    def reference(self, key: str, execution: int, purpose: str) -> dict:
        reference = {"ledger_key": key, "execution": execution}
        ledger = self._load()
        records = ledger["entries"].get(key)
        if not isinstance(records, list) or execution < 1 or execution > len(records):
            raise HarnessError("validation references an unknown ledger execution")
        record = records[execution - 1]
        reference.update(
            {
                "command": record.get("command"),
                "exit_code": record.get("exit_code"),
                "artifact": record.get("artifact"),
                "sha256": record.get("sha256"),
            }
        )
        self.assert_reference(reference, purpose)
        return reference
