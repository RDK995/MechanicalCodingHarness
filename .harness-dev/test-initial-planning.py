#!/usr/bin/env python3
"""Behavioural tests for initial plan publication and failure recovery."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from harnesslib import HarnessError, StateStore
from harnesslib.planning import init_plan
from harnesslib.state import _atomic_write

class InitialPlanningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.harness = self.root / '.harness'
        self.harness.mkdir()
        self.requirements = self.harness / 'requirements.md'
        self.requirements.write_text((ROOT / 'examples/requirements.example.md').read_text())
        self.store = StateStore(self.harness / 'state.json', self.harness / 'milestones.md', self.requirements)
        self.plan = json.loads((ROOT / 'examples/initial-plan.example.json').read_text())
        self.marker = self.harness / 'state.json.initializing'

    def publish(self):
        return init_plan(self.store, self.plan)

    def assert_absent(self):
        self.assertFalse(self.store.state_path.exists())
        self.assertFalse(self.store.milestones_path.exists())

    def test_valid_initialization_and_identical_replay(self):
        self.assertEqual(self.publish()['result'], 'PLANNED')
        self.store.validate()
        state = self.store.load()
        self.assertEqual(state['requirements'], {'FR1':'M1','FR2':'M1'})
        self.assertEqual(state['milestones']['M1']['tasks'], [])
        before = [p.read_bytes() for p in (self.store.state_path, self.store.milestones_path)]
        self.assertEqual(self.publish()['result'], 'ALREADY_PLANNED')
        self.assertEqual(before, [p.read_bytes() for p in (self.store.state_path, self.store.milestones_path)])

    def test_conflicting_existing_plan_is_preserved(self):
        self.publish()
        before = self.store.state_path.read_bytes()
        self.plan['milestones'][0]['outcome'] = 'A conflicting proposal'
        with self.assertRaisesRegex(HarnessError, 'existing'):
            self.publish()
        self.assertEqual(before, self.store.state_path.read_bytes())

    def test_invalid_proposals_never_publish(self):
        changes = [
            ('requirements', ['FR1']), ('requirements', ['FR1','FR3']),
            ('requirements', ['FR1','FR1','FR2']), ('criteria', []),
            ('architecture', ['C1']), ('title', 'Injected\n## M2 — title'),
            ('id', 'bad'), ('criteria', [{'id':'M2-AC1','text':'wrong owner'}]),
        ]
        original = copy.deepcopy(self.plan)
        for key,value in changes:
            with self.subTest(key=key,value=value):
                self.plan = copy.deepcopy(original)
                self.plan['milestones'][0][key] = value
                with self.assertRaises(HarnessError): self.publish()
                self.assert_absent()

    def test_duplicate_milestone_and_criterion_rejected(self):
        for field in ('milestones', 'criteria'):
            with self.subTest(field=field):
                plan = copy.deepcopy(self.plan)
                entries = plan['milestones'] if field == 'milestones' else plan['milestones'][0]['criteria']
                entries.append(copy.deepcopy(entries[0]))
                with self.assertRaises(HarnessError): init_plan(self.store, plan)
                self.assert_absent()

    def test_architecture_agreement_and_coverage(self):
        architecture = self.harness / 'architecture.md'
        architecture.write_text((ROOT / 'examples/architecture.example.md').read_text())
        self.plan['milestones'][0]['architecture'] = ['C1']
        with self.assertRaisesRegex(HarnessError, 'every architecture'): self.publish()
        self.plan['milestones'][0]['architecture'] = ['C1','C2']
        original = architecture.read_text()
        architecture.write_text(original.replace('AGREED', 'DRAFT'))
        with self.assertRaisesRegex(HarnessError, 'AGREED'): self.publish()
        self.assert_absent()
        architecture.write_text(original)
        self.publish()
        self.store.validate()

    def test_unresolved_requirements_are_rejected(self):
        self.requirements.write_text(self.requirements.read_text().replace('\nNone\n', '\nWhich error?\n'))
        with self.assertRaisesRegex(HarnessError, 'open questions'): self.publish()
        self.assert_absent()

    def test_partial_pair_is_preserved(self):
        self.store.milestones_path.write_text('existing data')
        with self.assertRaisesRegex(HarnessError, 'partial'): self.publish()
        self.assertEqual(self.store.milestones_path.read_text(), 'existing data')
        self.assertFalse(self.store.state_path.exists())

    def test_write_failure_rolls_back_both_files(self):
        def failing(path, text):
            if path == self.store.state_path: raise OSError('disk failure')
            _atomic_write(path, text)
        with patch('harnesslib.planning._atomic_write', side_effect=failing):
            with self.assertRaises(OSError): self.publish()
        self.assert_absent()
        self.assertFalse(self.marker.exists())

    def test_crash_marker_blocks_status_and_retry(self):
        def crash(path, text):
            if path == self.store.state_path: raise KeyboardInterrupt()
            _atomic_write(path, text)
        with patch('harnesslib.planning._atomic_write', side_effect=crash):
            with self.assertRaises(KeyboardInterrupt): self.publish()
        self.assertTrue(self.marker.exists())
        self.assertTrue(self.store.milestones_path.exists())
        with self.assertRaisesRegex(HarnessError, 'interrupted initialization'): self.publish()
        with self.assertRaisesRegex(HarnessError, 'interrupted initialization'): self.store.validate()

    def test_cli_concurrent_conflicting_plans_publish_only_one(self):
        processes = []
        for number in range(2):
            plan = copy.deepcopy(self.plan)
            plan['milestones'][0]['title'] = f'Candidate {number}'
            path = self.root / f'plan-{number}.json'
            path.write_text(json.dumps(plan))
            processes.append(subprocess.Popen([sys.executable, str(ROOT/'scripts/harnessctl.py'),
                'init-plan', '--plan', str(path)], cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        results = [(p.communicate(),p.returncode) for p in processes]
        self.assertEqual(sorted(code for _,code in results), [0,1])
        self.store.validate()
        self.assertFalse(self.marker.exists())

    def test_aliased_inputs_are_rejected(self):
        self.store.milestones_path = self.requirements
        with self.assertRaisesRegex(HarnessError, 'distinct'): self.publish()
        self.assertTrue(self.requirements.read_text().startswith('# Requirements'))

if __name__ == '__main__': unittest.main()
