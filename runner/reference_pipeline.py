"""Reference-only CPU integration rehearsal; cannot run a model or release a study.

Single-writer local evidence prototype. No resume or automatic retries. The existing
phase runners are unchanged; this wrapper preserves their original evidence too.
"""
import argparse
import hashlib
import json
from pathlib import Path

from analysis.estimate import estimate
from runner.common import ROOT, resolve_task, tree_digest
from runner.evidence import append_event, immutable_json, inspect_journal
from runner.phase_a import phase_a
from runner.phase_b import phase_b

TASK_IDS = ('circuit_001', 'migration_001', 'textdrop_001', 'filequery_001', 'codesearch_001')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def repetitions(values):
    if (not isinstance(values,(list,tuple)) or not values or
        any(type(v) is not int or v<0 for v in values) or len(values)!=len(set(values))):
        raise ValueError('unique nonnegative integer repetition IDs required')
    return list(values)


def verify_source(expected_manifest_sha256=None, source_root=None):
    """Byte binding only; a hash does not authenticate approval or authorship."""
    source_root=ROOT if source_root is None else Path(source_root).resolve()
    manifest_path=source_root/'manifest.json'
    if manifest_path.is_symlink():raise ValueError('symlink source manifest')
    actual_hash=sha(manifest_path)
    if expected_manifest_sha256 is not None and actual_hash!=expected_manifest_sha256:
        raise ValueError('source manifest hash mismatch')
    manifest=read(manifest_path)
    if not isinstance(manifest,dict) or not manifest:raise ValueError('invalid source manifest')
    for name,digest in manifest.items():
        if not isinstance(digest,str) or len(digest)!=64 or sha(checked_file(source_root,name))!=digest:
            raise ValueError('source file hash mismatch: '+str(name))
    actual={str(p.relative_to(source_root)) for p in source_root.rglob('*')
            if p.is_file() and not set(p.relative_to(source_root).parts)&{'__pycache__','.git','.pytest_cache'}
            and p!=manifest_path}
    if actual!=set(manifest):raise ValueError('source inventory differs from manifest')
    return actual_hash,manifest


def slots(tasks, upstream_repetitions=(0,), downstream_repetitions=(0,)):
    upstream_repetitions=repetitions(upstream_repetitions)
    downstream_repetitions=repetitions(downstream_repetitions)
    result=[]
    for task in tasks:
        for repeat in upstream_repetitions:
            for disclosed in ('B1','B2'):
                upstream=f'a-{task}-{disclosed}-r{repeat}'
                result.append(dict(slot_id=upstream,phase='A',task_id=task,
                    disclosed_future=disclosed,upstream_seed=repeat))
                for realized in ('B1','B2'):
                    for downstream in downstream_repetitions:
                        result.append(dict(slot_id=f'b-{task}-{disclosed}-r{repeat}-{realized}-s{downstream}',
                            phase='B',upstream_slot_id=upstream,task_id=task,disclosed_future=disclosed,
                            realized_future=realized,upstream_seed=repeat,downstream_seed=downstream))
    return result


def make_plan(tasks, upstream_repetitions=(0,), downstream_repetitions=(0,),
              expected_source_manifest_sha256=None):
    if (not isinstance(tasks,(list,tuple)) or not tasks or
        any(not isinstance(t,str) for t in tasks) or len(set(tasks))!=len(tasks)):
        raise ValueError('unique nonempty candidate task list required')
    if any(t not in TASK_IDS for t in tasks):raise ValueError('unknown candidate task')
    upstream=repetitions(upstream_repetitions);downstream=repetitions(downstream_repetitions)
    anchor,pins=verify_source(expected_source_manifest_sha256)
    for task in tasks:resolve_task(task)
    return dict(schema_version=1,purpose='repetition-aware source-bound reference CPU rehearsal',
        data_kind='infrastructure',formal_collection_enabled=False,
        human_gate_satisfied=False,model_execution_enabled=False,
        task_ids=list(tasks),task_classes={t:'unvalidated' for t in tasks},
        upstream_repetitions=upstream,downstream_repetitions=downstream,
        retry_policy='zero-automatic-retries',source_sha256=pins,source_manifest_sha256=anchor,
        assignments=slots(tasks,upstream,downstream))


