"""CPU-only neutral regressions; no model requests or candidate execution."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import urllib.error
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from runner.agent import complete, run_agent, validate_config
from runner.common import TASK, copy_tree, initialize
from runner.freeze_artifact import freeze
from runner.phase_b import phase_b
from tests.test_context_calibration import CONFIG

class DeadlinePolicy(unittest.TestCase):
    def run_case(self, stage='completion', at=20, error=None, legacy=False, cleanup=0,
                 response=None, second_timeout=False, cleanup_error=None):
        clock=[0.0]; calls=[]; requested=[]
        class Sandbox:
            def __init__(self,*args): pass
            def __enter__(self): return self
            def __exit__(self,*args):
                clock[0]+=cleanup
                if cleanup_error is not None: raise cleanup_error
            def run(self,command,timeout):
                calls.append(command)
                if (stage=='readiness' and len(calls)==1) or (stage=='command' and len(calls)>1):
                    clock[0]=at
                    if error is not None: raise error
                return {'returncode':0,'stdout':'neutral','stderr':''}
        def meter(*args):
            if stage=='tokenize':
                clock[0]=at
                if error is not None: raise error
            return {'count':25,'max_model_len':200}
        def request(config,messages,max_tokens,seed,timeout):
            requested.append(timeout)
            if stage=='completion':
                clock[0]=at
                if error is not None and (not second_timeout or len(requested)>1): raise error
                if second_timeout: clock[0]=1
            return {'choices':[{'message':{'content':response or '{"action":"submit_patch"}'},'finish_reason':'stop'}],
                    'usage':{'prompt_tokens':25,'completion_tokens':5}}
        config={**CONFIG}
        if not legacy: config['timeout_policy']='session-deadline-v1'
        with tempfile.TemporaryDirectory() as t, patch('runner.agent.time.monotonic',side_effect=lambda:clock[0]):
            log=Path(t)/'events.jsonl'
            metrics=run_agent(Path(t),'Neutral timeout fixture',config,0,log,request,Sandbox,meter)
            events=[json.loads(line) for line in log.read_text().splitlines()]
        return metrics,events,calls[1:],requested

    def test_direct_completion_timeout_at_deadline_is_budget_stop_with_unknown_usage(self):
        m,e,c,_=self.run_case(error=TimeoutError('secret must not be logged'))
        self.assertEqual(m['status'],'time_budget_exhausted')
        self.assertIsNone(m['tokens']);self.assertIsNone(m['input_tokens'])
        self.assertFalse(m['usage_complete']);self.assertEqual(m['observed_completion_tokens'],0)
        self.assertEqual(m['turns'],0);self.assertEqual(c,[])
        self.assertEqual(next(x for x in e if x['event']=='session_stop')['stage'],'completion')
        self.assertNotIn('secret',json.dumps(e))

    def test_direct_completion_timeout_before_deadline_is_infrastructure(self):
        m,*_=self.run_case(at=19.999,error=TimeoutError())
        self.assertEqual(m['status'],'infrastructure_error');self.assertIsNone(m['tokens'])

    def test_wrapped_socket_timeout_at_deadline_is_budget_stop(self):
        m,*_=self.run_case(error=urllib.error.URLError(TimeoutError()))
        self.assertEqual(m['status'],'time_budget_exhausted');self.assertEqual(m['error_class'],'URLError')

    def test_timeout_text_is_not_socket_timeout(self):
        m,*_=self.run_case(error=urllib.error.URLError('timed out'))
        self.assertEqual(m['status'],'infrastructure_error')

    def test_non_timeout_network_error_at_deadline_stays_missing(self):
        m,*_=self.run_case(error=ConnectionResetError())
        self.assertEqual(m['status'],'infrastructure_error')

    def test_cleanup_cannot_turn_early_transport_error_into_budget_stop(self):
        m,e,*_=self.run_case(at=2,error=TimeoutError(),cleanup=30)
        self.assertEqual(m['status'],'infrastructure_error');self.assertEqual(m['wall_seconds'],32)
        self.assertFalse(next(x for x in e if x['event']=='session_stop')['deadline_elapsed'])

    def test_cleanup_timeout_does_not_inherit_a_prior_operation_failure_time(self):
        m,*_=self.run_case(error=TimeoutError(),cleanup_error=TimeoutError())
        self.assertEqual(m['status'],'infrastructure_error')

    def test_cleanup_timeout_after_valid_response_is_infrastructure(self):
        m,*_=self.run_case(at=1,cleanup=30,cleanup_error=TimeoutError())
        self.assertEqual(m['status'],'infrastructure_error');self.assertTrue(m['usage_complete'])

    def test_tokenizer_timeout_at_deadline_has_no_unknown_completion_usage(self):
        m,e,c,r=self.run_case(stage='tokenize',error=TimeoutError())
        self.assertEqual(m['status'],'time_budget_exhausted');self.assertEqual(m['tokens'],0)
        self.assertTrue(m['usage_complete']);self.assertEqual(r,[]);self.assertEqual(c,[])

    def test_tokenizer_early_timeout_remains_missing(self):
        m,*_=self.run_case(stage='tokenize',at=3,error=urllib.error.URLError(TimeoutError()))
        self.assertEqual(m['status'],'infrastructure_error');self.assertIsNone(m['tokens'])

    def test_late_submit_response_is_logged_and_counted_but_not_submitted(self):
        m,e,c,r=self.run_case(at=20.1)
        self.assertEqual(m['status'],'time_budget_exhausted');self.assertEqual(m['tokens'],5)
        self.assertEqual(m['input_tokens'],25);self.assertTrue(m['usage_complete'])
        self.assertEqual(len([x for x in e if x['event']=='model_response']),1)
        self.assertEqual(c,[]);self.assertEqual(len(r),1)

    def test_late_command_response_cannot_execute(self):
        m,_,c,_=self.run_case(response='{"action":"run_command","command":"MUST_NOT_EXECUTE"}')
        self.assertEqual(m['status'],'time_budget_exhausted');self.assertEqual(c,[])

    def test_returned_usage_before_final_timeout_is_observed_not_complete(self):
        m,_,c,r=self.run_case(error=TimeoutError(),second_timeout=True,
            response='{"action":"run_command","command":"echo neutral"}')
        self.assertEqual(m['status'],'time_budget_exhausted');self.assertIsNone(m['tokens'])
        self.assertEqual(m['observed_completion_tokens'],5);self.assertEqual(m['observed_prompt_tokens'],25)
        self.assertEqual(c,['echo neutral']);self.assertEqual(r,[20,19])

    def test_command_timeout_at_session_deadline_keeps_known_model_usage(self):
        m,*_=self.run_case(stage='command',error=subprocess.TimeoutExpired('fixture',20),
            response='{"action":"run_command","command":"echo neutral"}')
        self.assertEqual(m['status'],'time_budget_exhausted');self.assertEqual(m['tokens'],5)
        self.assertTrue(m['usage_complete'])

    def test_early_command_timeout_is_infrastructure(self):
        m,*_=self.run_case(stage='command',at=3,error=subprocess.TimeoutExpired('fixture',3),
            response='{"action":"run_command","command":"echo neutral"}')
        self.assertEqual(m['status'],'infrastructure_error')

    def test_readiness_deadline_timeout_is_budget_stop(self):
        m,_,c,r=self.run_case(stage='readiness',error=subprocess.TimeoutExpired('fixture',20))
        self.assertEqual(m['status'],'time_budget_exhausted');self.assertEqual(r,[])
        self.assertEqual(c,[]);self.assertTrue(m['usage_complete'])

    def test_late_tokenizer_response_prevents_completion(self):
        m,_,c,r=self.run_case(stage='tokenize',at=20.1)
        self.assertEqual(m['status'],'time_budget_exhausted');self.assertEqual(r,[])
        self.assertEqual(c,[]);self.assertTrue(m['usage_complete'])

    def test_legacy_configuration_retains_historical_timeout_classification(self):
        for exc in (TimeoutError(),urllib.error.URLError(TimeoutError())):
            m,*_=self.run_case(error=exc,legacy=True)
            self.assertEqual(m['status'],'infrastructure_error');self.assertIsNone(m['tokens'])
            self.assertNotIn('timeout_policy',m)
        m,*_=self.run_case(stage='command',at=3,error=subprocess.TimeoutExpired('fixture',3),
            legacy=True,response='{"action":"run_command","command":"echo neutral"}')
        self.assertEqual(m['status'],'time_budget_exhausted')

    def test_unknown_policy_is_rejected(self):
        with self.assertRaises(ValueError):validate_config({**CONFIG,'timeout_policy':'guess'})

    def check_real_http_timeout(self, early):
        release=threading.Event(); requests=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                requests.append(self.path)
                release.wait(5)
                self.close_connection=True
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=lambda:server.serve_forever(poll_interval=.05),daemon=True)
        thread.start()
        class Sandbox:
            def __init__(self,*args): pass
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def run(self,*args): return {'returncode':0,'stdout':'','stderr':''}
        config={**CONFIG,'timeout_policy':'session-deadline-v1',
                'time_limit_seconds':20 if early else 1,
                'endpoint_url':f'http://127.0.0.1:{server.server_port}/v1'}
        def request(config,messages,budget,seed,timeout):
            return complete(config,messages,budget,seed,.05 if early else timeout)
        try:
            with tempfile.TemporaryDirectory() as t:
                metrics=run_agent(Path(t),'Neutral local HTTP fixture',config,0,Path(t)/'log.jsonl',
                    request,Sandbox,lambda *args:{'count':25,'max_model_len':200})
            self.assertEqual(requests,['/v1/chat/completions'])
            self.assertEqual(metrics['status'],'infrastructure_error' if early else 'time_budget_exhausted')
            self.assertFalse(metrics['usage_complete']);self.assertIsNone(metrics['tokens'])
            self.assertEqual(metrics['tool_calls'],0)
        finally:
            release.set();server.shutdown();server.server_close();thread.join(timeout=2)

    def test_real_http_session_deadline(self):
        self.check_real_http_timeout(False)

    def test_real_http_early_socket_timeout(self):
        self.check_real_http_timeout(True)

    def test_deadline_failure_is_false_but_transport_failure_is_missing_in_downstream_record(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);repo=root/'repo';copy_tree(TASK/'reference_A',repo);initialize(repo)
            upstream={'status':'submitted','tokens':5,'input_tokens':25,'tool_calls':0,'wall_seconds':1}
            with patch('runner.freeze_artifact.run_suite',return_value={'pass':True}),patch('runner.freeze_artifact.evaluate',return_value={'behavioral_leakage':None}):
                artifact,_=freeze(repo,root/'artifacts','B1',0,upstream)
            for status,expected in [('time_budget_exhausted',False),('infrastructure_error',None)]:
                metrics={**upstream,'status':status,'tokens':None,'input_tokens':None,
                    'timeout_policy':'session-deadline-v1','usage_complete':False,
                    'observed_completion_tokens':5,'observed_prompt_tokens':25}
                with patch('runner.phase_b.validate_config'),patch('runner.phase_b.run_agent',return_value=metrics),patch('runner.phase_b.grading_environment'),patch('runner.phase_b.run_suite',return_value={'pass':True}):
                    result=phase_b(root,artifact,'B1',0,CONFIG)
                self.assertIs(result['trajectory_success'],expected)
                self.assertIsNone(result['tokens_B']);self.assertTrue(result['B_pass'])
                self.assertFalse(result['usage_complete_B']);self.assertEqual(result['observed_tokens_B'],5)

if __name__=='__main__':unittest.main()
