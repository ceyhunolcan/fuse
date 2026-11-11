"""Owned localhost mock API plus recorded command adapters; no live model mode.

Host mode uses a visibly simulated fixture adapter. Isolated mode requires the
existing validated Linux x86_64 chroot and a newly retained CPU boundary report.
"""
import argparse
import base64
import copy
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import platform
import shlex
import threading
import urllib.error
import urllib.request
import uuid
from unittest.mock import patch

from runner.agent import complete, run_agent
from runner.candidate_controller import config_for, sync_tree
from runner.chroot_sandbox import ChrootSandbox, validate_identity
from runner.common import copy_tree, initialize, tree_digest
from runner.context import tokenize_chat
from runner.evidence import append_event, immutable_json, inspect_journal
from runner.reference_pipeline import checked_file, read, sha

MODES = ('success', 'no_edit', 'http_error', 'bad_json', 'missing_usage', 'truncated', 'redirect')
READINESS = 'python --version && git --version && test -w /workspace'
WRITE = "python -c " + shlex.quote("from pathlib import Path; Path('marker.txt').write_text('FUSE_HTTP_COMMAND_OK')")


def probe_command(hidden_paths):
    code = '''import json,os,socket
from pathlib import Path
paths = %r
reads = {}
for p in paths:
    try:
        Path(p).read_bytes(); reads[p] = True
    except OSError:
        reads[p] = False
try:
    sock = socket.socket(); sock.close(); denied = False
except OSError:
    denied = True
value = {'simulated':False,'uid':os.getuid(),'gid':os.getgid(),
         'file_reads':reads,'network_socket_denied':denied}
Path('probe.json').write_text(json.dumps(value))
assert value['uid'] != 0 and value['gid'] != 0
assert not any(reads.values()) and denied
print('FUSE_ISOLATED_PROBE_OK')
''' % list(hidden_paths)
    return 'python -c ' + shlex.quote(code)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OwnedMockAPI:
    """Only fixed local replies. Cannot proxy, call a model or serve arbitrary files."""
    def __init__(self, mode, probe):
        if mode not in MODES:
            raise ValueError('unknown mock case')
        self.mode, self.probe, self.completions = mode, probe, 0
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                if self.headers.get('Authorization') or self.headers.get('Cookie'):
                    self.send_error(400, 'mock API accepts no credentials')
                    return
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 131072:
                    self.send_error(413)
                    return
                body = json.loads(self.rfile.read(length))
                if self.path == '/tokenize':
                    reply = dict(count=25, max_model_len=100000, simulated=True)
                elif self.path == '/v1/chat/completions':
                    api.completions += 1
                    n = api.completions
                    if api.mode == 'http_error' and n == 2:
                        self.send_error(503, 'deliberate local mock failure')
                        return
                    if api.mode == 'redirect':
                        self.send_response(302)
                        self.send_header('Location', 'http://192.0.2.1/never-follow')
                        self.end_headers()
                        return
                    if api.mode == 'bad_json':
                        self.send_response(200)
                        self.end_headers()
                        self.wfile.write(b'{invalid-json')
                        return
                    action = ({'action': 'submit_patch'} if api.mode == 'no_edit' or n >= 3 else
                              {'action': 'run_command', 'command': WRITE if n == 1 else api.probe})
                    reply = dict(choices=[dict(message=dict(content=json.dumps(action)),
                                 finish_reason='length' if api.mode == 'truncated' and n == 1 else 'stop')],
                                 usage={} if api.mode == 'missing_usage' else
                                 dict(prompt_tokens=25, completion_tokens=5), simulated=True)
                else:
                    self.send_error(404)
                    return
                assert body['model'] == 'offline-scripted-fixture'
                data = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.05), daemon=True)
        self.endpoint = f'http://127.0.0.1:{self.server.server_port}/v1'

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class RecordedAdapters:
    def __init__(self, folder, repo, api, probe, hidden_paths, isolated, config):
        self.folder, self.repo, self.api, self.probe = folder, repo, api, probe
        self.hidden_paths, self.isolated, self.config = hidden_paths, isolated, config
        self.http_index = self.command_index = 0
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def record(self, name, value):
        path = immutable_json(self.folder / name, value)
        return dict(path=name, sha256=sha(path))

    def event(self, value):
        append_event(self.folder / 'durable-events.jsonl', value)

    def open(self, request, timeout=None):
        # A private opener prevents proxy settings and redirects from routing a
        # core HTTP call to anything except our owned ephemeral mock server.
        allowed = {self.api.endpoint + '/chat/completions', self.api.endpoint[:-3] + '/tokenize'}
        if request.full_url not in allowed or request.get_method() != 'POST':
            raise ValueError('only the owned local mock routes are permitted')
        if request.has_header('Authorization') or request.has_header('Cookie'):
            raise ValueError('mock transport must not carry credentials')
        self.http_index += 1
        index = self.http_index
        body = request.data or b''
        ref = self.record(f'http/{index:03}-request.json', dict(url=request.full_url, method='POST',
                          body_base64=base64.b64encode(body).decode(), body_sha256=hashlib.sha256(body).hexdigest(),
                          credentials_sent=False, mock_server=True))
        self.event(dict(event='http_started', index=index, **ref))
        try:
            response = self.opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            data = exc.read(131073)
            exc.close()
            ref = self.record(f'http/{index:03}-response.json', dict(status=exc.code,
                              body_base64=base64.b64encode(data).decode(),
                              body_sha256=hashlib.sha256(data).hexdigest(), mock_server=True))
            self.event(dict(event='http_finished', index=index, **ref))
            raise
        except (OSError, urllib.error.URLError) as exc:
            self.event(dict(event='http_failed', index=index, error_class=type(exc).__name__))
            raise
        with response:
            data = response.read(131073)
            status = response.getcode()
        if len(data) > 131072:
            self.event(dict(event='http_failed', index=index, error_class='ResponseTooLarge'))
            raise ValueError('mock response exceeds recording allowance')
        ref = self.record(f'http/{index:03}-response.json', dict(status=status,
                          body_base64=base64.b64encode(data).decode(), body_sha256=hashlib.sha256(data).hexdigest(),
                          mock_server=True))
        self.event(dict(event='http_finished', index=index, **ref))
        return io.BytesIO(data)

    @contextmanager
    def private_opener(self):
        # Single-writer rehearsal only. Scope ends even if decoding fails; the
        # actual complete()/tokenize_chat() implementations remain unchanged.
        with patch('urllib.request.urlopen', self.open):
            yield

    def request(self, *args):
        with self.private_opener():
            return complete(*args)

    def meter(self, *args):
        with self.private_opener():
            return tokenize_chat(*args)

    def sandbox(self, repo, image, user):
        if Path(repo) != self.repo:
            raise ValueError('recorded sandbox repository differs')
        owner = self
        actual = ChrootSandbox(repo, self.config) if self.isolated else None

        class Adapter:
            def __enter__(self):
                owner.event(dict(event='sandbox_enter_started', isolated=owner.isolated))
                if actual:
                    actual.__enter__()
                owner.event(dict(event='sandbox_entered', isolated=owner.isolated))
                return self

            def run(self, command, timeout):
                owner.command_index += 1
                index = owner.command_index
                ref = owner.record(f'commands/{index:03}-input.json', dict(command=command, timeout=timeout,
                                   isolated=owner.isolated))
                owner.event(dict(event='command_started', index=index, **ref))
                try:
                    if actual:
                        result = actual.run(command, timeout)
                    else:
                        if command == WRITE:
                            (owner.repo / 'marker.txt').write_text('FUSE_HTTP_COMMAND_OK')
                        elif command == owner.probe:
                            (owner.repo / 'probe.json').write_text(json.dumps(dict(simulated=True, uid=None, gid=None,
                                file_reads={p: False for p in owner.hidden_paths}, network_socket_denied=None)))
                        elif command != READINESS:
                            raise ValueError('host fixture adapter cannot execute shell commands')
                        result = dict(returncode=0, stdout='HOST FIXTURE STUB; isolation untested.', stderr='', simulated=True)
                except Exception as exc:
                    owner.event(dict(event='command_failed', index=index, error_class=type(exc).__name__))
                    raise
                ref = owner.record(f'commands/{index:03}-output.json', result)
                owner.event(dict(event='command_finished', index=index, **ref))
                return result

            def __exit__(self, kind, value, traceback):
                owner.event(dict(event='sandbox_exit_started', isolated=owner.isolated))
                if actual:
                    actual.__exit__(kind, value, traceback)
                owner.event(dict(event='sandbox_exited', isolated=owner.isolated))
                return False

        return Adapter()


