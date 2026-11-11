import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from analysis.estimate import contrast,estimate
from runner.agent import complete,run_agent,validate_config
from runner.common import TASK,copy_tree,initialize,tree_digest
from runner.fork_artifact import fork
from runner.freeze_artifact import freeze
from runner.phase_b import phase_b
from evaluator.metrics import invalidation


class Harness(unittest.TestCase):
    def test_request_preserves_verified_gemma_reasoning_settings(self):
        config={'endpoint_url':'http://example.invalid/v1','model':'gemma-test',
                'temperature':.2,'chat_template_kwargs':{'enable_thinking':True},
                'seed_supported':False}
        class Response:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self): return b'{"usage":{"prompt_tokens":63,"completion_tokens":269}}'
        with patch('runner.agent.urllib.request.urlopen',return_value=Response()) as request:
            result=complete(config,[{'role':'user','content':'format check'}],4096,12,30)
        payload=json.loads(request.call_args.args[0].data)
        self.assertEqual(payload['chat_template_kwargs'],{'enable_thinking':True})
        self.assertNotIn('seed',payload)
        self.assertEqual(payload['max_tokens'],4096)
        self.assertEqual(result['usage']['completion_tokens'],269)

    def test_crossed_estimator_and_failure_retention(self):
        values={(a,b):int(a==b) for a in ["B1","B2"] for b in ["B1","B2"]}
        self.assertEqual(contrast(values),1)
        self.assertEqual(contrast({key:1-v for key,v in values.items()}),-1)
        rows=[{"artifact_id":a,"realized_future":b,"disclosed_future":a,"downstream_seed":0,
               "upstream_seed":0,"task_id":"t","task_class":"divergent","data_kind":"study","trajectory_success":bool(v)}
              for (a,b),v in values.items()]
        self.assertEqual(estimate(rows)["equal_weight_tau_ID"],1)
        rows[0]['trajectory_success']=None
        self.assertEqual(estimate(rows)['tasks']['t']['tau_ID_missing_bounds'],[.5,1.0])
        self.assertIsNone(estimate(rows)['equal_weight_tau_ID'])
        rows[0]['trajectory_success']=False
        self.assertEqual(estimate(rows)["equal_weight_tau_ID"],.5)
        with self.assertRaises(ValueError): estimate(rows+rows[:1])

    def test_infrastructure_is_not_study(self):
        row={"data_kind":"infrastructure"}
        with self.assertRaises(ValueError): estimate([row])

    def test_invalidation_tracks_only_upstream_authorship(self):
        with tempfile.TemporaryDirectory() as temp:
            base,a,b=[Path(temp)/name for name in ['base','a','b']]
            for path in [base,a,b]: path.mkdir()
            (base/'file.py').write_text('base\n')
            (a/'file.py').write_text('base\nnew one\nnew two\n')
            (b/'file.py').write_text('base\nnew two\nfuture line\n')
            result=invalidation(base,a,b)
            self.assertEqual(result['authored_lines_at_risk'],2)
            self.assertEqual(result['invalidation'],1)

    def test_fresh_fork_and_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);repo=root/'repo';copy_tree(TASK/'reference_A',repo);initialize(repo)
            metrics={"status":"reference_fixture","tokens":None,"tool_calls":None,"wall_seconds":None}
            with patch('runner.freeze_artifact.run_suite',return_value={'pass':True}),patch('runner.freeze_artifact.evaluate',return_value={}):
                artifact,record=freeze(repo,root/'artifacts','B1',3,metrics)
            fork(artifact,root/'fresh')
            self.assertEqual(tree_digest(root/'fresh'),record['tree_sha256'])
            self.assertFalse((root/'fresh'/'artifact.json').exists())
            (artifact/'repo'/'fusecircuit'/'__init__.py').write_text('changed')
            with self.assertRaises(ValueError): fork(artifact,root/'bad')

    def test_repaired_upstream_failure_stays_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);repo=root/'repo';copy_tree(TASK/'reference_A',repo);initialize(repo)
            metrics={"status":"reference_fixture","tokens":None,"tool_calls":None,"wall_seconds":None}
            leakage={'behavioral_leakage':None}
            with patch('runner.freeze_artifact.run_suite',return_value={'pass':False}),patch('runner.freeze_artifact.evaluate',return_value=leakage):
                artifact,_=freeze(repo,root/'artifacts','B1',0,metrics)
            with patch('runner.phase_b.run_suite',return_value={'pass':True}):
                result=phase_b(root,artifact,'B1',0,{},reference=True)
            self.assertFalse(result['trajectory_success'])
            self.assertTrue(result['B_pass'])

    def test_agent_budget_and_fresh_messages(self):
        config={'endpoint_url':'http://example.invalid/v1','model':'fixture','model_revision':'test',
                'container_image':'fixture@sha256:abc','context_budget_tokens':100000,'max_turns':2,
                'max_generated_tokens':10,'max_output_tokens_per_turn':5,'time_limit_seconds':20,
                'temperature':0,'seed_supported':False}
        class Sandbox:
            def __init__(self,*args): pass
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def run(self,command,timeout): return {'returncode':0,'stdout':'ok','stderr':''}
        snapshots=[]
        def request(config,messages,budget,seed,timeout):
            snapshots.append(json.loads(json.dumps(messages)))
            return {'choices':[{'message':{'content':'{"action":"run_command","command":"ls"}'}}],
                    'usage':{'prompt_tokens':20,'completion_tokens':5}}
        with tempfile.TemporaryDirectory() as temp:
            first=run_agent(Path(temp),'one',config,1,Path(temp)/'one.jsonl',request,Sandbox)
            self.assertEqual(first['tokens'],10)
            self.assertEqual(first['tool_calls'],2)
            run_agent(Path(temp),'two',config,1,Path(temp)/'two.jsonl',request,Sandbox)
            self.assertEqual(len(snapshots[2]),2)
            self.assertNotIn('one',snapshots[2][1]['content'])
        with self.assertRaises(ValueError): validate_config(config,study=True)


if __name__=='__main__': unittest.main()
