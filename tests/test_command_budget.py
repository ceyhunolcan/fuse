"""Neutral watchdog regression cases; fixed responses are not model evidence."""
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from runner.agent import run_agent, validate_config, action_contract
from runner.command_budget import POLICY, CommandBudgetExpired, capped_timeout, receipt
from runner.chroot_sandbox import ChrootSandbox
from runner.sandbox import DockerSandbox, SandboxCleanupError
from analysis.model_usage import stream_usage
from tests.test_context_calibration import CONFIG


class CommandBudgets(unittest.TestCase):
    def config(self, **extra):
        return {**CONFIG,'timeout_policy':'session-deadline-v1',
                'command_budget_policy':POLICY,'max_command_seconds':2,**extra}

    def case(self, error='budget', backend='docker', remaining=20, cleanup=0,
             cleanup_error=None, readiness=False, enter_time=0, legacy=False):
        clock=[0.0];waits=[];requests=[]
        cfg=self.config(sandbox_backend=backend)
        if legacy:
            cfg.pop('command_budget_policy');cfg.pop('max_command_seconds')
        class Sandbox:
            def __init__(self,*args):pass
            def __enter__(self):clock[0]=enter_time;return self
            def __exit__(self,*args):
                clock[0]+=cleanup
                if cleanup_error:raise cleanup_error
            def run(self,command,timeout):
                waits.append(timeout)
                if len(waits)==1 and not readiness:return dict(returncode=0,stdout='',stderr='')
                if error=='budget':
                    clock[0]+=timeout
                    raise CommandBudgetExpired(command,timeout,backend)
                if error=='wrong-budget':raise CommandBudgetExpired(command,timeout+1,backend)
                if error=='wrong-backend':raise CommandBudgetExpired(command,timeout,'wrong')
                if isinstance(error,Exception):raise error
                return dict(returncode=0,stdout='neutral',stderr='',timed_out=False)
        def response(*args):
            requests.append(1);clock[0]=20-remaining
            return dict(choices=[dict(message=dict(content='{"action":"run_command","command":"echo neutral"}'),
                                      finish_reason='stop')],usage=dict(prompt_tokens=25,completion_tokens=5))
        with tempfile.TemporaryDirectory() as folder,patch('runner.agent.validate_config'), \
                patch('runner.agent.time.monotonic',side_effect=lambda:clock[0]):
            log=Path(folder)/'events.jsonl'
            metrics=run_agent(Path(folder),'neutral budget fixture',cfg,0,log,response,Sandbox,
                              lambda *args:dict(count=25,max_model_len=200))
            raw=log.read_bytes();events=[json.loads(x) for x in raw.splitlines()]
        return metrics,events,waits,raw,requests

    def test_command_cap_expiry_ends_session_and_preserves_known_usage(self):
        m,e,w,raw,r=self.case()
        self.assertEqual(m['status'],'command_budget_exhausted')
        self.assertEqual(w,[20,2]);self.assertEqual(r,[1])
        self.assertEqual((m['tokens'],m['input_tokens'],m['tool_calls']),(5,25,1))
        self.assertTrue(m['usage_complete'])
        b=next(x for x in e if x['event']=='command_budget')
        self.assertEqual(b['limiting_budget'],'command');self.assertEqual(b['effective_timeout_seconds'],2)
        t=next(x for x in e if x['event']=='tool_call')['output']
        self.assertIsNone(t['returncode']);self.assertTrue(t['timed_out'])
        self.assertIsNone(t['stdout']);self.assertFalse(t['output_capture_available'])
        self.assertEqual(t['outcome'],'command_watchdog_expired')
        self.assertEqual(stream_usage(raw)['original_terminal_metrics'],m)

    def test_session_is_limiting_budget_when_remaining_is_shorter(self):
        m,e,w,*_=self.case(remaining=1)
        self.assertEqual(m['status'],'time_budget_exhausted');self.assertEqual(w,[20,1])
        self.assertEqual(next(x for x in e if x['event']=='command_budget')['limiting_budget'],'session')
        self.assertEqual(m['tokens'],5)

    def test_tied_cap_is_session_budget_expiry(self):
        m,*_=self.case(remaining=2)
        self.assertEqual(m['status'],'time_budget_exhausted')

    def test_chroot_marker_gets_same_prospective_classification(self):
        m,*_=self.case(backend='chroot-circuit-v0')
        self.assertEqual(m['status'],'command_budget_exhausted');self.assertEqual(m['tokens'],5)

    def test_unmarked_early_subprocess_timeout_stays_infrastructure(self):
        m,e,*_=self.case(error=subprocess.TimeoutExpired('transport',.1),cleanup=30)
        self.assertEqual(m['status'],'infrastructure_error');self.assertIsNone(m['tokens'])
        self.assertFalse(next(x for x in e if x['event']=='session_stop')['deadline_elapsed'])
        self.assertFalse(any(x['event']=='tool_call' for x in e))

    def test_network_disconnect_is_never_command_budget_expiry(self):
        m,*_=self.case(error=ConnectionResetError())
        self.assertEqual(m['status'],'infrastructure_error');self.assertIsNone(m['tokens'])

    def test_setup_watchdog_is_not_agent_command_cap_failure(self):
        m,e,w,raw,r=self.case(readiness=True)
        self.assertEqual(m['status'],'time_budget_exhausted');self.assertEqual(r,[])
        self.assertFalse(any(x['event']=='command_budget' for x in e))
        self.assertEqual(m['tool_calls'],0)

    def test_setup_under_launch_minimum_stops_without_command_or_response(self):
        m,e,w,raw,r=self.case(enter_time=19.95)
        self.assertEqual(m['status'],'time_budget_exhausted');self.assertEqual(w,[]);self.assertEqual(r,[])
        self.assertEqual(m['error_class'],'SessionLaunchBudgetExpired')

    def test_under_launch_minimum_after_response_never_invokes_candidate(self):
        m,e,w,*_=self.case(remaining=.05)
        self.assertEqual(m['status'],'time_budget_exhausted');self.assertEqual(len(w),1)
        self.assertEqual(m['tool_calls'],0);self.assertEqual(m['tokens'],5)

    def test_cleanup_duration_cannot_change_command_to_session_expiry(self):
        m,e,*_=self.case(cleanup=30)
        self.assertEqual(m['status'],'command_budget_exhausted');self.assertEqual(m['wall_seconds'],32)
        self.assertFalse(next(x for x in e if x['event']=='session_stop')['deadline_elapsed'])

    def test_cleanup_error_replaces_budget_outcome_with_infrastructure(self):
        m,*_=self.case(cleanup_error=TimeoutError(),cleanup=30)
        self.assertEqual(m['status'],'infrastructure_error');self.assertIsNone(m['tokens'])

    def test_unresolved_cleanup_prevents_any_terminal_metrics(self):
        with self.assertRaises(SandboxCleanupError):self.case(cleanup_error=SandboxCleanupError())

    def test_mismatched_budget_marker_is_refused_as_infrastructure(self):
        for error in ('wrong-budget','wrong-backend'):
            with self.subTest(error=error):
                m,e,*_=self.case(error=error)
                self.assertEqual(m['status'],'infrastructure_error')
                self.assertFalse(any(x['event']=='tool_call' for x in e))

    def test_missing_opt_in_preserves_previous_early_timeout_classification(self):
        m,e,w,*_=self.case(error=CommandBudgetExpired('fixture',2,'docker'),legacy=True)
        self.assertEqual(m['status'],'infrastructure_error')
        self.assertFalse(any(x['event']=='command_budget' for x in e))

    def test_unknown_policy_missing_cap_or_excess_cap_is_rejected(self):
        for extra in ({'command_budget_policy':'unknown'},{'max_command_seconds':None},
                      {'max_command_seconds':61},{'max_command_seconds':True},
                      {'max_command_seconds':.1},{'max_command_seconds':0},
                      {'timeout_policy':'legacy-v0'}):
            with self.subTest(extra=extra),self.assertRaises(ValueError):validate_config(self.config(**extra))
        with self.assertRaises(ValueError):validate_config({**CONFIG,'max_command_seconds':60})

    def test_valid_policy_and_visible_cap(self):
        cfg=self.config(max_command_seconds=60);validate_config(cfg)
        self.assertIn('60-second watchdog',action_contract(cfg))
        self.assertIn('Expiry ends this session',action_contract(cfg))

    def test_cap_math_preserves_fractional_session_remainder_and_ceiling(self):
        self.assertEqual(capped_timeout(900),60);self.assertEqual(capped_timeout(.05),.05)
        self.assertEqual(receipt(900,60)['effective_timeout_seconds'],60)
        self.assertEqual(receipt(.25,60)['limiting_budget'],'session')

    def test_invalid_wait_budget_rejected_before_launch(self):
        for value in (0,-1,float('nan'),float('inf'),True,'60'):
            with self.subTest(value=value),self.assertRaises(ValueError):capped_timeout(value)

    def test_docker_receives_ceiling_instead_of_session_allowance(self):
        with tempfile.TemporaryDirectory() as folder:
            s=DockerSandbox(folder,CONFIG['container_image']);s.container='a'*64
            p=MagicMock(stdout=io.BytesIO(),stderr=io.BytesIO(),returncode=0)
            with patch('runner.sandbox.subprocess.Popen',return_value=p):s.run('neutral',900)
            p.wait.assert_called_once_with(timeout=60)

    def test_docker_watchdog_marker_only_after_owned_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            s=DockerSandbox(folder,CONFIG['container_image']);s.container='a'*64
            p=MagicMock(stdout=io.BytesIO(),stderr=io.BytesIO())
            p.wait.side_effect=[subprocess.TimeoutExpired('neutral',2),0]
            with patch('runner.sandbox.subprocess.Popen',return_value=p),patch.object(s,'_cleanup') as clean:
                with self.assertRaises(CommandBudgetExpired) as raised:s.run('neutral',2)
            clean.assert_called_once();self.assertEqual(raised.exception.timeout,2)
            self.assertEqual(raised.exception.backend,'docker')

    def test_docker_retains_bounded_output_from_expired_command(self):
        with tempfile.TemporaryDirectory() as folder:
            s=DockerSandbox(folder,CONFIG['container_image']);s.container='a'*64
            p=MagicMock(stdout=io.BytesIO(b'owned stdout'*3000),stderr=io.BytesIO(b'owned stderr'))
            p.wait.side_effect=[subprocess.TimeoutExpired('neutral',2),0]
            with patch('runner.sandbox.subprocess.Popen',return_value=p),patch.object(s,'_cleanup'):
                with self.assertRaises(CommandBudgetExpired) as raised:s.run('neutral',2)
            result=raised.exception.captured_result
            self.assertEqual(len(result['stdout']),20000);self.assertTrue(result['truncated'])
            self.assertEqual(result['stderr'],'owned stderr');self.assertTrue(result['output_capture_complete'])

    def test_docker_cleanup_uncertainty_cannot_emit_budget_marker(self):
        with tempfile.TemporaryDirectory() as folder:
            s=DockerSandbox(folder,CONFIG['container_image']);s.container='a'*64
            p=MagicMock(stdout=io.BytesIO(),stderr=io.BytesIO())
            p.wait.side_effect=[subprocess.TimeoutExpired('neutral',2),0]
            with patch('runner.sandbox.subprocess.Popen',return_value=p), \
                    patch.object(s,'_cleanup',side_effect=SandboxCleanupError()):
                with self.assertRaises(SandboxCleanupError):s.run('neutral',2)

    def chroot(self, prospective=True):
        s=object.__new__(ChrootSandbox);s.config=self.config() if prospective else CONFIG
        s.backend=MagicMock();s.backend.run.return_value=dict(returncode=-9,timed_out=True)
        return s

    def test_chroot_wrapper_normalizes_returned_timeout_and_caps(self):
        s=self.chroot()
        s.backend.run.return_value.update(stdout='owned partial',stderr='',truncated=False)
        with self.assertRaises(CommandBudgetExpired) as raised:s.run('neutral',900)
        s.backend.run.assert_called_once_with('neutral',60)
        self.assertEqual(raised.exception.timeout,60);self.assertEqual(raised.exception.backend,'chroot-circuit-v0')
        self.assertEqual(raised.exception.captured_result['stdout'],'owned partial')

    def test_chroot_without_policy_retains_original_returned_timeout(self):
        s=self.chroot(False);self.assertTrue(s.run('neutral',900)['timed_out'])
        s.backend.run.assert_called_once_with('neutral',60)

    def test_chroot_cannot_round_short_remaining_time_up_and_execute(self):
        s=self.chroot()
        with self.assertRaises(ValueError):s.run('neutral',.05)
        s.backend.run.assert_not_called()

    def test_chroot_non_timeout_result_passes_through(self):
        s=self.chroot();s.backend.run.return_value=dict(returncode=0,timed_out=False)
        self.assertEqual(s.run('neutral',2),dict(returncode=0,timed_out=False))

    def test_downstream_budget_failure_is_false_and_transport_failure_is_missing(self):
        from runner.common import TASK, copy_tree, initialize
        from runner.freeze_artifact import freeze
        from runner.phase_b import phase_b
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);repo=root/'repo';copy_tree(TASK/'reference_A',repo);initialize(repo)
            upstream=dict(status='submitted',tokens=5,input_tokens=25,tool_calls=0,wall_seconds=1)
            with patch('runner.freeze_artifact.run_suite',return_value={'pass':True}), \
                    patch('runner.freeze_artifact.evaluate',return_value={'behavioral_leakage':None}):
                artifact,_=freeze(repo,root/'artifacts','B1',0,upstream)
            for status,expected in [('command_budget_exhausted',False),('infrastructure_error',None)]:
                metrics={**upstream,'status':status,'command_budget_policy':POLICY,'max_command_seconds':2}
                with patch('runner.phase_b.validate_config'), \
                        patch('runner.phase_b.run_agent',return_value=metrics), \
                        patch('runner.phase_b.grading_environment'), \
                        patch('runner.phase_b.run_suite',return_value={'pass':True}):
                    result=phase_b(root,artifact,'B1',0,self.config())
                self.assertIs(result['trajectory_success'],expected)
                self.assertTrue(result['B_pass'])


if __name__=='__main__':unittest.main()
