"""Black-box JSON CLI contract shared by authored tasks; no candidate imports."""
import json
import os
import sys
import unittest
from pathlib import Path
from evaluator.contract import run_candidate
from evaluator.grade_policy import decode_candidate_json

def invoke_json(repo,module,request):
    return run_candidate([sys.executable,'-m',module,'--request',json.dumps(request,ensure_ascii=False)],Path(repo))

class JsonContract(unittest.TestCase):
    def request(self,payload):
        result=invoke_json(os.environ['FUSE_REPO'],self.module,payload)
        self.assertEqual(result.returncode,0,result.stderr)
        response=decode_candidate_json(result.stdout)
        self.assertIsInstance(response,dict)
        expected=None
        if self.module=='fusemigration':
            expected={'state','rolled_back'} if payload.get('action')=='rollback' else {'state','applied'}
            if payload.get('action')!='rollback' and payload.get('record',False):
                expected.add('receipt')
        elif self.module=='fusefilequery':
            expected={'columns','rows'}
        elif self.module=='fusecodesearch':
            expected={'matches'}
            if 'replacement' in payload:
                expected.update({'changes','files'})
        elif self.module=='fusetextdrop':
            expected={'state','result'}
        if expected is not None:
            self.assertEqual(set(response),expected,
                             'public response keys do not match documented contract')
        return response
    def bad(self,payload):
        result=invoke_json(os.environ['FUSE_REPO'],self.module,payload)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(result.stdout.strip(),'');self.assertTrue(result.stderr.strip())
