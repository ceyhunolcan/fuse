import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_suite(repo, task, phase, timeout=90):
    tests = Path(task) / phase / "tests"
    env = {**os.environ, "FUSE_REPO": str(Path(repo).resolve()), "PYTHONPATH": str(ROOT),
           "PYTHONDONTWRITEBYTECODE": "1"}
    start = time.monotonic()
    try:
        result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(tests), "-v"],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
        return {"pass": result.returncode == 0, "returncode": result.returncode,
                "stdout": result.stdout, "stderr": result.stderr, "seconds": time.monotonic()-start}
    except subprocess.TimeoutExpired:
        return {"pass": False, "returncode": None, "stdout": "", "stderr": "acceptance timeout",
                "seconds": time.monotonic()-start}


def run_suite_prospective(repo,task,phase,config):
    """Explicit unresolved statuses; no log-text inference or legacy conversion."""
    import hashlib
    import json
    import signal
    import tempfile
    import threading
    from evaluator.grade_policy import POLICY,validate_config,validate_grade
    from runner.sandbox import SandboxCleanupError
    validate_config(config)
    if config.get('grading_policy')!=POLICY:raise ValueError('explicit structured grading required')
    timeout=config['grader_suite_timeout_seconds'];command_timeout=config['grader_command_timeout_seconds']
    report_capture_max=32*1024**2
    start=time.monotonic()
    limits=dict(suite_timeout_seconds=timeout,command_timeout_seconds=command_timeout,
                bridge_watchdog_seconds=command_timeout+10,log_capture_max_bytes_per_stream=65536,
                candidate_capture_max_bytes_per_stream=config['grader_capture_max_bytes_per_stream'],
                worker_report_capture_max_bytes=report_capture_max)
    def unresolved(reason,**details):
        return dict(policy=POLICY,grade_status='unresolved',**{'pass':None},returncode=None,
            tests_run=None,assertion_failures=[],unresolved_reasons=[dict(reason=reason,**details)],
            suite_complete=False,limits=limits,stdout='',stderr='',seconds=time.monotonic()-start)
    container=os.environ.get('FUSE_TEST_CONTAINER');chroot=os.environ.get('FUSE_TEST_CHROOT')
    if bool(container)==bool(chroot):return unresolved('sandbox_selection')
    env={**os.environ,'FUSE_REPO':str(Path(repo).resolve()),'PYTHONPATH':str(ROOT),
         'PYTHONDONTWRITEBYTECODE':'1','FUSE_GRADING_POLICY':POLICY,
         'FUSE_GRADER_COMMAND_TIMEOUT_SECONDS':str(command_timeout),
         'FUSE_GRADER_CAPTURE_MAX_BYTES_PER_STREAM':str(config['grader_capture_max_bytes_per_stream'])}
    with tempfile.TemporaryDirectory(prefix='fuse-trusted-grade-') as folder:
        report_path=Path(folder)/'result.json'
        command=[sys.executable,'-m','evaluator.grade_worker','--tests',str(Path(task)/phase/'tests'),
                 '--result',str(report_path)]
        try:
            process=subprocess.Popen(command,cwd=ROOT,env=env,stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,close_fds=True)
        except OSError as exc:return unresolved('grader_launch',error_class=type(exc).__name__)
        buffers=[bytearray(),bytearray()];truncated=[False,False]
        def drain(stream,index):
            while data:=stream.read(4096):
                space=65536-len(buffers[index]);buffers[index].extend(data[:space]);truncated[index]|=len(data)>space
        threads=[threading.Thread(target=drain,args=(stream,index),daemon=True)
                 for index,stream in enumerate([process.stdout,process.stderr])]
        for thread in threads:thread.start()
        timed_out=False;wait_error=None
        try:process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:timed_out=True
        except OSError as exc:wait_error=type(exc).__name__
        finally:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            except OSError as exc:raise SandboxCleanupError('trusted grader process group cleanup unresolved') from exc
            try:process.wait(timeout=5)
            except (OSError,subprocess.TimeoutExpired) as exc:
                raise SandboxCleanupError('trusted grader process reap unresolved') from exc
            for thread in threads:thread.join(timeout=2)
            if any(t.is_alive() for t in threads):raise SandboxCleanupError('trusted grader pipes remain open')
        output=dict(stdout=buffers[0].decode(errors='replace'),stderr=buffers[1].decode(errors='replace'),
                    log_truncated=any(truncated),grader_process_reaped=True,grader_pid=process.pid)
        raw=None
        if report_path.exists():
            with report_path.open('rb') as stream:raw=stream.read(report_capture_max+1)
        output.update(worker_report_raw=None if raw is None else raw[:report_capture_max].decode(errors='replace'),
            worker_report_capture_truncated=raw is not None and len(raw)>report_capture_max,
            worker_report_sha256=None if raw is None or len(raw)>report_capture_max else hashlib.sha256(raw).hexdigest())
        if timed_out:
            report=unresolved('suite_watchdog',timeout_seconds=timeout)
        elif wait_error:
            report=unresolved('grader_wait',error_class=wait_error)
        else:
            try:
                if raw is None or len(raw)>report_capture_max:raise ValueError('structured grader record absent or exceeds limit')
                report=json.loads(raw);validate_grade(report)
                expected={'accepted':0,'rejected':1,'unresolved':2}[report['grade_status']]
                if process.returncode!=expected:raise ValueError('structured status/worker code mismatch')
                report.update(returncode=process.returncode,limits=limits,
                    worker_report_sha256=hashlib.sha256(raw).hexdigest())
            except (OSError,ValueError,TypeError,KeyError):report=unresolved('worker_report_invalid_or_absent')
        report.update(output,seconds=time.monotonic()-start)
        return validate_grade(report)


def uninvoked_grade(config,reason):
    from evaluator.grade_policy import POLICY,validate_config,validate_grade
    validate_config(config)
    if config.get('grading_policy')!=POLICY:raise ValueError('explicit structured grading required')
    return validate_grade(dict(policy=POLICY,grade_status='unresolved',**{'pass':None},returncode=None,
        tests_run=0,assertion_failures=[],unresolved_reasons=[dict(reason=reason)],suite_complete=False,
        invoked=False,seconds=0,stdout='',stderr='',limits=dict(
            suite_timeout_seconds=config['grader_suite_timeout_seconds'],
            command_timeout_seconds=config['grader_command_timeout_seconds'],
            candidate_capture_max_bytes_per_stream=config['grader_capture_max_bytes_per_stream'])))
