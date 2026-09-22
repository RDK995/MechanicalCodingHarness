#!/usr/bin/env python3
"""Mechanical cutover entry, compatibility, inventory, and archive tests."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from harnesslib import HarnessError, StateStore
from harnesslib.execution import advance, execution_status
from harnesslib.planning import init_plan
from harnesslib.runtime import RuntimeManager
from harnesslib.state import _render_split_child

class CutoverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.harness = self.root/'.harness'
        self.harness.mkdir()
        requirements = self.harness/'requirements.md'
        requirements.write_text((ROOT/'examples/requirements.example.md').read_text())
        self.store = StateStore(self.harness/'state.json', self.harness/'milestones.md', requirements)
        init_plan(self.store, json.loads((ROOT/'examples/initial-plan.example.json').read_text()))

    def save(self, state):
        self.store.state_path.write_text(json.dumps(state))
        self.store.milestones_path.write_text('# Milestones\n\n'+'\n'.join(_render_split_child(m) for m in state['milestones'].values()))

    def test_empty_todo_can_start_and_entry_gate_is_read_only(self):
        before = self.store.state_path.read_bytes()
        self.assertEqual(execution_status(self.store)['action'], 'OPEN_PHASE')
        self.assertEqual(before, self.store.state_path.read_bytes())

    def test_legacy_work_is_rejected_for_todo_in_progress_and_review(self):
        original = self.store.load()
        for status in ('TODO','IN_PROGRESS','REVIEW'):
            for legacy_tasks in (True,False):
                with self.subTest(status=status,tasks=legacy_tasks):
                    state = copy.deepcopy(original)
                    m = state['milestones']['M1']; m['status'] = status
                    if legacy_tasks:
                        m['tasks'] = [{'id':'M1-T1', 'routing':{'tier':'Mid','model':'sonnet','reason_code':'ORDINARY_IMPLEMENTATION'}}]
                    else:
                        m['reviews'] = [{'tier':'Mid','diff_range':'base..head','reason_code':'legacy'}]
                    self.save(state)
                    before = self.store.state_path.read_bytes()
                    with self.assertRaisesRegex(HarnessError, 'LEGACY_ORCHESTRATION'):
                        execution_status(self.store)
                    self.assertEqual(before, self.store.state_path.read_bytes())

    def test_incomplete_preflight_resumes_without_resetting_budget(self):
        state = self.store.load(); m = state['milestones']['M1']; m['status'] = 'IN_PROGRESS'
        m['runtime'] = {'preflight':{'status':'CANARY_REQUIRED','nonce':'kept'},'budgets':{'continuation':3},'events':[]}
        self.save(state)
        before = self.store.state_path.read_bytes()
        self.assertEqual(execution_status(self.store)['action'],'COMPLETE_PREFLIGHT')
        self.assertEqual(before, self.store.state_path.read_bytes())

    def test_interruption_after_phase_open_requires_runtime_initialization(self):
        self.store.open_phase('M1', 'baseline', 'm1-test')
        before = self.store.state_path.read_bytes()
        self.assertEqual(execution_status(self.store)['action'], 'INITIALIZE_RUNTIME')
        self.assertEqual(before, self.store.state_path.read_bytes())

    def test_legacy_evidence_without_tasks_still_requires_legacy_completion(self):
        state = self.store.load()
        state['milestones']['M1']['status'] = 'IN_PROGRESS'
        state['milestones']['M1']['validation'] = [{'command': 'historical validation'}]
        self.save(state)
        with self.assertRaisesRegex(HarnessError, 'LEGACY_ORCHESTRATION'):
            execution_status(self.store)

    def test_legacy_review_without_tasks_is_rejected(self):
        state = self.store.load(); state['milestones']['M1']['status'] = 'REVIEW'; self.save(state)
        with self.assertRaisesRegex(HarnessError, 'LEGACY_ORCHESTRATION'): execution_status(self.store)

    def test_missing_current_cannot_hide_incomplete_work(self):
        state = self.store.load(); state['current_milestone'] = None; self.save(state)
        with self.assertRaisesRegex(HarnessError, 'unfinished work'): execution_status(self.store)

    def test_complete_is_terminal_and_preserves_completed_legacy_history(self):
        state = self.store.load(); m = state['milestones']['M1']; m['status'] = 'DONE'
        for criterion in m['criteria']: criterion['status'] = 'PASS'
        m['tasks'] = [{'id':'historical','routing':{'tier':'Mid','model':'sonnet','reason_code':'legacy'}}]
        state['current_milestone'] = None; self.save(state)
        before = self.store.state_path.read_bytes()
        self.assertEqual(execution_status(self.store),{'action':'COMPLETE'})
        self.assertEqual(before,self.store.state_path.read_bytes())

    def test_advance_preserves_history_and_selects_next_todo(self):
        state = self.store.load(); child = copy.deepcopy(state['milestones']['M1'])
        child['id'] = 'M2'; child['requirements'] = ['FR2']; child['criteria'][0]['id'] = 'M2-AC1'
        state['milestones']['M1']['status'] = 'DEFERRED'; state['milestones']['M1']['requirements'] = ['FR1']
        state['milestones']['M2'] = child; state['requirements']['FR2'] = 'M2'; self.save(state)
        old = copy.deepcopy(state['milestones']['M1'])
        self.assertEqual(execution_status(self.store)['action'],'ADVANCE_MILESTONE')
        self.assertEqual(advance(self.store)['action'],'OPEN_PHASE')
        self.assertEqual(self.store.load()['milestones']['M1'],old)
        self.assertEqual(self.store.load()['current_milestone'],'M2')
        with self.assertRaises(HarnessError): advance(self.store)

    def test_navigator_cannot_be_dispatched_even_with_a_historical_budget(self):
        state = self.store.load(); m = state['milestones']['M1']; m['status'] = 'IN_PROGRESS'
        m['runtime'] = {'preflight':{'status':'PASSED'},'budgets':{'navigator':2},'events':[], 'active_dispatch':None}
        self.save(state)
        with self.assertRaises(HarnessError):
            RuntimeManager(self.store).authorize_dispatch('M1','navigator','historical',None)

class InventoryTests(unittest.TestCase):
    def test_active_agents_and_entrypoints(self):
        expected = {'mechanical-controller','mechanical-worker','mechanical-verifier','mechanical-reviewer','mechanical-advisor','as-built','runtime-canary','milestone-planner'}
        self.assertEqual({p.stem for p in (ROOT/'agents').glob('*.md')},expected)
        for name in ('implement','implement-mechanical'):
            text = (ROOT/f'skills/{name}/SKILL.md').read_text()
            self.assertIn('agent: harness:mechanical-controller',text)
            self.assertIn('execution-status',text)
        self.assertIn('agent: harness:milestone-planner',(ROOT/'skills/plan-milestones/SKILL.md').read_text())

    def test_active_plugin_references_resolve(self):
        files = list((ROOT/'agents').rglob('*.md'))+list((ROOT/'skills').rglob('*.md'))
        for path in files:
            for target in re.findall(r'\$\{CLAUDE_PLUGIN_ROOT\}/([A-Za-z0-9_./-]+)',path.read_text()):
                with self.subTest(source=path,target=target): self.assertTrue((ROOT/target.rstrip('.')).exists())
            for name in re.findall(r'harness:([a-z][a-z-]+)',path.read_text()):
                with self.subTest(source=path,name=name):
                    self.assertTrue((ROOT/f'agents/{name}.md').exists() or (ROOT/f'skills/{name}/SKILL.md').exists())

    def test_archive_digest_and_file_manifest(self):
        import tarfile
        manifest=json.loads((ROOT/'.harness-dev/legacy/manifest.json').read_text())
        archive=ROOT/'.harness-dev/legacy/plugin.tar'
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(),manifest['archive_sha256'])
        with tarfile.open(archive) as bundle:
            hashes={m.name:hashlib.sha256(bundle.extractfile(m).read()).hexdigest() for m in bundle.getmembers() if m.isfile()}
        self.assertEqual(hashes,manifest['files'])

if __name__=='__main__': unittest.main()
