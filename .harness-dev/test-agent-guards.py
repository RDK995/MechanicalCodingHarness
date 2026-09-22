#!/usr/bin/env python3
"""Static contract tests for bounded harness subagents."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIMITS = {
    "milestone-planner.md": 24,
    "mechanical-controller.md": 22,
    "mechanical-worker.md": 32,
    "mechanical-verifier.md": 24,
    "mechanical-reviewer.md": 42,
    "mechanical-advisor.md": 12,
    "runtime-canary.md": 2,
    "as-built.md": 30,
}


def frontmatter(path: Path) -> dict[str, str]:
    match = re.match(r"---\n(.*?)\n---\n", path.read_text(), re.DOTALL)
    if not match:
        raise AssertionError(f"missing frontmatter: {path}")
    result = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip()
    return result


class AgentGuardTests(unittest.TestCase):
    def test_every_subagent_has_a_hard_cap_and_is_foreground(self):
        for filename, expected in LIMITS.items():
            with self.subTest(agent=filename):
                metadata = frontmatter(ROOT / "agents" / filename)
                self.assertEqual(int(metadata["maxTurns"]), expected)
                self.assertEqual(metadata["background"], "false")

    def test_soft_handoffs_precede_hard_caps(self):
        expected_phrases = {
            "milestone-planner.md": "tool turn 19",
            "mechanical-controller.md": "tool turn 18",
            "mechanical-worker.md": "tool turn 26",
            "mechanical-verifier.md": "tool turn 19",
            "mechanical-reviewer.md": "tool turn 34",
            "mechanical-advisor.md": "tool turn 9",
            "as-built.md": "tool turn 25",
        }
        for filename, phrase in expected_phrases.items():
            with self.subTest(agent=filename):
                self.assertIn(phrase, (ROOT / "agents" / filename).read_text())

    def test_controller_treats_missing_contract_as_interrupted(self):
        skill = (ROOT / "skills" / "implement" / "SKILL.md").read_text()
        self.assertIn("missing terminal field", skill)
        self.assertIn("INTERRUPTED", skill)


if __name__ == "__main__":
    unittest.main()
