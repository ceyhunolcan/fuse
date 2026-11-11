"""Observe actual request/meter messages and retained Unicode feedback."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from runner.agent import run_agent
from tests.test_context_calibration import CONFIG

class FeedbackContract(unittest.TestCase):
    def exercise(self, config, count=25):
        measured=[]; requested=[]; commands=[]
        raw={'returncode':0,'stdout':'測é🙂'*300,'stderr':'é測🙂'*300,'timed_out':False}
        class Sandbox:
            def __init__(self,*args):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def run(self,command,timeout):
                commands.append(command)
                return copy.deepcopy(raw)
        def meter(config,messages,timeout):
            measured.append(copy.deepcopy(messages))
            return {'count':count,'max_model_len':200}
        def request(config,messages,max_tokens,seed,timeout):
            requested.append(copy.deepcopy(messages))
            action={'action':'run_command','command':'echo neutral'} if len(requested)==1 else {'action':'submit_patch'}
            return {'choices':[{'message':{'content':json.dumps(action)},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':count,'completion_tokens':5}}
        original=copy.deepcopy(config)
        with tempfile.TemporaryDirectory() as t:
            log=Path(t)/'events.jsonl'
            result=run_agent(Path(t),'Neutral feedback fixture',config,0,log,request,Sandbox,meter)
            events=[json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(config,original)
        return result,measured,requested,commands,events,raw

    def test_configured_bytes_agree_with_request_meter_and_unicode_streams(self):
        for limit in (1,16,4096):
            with self.subTest(limit=limit):
                result,measured,requested,commands,events,raw=self.exercise({**CONFIG,'tool_feedback_max_bytes':limit})
                self.assertEqual(result['status'],'submitted')
                self.assertEqual(commands[1:],['echo neutral'])
                self.assertEqual(measured,requested)
                self.assertEqual(len(requested),2)
                for messages in requested:
                    self.assertIn(f'Model-visible stdout and stderr are each limited to {limit} UTF-8 bytes.',messages[0]['content'])
                    self.assertNotIn('20,000 characters',messages[0]['content'])
                feedback=json.loads(requested[1][-1]['content'])['tool_result']
                for stream in ('stdout','stderr'):
                    self.assertLessEqual(len(feedback[stream].encode('utf-8')),limit)
                    self.assertEqual(feedback[stream],raw[stream].encode('utf-8')[:limit].decode('utf-8',errors='ignore'))
                self.assertEqual(feedback['feedback_truncated'],limit<2700)
                self.assertEqual(feedback['returncode'],0)
                self.assertFalse(feedback['timed_out'])
                retained=next(e for e in events if e['event']=='tool_call')['output']
                self.assertEqual(retained,raw)

    def test_legacy_path_keeps_feedback_without_claiming_exact_byte_limit(self):
        config={**CONFIG,'context_policy':'utf8-envelope-v0','context_budget_tokens':1000000}
        result,measured,requested,commands,events,raw=self.exercise(config)
        self.assertEqual(result['status'],'submitted');self.assertEqual(measured,[])
        self.assertEqual(commands[1:],['echo neutral'])
        self.assertEqual(json.loads(requested[1][-1]['content'])['tool_result'],raw)
        self.assertNotIn('20,000 characters',requested[0][0]['content'])
        self.assertNotIn('Model-visible stdout and stderr are each limited to',requested[0][0]['content'])
        self.assertIn('Command output can be truncated.',requested[0][0]['content'])

    def test_revised_prompt_is_measured_before_a_context_budget_stop(self):
        result,measured,requested,commands,events,raw=self.exercise(CONFIG,count=190)
        self.assertEqual(result['status'],'context_budget_exhausted')
        self.assertEqual(requested,[]);self.assertEqual(len(measured),1)
        self.assertEqual(commands[1:],[])
        self.assertIn('limited to 16 UTF-8 bytes.',measured[0][0]['content'])

    def test_invalid_feedback_budget_is_refused_before_callbacks(self):
        for value in (0,-1,True,'16',None):
            with self.subTest(value=value):
                def forbidden(*args):self.fail('invalid budget reached callback')
                with tempfile.TemporaryDirectory() as t,self.assertRaises(ValueError):
                    run_agent(Path(t),'neutral',{**CONFIG,'tool_feedback_max_bytes':value},0,Path(t)/'log',
                        forbidden,forbidden,forbidden)

if __name__=='__main__':unittest.main()
