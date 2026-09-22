#!/usr/bin/env python3
"""Prepare and run the guarded legacy/mechanical evaluation campaign."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import uuid


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = (
    ("known-path", "mechanical-known-path"),
    ("medium-cross-file", "mechanical-medium-cross-file"),
    ("oversized-split", "mechanical-oversized-split"),
    ("review-defect", "mechanical-review-defect"),
)
PLUGIN_PATHS = (".claude-plugin", "agents", "hooks", "scripts", "skills", "examples")
TERMINAL = {"COMPLETE", "FAILED", "CAPPED"}


class CampaignError(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise CampaignError(f"cannot read JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise CampaignError(f"JSON root must be an object: {path}")
    return value


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4()}.tmp"
    try:
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run(argv: list[str], *, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        argv, cwd=cwd, text=True, capture_output=True, check=False
    )
    if completed.returncode:
        raise CampaignError(
            f"command failed ({' '.join(argv)}): "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    return completed.stdout.strip()


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def copy_mechanical_plugin(destination: Path) -> None:
    destination.mkdir(parents=True)
    for name in PLUGIN_PATHS:
        source = ROOT / name
        target = destination / name
        if source.is_dir():
            shutil.copytree(
                source,
                target,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def copy_legacy_plugin(destination: Path, legacy_sha: str) -> None:
    tracked = set(run(["git", "ls-tree", "--name-only", legacy_sha], cwd=ROOT).splitlines())
    paths = [name for name in PLUGIN_PATHS if name in tracked]
    if not {"agents", "hooks", "scripts", "skills"} <= tracked:
        raise CampaignError("legacy ref is not a complete harness plugin")
    archive = subprocess.run(
        ["git", "-C", str(ROOT), "archive", "--format=tar", legacy_sha, *paths],
        capture_output=True,
        check=False,
    )
    if archive.returncode:
        raise CampaignError("cannot archive the pinned harness for the legacy arm")
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as bundle:
        for member in bundle.getmembers():
            target = (destination / member.name).resolve()
            if target != destination.resolve() and destination.resolve() not in target.parents:
                raise CampaignError("legacy plugin archive contains an unsafe path")
        bundle.extractall(destination, filter="data")
    if not (destination / "agents/orchestrator.md").is_file():
        raise CampaignError("legacy ref must contain the legacy orchestrator")
    # Some historical source exports omitted metadata. Supply only the namespace;
    # the resulting frozen tree hash includes this explicitly documented shim.
    manifest = destination / ".claude-plugin/plugin.json"
    if not manifest.exists():
        write_json(manifest, {"name": "harness", "version": "0.1.0"})


def commit(repository: Path, message: str) -> str:
    run(["git", "add", "-A"], cwd=repository)
    run(["git", "commit", "-qm", message], cwd=repository)
    return run(["git", "rev-parse", "HEAD"], cwd=repository)


def initialise_fixture(source: Path, destination: Path, cohort: str) -> str:
    shutil.copytree(source, destination)
    run(["git", "init", "-q", "-b", "main"], cwd=destination)
    run(["git", "config", "user.name", "Harness Evaluation"], cwd=destination)
    run(["git", "config", "user.email", "harness-eval@example.invalid"], cwd=destination)
    if cohort != "review-defect":
        return commit(destination, "Frozen fixture baseline")

    baseline = commit(destination, "Pre-implementation baseline")
    for relative in (Path(".harness/state.json"), Path(".harness/milestones.md")):
        path = destination / relative
        path.write_text(path.read_text().replace("BASELINE_SHA", baseline))
    ledger = destination / "inventory" / "ledger.py"
    text = ledger.read_text()
    correct = """        if next_count < 0:
            raise InsufficientStock(
                f\"withdrawal of {-amount} exceeds {self._on_hand} on hand\"
            )
        self._on_hand = next_count
        self._history.append((amount, reason))
"""
    defective = """        self._on_hand = next_count
        self._history.append((amount, reason))
        if next_count < 0:
            raise InsufficientStock(
                f\"withdrawal of {-amount} exceeds {self._on_hand - amount} on hand\"
            )
