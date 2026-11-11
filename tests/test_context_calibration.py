import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from runner.agent import run_agent
from runner.context import bounded_feedback, tokenize_chat

CONFIG = {'endpoint_url':'http://127.0.0.1:8000/v1', 'model':'fixture',
    'model_revision':'fixture', 'container_image':'fixture@sha256:abc',
    'context_budget_tokens':200, 'context_policy':'vllm-tokenize-v1',
    'context_safety_margin_tokens':10, 'tool_feedback_max_bytes':16,
    'action_recovery_policy':'bounded-rejection-v1', 'max_turns':4,
    'max_generated_tokens':100, 'max_output_tokens_per_turn':40,
    'time_limit_seconds':20, 'temperature':0, 'seed_supported':False,
    'allowed_model_control_suffixes':['<tool_call|>'],
    'chat_template_kwargs':{'enable_thinking':True}}

class FixtureSandbox:
    commands = []
    def __init__(self,*args): pass
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def run(self,command,timeout):
        self.commands.append(command)
        return {'returncode':0,'stdout':'測'*100, 'stderr':'', 'timed_out':False}

class Calibration(unittest.TestCase):
    def run_case(self, replies, counts=(25,), config=None, prompt='Neutral fixture'):
        requests=[]; seen=[]; FixtureSandbox.commands=[]
        replies=iter(replies); counts=iter(counts); current=25
        def meter(config,messages,timeout):
            nonlocal current
            current=next(counts,current)
            seen.append(copy.deepcopy(messages))
            return {'count':current,'max_model_len':200}
        def request(config,messages,max_tokens,seed,timeout):
            requests.append(max_tokens)
            content,finish,usage_count=next(replies)
            return {'choices':[{'message':{'content':content},'finish_reason':finish}],
                'usage':{'prompt_tokens':current if usage_count is None else usage_count,'completion_tokens':5}}
        with tempfile.TemporaryDirectory() as t:
            log=Path(t)/'events.jsonl'
            result=run_agent(Path(t),prompt,config or CONFIG,0,log,request_fn=request,
                sandbox_factory=FixtureSandbox,token_count_fn=meter)
            events=[json.loads(l) for l in log.read_text().splitlines()]
        return result,requests,seen,events,FixtureSandbox.commands[1:]

    def test_large_byte_prompt_fits_actual_token_count(self):
        r,c,*_=self.run_case([('{"action":"submit_patch"}','stop',None)],prompt='ordinary words '*4000)
        self.assertEqual(r['status'],'submitted');self.assertEqual(c,[40])

    def test_reserves_margin_and_limits_completion_to_remaining_context(self):
        r,c,*_=self.run_case([('{"action":"submit_patch"}','stop',None)],counts=(175,))
        self.assertEqual(r['status'],'submitted');self.assertEqual(c,[15])

    def test_no_inference_when_context_has_no_space(self):
        r,c,*_=self.run_case([],counts=(191,))
        self.assertEqual(r['status'],'context_budget_exhausted');self.assertEqual(c,[])

    def test_tokenizer_mismatch_prevents_command_execution(self):
        r,c,seen,es,cmds=self.run_case([('{"action":"run_command","command":"unsafe-prefix"}','stop',26)])
        self.assertEqual(r['status'],'token_accounting_mismatch');self.assertEqual(cmds,[])
        self.assertTrue(any(e['event']=='token_count_mismatch' for e in es))

    def test_length_even_valid_json_cannot_execute_and_is_bounded(self):
        r,c,seen,es,cmds=self.run_case([
            ('{"action":"run_command","command":"MUST_NOT_EXECUTE"}','length',None),
            ('{"action":"run_command","command":"echo ok"}','stop',None),
            ('{"action":"submit_patch"}','stop',None)])
        self.assertEqual(r['status'],'submitted');self.assertEqual(cmds,['echo ok'])
        self.assertNotIn('MUST_NOT_EXECUTE',json.dumps(seen[1]))
        tool=next(e for e in es if e['event']=='tool_call')
        self.assertEqual(len(tool['output']['stdout']),100)
        feedback=json.loads(seen[2][-1]['content'])['tool_result']
        self.assertLessEqual(len(feedback['stdout'].encode()),16)
        self.assertTrue(feedback['feedback_truncated'])

    def test_malformed_action_is_logged_but_not_echoed(self):
        r,c,seen,es,cmds=self.run_case([('x'*30000,'stop',None),('{"action":"submit_patch"}','stop',None)])
        self.assertEqual(r['status'],'submitted');self.assertEqual(cmds,[])
        self.assertLess(len(json.dumps(seen[1])),2500)
        self.assertEqual(es[1]['response']['choices'][0]['message']['content'],'x'*30000)

    def test_tokenizer_http_payload_matches_chat_template(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return b'{"count":25,"max_model_len":200,"tokens":[1]}'
        with patch('urllib.request.urlopen',return_value=Response()) as call:
            result=tokenize_chat(CONFIG,[{'role':'user','content':'hello'}],10)
        req=call.call_args.args[0];body=json.loads(req.data)
        self.assertEqual(req.full_url,'http://127.0.0.1:8000/tokenize')
        self.assertEqual(body['chat_template_kwargs'],{'enable_thinking':True})
        self.assertFalse(body['add_special_tokens']);self.assertTrue(body['add_generation_prompt'])
        self.assertEqual(result,{'count':25,'max_model_len':200})

    def test_feedback_does_not_mutate_raw_result(self):
        raw={'stdout':'é'*50,'stderr':''};new=bounded_feedback(raw,15)
        self.assertEqual(raw['stdout'],'é'*50)
        self.assertEqual(len(new['stdout'].encode()),14)

    def test_unavailable_tokenizer_stops_without_fallback(self):
        def fail(*args):raise OSError('unavailable')
        with tempfile.TemporaryDirectory() as t:
            r=run_agent(Path(t),'fixture',CONFIG,0,Path(t)/'events.jsonl',
                sandbox_factory=FixtureSandbox,token_count_fn=fail)
        self.assertEqual(r['status'],'infrastructure_error');self.assertIsNone(r['tokens'])

if __name__=='__main__':unittest.main()
