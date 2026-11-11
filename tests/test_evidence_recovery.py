import errno
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from runner.evidence import immutable_json,append_event,inspect_journal


class EvidenceRecoveryTests(unittest.TestCase):
    def test_immutable_record_cannot_replace_original(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'result.json';immutable_json(p,{'success':False});before=p.read_bytes()
            with self.assertRaises(FileExistsError):immutable_json(p,{'success':True})
            self.assertEqual(p.read_bytes(),before)

    def test_disk_full_retains_original_and_partial_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'result.json';immutable_json(p,{'success':False});before=p.read_bytes()
            with patch('runner.evidence.os.fsync',side_effect=OSError(errno.ENOSPC,'simulated full filesystem')):
                with self.assertRaises(OSError):immutable_json(p,{'success':True})
            self.assertEqual(p.read_bytes(),before);self.assertEqual(len(list(Path(directory).glob('*.part'))),1)

    def test_incomplete_or_corrupt_journal_preserved(self):
        for tail in (b'{"attempt":',b'{invalid}\n',b'[]\n'):
            with tempfile.TemporaryDirectory() as directory:
                p=Path(directory)/'events.jsonl';append_event(p,{'event':'attempt_started'})
                with p.open('ab') as stream:stream.write(tail)
                before=p.read_bytes();result=inspect_journal(p)
                self.assertFalse(result['pass_']);self.assertEqual(len(result['events']),1)
                self.assertEqual(result['unresolved_bytes'],len(tail));self.assertEqual(p.read_bytes(),before)

    def test_short_append_detected_with_unmodified_previous_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'events.jsonl';append_event(p,{'event':'attempt_started'});before=p.read_bytes()
            real_write=os.write
            def short(fd,data):return real_write(fd,data[:5])
            with patch('runner.evidence.os.write',side_effect=short):
                with self.assertRaisesRegex(OSError,'short journal'):append_event(p,{'event':'attempt_finished'})
            self.assertTrue(p.read_bytes().startswith(before));self.assertFalse(inspect_journal(p)['pass_'])


if __name__=='__main__':unittest.main()
