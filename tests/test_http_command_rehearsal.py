import base64
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
import urllib.request
from unittest.mock import patch

from runner.http_command_rehearsal import (
    MODES, OwnedMockAPI, RecordedAdapters, audit, run_rehearsal,
)
from runner.reference_pipeline import read, sha


class HTTPCommandRehearsalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.output = cls.root / 'baseline'
        # If user credentials/proxies exist, the mock must ignore them.
        with patch.dict(os.environ, {'FUSE_API_KEY': 'synthetic-secret-never-forward',
                                    'http_proxy': 'http://192.0.2.1:1',
                                    'HTTP_PROXY': 'http://192.0.2.1:1',
                                    'NO_PROXY': '', 'no_proxy': ''}):
            cls.result = run_rehearsal(cls.output)
        cls.anchor = sha(cls.output / 'plan.json')

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def duplicate(self):
        path = self.root / self._testMethodName
        shutil.copytree(self.output, path)
        return path

    def test_seven_cases_and_no_isolation_claim(self):
        self.assertTrue(self.result['pass_'])
        self.assertEqual([r['case'] for r in self.result['cases']], list(MODES))
        self.assertEqual(sum(r['mock_HTTP_requests'] for r in self.result['cases']), 24)
        self.assertFalse(self.result['real_isolation_validated'])
        self.assertEqual(self.result['model_requests'], 0)
        self.assertFalse(self.result['formal_collection_enabled'])

    def test_unknown_complete_usage_keeps_observed_prefix(self):
        m = read(self.output / 'sessions/http_error/metrics.json')
        self.assertIsNone(m['tokens'])
        self.assertIsNone(m['input_tokens'])
        self.assertGreater(m['observed_completion_tokens'], 0)
        self.assertTrue((self.output / 'sessions/http_error/snapshot/marker.txt').is_file())

    def test_truncated_valid_action_does_not_execute(self):
        self.assertFalse((self.output / 'sessions/truncated/snapshot/marker.txt').exists())
        self.assertTrue((self.output / 'sessions/truncated/snapshot/probe.json').is_file())

    def test_exact_invalid_and_redirect_response_bytes_preserved(self):
        bad = read(self.output / 'sessions/bad_json/http/002-response.json')
        self.assertEqual(base64.b64decode(bad['body_base64']), b'{invalid-json')
        redirect = read(self.output / 'sessions/redirect/http/002-response.json')
        self.assertEqual(redirect['status'], 302)
        self.assertEqual(len(list((self.output / 'sessions/redirect/http').glob('*request.json'))), 2)

    def test_credentials_absent_from_wire_bytes(self):
        for p in self.output.glob('sessions/*/http/*request.json'):
            value = read(p)
            self.assertFalse(value['credentials_sent'])
            self.assertNotIn(b'synthetic-secret-never-forward', base64.b64decode(value['body_base64']))

    def test_unowned_endpoint_rejected_before_operation(self):
        with OwnedMockAPI('success', 'probe') as api:
            adapter = RecordedAdapters(self.root, self.root, api, 'probe', [], False, {})
            with self.assertRaisesRegex(ValueError, 'owned local mock'):
                adapter.open(urllib.request.Request('http://192.0.2.1/no', data=b'{}'))
            self.assertEqual(adapter.http_index, 0)

    def test_patch_restored_after_exception(self):
        original = urllib.request.urlopen
        with OwnedMockAPI('success', 'probe') as api:
            adapter = RecordedAdapters(self.root, self.root, api, 'probe', [], False, {})
            with self.assertRaises(RuntimeError):
                with adapter.private_opener():
                    raise RuntimeError('synthetic')
        self.assertIs(urllib.request.urlopen, original)

    def test_wire_mutation_rejected(self):
        output = self.duplicate()
        p = output / 'sessions/success/http/002-response.json'
        value = read(p); value['status'] = 500
        p.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'evidence hash'):
            audit(output, self.anchor)

    def test_missing_receipt_rejected(self):
        output = self.duplicate()
        (output / 'receipts/no_edit.json').unlink()
        with self.assertRaisesRegex(ValueError, 'receipt'):
            audit(output, self.anchor)

    def test_snapshot_mutation_rejected(self):
        output = self.duplicate()
        (output / 'sessions/success/snapshot/marker.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'snapshot differs'):
            audit(output, self.anchor)

    def test_interruption_retained_and_not_resumed(self):
        output = self.root / 'interrupted'
        from runner.http_command_rehearsal import immutable_json
        def stop(path, value):
            if Path(path).parent.name == 'receipts':
                raise RuntimeError('stop before receipt')
            return immutable_json(path, value)
        with patch('runner.http_command_rehearsal.immutable_json', side_effect=stop):
            with self.assertRaisesRegex(RuntimeError, 'stop before receipt'):
                run_rehearsal(output, modes=['success'])
        self.assertTrue((output / 'sessions/success/metrics.json').exists())
        before = (output / 'attempts.jsonl').read_bytes()
        with self.assertRaises(ValueError):
            audit(output, sha(output / 'plan.json'))
        with self.assertRaises(FileExistsError):
            run_rehearsal(output)
        self.assertEqual((output / 'attempts.jsonl').read_bytes(), before)

    def test_wrong_platform_has_no_host_shell_fallback(self):
        output = self.root / 'isolated'
        with patch('runner.http_command_rehearsal.platform.system', return_value='Darwin'):
            with self.assertRaisesRegex(ValueError, 'no host-shell fallback'):
                run_rehearsal(output, isolated_config={})
        self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
