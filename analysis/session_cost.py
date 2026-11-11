"""Synthetic-only session accounting prototype; no collection or study adapter."""
import argparse
from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path

FIELDS = ('tokens', 'input_tokens', 'tool_calls', 'wall_seconds')
IDENTITY = ('phase', 'task_id', 'upstream_repetition', 'disclosed_future',
            'realized_future', 'downstream_repetition')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def assignments(plan):
    if (type(plan.get('schema_version')) is not int or plan['schema_version'] != 1 or
        plan.get('data_kind') != 'synthetic' or plan.get('formal_collection_enabled') is not False or
        plan.get('retry_policy') != 'zero-automatic-retries-v1'):
        raise ValueError('synthetic closed plan required')
    tasks = plan.get('task_ids')
    if (not isinstance(tasks, list) or not tasks or
        any(not isinstance(t, str) or not t for t in tasks) or len(set(tasks)) != len(tasks)):
        raise ValueError('invalid tasks')
    for field in ('upstream_repetitions', 'downstream_repetitions'):
        values = plan.get(field)
        if (not isinstance(values, list) or not values or
            any(type(v) is not int or v < 0 for v in values) or len(set(values)) != len(values)):
            raise ValueError('invalid repetitions')
    keys = set()
    for task in tasks:
        for r in plan['upstream_repetitions']:
            for d in ('B1', 'B2'):
                keys.add(('A', task, r, d, None, None))
                for future in ('B1', 'B2'):
                    for s in plan['downstream_repetitions']:
                        keys.add(('B', task, r, d, future, s))
    return keys


def identity(record):
    for field in ('upstream_repetition', 'downstream_repetition'):
        if record.get(field) is not None and type(record[field]) is not int:
            raise ValueError('repetition identity must exclude Boolean values')
    return tuple(record.get(field) for field in IDENTITY)


def aggregate(records):
    attempted = [r for r in records if r['invocation_state'] != 'not_started']
    report = dict(assigned=len(records), attempted=len(attempted),
        not_started=len(records)-len(attempted),
        finished=sum(r['invocation_state']=='finished' for r in records),
        interrupted=sum(r['invocation_state']=='interrupted' for r in records),
        outcome_counts=dict(sorted(Counter(r['outcome'] for r in records).items())))
    for field in FIELDS:
        known = [r['metrics'][field] for r in attempted if r['metrics'][field] is not None]
        missing = len(attempted)-len(known)
        stats = dict(known_sessions=len(known), unknown_sessions=missing,
            known_subtotal=sum(known), total=sum(known) if not missing else None)
        if field in ('tokens', 'input_tokens'):
            stats['observed_lower_bound'] = sum(
                r['metrics'][field] if r['metrics'][field] is not None else
                (r['observed_usage'][field] if r['observed_usage'][field] is not None else 0)
                for r in attempted)
        report[field] = stats
    return report


