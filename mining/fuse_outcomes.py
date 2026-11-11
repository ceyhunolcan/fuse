"""pytest plugin: write {nodeid: outcome} to $FUSE_OUTCOMES (failed if setup, call or teardown failed)."""
import json, os

_res = {}


def pytest_runtest_logreport(report):
    cur = _res.get(report.nodeid)
    if report.failed:
        _res[report.nodeid] = 'failed'
    elif report.skipped and cur is None:
        _res[report.nodeid] = 'skipped'
    elif report.when == 'call' and report.passed and cur is None:
        _res[report.nodeid] = 'passed'


def pytest_sessionfinish(session):
    with open(os.environ['FUSE_OUTCOMES'], 'w') as f:
        json.dump(_res, f)
