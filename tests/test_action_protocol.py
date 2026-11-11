import json
from pathlib import Path
import tempfile
import unittest
from runner.agent import parse_action, run_agent


class ModelControlSuffix(unittest.TestCase):
    def test_only_pinned_suffix_is_removed(self):
        action = {'action': 'run_command', 'command': 'ls -R /workspace'}
        text = '{"action":"run_command","command":"ls -R /workspace"}<tool_call|>'
        self.assertEqual(parse_action(text, ['<tool_call|>']), action)
        with self.assertRaises(ValueError):
            parse_action(text)
        self.assertEqual(parse_action('{"action":"submit_patch"}'), {'action':'submit_patch'})

    def test_suffix_does_not_allow_extra_objects_or_text(self):
        for text in ['{}{}<tool_call|>', '{} arbitrary text<tool_call|>',
                     '{}<unknown|>', '{}<tool_call|><tool_call|>']:
            with self.assertRaises(ValueError):
                parse_action(text, ['<tool_call|>'])
        with self.assertRaises(TypeError):
            parse_action(None, ['<tool_call|>'])

    def test_literal_inside_command_is_preserved(self):
        text = '{"action":"run_command","command":"echo <tool_call|>"}<tool_call|>'
        self.assertEqual(parse_action(text, ['<tool_call|>'])['command'], 'echo <tool_call|>')

    def test_fixed_protocol_executes_command_and_submits(self):
        config = {'endpoint_url':'http://example.invalid/v1', 'model':'fixture',
                  'model_revision':'test', 'container_image':'fixture@sha256:abc',
                  'context_budget_tokens':100000, 'max_turns':3,
                  'max_generated_tokens':50, 'max_output_tokens_per_turn':10,
                  'time_limit_seconds':20, 'temperature':0, 'seed_supported':False,
                  'allowed_model_control_suffixes':['<tool_call|>']}
        commands = []
        class Sandbox:
            def __init__(self,*args): pass
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def run(self,command,timeout):
                commands.append(command)
                return {'returncode':0,'stdout':'CURRENT_ONLY','stderr':''}
        replies = iter(['{} arbitrary<tool_call|>',
                        '{"action":"run_command","command":"echo CURRENT_ONLY"}<tool_call|>',
                        '{"action":"submit_patch"}<tool_call|>'])
        def request(*args):
            return {'choices':[{'message':{'content':next(replies)}}],
                    'usage':{'prompt_tokens':20,'completion_tokens':5}}
        with tempfile.TemporaryDirectory() as temp:
            metrics = run_agent(Path(temp), 'Protocol fixture.', config, 0,
                                Path(temp)/'events.jsonl', request_fn=request,
                                sandbox_factory=Sandbox)
        self.assertEqual(metrics['status'],'submitted')
        self.assertEqual(metrics['tool_calls'],1)
        self.assertEqual(commands[1:],['echo CURRENT_ONLY'])
