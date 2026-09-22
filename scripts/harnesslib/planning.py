"""Validate and publish an initial milestone plan without legacy orchestration."""

from __future__ import annotations

from collections import Counter
import fcntl
import json
from pathlib import Path
import re
import tempfile

from requirements_ids import functional_requirement_ids
from .state import HarnessError, StateStore, _atomic_write, _lock_path, _render_split_child


def section(text: str, heading: str) -> str:
    matches = list(re.finditer(
        rf"(?ms)^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)", text
    ))
    if len(matches) != 1:
        raise HarnessError(f"expected exactly one {heading} section")
    return matches[0].group(1).strip()


def line(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        raise HarnessError(f"{label} must be nonempty single-line text")
    return value.strip()


def ids(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise HarnessError(f"{label} must be an array of ids")
    if len(set(value)) != len(value):
        raise HarnessError(f"{label} repeats an id")
    return value


def build_plan(proposal: dict, requirements: str, architecture: str | None) -> tuple[dict, str]:
    if section(requirements, "Open Questions") != "None":
        raise HarnessError("requirements still have open questions")
    required, errors = functional_requirement_ids(requirements)
    if errors:
        raise HarnessError("; ".join(errors))
    components: set[str] = set()
    if architecture is not None:
        if section(architecture, "Status") != "AGREED":
            raise HarnessError("architecture must be AGREED")
        if section(architecture, "Open Architecture Questions") != "None":
            raise HarnessError("architecture still has open questions")
        names = re.findall(r"(?m)^### (C\d+)\s+—", section(architecture, "Components"))
        if not names or len(names) != len(set(names)):
            raise HarnessError("architecture needs unique component headings")
        components = set(names)
    entries = proposal.get("milestones") if isinstance(proposal, dict) else None
    if not isinstance(entries, list) or not entries:
        raise HarnessError("plan requires a nonempty milestones array")
    milestones = {}
    ownership = {}
    covered: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise HarnessError("each milestone must be an object")
        milestone_id = line(entry.get("id"), "milestone id")
        if not re.fullmatch(r"M\d+[a-z]*", milestone_id) or milestone_id in milestones:
            raise HarnessError("milestone ids must be unique M<number><optional suffix>")
        owned = ids(entry.get("requirements"), "requirements")
        if not owned or not set(owned) <= required or set(owned) & ownership.keys():
            raise HarnessError("each known requirement must have exactly one owner")
        advanced = ids(entry.get("architecture"), "architecture")
        if not set(advanced) <= components or (components and not advanced):
            raise HarnessError("milestone architecture must cite known components")
        criteria = entry.get("criteria")
        if not isinstance(criteria, list) or not criteria:
            raise HarnessError("each milestone requires nonempty criteria")
        parsed = []
        for criterion in criteria:
            if not isinstance(criterion, dict):
                raise HarnessError("criterion must be an object")
            criterion_id = line(criterion.get("id"), "criterion id")
            if not re.fullmatch(re.escape(milestone_id) + r"-AC[1-9]\d*", criterion_id):
                raise HarnessError("criterion id must belong to its milestone")
            parsed.append({"id": criterion_id, "text": line(criterion.get("text"), "criterion text"),
                           "status": "PENDING", "evidence": []})
        if any(count > 1 for count in Counter(item["id"] for item in parsed).values()):
            raise HarnessError("duplicate criterion id")
        milestones[milestone_id] = {
            "id": milestone_id, "title": line(entry.get("title"), "title"),
            "outcome": line(entry.get("outcome"), "outcome"),
            "architecture": ", ".join(advanced) or "N/A", "requirements": owned,
            "status": "TODO", "review_cycles": 0, "review_override": None,
            "baseline": {"commit": "", "branch": ""},
            "as_built": {"artifact": None, "result": "PENDING"}, "criteria": parsed,
            "tasks": [], "reviews": [], "findings": [], "validation": [], "follow_ups": [],
        }
        ownership.update({item: milestone_id for item in owned})
        covered.update(advanced)
    if set(ownership) != required:
        raise HarnessError("plan does not cover every in-scope requirement")
    if covered != components:
        raise HarnessError("plan does not cover every architecture component")
    state = {"schema_version": 1, "current_milestone": next(iter(milestones)),
             "requirements": ownership, "milestones": milestones}
    view = "# Milestones\n\n" + "\n".join(_render_split_child(item) for item in milestones.values())
    return state, view


def init_plan(store: StateStore, proposal: dict) -> dict:
    if store.requirements_path is None or store.milestones_path is None:
        raise HarnessError("initial planning requires requirements and milestone paths")
    architecture_path = store.requirements_path.parent / "architecture.md"
    paths = [store.state_path, store.milestones_path, store.requirements_path, architecture_path]
    if len({path.resolve() for path in paths}) != len(paths) or any(path.is_symlink() for path in paths):
        raise HarnessError("planning paths must be distinct and not symlinks")
    marker = store.state_path.with_name(store.state_path.name + ".initializing")
    with _lock_path(store.state_path).open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if marker.exists():
            raise HarnessError("interrupted initialization: inspect and recover the planning pair before removing its .initializing marker")
        state, view = build_plan(proposal, store.requirements_path.read_text(),
                                 architecture_path.read_text() if architecture_path.exists() else None)
        state_text = json.dumps(state, indent=2) + "\n"
        existing = [path.exists() for path in (store.state_path, store.milestones_path)]
        if any(existing):
            if all(existing):
                store.validate()
                if store.load() == state and store.milestones_path.read_text() == view:
                    return {"result": "ALREADY_PLANNED", "changed": False}
            raise HarnessError("existing or partial planning artifacts: preserve them and use documented migration/recovery")
        with tempfile.TemporaryDirectory(prefix="harness-plan-") as directory:
            staging = Path(directory)
            (staging / "state.json").write_text(state_text)
            (staging / "milestones.md").write_text(view)
            StateStore(staging / "state.json", staging / "milestones.md", store.requirements_path).validate()
        try:
            _atomic_write(marker, "Initial planning publication in progress.\n")
            _atomic_write(store.milestones_path, view)
            _atomic_write(store.state_path, state_text)
        except Exception:
            store.state_path.unlink(missing_ok=True)
            store.milestones_path.unlink(missing_ok=True)
            marker.unlink(missing_ok=True)
            raise
        marker.unlink()
        return {"result": "PLANNED", "changed": True, "milestones": len(state["milestones"])}
