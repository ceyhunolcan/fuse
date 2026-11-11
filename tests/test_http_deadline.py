"""Controlled timing boundaries with owned worker stubs; no HTTP or models."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runner.agent import validate_config
from runner.deadline_rehearsal import fixture_config
from runner.http_deadline import ClientDeadlineExceeded, EarlySocketTimeout, request_json, save_json


class DeliveryBoundary(unittest.TestCase):
    def run_boundary(self, late_at=None, early_error=False):
        clock = [0.0]
        class CompletedWorker:
            pid = 123
            returncode = 0
            def __init__(self,*args,**kwargs):
                self.folder = None
            def communicate(self, data=None, timeout=None):
                if data is not None:
                    config = json.loads(data)
                    self.folder = Path(config['folder'])
                    clock[0] = .1
                    raw = b'{"choices":[{"message":{"content":"must not execute"}}]}'
                    (self.folder/'response.body').write_bytes(raw)
                    save_json(self.folder/'worker-result.json', dict(
                        state='transport_error' if early_error else 'complete',status=200,
                        response_bytes=len(raw),observed_at=.1,error_class='TimeoutError',socket_timeout=early_error))
                    if early_error:
                        # Parent scheduling delay cannot recode a known early failure.
                        clock[0] = 2
                elif late_at == 'cleanup':
                    clock[0] = 2
                return b'',b''
            def poll(self):
                return 0
        original_loads = json.loads
        original_save = save_json
        def loads(raw,*args,**kwargs):
            value = original_loads(raw,*args,**kwargs)
            if late_at == 'decode' and isinstance(raw,bytes) and b'choices' in raw:
                clock[0] = 2
            return value
        def record(path,value):
            original_save(path,value)
            if late_at == 'evidence' and Path(path).name=='parent-result.json':
                clock[0] = 2
        with tempfile.TemporaryDirectory() as tmp:
            config = fixture_config('http://127.0.0.1:1/v1')
            config['_http_evidence_root'] = tmp
            with patch('runner.http_deadline.time.monotonic',lambda:clock[0]), \
                    patch('runner.http_deadline.subprocess.Popen',CompletedWorker), \
                    patch('runner.http_deadline.json.loads',loads), \
                    patch('runner.http_deadline.save_json',record):
                with self.assertRaises(EarlySocketTimeout if early_error else ClientDeadlineExceeded):
                    request_json(config,config['endpoint_url']+'/chat/completions',{}, {},1)
            folder=next(Path(tmp).iterdir())
            report=original_loads((folder/'parent-result.json').read_text())
            self.assertTrue(report['worker_reaped'])
            self.assertTrue((folder/'response.body').exists())
            if late_at=='evidence':
                self.assertTrue((folder/'delivery-refused.json').exists())
            else:
                self.assertFalse(report['response_accepted'])
            return report

    def test_decode_crossing_deadline_refuses_complete_body(self):
        self.run_boundary('decode')

    def test_cleanup_crossing_deadline_refuses_complete_body(self):
        self.run_boundary('cleanup')

    def test_evidence_write_crossing_deadline_refuses_delivery(self):
        self.run_boundary('evidence')

    def test_parent_delay_preserves_observed_early_socket_failure(self):
        report=self.run_boundary(early_error=True)
        self.assertEqual(report['state'],'transport_error')

    def test_policy_requires_explicit_limits_and_session_deadline(self):
        config=fixture_config('http://127.0.0.1:1/v1')
        for update in ({'timeout_policy':'legacy-v0'}, {'http_response_max_bytes':None},
                       {'http_socket_timeout_seconds':float('inf')}, {'http_transport':'unknown'}):
            with self.assertRaises(ValueError):
                validate_config({**config,**update})


if __name__=='__main__':
    unittest.main()
