"""Opt-in local model transport with a parent-enforced client deadline.

Only the controller invokes this trusted worker. It is not a command sandbox.
Raw partial bytes are retained; killing a client does not prove server cancellation.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


POLICY = 'deadline-worker-v1'


class ClientDeadlineExceeded(TimeoutError):
    pass


class WorkerCleanupError(OSError):
    pass


class ResponseTooLarge(ValueError):
    pass


class IncompleteResponse(ValueError):
    pass


class HTTPResponseError(OSError):
    pass


class EarlySocketTimeout(OSError):
    """An observed early socket failure remains infrastructure missingness."""
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def validate_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or
            parsed.username or parsed.password or parsed.query or parsed.fragment or
            parsed.path not in ('/v1/chat/completions', '/tokenize') or
            parsed.port is None or not 1 <= parsed.port <= 65535):
        raise ValueError('deadline transport requires numeric loopback and fixed API routes')


def validate_policy(config):
    policy = config.get('http_transport', 'urllib-v0')
    if policy not in ('urllib-v0', POLICY):
        raise ValueError('unsupported HTTP transport')
    if policy == POLICY:
        if config.get('timeout_policy') != 'session-deadline-v1':
            raise ValueError('deadline transport requires session deadline policy')
        validate_url(config['endpoint_url'].rstrip('/') + '/chat/completions')
        cap = config.get('http_response_max_bytes')
        socket_timeout = config.get('http_socket_timeout_seconds')
        if type(cap) is not int or not 1024 <= cap <= 64 * 1024 * 1024:
            raise ValueError('pin response byte limit between 1 KiB and 64 MiB')
        if (type(socket_timeout) not in (int, float) or
                not math.isfinite(socket_timeout) or socket_timeout <= 0):
            raise ValueError('pin a finite positive socket timeout')


def save_json(path, value):
    with Path(path).open('x') as stream:
        stream.write(json.dumps(value, indent=2) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def worker(config):
    """One POST only, no retries, proxies, redirects, or candidate-file reads."""
    folder = Path(config['folder'])
    validate_url(config['url'])
    body = (folder / 'request.body').read_bytes()
    headers = {'Content-Type': 'application/json'}
    if config.get('authorization'):
        headers['Authorization'] = config['authorization']
    request = urllib.request.Request(config['url'], data=body, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    status = None
    count = 0
    try:
        try:
            response = opener.open(request, timeout=config['socket_timeout'])
        except urllib.error.HTTPError as exc:
            response = exc  # Retain error bodies too, without following Location.
        with response:
            status = response.getcode()
            length_header = response.headers.get('Content-Length')
            expected = int(length_header) if length_header is not None else None
            if expected is not None and expected < 0:
                raise IncompleteResponse('invalid length')
            save_json(folder / 'response-head.json', dict(status=status, content_length=expected))
            with (folder / 'response.body.part').open('xb', buffering=0) as stream:
                while True:
                    chunk = response.read1(min(4096, config['max_bytes'] + 1 - count))
                    if not chunk:
                        break
                    stream.write(chunk)
                    count += len(chunk)
                    if count > config['max_bytes']:
                        raise ResponseTooLarge('response recording allowance exceeded')
                os.fsync(stream.fileno())
            if expected is not None and count != expected:
                raise IncompleteResponse('body does not match declared length')
        (folder / 'response.body.part').rename(folder / 'response.body')
        result = dict(state='complete', status=status, response_bytes=count,
                      observed_at=time.monotonic())
    except Exception as exc:
        # Error text can contain credentials or server details. Retain class only.
        result = dict(state='transport_error', error_class=type(exc).__name__, status=status,
                      response_bytes=count, observed_at=time.monotonic(),
                      socket_timeout=isinstance(exc, TimeoutError) or (
                          isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError)))
    save_json(folder / 'worker-result.json', result)


def _reap(process):
    if process.poll() is None:
        process.kill()
    try:
        process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        return False
    return process.poll() is not None


def request_json(config, url, payload, headers, timeout):
    """Accept JSON only after a complete, timely response and worker cleanup."""
    validate_policy(config)
    validate_url(url)
    endpoint = config['endpoint_url'].rstrip('/')
    if url not in (endpoint + '/chat/completions', endpoint[:-3] + '/tokenize'):
        raise ValueError('request must use the pinned local endpoint')
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('positive remaining allowance required')
    started = time.monotonic()
    deadline = min(started + timeout, config.get('_http_session_deadline', started + timeout))
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise ValueError('finite session deadline required')
    root = Path(config['_http_evidence_root'])
    folder = root / uuid.uuid4().hex
    folder.mkdir(exist_ok=False)
    body = json.dumps(payload).encode()
    with (folder / 'request.body').open('xb') as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    save_json(folder / 'request.json', dict(policy=POLICY, url=url, method='POST',
        request_sha256=hashlib.sha256(body).hexdigest(), deadline=deadline,
        socket_timeout=config['http_socket_timeout_seconds'], max_response_bytes=config['http_response_max_bytes'],
        credentials_sent=bool(headers.get('Authorization')), retry_policy='zero-automatic-retries'))
    result = dict(policy=POLICY, started_at=started, deadline=deadline, response_accepted=False,
                  worker_started=False, worker_reaped=True, server_cancellation_verified=False)
    process = None
    failure = None
    decoded = None
    try:
        if time.monotonic() >= deadline:
            result['state'] = 'deadline'
            raise ClientDeadlineExceeded()
        child_config = dict(url=url, folder=str(folder.resolve()),
            socket_timeout=min(config['http_socket_timeout_seconds'], timeout),
            max_bytes=config['http_response_max_bytes'], authorization=headers.get('Authorization'))
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True,
            env={k:v for k,v in os.environ.items() if k in ('PATH','SYSTEMROOT','TMPDIR')})
        result.update(worker_started=True, worker_pid=process.pid)
        try:
            process.communicate(json.dumps(child_config).encode(),
                                timeout=max(.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            result['state'] = 'deadline'
            raise ClientDeadlineExceeded() from None
        result['worker_returncode'] = process.returncode
        saved = folder / 'worker-result.json'
        observed = json.loads(saved.read_text()) if saved.exists() else None
        if process.returncode != 0 or observed is None:
            result['state'] = 'worker_error'
            raise OSError('HTTP worker failed')
        result['worker_result'] = observed
        # Preserve an early socket error even if parent scheduling resumes late.
        if observed['state'] != 'complete':
            result['state'] = 'transport_error'
            errors = {'ResponseTooLarge': ResponseTooLarge,
                      'IncompleteResponse': IncompleteResponse}
            error_type = errors.get(observed['error_class'], OSError)
            if observed.get('socket_timeout'):
                if observed['observed_at'] >= deadline:
                    result['state'] = 'deadline'
                    raise ClientDeadlineExceeded()
                error_type = EarlySocketTimeout
            raise error_type()
        if not 200 <= observed['status'] < 300:
            result['state'] = 'http_error'
            raise HTTPResponseError()
        if time.monotonic() >= deadline:
            result['state'] = 'deadline'
            raise ClientDeadlineExceeded()
        try:
            decoded = json.loads((folder / 'response.body').read_bytes())
        except (ValueError, UnicodeError):
            result['state'] = 'decode_error'
            raise
        if time.monotonic() >= deadline:
            result['state'] = 'deadline'
            raise ClientDeadlineExceeded()
        result.update(state='complete', response_accepted=True)
    except BaseException as exc:
        failure = exc
        result.setdefault('state', 'interrupted' if not isinstance(exc, Exception) else 'parent_error')
        result['error_class'] = type(exc).__name__
    finally:
        if process is not None:
            result['worker_reaped'] = _reap(process)
            result['worker_returncode'] = process.returncode
        if not result['worker_reaped']:
            result.update(state='cleanup_unresolved', response_accepted=False)
            failure = WorkerCleanupError()
        result['finished_at'] = time.monotonic()
        # Cleanup or evidence writes can use time. Recheck before returning data.
        if result['response_accepted'] and result['finished_at'] >= deadline:
            result.update(state='deadline', response_accepted=False, error_class='ClientDeadlineExceeded')
            failure = ClientDeadlineExceeded()
        result['raw_files'] = {p.name:dict(bytes=p.stat().st_size,
            sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in folder.glob('response.body*')}
        save_json(folder / 'parent-result.json', result)
    if failure is not None:
        raise failure
    if time.monotonic() >= deadline:
        save_json(folder / 'delivery-refused.json', dict(state='deadline', response_accepted=False))
        raise ClientDeadlineExceeded()
    return decoded


if __name__ == '__main__':
    if sys.argv[1:] != ['--worker']:
        raise SystemExit('trusted HTTP worker entry point only')
    worker(json.loads(sys.stdin.buffer.read()))