def checked_file(root, relative):
    """Evidence paths are confined to this trusted export; reject symlink traversal."""
    if not isinstance(relative, str):
        raise ValueError('invalid evidence path')
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('invalid evidence path')
    target = root / path
    for parent in (target, *target.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError('symlink in evidence path')
    if not target.is_file():
        raise ValueError(f'missing evidence: {relative}')
    return target


def grade_files(root, phase, identity, session_id=None):
    if phase == 'A':
        return [f'artifacts/{identity}/artifact.json', f'artifacts/{identity}/A_tests.json',
                f'artifacts/{identity}/patch.diff', f'sessions/{session_id}/assignment.json',
                f'sessions/{session_id}/prompt.txt', f'sessions/{session_id}/config.json']
    return [f'branches/{identity}/run.json', f'branches/{identity}/A_tests.json',
            f'branches/{identity}/B_tests.json', f'branches/{identity}/prompt.txt',
            f'branches/{identity}/config.json']


def receipt(root, assignment, record):
    phase = assignment['phase']
    identity = record['artifact_id'] if phase == 'A' else record['run_id']
    prefix = 'artifacts' if phase == 'A' else 'branches'
    session_id = None
    if phase == 'A':
        sessions = [path.parent.name for path in (root/'sessions').glob('*/assignment.json')
                    if read(path).get('artifact_id') == identity]
        if len(sessions) != 1:
            raise ValueError('upstream session binding is not unique')
        session_id = sessions[0]
    files = grade_files(root, phase, identity, session_id)
    return dict(assignment=assignment, data_kind='infrastructure', attempt_index=0,
                record=record, session_id=session_id, evidence_sha256={name: sha(root/name) for name in files},
                repo_tree_sha256=tree_digest(root/prefix/identity/'repo'))


def audit(root, expected_plan_sha256, *, source_root=None):
    root = Path(root).resolve()
    if sha(root/'plan.json') != expected_plan_sha256:
        raise ValueError('planning ledger hash mismatch')
    plan = read(root/'plan.json')
    tasks = plan.get('task_ids')
    upstream_repetitions=repetitions(plan.get('upstream_repetitions'))
    downstream_repetitions=repetitions(plan.get('downstream_repetitions'))
    source_anchor,source_pins=verify_source(plan.get('source_manifest_sha256'),source_root)
    if plan.get('source_sha256')!=source_pins or plan.get('source_manifest_sha256')!=source_anchor:
        raise ValueError('plan source binding differs')
    if (type(plan.get('schema_version')) is not int or plan['schema_version'] != 1 or
        not isinstance(tasks, list) or not tasks or len(set(tasks)) != len(tasks) or
        any(task not in TASK_IDS for task in tasks) or
        plan.get('data_kind') != 'infrastructure' or
        plan.get('formal_collection_enabled') is not False or
        plan.get('human_gate_satisfied') is not False or
        plan.get('model_execution_enabled') is not False or
        plan.get('retry_policy') != 'zero-automatic-retries' or
        plan.get('task_classes') != {task: 'unvalidated' for task in tasks} or
        plan.get('assignments') != slots(tasks,upstream_repetitions,downstream_repetitions)):
        raise ValueError('invalid reference planning ledger')
    journal = inspect_journal(root/'attempts.jsonl')
    if not journal['pass_']:
        raise ValueError('journal corrupt or truncated; preserve unresolved bytes')
    assignments = plan['assignments']
    expected_receipts = {item['slot_id']+'.json' for item in assignments}
    if {p.name for p in (root/'receipts').glob('*.json')} != expected_receipts:
        raise ValueError('missing or unexpected receipt')
    events = journal['events']
    if len(events) != 2*len(assignments):
        raise ValueError('unresolved, duplicate or retried attempt')
    artifacts = {}; seen_artifacts = set(); seen_runs = set(); rows = []
    for index, assignment in enumerate(assignments):
        slot_id = assignment['slot_id']; relative = f'receipts/{slot_id}.json'
        path = checked_file(root, relative)
        expected_start = dict(event='attempt_started', slot_id=slot_id, attempt_index=0)
        expected_end = dict(event='attempt_finished', slot_id=slot_id, attempt_index=0,
                            receipt_path=relative, receipt_sha256=sha(path))
        if events[index*2:index*2+2] != [expected_start, expected_end]:
            raise ValueError('attempt order, identity or receipt hash mismatch')
        saved = read(path); record = saved['record']; phase = assignment['phase']
        if (saved['assignment'] != assignment or saved['data_kind'] != 'infrastructure' or
            type(saved['attempt_index']) is not int or saved['attempt_index'] != 0 or
            record.get('data_kind') != 'infrastructure' or
            record.get('task_class') != 'unvalidated' or
            any(record.get(key) != assignment[key] for key in
                ('task_id', 'disclosed_future', 'upstream_seed'))):
            raise ValueError('receipt assignment or development-only identity mismatch')
        identity = record['artifact_id'] if phase == 'A' else record['run_id']
        if not isinstance(identity, str) or len(identity) != 32 or any(c not in '0123456789abcdef' for c in identity):
            raise ValueError('invalid artifact/run identity')
        session_id = saved.get('session_id')
        if phase == 'A':
            if not isinstance(session_id, str) or len(session_id) != 32 or any(c not in '0123456789abcdef' for c in session_id):
                raise ValueError('invalid upstream session identity')
        elif session_id is not None:
            raise ValueError('unexpected upstream session binding')
        files = grade_files(root, phase, identity, session_id)
        if set(saved['evidence_sha256']) != set(files):
            raise ValueError('incomplete grade evidence manifest')
        for name, digest in saved['evidence_sha256'].items():
            if sha(checked_file(root, name)) != digest:
                raise ValueError('grade evidence hash mismatch')
        if read(root/files[0]) != record:
            raise ValueError('original record and receipt disagree')
        prefix = 'artifacts' if phase == 'A' else 'branches'
        digest = tree_digest(root/prefix/identity/'repo')
        if digest != saved['repo_tree_sha256']:
            raise ValueError('repository tree hash mismatch')
        current = read(root/files[1])
        if type(current.get('pass')) is not bool or type(record.get('A_pass')) is not bool:
            raise ValueError('invalid A grade')
        if phase == 'A':
            if (identity in seen_artifacts or digest != record['tree_sha256'] or
                record['A_pass'] != current['pass'] or
                record['agent_metrics']['status'] != 'reference_fixture' or
                any(record['agent_metrics'].get(key) is not None for key in
                    ('tokens', 'input_tokens', 'tool_calls', 'wall_seconds'))):
                raise ValueError('invalid or duplicate upstream artifact')
            if (read(root/files[3]) != dict(artifact_id=identity, disclosed_future=assignment['disclosed_future'], seed=assignment['upstream_seed']) or
                read(root/files[5]) != {} or
                tree_digest(root/'sessions'/session_id/'repo') != digest):
                raise ValueError('upstream session evidence mismatch')
            seen_artifacts.add(identity); artifacts[slot_id] = record
        else:
            upstream = artifacts[assignment['upstream_slot_id']]
            future = read(root/files[2])
            if (identity in seen_runs or record['artifact_id'] != upstream['artifact_id'] or
                record['A_pass'] != upstream['A_pass'] or
                record['realized_future'] != assignment['realized_future'] or
                type(record['downstream_seed']) is not int or record['downstream_seed'] != assignment['downstream_seed'] or
                record['agent_status'] != 'reference_fixture' or
                read(root/files[4]) != {} or
                record['match'] is not (assignment['disclosed_future'] == assignment['realized_future']) or
                type(future.get('pass')) is not bool or
                record['downstream_A_pass'] is not current['pass'] or
                record['B_pass'] is not future['pass'] or
                record['trajectory_success'] is not (upstream['A_pass'] and current['pass'] and future['pass']) or
                record['scope_trajectory_success'] is not None or
                record['architecture_scores'] is not None or
                record['adjudication_status'] != 'pending' or
                any(record.get(key) is not None for key in ('tokens_A','tokens_B','input_tokens_A',
                    'input_tokens_B','tool_calls_A','tool_calls_B','wall_time_A','wall_time_B'))):
                raise ValueError('invalid downstream reference result')
            seen_runs.add(identity); rows.append(record)
    return dict(pass_=True, data_kind='infrastructure_diagnostic',
                formal_collection_enabled=False, human_gate_satisfied=False,model_execution_enabled=False,
                source_manifest_sha256=source_anchor,upstream_repetitions=upstream_repetitions,
                downstream_repetitions=downstream_repetitions,
                plan_sha256=expected_plan_sha256, journal_sha256=journal['raw_sha256'],
                task_count=len(tasks), upstream_artifacts=len(artifacts), downstream_assignments=len(rows),
                attempts=len(assignments), unresolved_attempts=0, automatic_retries=0,
                reference_successes=sum(row['trajectory_success'] for row in rows),
                diagnostic=estimate(rows, include_infrastructure=True),
                limitations='Reference replacement checks plumbing only. No Gemma inference, human validation, '
                            'GPU compatibility, authenticated attestation or power-loss certification. '
                            'Source pins describe this rehearsal, not a prospective study release.')


def run_reference(output, tasks=TASK_IDS, upstream_repetitions=(0,), downstream_repetitions=(0,),
                  expected_source_manifest_sha256=None):
    plan = make_plan(tasks,upstream_repetitions,downstream_repetitions,expected_source_manifest_sha256)
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=False)
    immutable_json(output/'plan.json', plan)
    expected_hash = sha(output/'plan.json')
    immutable_json(output/'plan-anchor.json', dict(plan_sha256=expected_hash,
                                                  formal_collection_enabled=False))
    artifacts = {}
    for assignment in plan['assignments']:
        slot_id = assignment['slot_id']
        append_event(output/'attempts.jsonl', dict(event='attempt_started', slot_id=slot_id, attempt_index=0))
        try:
            if assignment['phase'] == 'A':
                artifact, record = phase_a(output, assignment['disclosed_future'], assignment['upstream_seed'], {},
                                           reference=True, task_id=assignment['task_id'])
                artifacts[slot_id] = artifact
            else:
                record = phase_b(output, artifacts[assignment['upstream_slot_id']],
                                 assignment['realized_future'], assignment['downstream_seed'], {}, reference=True)
            relative = f'receipts/{slot_id}.json'
            immutable_json(output/relative, receipt(output, assignment, record))
            append_event(output/'attempts.jsonl', dict(event='attempt_finished', slot_id=slot_id,
                         attempt_index=0, receipt_path=relative, receipt_sha256=sha(output/relative)))
        except Exception as error:
            append_event(output/'attempts.jsonl', dict(event='attempt_failed', slot_id=slot_id,
                         attempt_index=0, error_type=type(error).__name__, message=str(error)))
            raise
    result = audit(output, expected_hash)
    immutable_json(output/'audit.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run'); run.add_argument('--output', type=Path, required=True)
    run.add_argument('--task-ids',nargs='+',default=list(TASK_IDS))
    run.add_argument('--upstream-repetitions',type=int,nargs='+',default=[0])
    run.add_argument('--downstream-repetitions',type=int,nargs='+',default=[0])
    run.add_argument('--expected-source-manifest-sha256',required=True)
    check = sub.add_parser('audit'); check.add_argument('--input', type=Path, required=True)
    check.add_argument('--expected-plan-sha256', required=True)
    args = parser.parse_args()
    result = run_reference(args.output,tasks=args.task_ids,upstream_repetitions=args.upstream_repetitions,
        downstream_repetitions=args.downstream_repetitions,
        expected_source_manifest_sha256=args.expected_source_manifest_sha256) if args.command == 'run' else audit(args.input, args.expected_plan_sha256)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
