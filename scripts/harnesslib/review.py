"""Mechanical review ingestion and milestone completion gates."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re

from .ledger import ValidationLedger
from .repository import Repository, project_root
from .results import file_sha256, load_result, repository_relative
from .state import HarnessError, StateStore, _replace_markdown_status, next_action


BLOCKING = {"BLOCKER", "IMPORTANT"}
SEVERITIES = BLOCKING | {"OPTIONAL"}


def _replace_markdown_criteria(text: str, milestone_id: str, criteria: list[dict]) -> str:
    headings = list(re.finditer(r"(?m)^## (?P<id>M[^ ]+)\s+—.*$", text))
    index = next(
        (position for position, heading in enumerate(headings) if heading.group("id") == milestone_id),
        None,
    )
    if index is None:
        raise HarnessError(f"milestone view has no section for {milestone_id}")
    heading = headings[index]
    end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
    section = text[heading.end():end]
    block = re.search(
        r"(?ms)^### Acceptance Criteria\s*\n(?P<body>.*?)(?=^### |^## |\Z)",
        section,
    )
    if not block:
        raise HarnessError(f"milestone view has no Acceptance Criteria for {milestone_id}")
    items = list(re.finditer(r"(?m)^(?P<prefix>\s*-\s*\[)(?P<mark>[ xX])", block.group("body")))
    if len(items) != len(criteria):
        raise HarnessError("criterion count differs between state and milestone view")
    body = block.group("body")
    pieces = []
    cursor = 0
    for item, criterion in zip(items, criteria):
        pieces.append(body[cursor:item.start("mark")])
        pieces.append("x" if criterion.get("status") == "PASS" else " ")
        cursor = item.end("mark")
    pieces.append(body[cursor:])
    updated = "".join(pieces)
    section = section[:block.start("body")] + updated + section[block.end("body"):]
    return text[:heading.end()] + section + text[end:]


def _validate_findings(findings: object) -> list[dict]:
    if not isinstance(findings, list):
        raise HarnessError("review findings must be an array")
    result = []
    ids = set()
    for finding in findings:
        if not isinstance(finding, dict) or not finding.get("id"):
            raise HarnessError("review contains a finding without an id")
        if finding["id"] in ids:
            raise HarnessError(f"review repeats finding id {finding['id']}")
        ids.add(finding["id"])
        if finding.get("severity") not in SEVERITIES:
            raise HarnessError(f"review finding {finding['id']} has invalid severity")
        for field in ("summary", "evidence", "suggested_correction"):
            if not isinstance(finding.get(field), str) or not finding[field].strip():
                raise HarnessError(f"review finding {finding['id']} lacks {field}")
        paths = finding.get("paths", [])
        if not isinstance(paths, list) or not all(isinstance(path, str) and path for path in paths):
            raise HarnessError(f"review finding {finding['id']}.paths must be a string array")
        normalised = deepcopy(finding)
        normalised["status"] = "OPEN"
        result.append(normalised)
    return result


class ReviewManager:
    def __init__(self, store: StateStore) -> None:
        self.store = store
        self.root = project_root(store.state_path)

    def enter(self, milestone_id: str) -> dict:
        repository = Repository(self.root)

        def operation(state: dict, view: str) -> tuple[dict, str, dict]:
            if state.get("current_milestone") != milestone_id:
                raise HarnessError(f"{milestone_id} is not the current milestone")
            milestone = state["milestones"][milestone_id]
            if milestone.get("status") != "IN_PROGRESS":
                raise HarnessError("enter-review requires IN_PROGRESS status")
            tasks = milestone.get("tasks", [])
            if not tasks or any(task.get("status") != "ACCEPTED" for task in tasks):
                raise HarnessError("enter-review requires every task to be ACCEPTED")
            runtime = milestone.get("runtime")
            if isinstance(runtime, dict):
                if runtime.get("preflight", {}).get("status") != "PASSED":
                    raise HarnessError("enter-review requires passed runtime preflight")
                if runtime.get("active_dispatch") is not None:
                    raise HarnessError("enter-review requires no active dispatch")
            if repository.changed_files():
                raise HarnessError("enter-review requires no uncommitted substantive files")
            active = milestone.get("active_review")
            if isinstance(active, dict):
                if not active.get("plan_registered"):
                    raise HarnessError("review findings do not yet have a correction plan")
                base = active["reviewed_head"]
                scope = "CORRECTION"
            else:
                base = milestone.get("baseline", {}).get("commit")
                scope = "MILESTONE"
            head = repository.head()
            if not base:
                raise HarnessError("enter-review requires a recorded baseline")
            milestone["review_target"] = {"base": base, "head": head, "scope": scope}
            milestone["status"] = "REVIEW"
            updated = _replace_markdown_status(view, milestone_id, "REVIEW")
            return state, updated, {
                "changed": True,
                "action": "DISPATCH_REVIEWER",
                "milestone": milestone_id,
                "base": base,
                "head": head,
                "scope": scope,
                "cycle": len(milestone.get("reviews", [])) + 1,
            }

        return self.store.mutate(operation)

    def packet(self, milestone_id: str) -> dict:
        self.store.validate()
        state = self.store.load()
        milestone = state.get("milestones", {}).get(milestone_id)
        if not isinstance(milestone, dict) or milestone.get("status") != "REVIEW":
            raise HarnessError("review-packet requires REVIEW status")
        target = milestone.get("review_target")
        if not isinstance(target, dict):
            raise HarnessError("review-packet requires a review target")
        if target.get("scope") == "CORRECTION":
            cycle = milestone.get("active_review", {}).get("cycle")
            tasks = [
                task for task in milestone.get("tasks", [])
                if task.get("id", "").startswith(f"{milestone_id}-C{cycle}-")
            ]
        else:
            tasks = milestone.get("tasks", [])
        tiers = {
            task.get("worker_result", {}).get("routing", task.get("routing", {})).get("tier")
            for task in tasks
        }
        tier = "Top" if "Top" in tiers else "Mid"
        cycle = len(milestone.get("reviews", [])) + 1
        return {
            "milestone": milestone_id,
            "cycle": cycle,
            "base": target["base"],
            "head": target["head"],
            "scope": target["scope"],
            "tier": tier,
            "model": "opus" if tier == "Top" else "sonnet",
            "reason_code": "TOP_DIFF_REVIEW" if tier == "Top" else "REVIEW_FLOOR",
            "requirements": ".harness/requirements.md",
            "architecture": (
                ".harness/architecture.md"
                if (self.root / ".harness" / "architecture.md").exists()
                else None
            ),
            "state": ".harness/state.json",
            "milestones": ".harness/milestones.md",
            "result": f".harness/results/{milestone_id}-review-{cycle}.json",
            "report": f".harness/reviews/{milestone_id}-cycle{cycle}.md",
        }

    def record(self, milestone_id: str, result_path: Path) -> dict:
        result, result_hash = load_result(result_path)
        relative = repository_relative(self.root, result_path, "review result")
        if not relative.startswith(".harness/results/"):
            raise HarnessError("review results must be stored under .harness/results/")

        def operation(state: dict, view: str) -> tuple[dict, str, dict]:
            milestone = state.get("milestones", {}).get(milestone_id)
            if state.get("current_milestone") != milestone_id or not isinstance(milestone, dict):
                raise HarnessError(f"{milestone_id} is not the current milestone")
            for review in milestone.get("reviews", []):
                if review.get("result_sha256") == result_hash:
                    return state, view, {"changed": False, **next_action(state)}
            if milestone.get("status") != "REVIEW":
                raise HarnessError("record-review-result requires REVIEW status")
            runtime = milestone.get("runtime")
            if isinstance(runtime, dict):
                active = runtime.get("active_dispatch")
                if not isinstance(active, dict) or active.get("kind") != "reviewer":
                    raise HarnessError("review result lacks a matching active dispatch")
            target = milestone.get("review_target")
            if not isinstance(target, dict):
                raise HarnessError("review target is absent")
            expected = {
                "schema_version": 1,
                "role": "reviewer",
                "milestone": milestone_id,
                "cycle": len(milestone.get("reviews", [])) + 1,
                "base": target["base"],
                "head": target["head"],
            }
            for field, value in expected.items():
                if result.get(field) != value:
                    raise HarnessError(f"review result {field} must be {value!r}")
            if Repository(self.root).head() != target["head"]:
                raise HarnessError("repository HEAD moved after reviewer dispatch")
            verdict = result.get("verdict")
            if verdict not in {"PASS", "CHANGES_REQUIRED", "BLOCKED"}:
                raise HarnessError(f"review verdict is invalid: {verdict!r}")
            if result.get("tier") not in {"Mid", "Top"}:
                raise HarnessError("review tier must be Mid or Top")
            if not result.get("model") or not result.get("reason_code"):
                raise HarnessError("review result requires model and reason_code")
            if result.get("scope") not in {"SUBSTANTIVE", "RECORD_ONLY"}:
                raise HarnessError("review scope must be SUBSTANTIVE or RECORD_ONLY")
            criteria = result.get("criteria")
            if not isinstance(criteria, list):
                raise HarnessError("review criteria must be an array")
            expected_ids = [item["id"] for item in milestone.get("criteria", [])]
            if [item.get("id") for item in criteria if isinstance(item, dict)] != expected_ids:
                raise HarnessError("review result must cover every criterion exactly once in order")
            criterion_results = {}
            for item in criteria:
                if item.get("status") not in {"PASS", "FAIL"}:
                    raise HarnessError(f"review criterion {item.get('id')} has invalid status")
                evidence = item.get("evidence")
                if not isinstance(evidence, list) or not evidence or not all(
                    isinstance(value, str) and value for value in evidence
                ):
                    raise HarnessError(f"review criterion {item.get('id')} lacks evidence")
                criterion_results[item["id"]] = item
            findings = _validate_findings(result.get("findings"))
            blocking = [item for item in findings if item["severity"] in BLOCKING]
            if verdict == "PASS" and (
                blocking or any(item["status"] != "PASS" for item in criteria)
            ):
                raise HarnessError("PASS review has failed criteria or blocking findings")
            if verdict == "CHANGES_REQUIRED" and not blocking:
                raise HarnessError("CHANGES_REQUIRED review requires a blocking finding")
            validation = result.get("validation")
            if not isinstance(validation, dict):
                raise HarnessError("review result requires ledgered validation")
            ValidationLedger(self.root).assert_reference(validation, "reviewer")
            if verdict == "PASS" and validation.get("exit_code") != 0:
                raise HarnessError("PASS review requires validation exit_code 0")
            report = result.get("report")
            report_artifact = None
            if verdict == "CHANGES_REQUIRED":
                if not isinstance(report, dict) or not report.get("artifact"):
                    raise HarnessError("CHANGES_REQUIRED review requires a report artifact")
                report_path = self.root / report["artifact"]
                report_artifact = repository_relative(self.root, report_path, "review report")
                if not report_artifact.startswith(".harness/reviews/"):
                    raise HarnessError("review report must be under .harness/reviews/")
                if not report_path.is_file() or file_sha256(report_path) != report.get("sha256"):
                    raise HarnessError("review report hash does not match its contents")
            for criterion in milestone["criteria"]:
                item = criterion_results[criterion["id"]]
                criterion["status"] = item["status"]
                criterion["evidence"] = item["evidence"]
            review_record = {
                "cycle": result["cycle"],
                "verdict": verdict,
                "tier": result["tier"],
                "model": result["model"],
                "reason_code": result["reason_code"],
                "scope": target["scope"],
                "correction_scope": result["scope"],
                "diff_range": f"{target['base']}...{target['head']}",
                "head": target["head"],
                "result_artifact": relative,
                "result_sha256": result_hash,
                "validation": deepcopy(validation),
            }
            if report_artifact:
                review_record["artifact"] = report_artifact
            milestone.setdefault("reviews", []).append(review_record)
            existing = {
                item.get("id"): item for item in milestone.setdefault("findings", [])
                if isinstance(item, dict) and item.get("id")
            }
            if verdict == "PASS":
                for item in existing.values():
                    if item.get("severity") in BLOCKING:
                        item["status"] = "RESOLVED"
            for finding in findings:
                existing[finding["id"]] = finding
            milestone["findings"] = list(existing.values())
            milestone.pop("review_passed", None)
            if verdict == "PASS":
                milestone["review_passed"] = {"head": target["head"], "cycle": result["cycle"]}
                milestone["status"] = "REVIEW"
                milestone.pop("active_review", None)
            elif verdict == "CHANGES_REQUIRED" and milestone.get("review_cycles", 0) < 2:
                milestone["review_cycles"] = milestone.get("review_cycles", 0) + 1
                milestone["active_review"] = {
                    "cycle": milestone["review_cycles"],
                    "reviewed_head": target["head"],
                    "findings": [item["id"] for item in blocking],
                    "report": report_artifact,
                    "scope": result["scope"],
                    "plan_registered": False,
                }
                milestone["status"] = "IN_PROGRESS"
            else:
                milestone["status"] = "BLOCKED"
            updated = _replace_markdown_status(view, milestone_id, milestone["status"])
            updated = _replace_markdown_criteria(updated, milestone_id, milestone["criteria"])
            return state, updated, {"changed": True, **next_action(state)}

        return self.store.mutate(operation)

    def record_as_built(
        self,
        milestone_id: str,
        artifact: Path | None,
        not_required: str | None,
    ) -> dict:
        def operation(state: dict, view: str) -> tuple[dict, str, dict]:
            milestone = state.get("milestones", {}).get(milestone_id)
            if not isinstance(milestone, dict) or not milestone.get("review_passed"):
                raise HarnessError("as-built recording requires a passing review")
            if artifact is not None:
                relative = repository_relative(self.root, artifact, "as-built artifact")
                if not relative.startswith(".harness/as-built/") or not artifact.is_file():
                    raise HarnessError("as-built artifact must exist under .harness/as-built/")
                record = {
                    "artifact": relative,
                    "sha256": file_sha256(artifact),
                    "result": "RECORDED",
                }
            else:
                if not not_required or not_required.strip() == "":
                    raise HarnessError("record-as-built requires an artifact or a reason")
                record = {"artifact": None, "result": "NOT_REQUIRED: " + not_required.strip()}
            if milestone.get("as_built") == record:
                return state, view, {"changed": False, **next_action(state)}
            milestone["as_built"] = record
            return state, view, {"changed": True, **next_action(state)}

        return self.store.mutate(operation)

    def close(self, milestone_id: str) -> dict:
        repository = Repository(self.root)
        current = self.store.load().get("milestones", {}).get(milestone_id)
        if isinstance(current, dict) and current.get("status") == "DONE":
            commit, created = repository.commit_harness(f"harness({milestone_id}): close milestone")
            return {"changed": False, "commit": commit, "commit_created": created, "action": "DONE"}

        def operation(state: dict, view: str) -> tuple[dict, str, dict]:
            milestone = state.get("milestones", {}).get(milestone_id)
            if state.get("current_milestone") != milestone_id or not isinstance(milestone, dict):
                raise HarnessError(f"{milestone_id} is not the current milestone")
            passed = milestone.get("review_passed")
            if milestone.get("status") != "REVIEW" or not isinstance(passed, dict):
                raise HarnessError("phase-close requires a passing REVIEW milestone")
            if Repository(self.root).head() != passed.get("head"):
                raise HarnessError("phase-close HEAD differs from the reviewed HEAD")
            if any(item.get("status") != "PASS" for item in milestone.get("criteria", [])):
                raise HarnessError("phase-close requires every criterion to PASS")
            unresolved = [
                item.get("id") for item in milestone.get("findings", [])
                if item.get("severity") in BLOCKING and item.get("status") != "RESOLVED"
            ]
            if unresolved:
                raise HarnessError("phase-close has unresolved findings: " + ", ".join(unresolved))
            as_built = milestone.get("as_built")
            if not isinstance(as_built, dict) or as_built.get("result") in {None, "PENDING"}:
                raise HarnessError("phase-close requires an as-built result or NOT_REQUIRED")
            runtime = milestone.get("runtime")
            if isinstance(runtime, dict) and runtime.get("active_dispatch") is not None:
                raise HarnessError("phase-close requires no active dispatch")
            milestone["status"] = "DONE"
            remaining = [
                key for key, item in state["milestones"].items()
                if key != milestone_id and item.get("status") not in {"DONE", "DEFERRED"}
            ]
            state["current_milestone"] = remaining[0] if remaining else None
            updated = _replace_markdown_status(view, milestone_id, "DONE")
            updated = _replace_markdown_criteria(updated, milestone_id, milestone["criteria"])
            return state, updated, {"changed": True}

        result = self.store.mutate(operation)
        commit, created = repository.commit_harness(f"harness({milestone_id}): close milestone")
        return {**result, "commit": commit, "commit_created": created, "action": "DONE"}