"""
    if correct not in text:
        raise CampaignError("review-defect fixture no longer matches its frozen seed")
    ledger.write_text(text.replace(correct, defective))
    return commit(destination, "Seed defective implementation for review")


def clone_fixture(source: Path, destination: Path, commit_sha: str) -> None:
    run(["git", "clone", "-q", "--local", str(source), str(destination)])
    run(["git", "checkout", "-q", commit_sha], cwd=destination)
    run(["git", "config", "user.name", "Harness Evaluation"], cwd=destination)
    run(["git", "config", "user.email", "harness-eval@example.invalid"], cwd=destination)


def prepare(output: Path, claude: Path, max_budget: float, timeout: int, legacy_ref: str) -> dict:
    output = output.resolve()
    if output.exists():
        raise CampaignError(f"refusing to replace existing campaign: {output}")
    if not claude.is_absolute() or not claude.is_file() or not os.access(claude, os.X_OK):
        raise CampaignError("claude must be an absolute executable path")
    if not 0 < max_budget <= 100:
        raise CampaignError("max budget must be greater than zero and no more than 100")
    if not 60 <= timeout <= 14_400:
        raise CampaignError("timeout must be between 60 and 14400 seconds")
    legacy_sha = run(["git", "rev-parse", "--verify", "--end-of-options", legacy_ref + "^{commit}"], cwd=ROOT)
    claude_version = run([str(claude), "--version"])
    output.mkdir(parents=True)
    try:
        plugins = output / "plugins"
        legacy_plugin = plugins / "legacy"
        mechanical_plugin = plugins / "mechanical"
        copy_legacy_plugin(legacy_plugin, legacy_sha)
        copy_mechanical_plugin(mechanical_plugin)
        plugin_hashes = {
            "legacy": tree_hash(legacy_plugin),
            "mechanical": tree_hash(mechanical_plugin),
        }
        runs = []
        order = []
        for index, (cohort, fixture_name) in enumerate(FIXTURES):
            seed = output / "seeds" / fixture_name
            fixture_commit = initialise_fixture(
                ROOT / "fixtures" / fixture_name, seed, cohort
            )
            pair_id = str(uuid.uuid4())
            arms = ("legacy", "mechanical") if index % 2 == 0 else ("mechanical", "legacy")
            for arm in arms:
                run_id = str(uuid.uuid4())
                worktree = output / "worktrees" / run_id
                clone_fixture(seed, worktree, fixture_commit)
                session_ids = [str(uuid.uuid4()) for _ in range(5)]
                manifest = {
                    "schema_version": 1,
                    "run_id": run_id,
                    "pair_id": pair_id,
                    "arm": arm,
                    "cohort": cohort,
                    "fixture": fixture_name,
                    "fixture_commit": fixture_commit,
                    "plugin": str(plugins / arm),
                    "plugin_sha256": plugin_hashes[arm],
                    "worktree": str(worktree),
                    "session_ids": session_ids,
                    "invocations": [],
                    "status": "PENDING",
                    "max_budget_usd": max_budget,
                    "timeout_seconds": timeout,
                }
                path = output / "runs" / f"{run_id}.json"
                write_json(path, manifest)
                runs.append(str(path.relative_to(output)))
                order.append(run_id)
        campaign = {
            "schema_version": 1,
            "campaign_id": str(uuid.uuid4()),
            "created_at": now(),
            "claude": str(claude),
            "claude_version": claude_version,
            "harness_repository": str(ROOT),
            "legacy_commit": legacy_sha,
            "plugin_hashes": plugin_hashes,
            "runs": runs,
            "order": order,
        }
        write_json(output / "campaign.json", campaign)
        return campaign
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise


def load_campaign(root: Path) -> tuple[dict, dict[str, tuple[Path, dict]]]:
    root = root.resolve()
    campaign = read_json(root / "campaign.json")
    runs = {}
    for relative in campaign.get("runs", []):
        path = root / relative
        manifest = read_json(path)
        runs[manifest["run_id"]] = (path, manifest)
    if list(runs) != campaign.get("order"):
        raise CampaignError("campaign run order differs from frozen manifests")
    return campaign, runs


def preflight(root: Path) -> dict:
    campaign, runs = load_campaign(root)
    observed_version = run([campaign["claude"], "--version"])
    if observed_version != campaign["claude_version"]:
        raise CampaignError("Claude version changed after campaign preparation")
    for arm, expected in campaign["plugin_hashes"].items():
        if tree_hash(root / "plugins" / arm) != expected:
            raise CampaignError(f"{arm} plugin snapshot changed")
    for _, manifest in runs.values():
        worktree = Path(manifest["worktree"])
        if run(["git", "status", "--porcelain"], cwd=worktree) and not manifest["invocations"]:
            raise CampaignError(f"unlaunched worktree is dirty: {manifest['run_id']}")
        if tree_hash(Path(manifest["plugin"])) != manifest["plugin_sha256"]:
            raise CampaignError(f"run plugin changed: {manifest['run_id']}")
    return {
        "campaign_id": campaign["campaign_id"],
        "runs": len(runs),
        "pending": sum(item[1]["status"] == "PENDING" for item in runs.values()),
        "max_total_authorized_usd": sum(
            item[1]["max_budget_usd"] * len(item[1]["session_ids"])
            for item in runs.values()
        ),
    }


def next_invocation(root: Path) -> tuple[Path, dict, str] | None:
    _, runs = load_campaign(root)
    for run_id in read_json(root / "campaign.json")["order"]:
        path, manifest = runs[run_id]
        if manifest["status"] in TERMINAL:
            continue
        if manifest["status"] == "RUNNING":
            raise CampaignError(
                f"run {run_id} has an unresolved recorded launch; inspect it before continuing"
            )
        index = len(manifest["invocations"])
        if index >= len(manifest["session_ids"]):
            manifest["status"] = "CAPPED"
            write_json(path, manifest)
            continue
        return path, manifest, manifest["session_ids"][index]
    return None


def invocation_contract(root: Path) -> dict:
    selected = next_invocation(root)
    if selected is None:
        return {"complete": True}
    _, manifest, session_id = selected
    prompt = "/harness:implement" if manifest["arm"] == "legacy" else "/harness:implement-mechanical"
    argv = [
        read_json(root / "campaign.json")["claude"],
        "--plugin-dir", manifest["plugin"],
        "--permission-mode", "acceptEdits",
        "--allowedTools", "Read Write Edit Bash Grep Glob Agent",
        "--max-budget-usd", str(manifest["max_budget_usd"]),
        "--session-id", session_id,
        "--output-format", "json",
        "-p", prompt,
    ]
    return {
        "complete": False,
        "run_id": manifest["run_id"],
        "session_id": session_id,
        "arm": manifest["arm"],
        "cohort": manifest["cohort"],
        "max_budget_usd": manifest["max_budget_usd"],
        "authorization": f"Authorize paid mechanical evaluation {session_id}",
        "cwd": manifest["worktree"],
        "argv": argv,
    }


def terminal_state(manifest: dict) -> bool:
    state_path = Path(manifest["worktree"]) / ".harness" / "state.json"
    if not state_path.is_file():
        return False
    state = read_json(state_path)
    if manifest["cohort"] == "oversized-split":
        return bool(state.get("splits")) or any(
            item.get("status") == "BLOCKED" for item in state.get("milestones", {}).values()
        )
    return state.get("current_milestone") is None or any(
        item.get("status") == "BLOCKED" for item in state.get("milestones", {}).values()
    )


def run_one(root: Path, acknowledgement: str) -> dict:
    selected = next_invocation(root)
    if selected is None:
        raise CampaignError("campaign has no pending paid invocation")
    path, manifest, session_id = selected
    if acknowledgement != session_id:
        raise CampaignError("paid-run acknowledgement does not match the next session id")
    contract = invocation_contract(root)
    event = {"session_id": session_id, "started_at": now(), "status": "RUNNING"}
    manifest["invocations"].append(event)
    manifest["status"] = "RUNNING"
    write_json(path, manifest)
    environment = {**os.environ, "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"}
    try:
        completed = subprocess.run(
            contract["argv"],
            cwd=contract["cwd"],
            text=True,
            capture_output=True,
            check=False,
            timeout=manifest["timeout_seconds"],
            env=environment,
        )
        event.update(
            {
                "finished_at": now(),
                "exit_code": completed.returncode,
                "stdout": f"outputs/{manifest['run_id']}/{session_id}.stdout.json",
                "stderr": f"outputs/{manifest['run_id']}/{session_id}.stderr.txt",
            }
        )
        stdout_path = root / event["stdout"]
        stderr_path = root / event["stderr"]
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text(completed.stdout)
        stderr_path.write_text(completed.stderr)
        if completed.returncode:
            event["status"] = "FAILED"
            manifest["status"] = "FAILED"
        elif terminal_state(manifest):
            event["status"] = "COMPLETE"
            manifest["status"] = "COMPLETE"
        elif len(manifest["invocations"]) >= len(manifest["session_ids"]):
            event["status"] = "CAPPED"
            manifest["status"] = "CAPPED"
        else:
            event["status"] = "CONTINUE"
            manifest["status"] = "PENDING"
    except subprocess.TimeoutExpired as error:
        event.update({"finished_at": now(), "status": "CAPPED", "error": str(error)})
        manifest["status"] = "CAPPED"
    write_json(path, manifest)
    return {"run_id": manifest["run_id"], "session_id": session_id, "status": manifest["status"]}


def stage_sessions(root: Path, run_id: str, projects_root: Path) -> dict:
    _, runs = load_campaign(root)
    if run_id not in runs:
        raise CampaignError(f"unknown run id: {run_id}")
    _, manifest = runs[run_id]
    staged = []
    for event in manifest["invocations"]:
        session_id = event["session_id"]
        matches = list(projects_root.rglob(f"{session_id}.jsonl"))
        if len(matches) != 1:
            raise CampaignError(f"expected one transcript for {session_id}, found {len(matches)}")
        source = matches[0]
        destination_base = root / "evidence" / run_id / session_id
        destination_base.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination_base.with_suffix(".jsonl"))
        source_directory = source.with_suffix("")
        if source_directory.is_dir():
            shutil.copytree(source_directory, destination_base, dirs_exist_ok=True)
        staged.append(str(destination_base))
    return {"run_id": run_id, "session_dirs": staged}


def hidden_check(cohort: str, worktree: Path) -> bool:
    programs = {
        "known-path": "from counter import increment; assert [increment(x) for x in (4,0,-3)] == [5,1,-2]",
        "medium-cross-file": "from src.api import quote; assert quote(100,.1) == 110 and quote(100,.2) == 120",
        "review-defect": (
            "from inventory.ledger import Ledger,InsufficientStock; "
            "x=Ledger(); x.apply(3,'delivery'); before=x.history(); "
            "\ntry: x.apply(-5,'sale')\nexcept InsufficientStock: pass\n"
            "else: raise AssertionError('no rejection')\n"
            "assert x.on_hand == 3 and x.history() == before"
        ),
    }
    if cohort == "oversized-split":
        state = read_json(worktree / ".harness" / "state.json")
        required = {"SUBSYSTEMS_GT_3", "CONCURRENCY_LIFECYCLE", "IMPLEMENTATION_PLUS_LIVE_PROOF"}
        return bool(state.get("splits")) and required <= set(state["splits"][0]["reason_codes"])
    completed = subprocess.run(
        [sys.executable, "-c", programs[cohort]],
        cwd=worktree,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0


def grade_run(root: Path, run_id: str) -> dict:
    _, runs = load_campaign(root)
    if run_id not in runs:
        raise CampaignError(f"unknown run id: {run_id}")
    _, manifest = runs[run_id]
    worktree = Path(manifest["worktree"])
    state_path = worktree / ".harness" / "state.json"
    state = read_json(state_path)
    hidden = hidden_check(manifest["cohort"], worktree)
    plugin = Path(manifest["plugin"])
    check = subprocess.run(
        [
            sys.executable, str(plugin / "scripts" / "check-state.py"), str(state_path),
            "--milestones", str(worktree / ".harness" / "milestones.md"),
            "--requirements", str(worktree / ".harness" / "requirements.md"),
        ],
        text=True, capture_output=True, check=False,
    )
    milestones = list(state.get("milestones", {}).values())
    split = manifest["cohort"] == "oversized-split"
    done = state.get("current_milestone") is None and all(
        item.get("status") in {"DONE", "DEFERRED"} for item in milestones
    )
    tasks = [task for item in milestones for task in item.get("tasks", [])]
    reviews = [review for item in milestones for review in item.get("reviews", [])]
    review_defect_caught = manifest["cohort"] != "review-defect" or any(
        review.get("verdict") == "CHANGES_REQUIRED" for review in reviews
    )
    verified_tasks = tasks
    if manifest["cohort"] == "review-defect":
        verified_tasks = [task for task in tasks if task.get("id") != "M1-T1"]
    accuracy = {
        "behavioural_fixtures_pass": hidden and review_defect_caught and (split or done),
        "no_false_pass": not (done and not hidden),
        "hidden_tests_pass": hidden,
        "independent_verification": split or bool(verified_tasks) and all(
            task.get("status") == "ACCEPTED" and isinstance(task.get("verifier_result"), dict)
            for task in verified_tasks
        ),
        "independent_milestone_review": split or bool(reviews) and any(
            review.get("verdict") == "PASS" for review in reviews
        ),
        "state_validation_pass": check.returncode == 0,
        "requirement_ownership_complete": (
            set(state.get("requirements", {})) == {"FR1", "FR2", "FR3", "FR4"}
            if split
            else set(state.get("requirements", {})) == {"FR1"}
        ),
    }
    result = {
        "schema_version": 1,
        "run_id": run_id,
        "arm": manifest["arm"],
        "cohort": manifest["cohort"],
        "status": manifest["status"],
        **accuracy,
    }
    write_json(root / "evidence" / run_id / "accuracy.json", result)
    return result


def measure_run(root: Path, run_id: str) -> dict:
    _, runs = load_campaign(root)
    if run_id not in runs:
        raise CampaignError(f"unknown run id: {run_id}")
    path, manifest = runs[run_id]
    session_dirs = [
        str(root / "evidence" / run_id / event["session_id"])
        for event in manifest["invocations"]
    ]
    if not session_dirs or any(not Path(item).with_suffix(".jsonl").is_file() for item in session_dirs):
        raise CampaignError("stage every launched session before measurement")
    output = root / "evidence" / run_id / "measurement.json"
    completed = subprocess.run(
        [
            sys.executable, str(ROOT / ".harness-dev" / "measure-context.py"),
            *session_dirs,
            "--run-manifest", str(path),
            "--harness-repo", manifest["plugin"],
            "--json", str(output),
        ],
        text=True, capture_output=True, check=False,
    )
    if completed.returncode:
        raise CampaignError("measurement failed: " + completed.stderr.strip())
    return read_json(output)


def accuracy_bundle(root: Path) -> dict:
    _, runs = load_campaign(root)
    evidence = {}
    reports = []
    for run_id, (_, manifest) in runs.items():
        measurement = root / "evidence" / run_id / "measurement.json"
        accuracy = root / "evidence" / run_id / "accuracy.json"
        if not measurement.is_file() or not accuracy.is_file():
            raise CampaignError(f"run {run_id} lacks measurement or accuracy evidence")
        reports.append(str(measurement))
        if manifest["arm"] == "mechanical":
            record = read_json(accuracy)
            evidence[run_id] = {
                field: record[field]
                for field in (
                    "behavioural_fixtures_pass", "no_false_pass", "hidden_tests_pass",
                    "independent_verification", "independent_milestone_review",
                    "state_validation_pass", "requirement_ownership_complete",
                )
            }
    bundle = {"schema_version": 1, "runs": evidence}
    path = root / "evidence" / "accuracy.json"
    write_json(path, bundle)
    return {
        "accuracy_evidence": str(path),
        "reports": reports,
        "compare_argv": [
            sys.executable,
            str(ROOT / ".harness-dev" / "compare-harness-runs.py"),
            *reports,
            "--accuracy-evidence", str(path),
            "--output", str(root / "evidence" / "comparison.json"),
        ],
    }


def status(root: Path) -> dict:
    campaign, runs = load_campaign(root)
    return {
        "campaign_id": campaign["campaign_id"],
        "runs": [
            {
                "run_id": run_id,
                "arm": manifest["arm"],
                "cohort": manifest["cohort"],
                "status": manifest["status"],
                "invocations": len(manifest["invocations"]),
            }
            for run_id, (_, manifest) in runs.items()
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preparing = commands.add_parser("prepare")
    preparing.add_argument("--output", required=True, type=Path)
    preparing.add_argument("--claude", required=True, type=Path)
    preparing.add_argument("--legacy-ref", required=True, help="explicit legacy commit/ref, frozen to a SHA")
    preparing.add_argument("--max-budget-usd", type=float, default=20)
    preparing.add_argument("--timeout-seconds", type=int, default=3600)
    for name in ("preflight", "next", "status", "evidence"):
        command = commands.add_parser(name)
        command.add_argument("campaign", type=Path)
    running = commands.add_parser("run-one")
    running.add_argument("campaign", type=Path)
    running.add_argument("--acknowledge-paid-run", required=True)
    staging = commands.add_parser("stage")
    staging.add_argument("campaign", type=Path)
    staging.add_argument("--run-id", required=True)
    staging.add_argument("--claude-projects-root", required=True, type=Path)
    for name in ("grade", "measure"):
        command = commands.add_parser(name)
        command.add_argument("campaign", type=Path)
        command.add_argument("--run-id", required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.output, args.claude.resolve(), args.max_budget_usd, args.timeout_seconds, args.legacy_ref)
        elif args.command == "preflight":
            result = preflight(args.campaign)
        elif args.command == "next":
            result = invocation_contract(args.campaign)
        elif args.command == "run-one":
            result = run_one(args.campaign, args.acknowledge_paid_run)
        elif args.command == "stage":
            result = stage_sessions(args.campaign, args.run_id, args.claude_projects_root)
        elif args.command == "grade":
            result = grade_run(args.campaign, args.run_id)
        elif args.command == "measure":
            result = measure_run(args.campaign, args.run_id)
        elif args.command == "evidence":
            result = accuracy_bundle(args.campaign)
        else:
            result = status(args.campaign)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (CampaignError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
