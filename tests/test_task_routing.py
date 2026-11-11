import json
from pathlib import Path
import tempfile
import unittest
import hashlib
from unittest.mock import patch
from runner.cli import smoke,validate
from runner.common import ROOT,resolve_task,save_json
from runner.phase_a import phase_a

TASKS=['textdrop_001','codesearch_001']
class TaskRouting(unittest.TestCase):
    def test_unknown_and_path_traversal_task_is_rejected(self):
        for task_id in ['../circuit_001','circuit_001/../../','missing_001',None]:
            with self.assertRaises(ValueError):resolve_task(task_id)

    def test_released_tasks_reach_the_model_config_check(self):
        # The release lifts the development gate for the three study tasks only;
        # an empty config must then fail validation before any model request.
        with tempfile.TemporaryDirectory() as t,patch('runner.phase_a.run_agent') as request:
            for task_id in TASKS:
                with self.assertRaisesRegex(ValueError,'configure before model execution'):
                    phase_a(Path(t),'B1',0,{},task_id=task_id)
            request.assert_not_called()

    def test_reference_crossovers_route_specs_histories_probes_and_records(self):
        # This installs trusted references; 16 successes are plumbing fixtures,
        # never model or treatment-effect evidence.
        import subprocess
        records=[]
        def source_bytes():
            return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in ROOT.rglob('*') if p.is_file() and
                    not set(p.relative_to(ROOT).parts)&{'__pycache__','.git','.pytest_cache'}}
        before=source_bytes()
        with tempfile.TemporaryDirectory() as t:
            destination=Path(t)/'fixture-evidence'
            for task_id in TASKS:
                output=Path(t)/task_id
                summary=smoke(output,{},reference=True,task_id=task_id,export_parquet=False)
                upstream=[json.loads(l) for l in (output/'raw/upstream.jsonl').read_text().splitlines()]
                rows=[json.loads(l) for l in (output/'raw/results.jsonl').read_text().splitlines()]
                attempts=[json.loads(l) for l in (output/'raw/attempts.jsonl').read_text().splitlines()]
                self.assertEqual(len(upstream),2);self.assertEqual(len(rows),4);self.assertEqual(len(attempts),4)
                self.assertEqual({(r['disclosed_future'],r['realized_future']) for r in rows},{(a,b) for a in ('B1','B2') for b in ('B1','B2')})
                self.assertTrue(all(r['task_id']==task_id and r['data_kind']=='infrastructure' and r['task_class']=='unvalidated' for r in rows))
                self.assertTrue(all(r['trajectory_success'] is True and r['scope_trajectory_success'] is None for r in rows))
                self.assertTrue(all(u['leakage']['scope_clean'] is None and u['leakage']['behavioral_observed']=={'B1':False,'B2':False} for u in upstream))
                for log in output.glob('sessions/*/prompt.txt'):
                    text=log.read_text();self.assertIn((resolve_task(task_id)/'A/requirement.md').read_text().strip(),text)
                for repo in output.glob('branches/*/repo'):
                    subjects=subprocess.check_output(['git','log','--format=%s'],cwd=repo,text=True).splitlines()
                    self.assertEqual(subjects,['Initial repository'])
                    self.assertFalse(any((repo/name).exists() for name in ['A','B1','B2','events.jsonl','assignment.json']))
                records.append({'task_id':task_id,'upstream_artifacts':2,'downstream_records':4,'all_reference_routes_passed':True})
                # Retain only trusted fixture JSON evidence; no candidate copies needed.
                save_json(destination/task_id/'fixture_summary.json',{'kind':'reference_fixture_not_model_evidence','summary':summary,'upstream':upstream,'rows':rows,'attempts':attempts})
            save_json(destination/'summary.json',{'passed':True,'task_checks':records,'model_requests':0,'formal_study_runs':0})
        self.assertEqual(source_bytes(),before,'routing tests must not change running source bytes or inventory')

if __name__=='__main__':unittest.main()
