"""Explicit prospective grader failure categories; legacy helpers stay selected by default."""
import json
import os

POLICY = 'structured-grading-v1'


class GraderCommandTimeout(RuntimeError):
    def __init__(self,message,**provenance):
        super().__init__(message);self.provenance=provenance


class GraderInfrastructureError(RuntimeError):
    def __init__(self,message,**provenance):
        super().__init__(message);self.provenance=provenance


def selected():
    return os.environ.get('FUSE_GRADING_POLICY') == POLICY


def decode_candidate_json(raw):
    if not selected():
        return json.loads(raw)
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise AssertionError('candidate response is not valid JSON') from exc


def validate_config(config):
    policy=config.get('grading_policy')
    if policy is None:
        if any(k in config for k in ('grader_suite_timeout_seconds','grader_command_timeout_seconds','grader_capture_max_bytes_per_stream')):
            raise ValueError('grader limits require an explicit grading policy')
        return
    if policy != POLICY:
        raise ValueError('unsupported grading policy')
    for key,ceiling in [('grader_suite_timeout_seconds',900),('grader_command_timeout_seconds',60),
                        ('grader_capture_max_bytes_per_stream',16*1024**2)]:
        if type(config.get(key)) is not int or not 1 <= config[key] <= ceiling:
            raise ValueError('invalid prospective grader budget: '+key)


def validate_grade(grade):
    if not isinstance(grade,dict) or grade.get('policy')!=POLICY:
        raise ValueError('structured original grade required')
    status=grade.get('grade_status')
    expected={'accepted':True,'rejected':False,'unresolved':None}
    if status not in expected or grade.get('pass') is not expected[status]:
        raise ValueError('grade status/pass mismatch')
    reasons=grade.get('unresolved_reasons')
    if not isinstance(reasons,list) or any(not isinstance(x,dict) or not x.get('reason') for x in reasons):
        raise ValueError('invalid unresolved grader provenance')
    if (status=='unresolved') != bool(reasons):
        raise ValueError('unresolved grader reasons mismatch')
    if grade.get('suite_complete') is not (status!='unresolved'):
        raise ValueError('suite completion/status mismatch')
    failures=grade.get('assertion_failures')
    if not isinstance(failures,list):raise ValueError('assertion evidence required')
    if status!='unresolved':
        if type(grade.get('tests_run')) is not int or grade['tests_run']<=0:
            raise ValueError('resolved grade needs executed tests')
        if (status=='rejected') != bool(failures):raise ValueError('resolved assertion evidence mismatch')
    return grade
