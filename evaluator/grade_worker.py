"""Trusted unittest worker. The result file remains outside the candidate mount."""
import argparse
import json
import os
from pathlib import Path
import sys
import unittest
from evaluator.grade_policy import POLICY, GraderCommandTimeout, GraderInfrastructureError


class Result(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.unresolved=[]
        self.case_failures=[]

    def operational_error(self,test,error):
        kind=error[0]
        reason=('command_watchdog' if issubclass(kind,GraderCommandTimeout)
                else 'grader_transport_or_bridge' if issubclass(kind,GraderInfrastructureError)
                else 'unexpected_grader_error')
        self.unresolved.append(dict(test_id=test.id(),reason=reason,error_class=kind.__name__,
            provenance=getattr(error[1],'provenance',{})))
        # No further candidate calls after unresolved operational state.
        self.failfast=True
        self.stop()

    def addError(self,test,error):
        self.operational_error(test,error)
        super().addError(test,error)

    def addFailure(self,test,error):
        self.case_failures.append(dict(test_id=test.id(),error_class=error[0].__name__))
        super().addFailure(test,error)

    def addSubTest(self,test,subtest,error):
        if error is not None:
            if issubclass(error[0],test.failureException):
                self.case_failures.append(dict(test_id=subtest.id(),error_class=error[0].__name__))
            else:
                self.operational_error(subtest,error)
        super().addSubTest(test,subtest,error)


def classify(result):
    reasons=list(result.unresolved)
    if result.testsRun==0:
        reasons.append(dict(reason='no_tests_executed'))
    if result.skipped or result.expectedFailures or result.unexpectedSuccesses:
        reasons.append(dict(reason='suite_has_unaccepted_skip_or_expectation',
            skipped=len(result.skipped),expected_failures=len(result.expectedFailures),
            unexpected_successes=len(result.unexpectedSuccesses)))
    status='unresolved' if reasons else 'rejected' if result.case_failures else 'accepted'
    return dict(policy=POLICY,grade_status=status,
        **{'pass':True if status=='accepted' else False if status=='rejected' else None},
        tests_run=result.testsRun,assertion_failures=result.case_failures,
        unresolved_reasons=reasons,suite_complete=not reasons)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tests',required=True,type=Path)
    p.add_argument('--result',required=True,type=Path)
    a=p.parse_args()
    if os.environ.get('FUSE_GRADING_POLICY')!=POLICY:
        raise ValueError('explicit prospective grading worker required')
    suite=unittest.defaultTestLoader.discover(str(a.tests))
    result=unittest.TextTestRunner(verbosity=2,resultclass=Result).run(suite)
    report=classify(result)
    with a.result.open('x') as stream:
        json.dump(report,stream,indent=2,sort_keys=True);stream.write('\n')
    return 0 if report['grade_status']=='accepted' else 1 if report['grade_status']=='rejected' else 2


if __name__=='__main__':sys.exit(main())