def run_rehearsal(output, modes=MODES, isolated_config=None):
    if not isinstance(modes, (list, tuple)) or not modes or len(set(modes)) != len(modes) or any(m not in MODES for m in modes):
        raise ValueError('unique fixed mock cases required')
    isolated = isolated_config is not None
    if isolated:
        if platform.system() != 'Linux' or platform.machine() != 'x86_64' or os.geteuid() != 0:
            raise ValueError('isolated mock requires Linux x86_64 root; no host-shell fallback')
        if list(Path('/dev').glob('nvidia[0-9]*')):
            raise ValueError('select a CPU-only runtime before isolated rehearsal')
        validate_identity(isolated_config)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    trusted = output / 'trusted-only'
    trusted.mkdir()
    hidden = [str(trusted / name) for name in ('hidden-tests.txt', 'undisclosed-future.txt', 'other-branch.txt')]
    for path in hidden:
        Path(path).write_text('SYNTHETIC_HOST_ONLY_SENTINEL_' + uuid.uuid4().hex)
        assert Path(path).read_text().startswith('SYNTHETIC_HOST_ONLY_SENTINEL_')
    probe = probe_command(hidden)
    plan = dict(schema_version=1, kind='mock_http_command_integration_not_study', modes=list(modes),
                isolated=isolated, hidden_probe_paths=hidden, write_command=WRITE, probe_command=probe,
                formal_collection_enabled=False, human_gate_satisfied=False, model_execution_enabled=False,
                model_requests=0, usage_kind='synthetic_mock_counts', retry_policy='zero-automatic-retries',
                chroot_identity=isolated_config['sandbox_identity'] if isolated else None,
                source_sha256={name: sha(Path(__file__).parent / name) for name in
                               ('http_command_rehearsal.py', 'agent.py', 'context.py', 'chroot_sandbox.py', 'chroot_linux.py')})
    immutable_json(output / 'plan.json', plan)
    anchor = sha(output / 'plan.json')
    immutable_json(output / 'plan-anchor.json', dict(plan_sha256=anchor, formal_collection_enabled=False))
    for mode in modes:
        append_event(output / 'attempts.jsonl', dict(event='attempt_started', case=mode, attempt_index=0))
        try:
            folder = output / 'sessions' / mode
            repo = folder / 'repo'
            repo.mkdir(parents=True)
            (repo / 'README.txt').write_text('Neutral HTTP/command fixture; no benchmark requirement or test mounted.\n')
            initialize(repo)
            with OwnedMockAPI(mode, probe) as api:
                config = config_for('success')
                config.update(endpoint_url=api.endpoint, max_turns=4, time_limit_seconds=120,
                              api_key_env='FUSE_MOCK_UNUSED_' + uuid.uuid4().hex)
                if isolated:
                    config.update(sandbox_backend='chroot-circuit-v0', sandbox_identity=copy.deepcopy(isolated_config['sandbox_identity']))
                    config.pop('container_image')
                prompt = 'Neutral transport fixture. Follow the documented JSON action schema.'
                immutable_json(folder / 'inputs.json', dict(prompt=prompt, config=config, mode=mode, isolated=isolated))
                adapters = RecordedAdapters(folder, repo, api, probe, hidden, isolated, config)
                metrics = run_agent(repo, prompt, config, 0, folder / 'scaffold-events.jsonl',
                                    request_fn=adapters.request, sandbox_factory=adapters.sandbox, token_count_fn=adapters.meter)
                metrics.update(usage_kind='synthetic_mock_counts', data_kind='infrastructure', model_requests=0,
                               isolated=isolated, mock_http_requests=adapters.http_index)
                immutable_json(folder / 'metrics.json', metrics)
            journal = inspect_journal(folder / 'scaffold-events.jsonl')
            if not journal['pass_']:
                raise ValueError('scaffold log incomplete')
            immutable_json(folder / 'sealed-scaffold-events.json', journal['events'])
            copy_tree(repo, folder / 'snapshot')
            sync_tree(folder)
            files = {str(p.relative_to(output)): sha(p) for p in folder.rglob('*')
                     if p.is_file() and not {'repo', 'snapshot'} & set(p.relative_to(folder).parts)}
            receipt = dict(case=mode, attempt_index=0, data_kind='infrastructure', metrics=metrics,
                           evidence_sha256=files, repository_sha256=tree_digest(repo),
                           snapshot_sha256=tree_digest(folder / 'snapshot'))
            immutable_json(output / 'receipts' / (mode + '.json'), receipt)
            append_event(output / 'attempts.jsonl', dict(event='attempt_finished', case=mode, attempt_index=0,
                         receipt_sha256=sha(output / 'receipts' / (mode + '.json'))))
        except Exception as exc:
            append_event(output / 'attempts.jsonl', dict(event='attempt_failed', case=mode, attempt_index=0,
                         error_class=type(exc).__name__))
            raise
    result = audit(output, anchor)
    immutable_json(output / 'audit.json', result)
    return result


