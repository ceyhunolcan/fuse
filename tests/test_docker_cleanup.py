"""Connection-loss cleanup checks use owned IDs and mocked Docker CLI calls."""
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from runner.sandbox import DockerSandbox, SandboxCleanupError, confirm_container_absent

ID='a'*64
IMAGE='python@sha256:'+'1'*64

def result(code=0, stdout='', stderr=''):
    return subprocess.CompletedProcess(['mock-docker'],code,stdout,stderr)

def absent(identifier=ID):
    return result(1,stderr='Error response from daemon: No such container: '+identifier+'\n')


class DockerCleanup(unittest.TestCase):
    def backend(self, folder):
        backend=DockerSandbox(Path(folder),IMAGE)
        backend.container=ID
        return backend

    def test_known_missing_container_confirms_absence_with_case_variants(self):
        for text in ('Error: No such object: '+ID,'error: no such container: '+ID,
                     'Error response from daemon: No such container: '+ID):
            with self.subTest(text=text),patch('runner.sandbox.subprocess.run',return_value=result(1,stderr=text)):
                self.assertTrue(confirm_container_absent(ID)['absence_confirmed'])

    def test_daemon_disconnect_or_wrong_identifier_is_not_absence(self):
        for reply in (result(1,stderr='Cannot connect to the Docker daemon'),absent('b'*64),
                      result(0,stdout='existing'),result(125,stderr='No such object: '+ID)):
            with self.subTest(reply=reply),patch('runner.sandbox.subprocess.run',return_value=reply):
                with self.assertRaises(SandboxCleanupError):confirm_container_absent(ID)

    def test_inspection_timeout_or_launch_error_remains_unresolved(self):
        for error in (subprocess.TimeoutExpired(['docker'],10),FileNotFoundError('mock')):
            with self.subTest(error=type(error).__name__),patch('runner.sandbox.subprocess.run',side_effect=error):
                with self.assertRaises(SandboxCleanupError):confirm_container_absent(ID)

    def test_unowned_identifier_is_refused_before_any_cli_call(self):
        with patch('runner.sandbox.subprocess.run') as call:
            with self.assertRaises(ValueError):confirm_container_absent('--all')
            call.assert_not_called()

    def test_successful_removal_still_requires_confirmed_absence(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=self.backend(folder)
            with patch('runner.sandbox.subprocess.run',side_effect=[result(),result(1,stderr='connection lost')]):
                with self.assertRaises(SandboxCleanupError):backend.__exit__(None,None,None)
            self.assertFalse(backend.cleanup_verified)
            self.assertEqual(backend.container,ID)

    def test_failed_removal_with_disconnected_inspection_blocks_export(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=self.backend(folder)
            with patch('runner.sandbox.subprocess.run',side_effect=[result(1,stderr='connection lost'),result(1,stderr='connection lost')]):
                with self.assertRaises(SandboxCleanupError):backend.__exit__(None,None,None)
            self.assertFalse(backend.cleanup_verified)
            self.assertIsNone(backend.cleanup_report)

    def test_already_absent_container_can_resolve_failed_removal(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=self.backend(folder)
            with patch('runner.sandbox.subprocess.run',side_effect=[result(1),absent()]):
                backend.__exit__(None,None,None)
            self.assertTrue(backend.cleanup_verified)
            self.assertEqual(backend.cleanup_report['removal_returncode'],1)

    def test_creation_timeout_cleans_preassigned_name_and_preserves_timeout(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=DockerSandbox(Path(folder),IMAGE)
            error=subprocess.TimeoutExpired(['docker','run'],30)
            with patch('runner.sandbox.subprocess.run',side_effect=[error,result(),absent(backend.name)]) as call:
                with self.assertRaises(subprocess.TimeoutExpired):backend.__enter__()
            self.assertEqual(call.call_args_list[1].args[0],['docker','rm','-f',backend.name])
            self.assertTrue(backend.cleanup_verified)
            self.assertIsNone(backend.container)

    def test_creation_disconnect_with_unresolved_cleanup_retains_primary_cause(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=DockerSandbox(Path(folder),IMAGE)
            error=subprocess.TimeoutExpired(['docker','run'],30)
            with patch('runner.sandbox.subprocess.run',side_effect=[error,result(1),result(1,stderr='connection lost')]):
                with self.assertRaises(SandboxCleanupError) as raised:backend.__enter__()
            self.assertIs(raised.exception.__context__,error)
            self.assertFalse(backend.cleanup_verified)

    def test_successful_lifecycle_has_name_and_records_cleanup_once(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=DockerSandbox(Path(folder),IMAGE)
            with patch('runner.sandbox.subprocess.run',side_effect=[result(stdout=ID),result(),absent()]) as call:
                with backend:pass
                backend.__exit__(None,None,None)
            self.assertEqual(call.call_count,3)
            args=call.call_args_list[0].args[0]
            self.assertEqual(args[args.index('--name')+1],backend.name)
            self.assertIn('--pull=never',args)
            self.assertTrue(backend.cleanup_verified)

    def test_command_timeout_stops_container_before_propagating(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=self.backend(folder);process=MagicMock(stdout=io.BytesIO(),stderr=io.BytesIO())
            error=subprocess.TimeoutExpired(['docker','exec'],.1)
            process.wait.side_effect=[error,0]
            with patch('runner.sandbox.subprocess.Popen',return_value=process),patch.object(backend,'_cleanup') as cleanup:
                with self.assertRaises(subprocess.TimeoutExpired):backend.run('owned fixture',.1)
            process.kill.assert_called_once();cleanup.assert_called_once()
            self.assertEqual(process.wait.call_args.kwargs,{'timeout':2})

    def test_closed_or_reused_instance_cannot_start_another_command_or_container(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=self.backend(folder);backend.creation_attempted=True;backend.cleanup_verified=True
            with patch('runner.sandbox.subprocess.run') as call,patch('runner.sandbox.subprocess.Popen') as launch:
                with self.assertRaises(ValueError):backend.__enter__()
                with self.assertRaises(ValueError):backend.run('owned fixture',1)
            call.assert_not_called();launch.assert_not_called()

    def test_unresolved_agent_cleanup_preserves_usage_prefix_and_blocks_A_freeze(self):
        import json
        from runner.agent import run_agent
        from runner.phase_a import phase_a
        from analysis.model_usage import stream_usage
        from tests.test_context_calibration import CONFIG
        class LostCleanup:
            def __init__(self,*args):pass
            def __enter__(self):return self
            def run(self,*args):return dict(returncode=0,stdout='',stderr='',truncated=False)
            def __exit__(self,*args):raise SandboxCleanupError('owned mocked disconnection')
        def meter(*args):return dict(count=25,max_model_len=200)
        def response(*args):
            return dict(choices=[dict(message=dict(content='{"action":"submit_patch"}'),finish_reason='stop')],
                        usage=dict(prompt_tokens=25,completion_tokens=5))
        def agent_fixture(*args):return run_agent(*args,request_fn=response,sandbox_factory=LostCleanup,token_count_fn=meter)
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'attempt'
            with patch('runner.phase_a.run_agent',side_effect=agent_fixture),patch('runner.phase_a.freeze') as freeze:
                with self.assertRaises(SandboxCleanupError):phase_a(output,'B1',0,CONFIG)
                freeze.assert_not_called()
            logs=list(output.glob('sessions/*/events.jsonl'));self.assertEqual(len(logs),1)
            raw=logs[0].read_bytes();events=[json.loads(line) for line in raw.splitlines()]
            self.assertEqual(events[-1]['event'],'sandbox_cleanup_unresolved')
            self.assertFalse(any(e['event']=='session_end' for e in events))
            usage=stream_usage(raw)
            self.assertEqual(usage['observed_usage'],dict(tokens=5,input_tokens=25))
            self.assertIsNone(usage['metrics']['tokens'])
            self.assertFalse((output/'artifacts').exists())

    def test_cleanup_failure_during_agent_setup_never_requests_a_fixture_response(self):
        import json
        from runner.agent import run_agent
        from tests.test_context_calibration import CONFIG
        class LostCreation:
            def __init__(self,*args):pass
            def __enter__(self):raise SandboxCleanupError('owned mocked unresolved creation')
            def __exit__(self,*args):pass
        request=MagicMock()
        with tempfile.TemporaryDirectory() as folder:
            log=Path(folder)/'events.jsonl'
            with self.assertRaises(SandboxCleanupError):
                run_agent(Path(folder),'neutral fixture',CONFIG,0,log,request,LostCreation,request)
            request.assert_not_called()
            events=[json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(events,[dict(event='sandbox_cleanup_unresolved',stage='sandbox_setup',error_class='SandboxCleanupError')])


if __name__=='__main__':unittest.main()