def summarize(plan, sessions, branches, expected_plan_sha256):
    if digest(plan) != expected_plan_sha256:
        raise ValueError('plan hash mismatch')
    expected = assignments(plan)
    by_key, by_id = {}, {}
    for record in sessions:
        key = identity(record); sid = record.get('session_id')
        if (record.get('data_kind') != 'synthetic' or key not in expected or key in by_key or
            not isinstance(sid, str) or not sid or sid in by_id or
            type(record.get('attempt_index')) is not int or record['attempt_index'] != 0):
            raise ValueError('unassigned, duplicate or retried session')
        state, outcome = record.get('invocation_state'), record.get('outcome')
        if state not in ('finished', 'interrupted', 'not_started'):
            raise ValueError('invalid invocation state')
        if (state == 'not_started' and outcome != 'not_started' or
            state == 'interrupted' and outcome != 'unresolved' or
            state == 'finished' and outcome not in ('success', 'failure', 'infrastructure')):
            raise ValueError('inconsistent outcome state')
        metrics, observed = record.get('metrics'), record.get('observed_usage')
        if (not isinstance(metrics, dict) or set(metrics) != set(FIELDS) or
            not isinstance(observed, dict) or set(observed) != {'tokens', 'input_tokens'} or
            type(record.get('usage_complete')) is not bool):
            raise ValueError('invalid usage schema')
        for field, value in metrics.items():
            if value is not None and (type(value) not in (int, float) or
                type(value) is float and not math.isfinite(value) or value < 0 or
                field != 'wall_seconds' and type(value) is not int):
                raise ValueError('invalid measured cost')
        for value in observed.values():
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError('invalid partial usage')
        if state == 'not_started':
            if record['usage_complete'] or any(v is not None for v in (*metrics.values(), *observed.values())):
                raise ValueError('uninvoked slot cannot declare measured usage')
        else:
            complete = record['usage_complete']
            for field in ('tokens', 'input_tokens'):
                if complete != (metrics[field] is not None):
                    raise ValueError('complete and partial usage disagree')
                if complete and observed[field] is not None and observed[field] != metrics[field]:
                    raise ValueError('partial usage disagrees with full usage')
        by_key[key] = record; by_id[sid] = record
    if set(by_key) != expected:
        raise ValueError('missing assigned session or placeholder')
    expected_b = {k for k in expected if k[0]=='B'}; seen = set()
    for branch in branches:
        key = identity(branch)
        if branch.get('data_kind') != 'synthetic' or key not in expected_b or key in seen:
            raise ValueError('unassigned or duplicate branch')
        a = by_key[('A', *key[1:4], None, None)]; b = by_key[key]
        if branch.get('session_A') != a['session_id'] or branch.get('session_B') != b['session_id']:
            raise ValueError('branch session binding mismatch')
        for phase, origin in (('A', a), ('B', b)):
            copied = branch.get('cost_'+phase)
            if not isinstance(copied, dict) or digest(copied) != digest(origin['metrics']):
                raise ValueError('copied branch cost disagrees with originating session')
        seen.add(key)
    if seen != expected_b:
        raise ValueError('missing assigned branch')
    return dict(schema_version=1, data_kind='synthetic', plan_sha256=expected_plan_sha256,
        phases={p:aggregate([r for r in sessions if r['phase']==p]) for p in ('A','B')},
        overall=aggregate(sessions), branches=len(branches),
        formal_collection_enabled=False, human_gate_satisfied=False,
        monetary_cost=None, GPU_allocation_or_billing=None,
        limitation='Synthetic arithmetic and identity audit only; no study adapter, release authorization or measured GPU charge.')


def render(plan, sessions, branches, output, expected_plan_sha256):
    report = summarize(plan, sessions, branches, expected_plan_sha256)
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    (output/'summary.json').write_text(json.dumps(report, indent=2)+'\n')
    with (output/'sessions.csv').open('x', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['data_kind','session_id',*IDENTITY,
            'invocation_state','outcome','usage_complete',*FIELDS,'observed_tokens','observed_input_tokens'])
        writer.writeheader()
        for r in sorted(sessions, key=lambda r:r['session_id']):
            writer.writerow({k:r[k] for k in ('data_kind','session_id',*IDENTITY,
                'invocation_state','outcome','usage_complete')} | r['metrics'] |
                {'observed_'+k:v for k,v in r['observed_usage'].items()})
    text='# Synthetic session cost rehearsal\n\nAll usage and outcomes are invented. No model, human or formal results.\n\n'
    text+='| Phase | Assigned | Attempted | Unknown token sessions | Known token subtotal | Token total |\n|---|---:|---:|---:|---:|---|\n'
    for phase, row in [*report['phases'].items(), ('Overall',report['overall'])]:
        tokens=row['tokens'];total='Unavailable' if tokens['total'] is None else str(tokens['total'])
        text+=f"| {phase} | {row['assigned']} | {row['attempted']} | {tokens['unknown_sessions']} | {tokens['known_subtotal']} | {total} |\n"
    text+='\nEach originating A session and each B session is counted once, including failures. Unknown usage is not zero; observed lower bounds retain partial counts. Explicitly uninvoked slots contribute no invocation cost but remain assigned. CSV blanks mean unmeasured values. Summed session wall time excludes costs outside the recorded sessions and is not elapsed time, GPU hours or billing.\n'
    (output/'README.md').write_text(text)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('plan','sessions','branches','output'):
        parser.add_argument('--'+name, required=True, type=Path)
    parser.add_argument('--expected-plan-sha256', required=True)
    args=parser.parse_args()
    render(*(json.loads(p.read_text()) for p in (args.plan,args.sessions,args.branches)),
           args.output,args.expected_plan_sha256)


if __name__=='__main__':main()
