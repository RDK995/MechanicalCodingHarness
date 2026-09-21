"""Structured worker and verifier result validation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .state import HarnessError


RESULTS = {"PASS", "FAIL", "BLOCKED"}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repository_relative(root: Path, path: Path, label: str) -> str:
    root = root.resolve()
    candidate = path.resolve()
    if candidate != root and root not in candidate.parents:
        raise HarnessError(f"{label} must be inside the project repository")
    return candidate.relative_to(root).as_posix()


def load_result(path: Path) -> tuple[dict, str]:
    try:
        content = path.read_bytes()
        result = json.loads(content)
    except (OSError, json.JSONDecodeError) as error:
        raise HarnessError(f"cannot read valid result JSON from {path}: {error}") from error
    if not isinstance(result, dict):
        raise HarnessError("result root must be an object")
    return result, hashlib.sha256(content).hexdigest()


def assert_record_immutable(record: dict, root: Path, role: str) -> None:
    artifact = root / record.get("artifact", "")
    if not artifact.is_file() or file_sha256(artifact) != record.get("sha256"):
        raise HarnessError(f"recorded {role} result artifact changed after acceptance")
    validation = record.get("validation")
    if not isinstance(validation, dict):
        raise HarnessError(f"recorded {role} result lacks validation evidence")
    evidence = root / validation.get("artifact", "")
    if not evidence.is_file() or file_sha256(evidence) != validation.get("sha256"):
        raise HarnessError(f"recorded {role} validation artifact changed after acceptance")
    from .ledger import ValidationLedger

    ValidationLedger(root).assert_reference(validation, role)


def validate_result(
    result: dict,
    *,
    role: str,
    milestone_id: str,
    task_id: str,
    attempt: int,
    snapshot: dict,
    root: Path,
) -> dict:
    expected = {
        "schema_version": 1,
        "role": role,
        "milestone": milestone_id,
        "task": task_id,
        "attempt": attempt,
    }
    for field, value in expected.items():
        if result.get(field) != value:
            raise HarnessError(f"result {field} must be {value!r}")
    verdict = result.get("result")
    if verdict not in RESULTS:
        raise HarnessError(f"result verdict is invalid: {verdict!r}")
    for field in ("base", "snapshot"):
        if result.get(field) != snapshot[field]:
            raise HarnessError(f"result {field} does not match the current workspace")
    changed_files = result.get("changed_files")
    if not isinstance(changed_files, list) or not all(
        isinstance(path, str) and path for path in changed_files
    ):
        raise HarnessError("result changed_files must be a string array")
    if sorted(set(changed_files)) != snapshot["changed_files"]:
        raise HarnessError("result changed_files do not match the current workspace")
    if role == "worker" and verdict == "PASS" and not changed_files:
        raise HarnessError("a passing worker result must contain a substantive change")

    validation = result.get("validation")
    if not isinstance(validation, dict):
        raise HarnessError("result requires a validation object")
    if not isinstance(validation.get("command"), str) or not validation["command"].strip():
        raise HarnessError("validation requires a non-empty command")
    if not isinstance(validation.get("exit_code"), int):
        raise HarnessError("validation requires an integer exit_code")
    if verdict == "PASS" and validation["exit_code"] != 0:
        raise HarnessError("a PASS result requires validation exit_code 0")
    artifact_value = validation.get("artifact")
    if not isinstance(artifact_value, str) or not artifact_value:
        raise HarnessError("validation requires an artifact path")
    artifact = root / artifact_value
    relative_artifact = repository_relative(root, artifact, "validation artifact")
    if not artifact.is_file():
        raise HarnessError(f"validation artifact does not exist: {relative_artifact}")
    actual_hash = file_sha256(artifact)
    if validation.get("sha256") != actual_hash:
        raise HarnessError("validation artifact hash does not match its contents")
    from .ledger import ValidationLedger

    ValidationLedger(root).assert_reference(validation, role)
    return {
        "verdict": verdict,
        "validation": {
            "command": validation["command"].strip(),
            "exit_code": validation["exit_code"],
            "artifact": relative_artifact,
            "sha256": actual_hash,
            "ledger_key": validation["ledger_key"],
            "execution": validation["execution"],
        },
    }