def audit(output, expected_plan_sha256):
    output = Path(output).resolve()
    if sha(checked_file(output, 'plan.json')) != expected_plan_sha256:
        raise ValueError('mock planning ledger hash differs')
    plan = read(output / 'plan.json')
    modes = plan['modes']
    if (not isinstance(modes, list) or not modes or len(set(modes)) != len(modes) or any(m not in MODES for m in modes) or
        type(plan['isolated']) is not bool or plan['formal_collection_enabled'] is not False or
        plan['human_gate_satisfied'] is not False or plan['model_execution_enabled'] is not False or
        plan['model_requests'] != 0 or plan['usage_kind'] != 'synthetic_mock_counts' or
        plan['retry_policy'] != 'zero-automatic-retries' or
        plan['write_command'] != WRITE or plan['probe_command'] != probe_command(plan['hidden_probe_paths'])):
        raise ValueError('invalid mock-only plan')
    if list(output.rglob('*.part')):
        raise ValueError('partial publication remains unresolved')
    outer = inspect_journal(output / 'attempts.jsonl')
    if not outer['pass_'] or len(outer['events']) != 2 * len(modes):
        raise ValueError('unresolved or corrupt originating attempt journal')
    if {p.name for p in (output / 'receipts').glob('*.json')} != {m + '.json' for m in modes}:
        raise ValueError('missing or unexpected originating receipt')
    results = []
    for index, mode in enumerate(modes):
        receipt_path = checked_file(output, f'receipts/{mode}.json')
        if outer['events'][index*2:index*2+2] != [dict(event='attempt_started', case=mode, attempt_index=0),
            dict(event='attempt_finished', case=mode, attempt_index=0, receipt_sha256=sha(receipt_path))]:
            raise ValueError('originating attempt order/receipt differs')
        receipt = read(receipt_path)
        folder = output / 'sessions' / mode
        files = {str(p.relative_to(output)) for p in folder.rglob('*')
                 if p.is_file() and not {'repo', 'snapshot'} & set(p.relative_to(folder).parts)}
        if (receipt['case'] != mode or type(receipt['attempt_index']) is not int or receipt['attempt_index'] != 0 or
            receipt['data_kind'] != 'infrastructure' or set(receipt['evidence_sha256']) != files):
            raise ValueError('receipt identity or complete evidence manifest differs')
        for name, digest in receipt['evidence_sha256'].items():
            if sha(checked_file(output, name)) != digest:
                raise ValueError('retained HTTP/command evidence hash differs')
        if (tree_digest(folder / 'repo') != receipt['repository_sha256'] or
            tree_digest(folder / 'snapshot') != receipt['snapshot_sha256'] or
            receipt['repository_sha256'] != receipt['snapshot_sha256']):
            raise ValueError('repository/snapshot differs')
        metrics = read(folder / 'metrics.json')
        inputs = read(folder / 'inputs.json')
        if metrics != receipt['metrics'] or metrics['isolated'] is not plan['isolated'] or inputs['mode'] != mode:
            raise ValueError('metrics/input identity differs')
        events = inspect_journal(folder / 'durable-events.jsonl')
        scaffold = inspect_journal(folder / 'scaffold-events.jsonl')
        sealed = read(folder / 'sealed-scaffold-events.json')
        if not events['pass_'] or not scaffold['pass_'] or scaffold['events'] != sealed:
            raise ValueError('session journal or sealed log differs')
        expected_metrics = dict(metrics)
        for key in ('usage_kind', 'data_kind', 'model_requests', 'isolated', 'mock_http_requests'):
            expected_metrics.pop(key)
        if sealed[-1] != dict(event='session_end', metrics=expected_metrics):
            raise ValueError('scaffold final metrics differ')
        pending = {}; http_n = cmd_n = 0
        lifecycle = []
        for event in events['events']:
            kind = event['event']
            if kind.startswith('sandbox_'):
                if event != dict(event=kind, isolated=plan['isolated']):
                    raise ValueError('sandbox lifecycle identity differs')
                lifecycle.append(kind)
                continue
            if kind not in ('http_started', 'http_finished', 'command_started', 'command_finished'):
                raise ValueError('unresolved or unknown adapter event')
            operation = 'http' if kind.startswith('http_') else 'commands'
            n = event['index']
            started = kind.endswith('started')
            name = f'{operation}/{n:03}-' + ('request.json' if started else 'response.json') if operation == 'http' else f'commands/{n:03}-' + ('input.json' if started else 'output.json')
            path = checked_file(folder, name)
            if event != dict(event=kind, index=n, path=name, sha256=sha(path)):
                raise ValueError('adapter operation hash/identity differs')
            if started:
                if operation in pending:
                    raise ValueError('overlapping adapter operation')
                if operation == 'http':
                    http_n += 1
                    if n != http_n:
                        raise ValueError('HTTP order differs')
                else:
                    cmd_n += 1
                    if n != cmd_n:
                        raise ValueError('command order differs')
                pending[operation] = n
            elif pending.pop(operation, None) != n:
                raise ValueError('unbound adapter result')
            value = read(path)
            if operation == 'http':
                raw = base64.b64decode(value['body_base64'], validate=True)
                if hashlib.sha256(raw).hexdigest() != value['body_sha256'] or value['mock_server'] is not True:
                    raise ValueError('wire body digest differs')
                if started:
                    allowed = {inputs['config']['endpoint_url'] + '/chat/completions', inputs['config']['endpoint_url'][:-3] + '/tokenize'}
                    if value['url'] not in allowed or value['credentials_sent'] is not False:
                        raise ValueError('unowned route or credentials in mock request')
        if pending or lifecycle != ['sandbox_enter_started', 'sandbox_entered', 'sandbox_exit_started', 'sandbox_exited']:
            raise ValueError('unresolved command or sandbox lifecycle')
        expected = {'success': ('submitted', 3, 3), 'no_edit': ('submitted', 1, 1),
                    'http_error': ('infrastructure_error', 1, 2), 'bad_json': ('infrastructure_error', 0, 1),
                    'missing_usage': ('infrastructure_error', 1, 1), 'truncated': ('submitted', 3, 2),
                    'redirect': ('infrastructure_error', 0, 1)}[mode]
        status, turns, commands = expected
        if (metrics['status'], metrics['turns'], cmd_n) != (status, turns, commands) or metrics['mock_http_requests'] != http_n:
            raise ValueError('scripted status or operation counts differ')
        expected_http = 6 if mode in ('success', 'truncated') else 4 if mode == 'http_error' else 2
        if http_n != expected_http:
            raise ValueError('HTTP retry or missing operation')
        if status == 'infrastructure_error' and (metrics['tokens'] is not None or metrics['input_tokens'] is not None):
            raise ValueError('unknown complete usage must stay null')
        marker = folder / 'snapshot/marker.txt'
        if marker.exists() != (mode in ('success', 'http_error')):
            raise ValueError('truncated/invalid action executed or expected export missing')
        probe_path = folder / 'snapshot/probe.json'
        boundary = False
        if mode in ('success', 'truncated'):
            probe = read(probe_path)
            if set(probe['file_reads']) != set(plan['hidden_probe_paths']) or any(probe['file_reads'].values()):
                raise ValueError('outside-workspace read probe differs')
            if plan['isolated']:
                boundary = (probe['simulated'] is False and probe['uid'] > 0 and probe['gid'] > 0 and
                            probe['network_socket_denied'] is True)
                if not boundary:
                    raise ValueError('real isolated command probe failed')
            elif probe['simulated'] is not True or probe['network_socket_denied'] is not None:
                raise ValueError('host stub cannot claim an isolation pass')
        results.append(dict(case=mode, status=status, mock_HTTP_requests=http_n, command_operations=cmd_n,
                            marker_exported=marker.exists(), real_isolation_probe_pass=boundary,
                            complete_usage_known=metrics['tokens'] is not None))
    return dict(pass_=True, kind='mock_http_command_integration_not_study', plan_sha256=expected_plan_sha256,
                journal_sha256=outer['raw_sha256'], cases=results, isolated=plan['isolated'],
                real_isolation_validated=plan['isolated'] and any(r['real_isolation_probe_pass'] for r in results),
                formal_collection_enabled=False, human_gate_satisfied=False, model_execution_enabled=False,
                model_requests=0, GPU_used=False, automatic_retries=0,
                limitations='Owned loopback API provides scripted counts/actions, not model inference or real tokenization. '
                'Host stub never validates isolation. Linux chroot uses a shared kernel and selected probes only. '
                'Single writer; no live release, human authentication, GPU compatibility or power-loss certification.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    host = sub.add_parser('run-host-mock')
    host.add_argument('--output', type=Path, required=True)
    isolated = sub.add_parser('run-isolated-mock')
    isolated.add_argument('--output', type=Path, required=True)
    isolated.add_argument('--CPU-boundary-config', dest='cpu_config', type=Path, required=True)
    check = sub.add_parser('audit')
    check.add_argument('--input', type=Path, required=True)
    check.add_argument('--expected-plan-sha256', required=True)
    args = parser.parse_args()
    if args.command == 'audit':
        result = audit(args.input, args.expected_plan_sha256)
    else:
        result = run_rehearsal(args.output, isolated_config=read(args.cpu_config) if args.command == 'run-isolated-mock' else None)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
