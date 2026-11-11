import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from runner.agent import validate_config
from runner.chroot_sandbox import ChrootSandbox, sanitized_export


class Transfer(unittest.TestCase):
    def test_export_rejects_links_and_discards_agent_git_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'source';source.mkdir()
            (source/'current.py').write_text('present')
            (source/'.git').mkdir();(source/'.git/config').write_text('untrusted')
            sanitized_export(source,root/'clean')
            self.assertEqual((root/'clean/current.py').read_text(),'present')
            self.assertFalse((root/'clean/.git').exists())
            (source/'escape').symlink_to('/etc/passwd')
            with self.assertRaises(ValueError): sanitized_export(source,root/'rejected')
            self.assertFalse((root/'rejected').exists())

    def test_missing_evidence_cannot_enable_chroot(self):
        config={'endpoint_url':'http://example.invalid','model':'fixture','model_revision':'fixture',
                'context_budget_tokens':32768,'sandbox_backend':'chroot-circuit-v0'}
        with self.assertRaisesRegex(ValueError,'missing chroot identity'):
            validate_config(config)

    def test_transport_error_preserves_completed_edits_and_propagates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / 'repo'; repo.mkdir()
            (repo / '.git').mkdir(); (repo / '.git/config').write_text('trusted')
            (repo / 'current.py').write_text('before')
            parent = tempfile.TemporaryDirectory(dir=root)
            backend_root = Path(parent.name) / 'sandbox'
            workspace = backend_root / 'workspace'; workspace.mkdir(parents=True)
            (workspace / 'current.py').write_text('completed edit')
            (workspace / '.git').mkdir(); (workspace / '.git/config').write_text('untrusted')
            events = []
            class Backend:
                uid = 12345
                root = backend_root
                def close(self): events.append('closed')
            sandbox = object.__new__(ChrootSandbox)
            sandbox.repo, sandbox.parent, sandbox.backend, sandbox.export = repo, parent, Backend(), True
            error = OSError('transport failure')
            with patch('runner.chroot_sandbox.chroot_linux.kill_uid', side_effect=lambda uid: events.append('killed')):
                self.assertIsNone(sandbox.__exit__(type(error), error, None))
            self.assertEqual(events, ['killed', 'closed'])
            self.assertEqual((repo / 'current.py').read_text(), 'completed edit')
            self.assertEqual((repo / '.git/config').read_text(), 'trusted')
            self.assertFalse(Path(parent.name).exists())

    def test_exception_export_rejects_links_without_replacing_repository(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); repo = root / 'repo'; repo.mkdir()
            (repo / 'current.py').write_text('before')
            parent = tempfile.TemporaryDirectory(dir=root)
            backend_root = Path(parent.name) / 'sandbox'
            workspace = backend_root / 'workspace'; workspace.mkdir(parents=True)
            (workspace / 'escape').symlink_to('/etc/passwd')
            events = []
            class Backend:
                uid = 12345
                root = backend_root
                def close(self): events.append('closed')
            sandbox = object.__new__(ChrootSandbox)
            sandbox.repo, sandbox.parent, sandbox.backend, sandbox.export = repo, parent, Backend(), True
            with patch('runner.chroot_sandbox.chroot_linux.kill_uid'):
                with self.assertRaisesRegex(ValueError, 'special file or link'):
                    sandbox.__exit__(OSError, OSError('transport failure'), None)
            self.assertEqual(events, ['closed'])
            self.assertEqual((repo / 'current.py').read_text(), 'before')
            self.assertFalse(Path(parent.name).exists())


if __name__=='__main__': unittest.main()
