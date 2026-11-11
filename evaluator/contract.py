"""Black-box CLI helpers; acceptance tests never import candidate internals."""
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from evaluator.grade_policy import (selected, decode_candidate_json,
    GraderCommandTimeout, GraderInfrastructureError)


def prospective_candidate(args,repo):
    from evaluator.grade_bridge import DOCKER_CODE
    timeout=int(os.environ['FUSE_GRADER_COMMAND_TIMEOUT_SECONDS'])
    capture=int(os.environ['FUSE_GRADER_CAPTURE_MAX_BYTES_PER_STREAM'])
    if not 1 <= timeout <= 60:raise GraderInfrastructureError('invalid declared CLI limit',reason='invalid_limit')
    if not 1 <= capture <= 16*1024**2:raise GraderInfrastructureError('invalid declared capture limit',reason='invalid_capture_limit')
    chroot=os.environ.get('FUSE_TEST_CHROOT');container=os.environ.get('FUSE_TEST_CONTAINER')
    if bool(chroot)==bool(container):
        raise GraderInfrastructureError('exactly one grader sandbox required',reason='sandbox_selection')
    args=list(args);args[0]='python'
    if chroot:
        bridge=Path(__file__).resolve().parents[1]/'runner/chroot_exec.py'
        command=[sys.executable,'-I',str(bridge),chroot,shlex.join(args),str(timeout),str(capture)]
        backend='chroot-circuit-v0'
    else:
        command=['docker','exec',container,'python','-I','-c',DOCKER_CODE,json.dumps(args),str(timeout),str(capture)]
        backend='docker'
    try:
        result=subprocess.run(command,capture_output=True,text=True,timeout=timeout+10)
    except subprocess.TimeoutExpired as exc:
        raise GraderInfrastructureError('trusted grader bridge watchdog expired',
            reason='bridge_watchdog',backend=backend,timeout_seconds=timeout+10) from exc
    except OSError as exc:
        raise GraderInfrastructureError('trusted grader bridge could not run',
            reason='bridge_launch',backend=backend,error_class=type(exc).__name__) from exc
    if result.returncode!=0:
        raise GraderInfrastructureError('trusted grader bridge returned nonzero',
            reason='bridge_returncode',backend=backend,returncode=result.returncode)
    try:
        payload=json.loads(result.stdout)
        if (not isinstance(payload,dict) or type(payload.get('returncode')) is not int
                or type(payload.get('timed_out')) is not bool
                or any(not isinstance(payload.get(k),str) for k in ('stdout','stderr'))):
            raise ValueError('invalid trusted grader envelope')
    except (ValueError,TypeError) as exc:
        raise GraderInfrastructureError('trusted grader bridge result invalid',
            reason='bridge_envelope',backend=backend) from exc
    if payload['timed_out']:
        raise GraderCommandTimeout('declared candidate CLI watchdog expired',
            backend=backend,timeout_seconds=timeout,partial_stdout=payload['stdout'],
            partial_stderr=payload['stderr'],truncated=payload.get('truncated'))
    if payload.get('truncated') is not False:
        raise GraderInfrastructureError('candidate output capture is incomplete',
            reason='candidate_output_capture_limit',backend=backend)
    return subprocess.CompletedProcess(args,payload['returncode'],payload['stdout'],payload['stderr'])


def run_candidate(args, repo, timeout=5):
    args=list(args)
    if selected():return prospective_candidate(args,repo)
    if os.environ.get('FUSE_TEST_CHROOT'):
        descriptor=os.environ['FUSE_TEST_CHROOT']
        args[0]='python'
        bridge=Path(__file__).resolve().parents[1]/'runner/chroot_exec.py'
        result=subprocess.run([sys.executable,'-I',str(bridge),descriptor,shlex.join(args),str(timeout)],
                              capture_output=True,text=True,timeout=timeout+10)
        if result.returncode!=0:
            raise ValueError('trusted grading bridge failed: '+result.stderr[:1000])
        payload=json.loads(result.stdout)
        if payload['timed_out']:
            raise subprocess.TimeoutExpired(args,timeout)
        return subprocess.CompletedProcess(args,payload['returncode'],payload['stdout'],payload['stderr'])
    if os.environ.get('FUSE_TEST_CONTAINER'):
        args=['docker','exec',os.environ['FUSE_TEST_CONTAINER'],'python',*args[1:]]
    return subprocess.run(args,cwd=repo,capture_output=True,text=True,timeout=timeout,
                          env={**os.environ,'PYTHONPATH':str(repo),'PYTHONDONTWRITEBYTECODE':'1'})


def invoke(source, values, fmt=None):
    repo = Path(os.environ["FUSE_REPO"]).resolve()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", dir=repo, delete=False) as file:
        file.write(source)
        path = Path(file.name)
    args = [os.environ.get("FUSE_PYTHON", sys.executable), "-m", "fusecircuit", str(path), "--inputs", json.dumps(values)]
    if fmt is not None:
        args += ["--format", fmt]
    sandbox_input=None
    if os.environ.get('FUSE_TEST_CHROOT'):
        descriptor=json.loads(Path(os.environ['FUSE_TEST_CHROOT']).read_text())
        sandbox_input=Path(descriptor['root'])/'workspace'/path.name
        sandbox_input.write_text(source)
        os.chown(sandbox_input,descriptor['uid'],descriptor['uid'])
        args[3]='/workspace/'+path.name
    elif os.environ.get('FUSE_TEST_CONTAINER'):
        args[3]='/workspace/'+path.name
    try:
        return run_candidate(args,repo)
    finally:
        path.unlink(missing_ok=True)
        if sandbox_input is not None: sandbox_input.unlink(missing_ok=True)


class Contract(unittest.TestCase):
    def good(self, source, values, expected, fmt=None):
        result = invoke(source, values, fmt)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(decode_candidate_json(result.stdout), expected)

    def bad(self, source, values, fmt=None):
        result = invoke(source, values, fmt)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")
        self.assertTrue(result.stderr.strip())
