"""Deterministic invented outcomes, stored separately from research observations."""
import argparse
import copy
import json
from pathlib import Path


def fixtures():
    assignments, complete = [], []
    # Two blocks/disclosure artifacts and two downstream repetitions per future.
    # Every block has the same cell means, so block-resampling CI is degenerate.
    # For divergent: .75, .25, .25, .75 -> tau .5.
    # For convergent: .5 in every cell -> tau 0.
    for task, category in [('synthetic-divergent', 'divergent'), ('synthetic-convergent', 'convergent')]:
        for upstream in range(2):
            for disclosure in ('B1', 'B2'):
                for realized in ('B1', 'B2'):
                    for downstream in range(2):
                        assignment = dict(task_id=task, task_class=category,
                            artifact_id=f'{task}-{upstream}-{disclosure}',
                            disclosed_future=disclosure, realized_future=realized,
                            upstream_seed=upstream, downstream_seed=downstream)
                        if category == 'convergent':
                            success = downstream == 0
                        elif disclosure == realized:
                            success = upstream == 0 or downstream == 0
                        else:
                            success = upstream == 0 and downstream == 0
                        assignments.append(assignment)
                        complete.append(dict(assignment, data_kind='synthetic',
                            A_pass=True, downstream_A_pass=True, B_pass=success,
                            trajectory_success=success, outcome_reason='completed'))
    missing = copy.deepcopy(complete)
    # One of four assigned Y11 outcomes is missing; hand bound tau=[.375,.5].
    missing[0].update(trajectory_success=None, downstream_A_pass=None, B_pass=None,
                      outcome_reason='infrastructure')
    deadline = copy.deepcopy(complete)
    deadline[0].update(trajectory_success=False, B_pass=False, outcome_reason='deadline')
    upstream_failure = copy.deepcopy(complete)
    # Entire shared A artifact fails even if all B branches repair its code.
    for row in upstream_failure:
        if row['artifact_id'] == complete[0]['artifact_id']:
            row.update(A_pass=False, downstream_A_pass=True, B_pass=True, trajectory_success=False)
    negative = copy.deepcopy(complete)
    for row in negative:
        if row['task_class'] == 'divergent':
            row['trajectory_success'] = not row['trajectory_success']
            row['B_pass'] = row['trajectory_success']
    ledger = dict(schema_version=1, data_kind='synthetic', assignments=assignments,
                  note='Invented classes and outcomes; no model, human or study observations.')
    return ledger, dict(complete=complete, missing=missing, deadline=deadline,
                       upstream_failure=upstream_failure, negative=negative)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    ledger, scenarios = fixtures()
    (args.output/'ledger.json').write_text(json.dumps(ledger, indent=2)+'\n')
    for name, rows in scenarios.items():
        (args.output/(name+'.jsonl')).write_text(''.join(json.dumps(r)+'\n' for r in rows))
    (args.output/'README.md').write_text(
        '# Synthetic analysis fixtures\n\nAll outcomes and classes are invented. '
        'These files must never enter a study dataset or satisfy a human/runtime gate.\n')


if __name__ == '__main__':
    main()
