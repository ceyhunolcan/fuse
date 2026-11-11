"""Owned CPU fixtures for the actual opt-in completion and tokenizer paths.

The sandbox is a fixed command stub. This is transport validation, not isolation
or Gemma compatibility validation. No model requests or benchmark outcomes.
"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import platform
import shlex
import subprocess
import threading
import time
from unittest.mock import patch

from runner.agent import run_agent
from runner.http_deadline import request_json


MODES = ('success', 'completion_trickle', 'tokenizer_trickle', 'slow_headers',
         'early_timeout', 'bad_json', 'http_error', 'redirect', 'oversized',
         'short_body', 'missing_usage', 'truncated_action')
WRITE = 'python -c ' + shlex.quote("from pathlib import Path; Path('marker.txt').write_text('NEUTRAL_EDIT_RETAINED')")
PROHIBITED = 'FIXTURE_MUST_NOT_EXECUTE'


class OwnedAPI:
    def __init__(self, mode):
        if mode not in MODES:
            raise ValueError('unknown owned fixture')
        self.mode = mode
        self.requests = []
        self.completions = 0
        self.release = threading.Event()
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                if self.headers.get('Authorization') or self.headers.get('Cookie'):
                    self.send_error(400)
                    return
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 131072:
                    self.send_error(413)
                    return
                payload = json.loads(self.rfile.read(length))
                if self.path not in ('/tokenize', '/v1/chat/completions') or payload['model'] != 'offline-deadline-fixture':
                    self.send_error(404)
                    return
                api.requests.append(dict(path=self.path, payload=payload, credentials_sent=False))
                tokenize = self.path == '/tokenize'
                if not tokenize:
                    api.completions += 1
                # On completion_trickle/http_error, edit once before the fault.
                fail = tokenize if api.mode == 'tokenizer_trickle' else not tokenize
                if api.mode in ('completion_trickle', 'http_error'):
                    fail = not tokenize and api.completions == 2
                if fail and api.mode in ('slow_headers', 'early_timeout'):
                    api.release.wait(60)
                    self.close_connection = True
                    return
                if fail and api.mode == 'redirect':
                    self.send_response(302)
                    self.send_header('Location', 'http://192.0.2.1/never-follow')
                    self.end_headers()
                    return
                if tokenize:
                    reply = dict(count=25, max_model_len=100000)
                else:
                    action = ({'action':'run_command','command':WRITE}
                              if api.completions == 1 and api.mode in ('success','completion_trickle','http_error')
                              else {'action':'submit_patch'})
                    if api.mode == 'truncated_action' and api.completions == 1:
                        action = {'action':'run_command','command':PROHIBITED}
                    reply = dict(choices=[dict(message=dict(content=json.dumps(action)),
                        finish_reason='length' if api.mode == 'truncated_action' and api.completions == 1 else 'stop')],
                        usage={} if api.mode == 'missing_usage' else dict(prompt_tokens=25, completion_tokens=5))
                data = json.dumps(reply).encode()
                if fail and api.mode in ('completion_trickle','tokenizer_trickle'):
                    data = b' ' * 10000 + data
                if fail and api.mode == 'bad_json':
                    data = b'{deliberately invalid'
                if fail and api.mode == 'oversized':
                    data = b' ' * 8192
                self.send_response(503 if fail and api.mode == 'http_error' else 200)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length', str(len(data) + (20 if fail and api.mode == 'short_body' else 0)))
                self.end_headers()
                try:
                    if fail and api.mode in ('completion_trickle','tokenizer_trickle'):
                        # Valid JSON in flight, socket remains active beyond the
                        # total allowance. Partial bytes must never be decoded.
                        for byte in data:
                            self.wfile.write(bytes([byte])); self.wfile.flush()
                            if api.release.wait(.025):
                                break
                    else:
                        self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                self.close_connection = True

        self.server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=lambda:self.server.serve_forever(poll_interval=.025),daemon=True)
        self.endpoint = f'http://127.0.0.1:{self.server.server_port}/v1'

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.release.set()
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)


def fixture_config(endpoint):
    return dict(endpoint_url=endpoint, model='offline-deadline-fixture', model_revision='owned-cpu-fixture',
        container_image='fixture@sha256:stub', context_budget_tokens=100000,
        context_policy='vllm-tokenize-v1', context_safety_margin_tokens=10, tool_feedback_max_bytes=1024,
        action_recovery_policy='bounded-rejection-v1',max_turns=4,max_generated_tokens=100,
        max_output_tokens_per_turn=40,time_limit_seconds=1,temperature=0,seed_supported=False,
        timeout_policy='session-deadline-v1',http_transport='deadline-worker-v1',
        http_response_max_bytes=4096,http_socket_timeout_seconds=10,
        api_key_env='FUSE_DEADLINE_REHEARSAL_NO_CREDENTIALS')


def check(name, predicate, checks):
    if not predicate:
        raise AssertionError(name)
    checks.append(name)


def run(output, isolated_config=None):
    isolated = isolated_config is not None
    if isolated:
        from runner.chroot_sandbox import ChrootSandbox, validate_identity
        if (platform.system() != 'Linux' or platform.machine() != 'x86_64' or os.geteuid() != 0 or
                list(Path('/dev').glob('nvidia[0-9]*'))):
            raise ValueError('isolated transport rehearsal requires Linux x86_64 root and GPUs off')
        validate_identity(isolated_config)
    if os.environ.get('FUSE_DEADLINE_REHEARSAL_NO_CREDENTIALS'):
        raise ValueError('neutral fixture environment must contain no credential')
    output = Path(output).resolve()
    output.mkdir(parents=True,exist_ok=False)
    checks = []
    rows = []
    total_requests = 0
    for mode in MODES:
        folder = output / mode
        folder.mkdir()
        repo = folder / 'repo'; repo.mkdir()
        commands = []
        class Sandbox:
            def __init__(self,*args):
                self.actual = ChrootSandbox(repo,isolated_config) if isolated else None
            def __enter__(self):
                if self.actual:
                    self.actual.__enter__()
                return self
            def __exit__(self,*args):
                # Model edits remain in this neutral workspace after failure.
                if self.actual:
                    self.actual.__exit__(*args)
            def run(self,command,timeout):
                commands.append(command)
                if self.actual:
                    return self.actual.run(command,timeout)
                if command == WRITE:
                    (repo/'marker.txt').write_text('NEUTRAL_EDIT_RETAINED')
                elif command != 'python --version && git --version && test -w /workspace':
                    raise ValueError('stub never executes shell or arbitrary commands')
                return dict(returncode=0,stdout='neutral fixture stub',stderr='')
        with OwnedAPI(mode) as api:
            config = fixture_config(api.endpoint)
            if mode == 'early_timeout':
                config['http_socket_timeout_seconds'] = .1
            if mode in ('success','truncated_action'):
                config['time_limit_seconds'] = 4
            if isolated:
                config.update(isolated_config)
                config['time_limit_seconds'] = 10
                config.pop('container_image')
            started = time.monotonic()
            metrics = run_agent(repo,'Neutral deadline integration fixture.',config,0,
                                folder/'events.jsonl',sandbox_factory=Sandbox)
            elapsed = time.monotonic()-started
            (folder/'owned-requests.json').write_text(json.dumps(api.requests,indent=2)+'\n')
            total_requests += len(api.requests)
        reports = [json.loads(p.read_text()) for p in (folder/'events.jsonl.http').glob('*/parent-result.json')]
        row = dict(case=mode,metrics=metrics,elapsed_seconds=elapsed,commands=commands,
                   retained_edit=(repo/'marker.txt').exists(),http_requests=len(reports),
                   data_kind='neutral_transport_fixture',model_requests=0,actual_chroot_used=isolated)
        (folder/'result.json').write_text(json.dumps(row,indent=2)+'\n')
        check(mode+': all worker processes reaped',all(r['worker_reaped'] for r in reports),checks)
        check(mode+': request count proves zero retries',len(reports)==len(api.requests),checks)
        check(mode+': prohibited action not executed',PROHIBITED not in commands,checks)
        check(mode+': config has no transient controller fields',not any(k.startswith('_http') for k in config),checks)
        if mode in ('success','truncated_action'):
            check(mode+': submitted',metrics['status']=='submitted',checks)
        elif mode in ('completion_trickle','tokenizer_trickle','slow_headers'):
            check(mode+': total deadline stops active response',metrics['status']=='time_budget_exhausted',checks)
            if not isolated:
                check(mode+': measured local elapsed bound',elapsed < config['time_limit_seconds'] + .75,checks)
            check(mode+': no accepted stalled response',any(r['state']=='deadline' and not r['response_accepted'] for r in reports),checks)
        else:
            check(mode+': infrastructure missingness',metrics['status']=='infrastructure_error',checks)
        if mode in ('completion_trickle','http_error'):
            check(mode+': earlier edit survives failure',row['retained_edit'],checks)
            check(mode+': observed earlier usage preserved',metrics['observed_completion_tokens']==5 and metrics['observed_prompt_tokens']==25,checks)
        if mode in ('completion_trickle','tokenizer_trickle'):
            check(mode+': partial response bytes retained',any(p.stat().st_size > 0 for p in (folder/'events.jsonl.http').glob('*/response.body.part')),checks)
        if mode == 'tokenizer_trickle':
            check(mode+': no completion after failed meter',api.completions==0 and metrics['tokens']==0,checks)
        elif mode not in ('success','truncated_action'):
            check(mode+': unavailable usage remains unknown',metrics['tokens'] is None and metrics['input_tokens'] is None,checks)
        if mode == 'oversized':
            check(mode+': capped partial recording',any(p.stat().st_size==4097 for p in (folder/'events.jsonl.http').glob('*/response.body.part')),checks)
        rows.append(row)

    # Interrupt a running trusted worker, then verify cleanup and partial evidence.
    folder = output/'interrupted'; folder.mkdir()
    with OwnedAPI('tokenizer_trickle') as api:
        config = fixture_config(api.endpoint)
        config['_http_evidence_root'] = str(folder)
        original = subprocess.Popen.communicate
        entered = [False]
        def interrupt(process, *args, **kwargs):
            if not entered[0]:
                entered[0] = True
                try:
                    original(process, *args, **{**kwargs,'timeout':.3})
                except subprocess.TimeoutExpired:
                    raise KeyboardInterrupt() from None
            return original(process,*args,**kwargs)
        try:
            with patch.object(subprocess.Popen,'communicate',interrupt):
                request_json(config,api.endpoint[:-3]+'/tokenize',dict(model=config['model']),{},5)
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError('interrupt was not propagated')
        total_requests += len(api.requests)
    report = json.loads(next(folder.glob('*/parent-result.json')).read_text())
    check('interrupt: worker reaped and durable refusal',report['worker_reaped'] and report['state']=='interrupted' and not report['response_accepted'],checks)
    check('interrupt: partial bytes retained',any(p.stat().st_size>0 for p in folder.glob('*/response.body.part')),checks)

    # Refuse expired sessions and external endpoints before any worker starts.
    folder=output/'expired'; folder.mkdir()
    config=fixture_config('http://127.0.0.1:1/v1')
    config.update(_http_evidence_root=str(folder),_http_session_deadline=time.monotonic()-1)
    try:
        request_json(config,config['endpoint_url']+'/chat/completions',{}, {},1)
    except TimeoutError:
        pass
    report=json.loads(next(folder.glob('*/parent-result.json')).read_text())
    check('expired: no worker or request started',not report['worker_started'] and not report['response_accepted'],checks)
    for endpoint in ('http://192.0.2.1/v1','http://localhost:8000/v1','http://127.0.0.1:8000/v1?x=1'):
        config=fixture_config(endpoint); config['_http_evidence_root']=str(output/'must-not-exist')
        try:
            request_json(config,endpoint+'/chat/completions',{}, {},1)
        except ValueError:
            pass
        else:
            raise AssertionError('unsafe endpoint accepted')
    check('endpoint refusals: no recording or worker created',not (output/'must-not-exist').exists(),checks)
    summary=dict(status='passed',checks=checks,check_count=len(checks),cases=rows,
                 owned_loopback_requests=total_requests,model_requests=0,GPU_sessions=0,
                 formal_collection_enabled=False,actual_chroot_used=isolated,
                 sandbox_isolation_verified=False,
                 server_inference_cancellation_verified=False,exact_Gemma_runtime_compatibility_verified=False)
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=run(args.output)
    print(json.dumps({k:v for k,v in result.items() if k not in ('checks','cases')},indent=2))
