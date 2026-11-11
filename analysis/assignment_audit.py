"""Ledger reconciliation for synthetic rehearsals only; never releases collection.

This diagnostic schema is not a frozen study ledger or a verifier of human ratings.
Formal use needs a separate release record and reconciliation of actual attempts.
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path
from analysis.estimate import estimate, load

IDENTITY = ('task_id', 'task_class', 'artifact_id', 'disclosed_future',
            'realized_future', 'upstream_seed', 'downstream_seed')


def key(row):
    return row['artifact_id'], row['realized_future'], row['downstream_seed']


def audit_assignments(rows, ledger):
    if ledger.get('data_kind') != 'synthetic' or ledger.get('schema_version') != 1:
        raise ValueError('this rehearsal accepts only synthetic schema version 1')
    assignments = ledger['assignments']
    if not assignments:
        raise ValueError('empty assignment ledger')
    expected, artifacts, blocks, classes = {}, {}, defaultdict(list), {}
    for assignment in assignments:
        for field in IDENTITY:
            value = assignment[field]
            if field.endswith('seed'):
                if type(value) is not int or value < 0:
                    raise ValueError('seeds must be nonnegative integers')
            elif not isinstance(value, str) or not value:
                raise ValueError('assignment identities must be nonempty strings')
        if assignment['task_class'] not in ('convergent', 'divergent', 'unclear'):
            raise ValueError('invalid diagnostic task class')
        if any(assignment[f] not in ('B1', 'B2') for f in ('disclosed_future', 'realized_future')):
            raise ValueError('invalid future identity')
        task = assignment['task_id']
        if classes.setdefault(task, assignment['task_class']) != assignment['task_class']:
            raise ValueError('inconsistent ledger task class')
        artifact = assignment['artifact_id']
        identity = (task, assignment['task_class'], assignment['disclosed_future'], assignment['upstream_seed'])
        if artifacts.setdefault(artifact, identity) != identity:
            raise ValueError('artifact reused across upstream identities')
        assignment_key = key(assignment)
        if assignment_key in expected:
            raise ValueError('duplicate ledger assignment')
        expected[assignment_key] = assignment
        blocks[(task, assignment['upstream_seed'])].append(assignment)
    task_repetitions = {}
    for (task, _), block in blocks.items():
        grouped = defaultdict(list)
        for a in block:
            grouped[(a['disclosed_future'], a['realized_future'])].append(a)
        if set(grouped) != {('B1','B1'), ('B1','B2'), ('B2','B1'), ('B2','B2')}:
            raise ValueError('each upstream block must contain all four cells')
        repetitions = {tuple(sorted(a['downstream_seed'] for a in group)) for group in grouped.values()}
        if len(repetitions) != 1:
            raise ValueError('unbalanced downstream repetitions within block')
        repetition = next(iter(repetitions))
        if task_repetitions.setdefault(task, repetition) != repetition:
            raise ValueError('unbalanced downstream repetitions across task blocks')
        for disclosure in ('B1', 'B2'):
            if len({a['artifact_id'] for a in block if a['disclosed_future'] == disclosure}) != 1:
                raise ValueError('each disclosure/block must use one shared artifact')
    observed, artifact_outcomes = {}, {}
    for row in rows:
        if row.get('data_kind') != 'synthetic':
            raise ValueError('non-synthetic record in rehearsal')
        assignment_key = key(row)
        if assignment_key in observed:
            raise ValueError('duplicate outcome assignment')
        if assignment_key not in expected:
            raise ValueError('unassigned outcome')
        if any(type(row[field]) is not type(expected[assignment_key][field]) or
               row[field] != expected[assignment_key][field] for field in IDENTITY):
            raise ValueError('outcome metadata differs from ledger')
        observed[assignment_key] = row
        for field in ('A_pass', 'downstream_A_pass', 'B_pass', 'trajectory_success'):
            if row[field] is not None and type(row[field]) is not bool:
                raise ValueError('outcomes must be boolean or null')
        a_outcome = row['A_pass']
        if row['artifact_id'] in artifact_outcomes and artifact_outcomes[row['artifact_id']] is not a_outcome:
            raise ValueError('inconsistent upstream outcome for shared artifact')
        artifact_outcomes[row['artifact_id']] = a_outcome
        reason, outcome = row['outcome_reason'], row['trajectory_success']
        if reason == 'infrastructure':
            if outcome is not None:
                raise ValueError('infrastructure missingness requires null trajectory')
        elif reason == 'deadline':
            if outcome is not False:
                raise ValueError('prospective deadline must be retained as failure')
        elif reason == 'completed':
            grades = [row[f] for f in ('A_pass', 'downstream_A_pass', 'B_pass')]
            if None in grades or outcome is not all(grades):
                raise ValueError('completed trajectory must preserve upstream and downstream failures')
        else:
            raise ValueError('unknown diagnostic outcome reason')
    missing = set(expected) - set(observed)
    if missing:
        raise ValueError(f'missing assigned records: {len(missing)}; retain null placeholders')
    return dict(data_kind='synthetic', assigned=len(expected), observed=len(observed),
                artifacts=len(artifacts), upstream_blocks=len(blocks),
                human_gate_satisfied=False, formal_collection_enabled=False)


def audited_estimate(rows, ledger):
    audit = audit_assignments(rows, ledger)
    result = estimate(rows, include_infrastructure=True)
    # The underlying diagnostic estimator is never called with study opt-in.
    return dict(data_kind='synthetic', assignment_audit=audit, estimate=result,
                formal_collection_enabled=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', required=True, type=Path)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = audited_estimate(load(args.input), json.loads(args.ledger.read_text()))
    # Never overwrite a retained diagnostic result.
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result['assignment_audit']))


if __name__ == '__main__':
    main()
