"""Trusted local worker fixtures and mocked CLI transport; no model calls."""
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from evaluator.contract import run_candidate
from evaluator.correctness import run_suite,run_suite_prospective
from evaluator.grade_policy import (POLICY,GraderCommandTimeout,GraderInfrastructureError,
    decode_candidate_json,validate_config,validate_grade)
from evaluator.grade_worker import Result,classify

CONFIG=dict(grading_policy=POLICY,grader_suite_timeout_seconds=5,grader_command_timeout_seconds=1,grader_capture_max_bytes_per_stream=2097152)


class GradingPolicy(unittest.TestCase):
    def worker(self,body,config=None):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);tests=root/'A/tests';tests.mkdir(parents=True)
            (tests/'test_owned.py').write_text('import unittest,time\nfrom evaluator.grade_policy import GraderCommandTimeout,GraderInfrastructureError\nclass Owned(unittest.TestCase):\n'+body)
            with patch.dict(os.environ,{'FUSE_TEST_CONTAINER':'a'*64}):
                os.environ.pop('FUSE_TEST_CHROOT',None)
                result=run_suite_prospective(root,root,'A',config or CONFIG)
        return result

    def test_trusted_worker_acceptance_has_executed_count_and_limits(self):
        g=self.worker('    def test_ok(self): self.assertEqual(2+2,4)\n')
        self.assertEqual(g['grade_status'],'accepted');self.assertIs(g['pass'],True)
        self.assertEqual(g['tests_run'],1);self.assertTrue(g['grader_process_reaped'])
        self.assertEqual(g['limits']['command_timeout_seconds'],1)

    def test_assertion_failure_is_rejected(self):
        g=self.worker('    def test_bad(self): self.assertEqual(2,3)\n')
        self.assertEqual(g['grade_status'],'rejected');self.assertIs(g['pass'],False)
        self.assertEqual(len(g['assertion_failures']),1);self.assertEqual(g['unresolved_reasons'],[])

    def test_command_watchdog_has_null_grade_and_provenance(self):
        g=self.worker("    def test_timeout(self): raise GraderCommandTimeout('owned',backend='docker',timeout_seconds=1)\n")
        self.assertEqual(g['grade_status'],'unresolved');self.assertIsNone(g['pass'])
        self.assertEqual(g['unresolved_reasons'][0]['reason'],'command_watchdog')
        self.assertEqual(g['unresolved_reasons'][0]['provenance']['timeout_seconds'],1)

    def test_bridge_error_is_unresolved(self):
        g=self.worker("    def test_bridge(self): raise GraderInfrastructureError('owned',reason='bridge_launch')\n")
        self.assertEqual(g['unresolved_reasons'][0]['reason'],'grader_transport_or_bridge');self.assertIsNone(g['pass'])

    def test_unexpected_test_error_remains_unresolved(self):
        g=self.worker("    def test_unexpected(self): raise KeyError('owned')\n")
        self.assertEqual(g['unresolved_reasons'][0]['reason'],'unexpected_grader_error');self.assertIsNone(g['pass'])

    def test_outer_watchdog_retains_log_and_reaps_worker(self):
        g=self.worker("    def test_stall(self):\n        print('owned partial grader output',flush=True)\n        time.sleep(3)\n",{**CONFIG,'grader_suite_timeout_seconds':1})
        self.assertIsNone(g['pass']);self.assertIsNone(g['returncode']);self.assertTrue(g['grader_process_reaped'])
        self.assertEqual(g['unresolved_reasons'][0]['reason'],'suite_watchdog')
        self.assertIn('owned partial grader output',g['stdout'])

    def test_no_tests_and_skipped_suite_cannot_pass(self):
        for body in ('    pass\n',"    @unittest.skip('owned')\n    def test_skip(self): pass\n"):
            with self.subTest(body=body):
                g=self.worker(body);self.assertEqual(g['grade_status'],'unresolved');self.assertIsNone(g['pass'])

    def test_operational_error_stops_later_tests(self):
        g=self.worker("    def test_a(self): raise GraderCommandTimeout('owned')\n    def test_z(self): self.fail('MUST_NOT_RUN')\n")
        self.assertEqual(g['tests_run'],1);self.assertEqual(g['assertion_failures'],[])
        self.assertNotIn('MUST_NOT_RUN',g['stderr'])

    def test_operational_subtest_stops_remaining_candidate_calls(self):
        class Owned(unittest.TestCase):
            def test_a(self):
                for index in range(3):
                    with self.subTest(index=index):
                        executed.append(index)
                        if index==0:raise GraderCommandTimeout('owned')
        executed=[];result=unittest.TextTestRunner(stream=io.StringIO(),verbosity=2,resultclass=Result).run(unittest.TestSuite([Owned('test_a')]))
        self.assertEqual(executed,[0]);self.assertEqual(classify(result)['grade_status'],'unresolved')

    def test_assertion_subtests_are_rejected(self):
        class Owned(unittest.TestCase):
            def test_a(self):
                with self.subTest(value=1):self.assertEqual(1,2)
        result=unittest.TextTestRunner(stream=io.StringIO(),verbosity=2,resultclass=Result).run(unittest.TestSuite([Owned('test_a')]))
        self.assertEqual(classify(result)['grade_status'],'rejected')

    def test_known_assertion_before_unresolved_error_is_preserved(self):
        g=self.worker("    def test_a(self): self.fail('owned assertion')\n    def test_b(self): raise GraderCommandTimeout('owned')\n")
        self.assertIsNone(g['pass']);self.assertEqual(len(g['assertion_failures']),1)
        self.assertEqual(len(g['unresolved_reasons']),1)

    def environment(self,**extra):
        return dict(FUSE_GRADING_POLICY=POLICY,FUSE_GRADER_COMMAND_TIMEOUT_SECONDS='1',FUSE_GRADER_CAPTURE_MAX_BYTES_PER_STREAM='2097152',FUSE_TEST_CONTAINER='a'*64,**extra)

    def bridge(self,payload=None,code=0,error=None):
        with patch.dict(os.environ,self.environment()):
            os.environ.pop('FUSE_TEST_CHROOT',None)
            result=subprocess.CompletedProcess(['owned'],code,json.dumps(payload),'owned bridge stderr')
            with patch('evaluator.contract.subprocess.run',side_effect=error,return_value=result) as call:
                value=run_candidate(['python','-m','owned'],Path('/owned'))
        return value,call

    def test_bridge_distinguishes_candidate_nonzero_from_daemon_failure(self):
        value,call=self.bridge(dict(returncode=7,stdout='',stderr='candidate error',timed_out=False,truncated=False))
        self.assertEqual(value.returncode,7);self.assertEqual(value.stderr,'candidate error')
        with self.assertRaises(GraderInfrastructureError):self.bridge(code=1)
        args=call.call_args.args[0];self.assertEqual(args[:3],['docker','exec','a'*64])
        self.assertEqual(args[3:6],['python','-I','-c']);self.assertEqual(call.call_args.kwargs['timeout'],11)

    def test_bridge_uses_declared_command_timeout_not_call_default(self):
        value,call=self.bridge(dict(returncode=0,stdout='{}',stderr='',timed_out=False,truncated=False))
        self.assertEqual(call.call_args.args[0][-2],'1')

    def test_inner_watchdog_is_typed_with_partial_output(self):
        with self.assertRaises(GraderCommandTimeout) as raised:
            self.bridge(dict(returncode=-9,stdout='partial',stderr='',timed_out=True,truncated=False))
        self.assertEqual(raised.exception.provenance['partial_stdout'],'partial')

    def test_bridge_watchdog_launch_and_envelope_failures_stay_infrastructure(self):
        for error in (subprocess.TimeoutExpired('bridge',11),FileNotFoundError()):
            with self.subTest(error=error),self.assertRaises(GraderInfrastructureError):self.bridge(error=error)
        for payload in (None,{},dict(returncode=True,stdout='',stderr='',timed_out=False)):
            with self.subTest(payload=payload),self.assertRaises(GraderInfrastructureError):self.bridge(payload)

    def test_capture_truncation_is_unresolved_instead_of_invalid_JSON_failure(self):
        with self.assertRaises(GraderInfrastructureError) as raised:
            self.bridge(dict(returncode=0,stdout='{',stderr='',timed_out=False,truncated=True))
        self.assertEqual(raised.exception.provenance['reason'],'candidate_output_capture_limit')

    def test_prospective_host_fallback_is_refused_before_subprocess(self):
        with patch.dict(os.environ,{},clear=True),patch('evaluator.contract.subprocess.run') as call:
            os.environ.update(FUSE_GRADING_POLICY=POLICY,FUSE_GRADER_COMMAND_TIMEOUT_SECONDS='1',FUSE_GRADER_CAPTURE_MAX_BYTES_PER_STREAM='2097152')
            with self.assertRaises(GraderInfrastructureError):run_candidate(['python'],Path('/owned'))
            call.assert_not_called()

    def test_conflicting_sandbox_selectors_refuse(self):
        with patch.dict(os.environ,self.environment(FUSE_TEST_CHROOT='/owned/descriptor')),patch('evaluator.contract.subprocess.run') as call:
            with self.assertRaises(GraderInfrastructureError):run_candidate(['python'],Path('/owned'))
            call.assert_not_called()

    def test_chroot_uses_same_typed_timeout(self):
        env=dict(FUSE_GRADING_POLICY=POLICY,FUSE_GRADER_COMMAND_TIMEOUT_SECONDS='1',FUSE_GRADER_CAPTURE_MAX_BYTES_PER_STREAM='2097152',FUSE_TEST_CHROOT='/owned/descriptor')
        with patch.dict(os.environ,env):
            os.environ.pop('FUSE_TEST_CONTAINER',None)
            result=subprocess.CompletedProcess(['owned'],0,json.dumps(dict(returncode=-9,stdout='partial',stderr='',timed_out=True,truncated=False)),'')
            with patch('evaluator.contract.subprocess.run',return_value=result):
                with self.assertRaises(GraderCommandTimeout) as raised:run_candidate(['python','-m','owned'],Path('/owned'))
            self.assertEqual(raised.exception.provenance['backend'],'chroot-circuit-v0')

    def test_invalid_candidate_JSON_is_assertion_only_for_selected_policy(self):
        with patch.dict(os.environ,{'FUSE_GRADING_POLICY':POLICY}):
            with self.assertRaises(AssertionError):decode_candidate_json('{')
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(json.JSONDecodeError):decode_candidate_json('{')

    def test_legacy_timeout_result_retains_original_false(self):
        with patch('evaluator.correctness.subprocess.run',side_effect=subprocess.TimeoutExpired('legacy',90)):
            result=run_suite(Path('/owned'),Path('/owned'),'A')
        self.assertIs(result['pass'],False);self.assertNotIn('grade_status',result)

    def test_limits_and_status_types_cannot_be_guessed(self):
        for extra in ({'grading_policy':'unknown'},{'grader_command_timeout_seconds':True},
                      {'grader_command_timeout_seconds':0},{'grader_command_timeout_seconds':61},
                      {'grader_suite_timeout_seconds':None}):
            with self.subTest(extra=extra),self.assertRaises(ValueError):validate_config({**CONFIG,**extra})
        with self.assertRaises(ValueError):validate_config({'grader_command_timeout_seconds':5})
        with self.assertRaises(ValueError):validate_grade(dict(policy=POLICY,grade_status='unresolved',**{'pass':False},unresolved_reasons=[]))

    def test_missing_boundary_returns_unresolved_without_worker_launch(self):
        with patch.dict(os.environ,{},clear=True),patch('evaluator.correctness.subprocess.Popen') as call:
            grade=run_suite_prospective(Path('/owned'),Path('/owned'),'A',CONFIG)
            self.assertEqual(grade['unresolved_reasons'][0]['reason'],'sandbox_selection');call.assert_not_called()

    def test_explicit_capture_allowance_is_passed_to_both_bridge_backends(self):
        value,call=self.bridge(dict(returncode=0,stdout='{}',stderr='',timed_out=False,truncated=False))
        self.assertEqual(call.call_args.args[0][-1],'2097152')
        env=self.environment(FUSE_TEST_CHROOT='/owned/descriptor');env.pop('FUSE_TEST_CONTAINER')
        with patch.dict(os.environ,env):
            os.environ.pop('FUSE_TEST_CONTAINER',None)
            payload=dict(returncode=0,stdout='{}',stderr='',timed_out=False,truncated=False)
            with patch('evaluator.contract.subprocess.run',return_value=subprocess.CompletedProcess(['owned'],0,json.dumps(payload),'')) as call:
                run_candidate(['python','-m','owned'],Path('/owned'))
                self.assertEqual(call.call_args.args[0][-1],'2097152')

    def test_chroot_capture_default_and_explicit_limit_use_bounded_drain(self):
        from runner.chroot_linux import ChrootCandidate
        from unittest.mock import MagicMock
        backend=object.__new__(ChrootCandidate);backend.root=Path('/owned');backend.uid=12345;backend.launcher=Path('/owned/launcher')
        for cap in (None,50000):
            p=MagicMock(stdout=io.BytesIO(b'x'*40000),stderr=io.BytesIO(),returncode=0,pid=12345)
            with patch('runner.chroot_linux.subprocess.Popen',return_value=p),patch('runner.chroot_linux.os.killpg'),patch('runner.chroot_linux.kill_uid'):
                result=backend.run('owned',1) if cap is None else backend.run('owned',1,output_max_bytes=cap)
            self.assertEqual(len(result['stdout']),20000 if cap is None else 40000)
            self.assertEqual(result['truncated'],cap is None)
        for cap in (True,0,16*1024**2+1):
            with patch('runner.chroot_linux.subprocess.Popen') as spawn,self.assertRaises(ValueError):
                backend.run('owned',1,output_max_bytes=cap)
            spawn.assert_not_called()

    def test_large_partial_watchdog_output_keeps_typed_worker_provenance(self):
        g=self.worker("    def test_timeout(self): raise GraderCommandTimeout('owned',partial_stdout='x'*1200000)\n")
        self.assertEqual(g['unresolved_reasons'][0]['reason'],'command_watchdog')
        self.assertEqual(len(g['unresolved_reasons'][0]['provenance']['partial_stdout']),1200000)
        self.assertFalse(g['worker_report_capture_truncated'])


if __name__=='__main__':unittest.main()
