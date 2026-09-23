#!/usr/bin/env python3
"""Regression checks for current-scope model routing."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_checker():
    spec = importlib.util.spec_from_file_location("check_state", ROOT / "scripts/check-state.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ModelRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checker = load_checker()

    def test_top_task_without_detail_is_rejected(self):
        state = json.loads((ROOT / "examples/state.example.json").read_text())
        state["milestones"]["M1"]["tasks"] = [{"id": "M1-T1", "routing": {
            "tier": "Top", "model": "opus", "reason_code": "DIFFICULT_CONCURRENCY"
        }}]
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / ".harness/state.json"
            state_path.parent.mkdir()
            state_path.write_text(json.dumps(state))
            errors = self.checker.validate(state, state_path, None, False, None)
        self.assertTrue(any("Top routing requires a named detail" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
