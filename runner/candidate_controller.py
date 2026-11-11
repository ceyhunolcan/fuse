"""Offline candidate controller around the unchanged agent loop.

No HTTP transport, real command sandbox, study mode, resume or automatic retry.
Trusted fixtures simulate actions; this is evidence integration, not model skill.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import uuid

from analysis.estimate import estimate
from evaluator.correctness import run_suite
from runner.agent import run_agent
from runner.common import ROOT, copy_tree, initialize, install_reference, resolve_task, tree_digest
from runner.evidence import append_event, immutable_json, inspect_journal
from runner.fork_artifact import fork
from runner.freeze_artifact import freeze
from runner.reference_pipeline import TASK_IDS, checked_file, make_plan, read, sha, slots

INSTALL = 'FUSE_OFFLINE_INSTALL_FIXTURE'
READINESS = 'python --version && git --version && test -w /workspace'
MODES = ('success', 'no_edit', 'transport_error', 'budget_stop', 'truncated_then_submit')


def sync_tree(folder):
    """Flush trusted completed exports before publishing their receipt."""
    paths = list(folder.rglob('*'))
    if folder.is_symlink() or any(p.is_symlink() for p in paths):
        raise ValueError('symlink in trusted offline export')
    for path in paths:
        if path.is_file():
            fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    for path in sorted([folder, *(p for p in paths if p.is_dir())], key=lambda p: len(p.parts), reverse=True):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def config_for(mode):
    # This configuration is synthetic. The fake Docker identity is never used
    # to construct DockerSandbox: the only factory is the fixed adapter below.
    return dict(status='offline_rehearsal', data_kind='infrastructure',
                endpoint_url='offline://no-network', model='offline-scripted-fixture',
                model_revision='offline-scripted-fixture-v1',
                container_image='offline-unused@sha256:' + '0' * 64,
                context_budget_tokens=100000, context_policy='vllm-tokenize-v1',
                context_safety_margin_tokens=256, tool_feedback_max_bytes=4096,
                action_recovery_policy='bounded-rejection-v1', timeout_policy='session-deadline-v1',
                max_turns=1 if mode == 'budget_stop' else 3,
                max_generated_tokens=50, max_output_tokens_per_turn=10,
                time_limit_seconds=20, temperature=0, seed_supported=False,
                allowed_model_control_suffixes=['<tool_call|>'])


def build_plan(tasks, injections):
    source = make_plan(tasks)
    assignments = slots(tasks)
    if not isinstance(injections, dict) or any(
        key not in {a['slot_id'] for a in assignments} or value not in MODES
        for key, value in injections.items()
    ):
        raise ValueError('injection must name an assigned slot and a fixed offline mode')
    prompts = {}
    for a in assignments:
        task = resolve_task(a['task_id'])
        if a['phase'] == 'A':
            prompt = (ROOT / 'prompts/phase_a.md').read_text().format(
                current_requirement=(task / 'A/requirement.md').read_text(),
                future_disclosure=(task / a['disclosed_future'] / 'disclosure.md').read_text())
        else:
            prompt = (ROOT / 'prompts/phase_b.md').read_text().format(
                current_requirement=(task / 'A/requirement.md').read_text(),
                future_requirement=(task / a['realized_future'] / 'full_requirement.md').read_text())
        prompts[a['slot_id']] = prompt
    return dict(schema_version=1, purpose='offline candidate controller evidence integration',
                data_kind='infrastructure', formal_collection_enabled=False,
                human_gate_satisfied=False, model_execution_enabled=False,
                transport='fixed-offline-script-v1', sandbox='trusted-fixture-adapter-v1',
                usage_kind='simulated_counts_not_model_usage',
                task_ids=list(tasks), task_classes={t: 'unvalidated' for t in tasks},
                retry_policy='zero-automatic-retries', assignments=assignments,
                failure_injections=dict(injections), prompts_by_slot=prompts,
                offline_config_by_slot={a['slot_id']: config_for(injections.get(a['slot_id'], 'success'))
                                        for a in assignments},
                source_sha256=source['source_sha256'])


class OfflineSession:
    """Instrument injected adapters without patching agent.py or its globals."""
    def __init__(self, folder, repo, reference, mode):
        self.folder, self.repo, self.reference = folder, repo, reference
        self.mode = mode
        self.requests = self.commands = self.meters = 0

    def event(self, value):
        append_event(self.folder / 'durable-events.jsonl', value)

    def record(self, name, value):
        path = immutable_json(self.folder / name, value)
        return dict(path=name, sha256=sha(path))

    def meter(self, config, messages, timeout):
        self.meters += 1
        # Exercises scaffold count/accounting branches, never a real tokenizer.
        value = dict(count=25, max_model_len=100000, simulated=True)
        ref = self.record(f'meters/{self.meters:03}.json', value)
        self.event(dict(event='tokenized', index=self.meters, **ref))
        return value

    def request(self, config, messages, max_tokens, seed, timeout):
        self.requests += 1
        index = self.requests
        value = dict(messages=copy.deepcopy(messages), max_tokens=max_tokens,
                     requested_seed=seed, timeout=timeout, simulated=True)
        ref = self.record(f'requests/{index:03}.json', value)
        self.event(dict(event='request_started', index=index, **ref))
        if self.mode == 'transport_error' and index == 2:
            self.event(dict(event='request_failed', index=index, error_class='TimeoutError'))
            raise TimeoutError('offline early transport fixture')
        submit = self.mode == 'no_edit' or index > 1
        action = {'action': 'submit_patch'} if submit else {'action': 'run_command', 'command': INSTALL}
        finish = 'length' if self.mode == 'truncated_then_submit' and index == 1 else 'stop'
        response = dict(choices=[dict(message=dict(content=json.dumps(action)), finish_reason=finish)],
                        usage=dict(prompt_tokens=25, completion_tokens=5), simulated=True)
        ref = self.record(f'responses/{index:03}.json', response)
        self.event(dict(event='response_returned', index=index, **ref))
        return response

    def sandbox(self, repo, image, user):
        if Path(repo) != self.repo:
            raise ValueError('offline adapter repository mismatch')
        session = self

        class Adapter:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def run(self, command, timeout):
                session.commands += 1
                index = session.commands
                ref = session.record(f'commands/{index:03}-input.json',
                                     dict(command=command, timeout=timeout, simulated=True))
                session.event(dict(event='command_started', index=index, **ref))
                if (index == 1 and command != READINESS) or (index > 1 and command != INSTALL):
                    raise ValueError('offline adapter accepts only fixed fixture actions')
                if index > 1:
                    install_reference(session.repo, session.reference)
                result = dict(returncode=0, stdout='Offline fixture adapter; no shell or isolation check.',
                              stderr='', truncated=False, simulated=True)
                ref = session.record(f'commands/{index:03}-output.json', result)
                session.event(dict(event='command_finished', index=index, **ref))
                return result

        return Adapter()


def execute(output, plan, assignment, artifacts):
    slot = assignment['slot_id']
    folder = output / 'sessions' / slot
    folder.mkdir(parents=True, exist_ok=False)
    task = resolve_task(assignment['task_id'])
    repo = folder / 'repo'
    is_a = assignment['phase'] == 'A'
    if is_a:
        copy_tree(task / 'base_repo', repo)
        initialize(repo)
    else:
        upstream = fork(artifacts[assignment['upstream_slot_id']], repo)
    mode = plan['failure_injections'].get(slot, 'success')
    config = plan['offline_config_by_slot'][slot]
    prompt = plan['prompts_by_slot'][slot]
    immutable_json(folder / 'inputs.json', dict(prompt=prompt, config=config,
                   assignment=assignment, data_kind='infrastructure', simulation_mode=mode))
    reference = task / ('reference_A' if is_a else 'reference_' + assignment['realized_future'])
    session = OfflineSession(folder, repo, reference, mode)
    metrics = run_agent(repo, prompt, config, 0, folder / 'scaffold-events.jsonl',
                        request_fn=session.request, sandbox_factory=session.sandbox,
                        token_count_fn=session.meter)
    metrics.update(transport_kind=plan['transport'], sandbox_kind=plan['sandbox'],
                   usage_kind=plan['usage_kind'])
    immutable_json(folder / 'metrics.json', metrics)
    # The unchanged scaffold log is an original, non-fsync log. Seal a durable
    # copy after completion; requests/responses/commands above were already fsynced.
    log = inspect_journal(folder / 'scaffold-events.jsonl')
    if not log['pass_']:
        raise ValueError('incomplete scaffold log; preserve unresolved session')
    immutable_json(folder / 'sealed-scaffold-events.json', log['events'])
    if is_a:
        artifact, record = freeze(repo, output / 'artifacts', assignment['disclosed_future'],
                                  0, metrics, data_kind='infrastructure', task_id=assignment['task_id'])
        artifacts[slot] = artifact
        identity = record['artifact_id']
        prefix = 'artifacts'
        extra = [f'{prefix}/{identity}/{name}' for name in ('artifact.json', 'A_tests.json', 'patch.diff')]
    else:
        identity = uuid.uuid4().hex
        prefix = 'branches'
        branch = output / prefix / identity
        copy_tree(repo, branch / 'repo')
        initialize(branch / 'repo')
        a_grade = run_suite(branch / 'repo', task, 'A')
        b_grade = run_suite(branch / 'repo', task, assignment['realized_future'])
        infrastructure = 'infrastructure_error' in (metrics['status'], upstream['agent_metrics']['status'])
        success = (upstream['A_pass'] and a_grade['pass'] and b_grade['pass'] and
                   upstream['agent_metrics']['status'] == metrics['status'] == 'submitted')
        record = dict(run_id=identity, task_id=assignment['task_id'], task_class='unvalidated',
                      artifact_id=upstream['artifact_id'], upstream_seed=0, downstream_seed=0,
                      disclosed_future=assignment['disclosed_future'], realized_future=assignment['realized_future'],
                      match=assignment['disclosed_future'] == assignment['realized_future'],
                      A_pass=upstream['A_pass'], downstream_A_pass=a_grade['pass'], B_pass=b_grade['pass'],
                      trajectory_success=None if infrastructure else bool(success),
                      scope_trajectory_success=None, architecture_scores=None, adjudication_status='pending',
                      agent_status=metrics['status'], agent_metrics=metrics,
                      data_kind='infrastructure', usage_kind=plan['usage_kind'])
        for name, value in (('A_tests.json', a_grade), ('B_tests.json', b_grade), ('run.json', record)):
            immutable_json(branch / name, value)
        extra = [f'{prefix}/{identity}/{name}' for name in ('run.json', 'A_tests.json', 'B_tests.json')]
    session_files = [str(p.relative_to(output)) for p in sorted(folder.rglob('*'))
                     if p.is_file() and 'repo' not in p.relative_to(folder).parts]
    sync_tree(folder)
    sync_tree(output / prefix / identity)
    return dict(assignment=assignment, attempt_index=0, data_kind='infrastructure',
                record=record, evidence_sha256={name: sha(output / name) for name in session_files + extra},
                repo_tree_sha256=tree_digest(output / prefix / identity / 'repo'),
                session_tree_sha256=tree_digest(repo))


def audit_session(folder, metrics, mode, prompt, config):
    journal = inspect_journal(folder / 'durable-events.jsonl')
    scaffold = inspect_journal(folder / 'scaffold-events.jsonl')
    sealed = read(folder / 'sealed-scaffold-events.json')
    if not journal['pass_'] or not scaffold['pass_'] or scaffold['events'] != sealed:
        raise ValueError('session journal or sealed scaffold log mismatch')
    original_metrics = dict(metrics)
    for key in ('transport_kind', 'sandbox_kind', 'usage_kind'):
        original_metrics.pop(key)
    if not sealed or sealed[-1] != dict(event='session_end', metrics=original_metrics):
        raise ValueError('session-end metrics disagree')
    inputs = read(folder / 'inputs.json')
    if inputs['prompt'] != prompt or inputs['config'] != config or inputs['simulation_mode'] != mode:
        raise ValueError('session inputs differ from prospective offline plan')
    counts = {'tokenized': 0, 'request_started': 0, 'response_returned': 0,
              'request_failed': 0, 'command_started': 0, 'command_finished': 0}
    requests, responses, commands = {}, {}, {}
    pending_request = pending_command = None
    for event in journal['events']:
        kind = event.get('event')
        if kind not in counts or type(event.get('index')) is not int:
            raise ValueError('unexpected session event')
        counts[kind] += 1
        index = event['index']
        if kind == 'request_failed':
            if pending_request != index or event != dict(event=kind, index=index, error_class='TimeoutError'):
                raise ValueError('unbound transport failure')
            pending_request = None
            continue
        expected_name = {
            'tokenized': f'meters/{index:03}.json', 'request_started': f'requests/{index:03}.json',
            'response_returned': f'responses/{index:03}.json',
            'command_started': f'commands/{index:03}-input.json',
            'command_finished': f'commands/{index:03}-output.json',
        }[kind]
        path = checked_file(folder, expected_name)
        if event != dict(event=kind, index=index, path=expected_name, sha256=sha(path)):
            raise ValueError('session event evidence binding differs')
        value = read(path)
        if kind in ('tokenized', 'request_started', 'command_started') and index != counts[kind]:
            raise ValueError('duplicate or nonsequential session operation')
        if value.get('simulated') is not True:
            raise ValueError('unlabeled simulated operation')
        if kind == 'tokenized' and value != dict(count=25, max_model_len=100000, simulated=True):
            raise ValueError('offline meter changed')
        if kind == 'request_started':
            if pending_request is not None:
                raise ValueError('overlapping transport operations')
            pending_request = index
            requests[index] = value
        elif kind == 'response_returned':
            if pending_request != index:
                raise ValueError('unbound response')
            pending_request = None
            responses[index] = value
        elif kind == 'command_started':
            if pending_command is not None or value['command'] != (READINESS if index == 1 else INSTALL):
                raise ValueError('unbound or arbitrary command')
            pending_command = index
            commands[index] = value
        elif kind == 'command_finished':
            if pending_command != index or value['returncode'] != 0:
                raise ValueError('unbound command result')
            pending_command = None
    expected = {'success': (2, 2, 0, 2, 'submitted'), 'no_edit': (1, 1, 0, 1, 'submitted'),
                'transport_error': (2, 1, 1, 2, 'infrastructure_error'),
                'budget_stop': (1, 1, 0, 2, 'turn_budget_exhausted'),
                'truncated_then_submit': (2, 2, 0, 1, 'submitted')}[mode]
    req_n, resp_n, fail_n, cmd_n, status = expected
    if (pending_request is not None or pending_command is not None or
        (counts['tokenized'], counts['request_started'], counts['response_returned'], counts['request_failed'],
         counts['command_started'], counts['command_finished'], metrics['status']) !=
        (req_n, req_n, resp_n, fail_n, cmd_n, cmd_n, status)):
        raise ValueError('unresolved session or scripted outcome mismatch')
    first = requests[1]['messages']
    if len(first) != 2 or first[0]['role'] != 'system' or first[1] != dict(role='user', content=prompt):
        raise ValueError('session must start fresh with only its assigned prompt')
    logged_responses = [event['response'] for event in sealed if event['event'] == 'model_response']
    if logged_responses != list(responses.values()):
        raise ValueError('durable responses and scaffold log differ')
    tool_calls = [event for event in sealed if event['event'] == 'tool_call']
    if len(tool_calls) != cmd_n - 1 or metrics['tool_calls'] != len(tool_calls):
        raise ValueError('command count disagrees with scaffold')
    for index, tool in enumerate(tool_calls, 2):
        if tool['command'] != commands[index]['command'] or tool['output'] != read(folder / f'commands/{index:03}-output.json'):
            raise ValueError('command evidence and scaffold disagree')
    if (metrics['observed_completion_tokens'] != 5 * resp_n or
        metrics['observed_prompt_tokens'] != 25 * resp_n or metrics['turns'] != resp_n or
        metrics['tokens'] != (None if fail_n else 5 * resp_n) or
        metrics['input_tokens'] != (None if fail_n else 25 * resp_n) or
        metrics['usage_complete'] is not (fail_n == 0)):
        raise ValueError('simulated usage accounting differs')
    return req_n


def audit(output, expected_plan_sha256):
    output = Path(output).resolve()
    if sha(checked_file(output, 'plan.json')) != expected_plan_sha256:
        raise ValueError('offline planning ledger hash mismatch')
    plan = read(output / 'plan.json')
    tasks = plan['task_ids']
    injections = plan['failure_injections']
    if (not isinstance(tasks, list) or not tasks or len(set(tasks)) != len(tasks) or
        any(t not in TASK_IDS for t in tasks) or plan['assignments'] != slots(tasks) or
        plan['task_classes'] != {t: 'unvalidated' for t in tasks} or
        plan['data_kind'] != 'infrastructure' or plan['formal_collection_enabled'] is not False or
        plan['model_execution_enabled'] is not False or plan['human_gate_satisfied'] is not False or
        plan['transport'] != 'fixed-offline-script-v1' or plan['sandbox'] != 'trusted-fixture-adapter-v1' or
        plan['usage_kind'] != 'simulated_counts_not_model_usage' or
        plan['retry_policy'] != 'zero-automatic-retries' or type(plan['schema_version']) is not int or
        plan['schema_version'] != 1 or not isinstance(injections, dict) or
        any(k not in {a['slot_id'] for a in slots(tasks)} or v not in MODES for k, v in injections.items())):
        raise ValueError('invalid offline-only plan')
    if list(output.rglob('*.part')):
        raise ValueError('partial publication retained; export is unresolved')
    journal = inspect_journal(output / 'attempts.jsonl')
    assignments = plan['assignments']
    if not journal['pass_'] or len(journal['events']) != 2 * len(assignments):
        raise ValueError('corrupt or unresolved attempt journal; preserve original bytes')
    if {p.name for p in (output / 'receipts').glob('*.json')} != {a['slot_id'] + '.json' for a in assignments}:
        raise ValueError('missing or unexpected assignment receipt')
    artifacts, seen, rows = {}, set(), []
    simulated_requests = 0
    for index, assignment in enumerate(assignments):
        slot = assignment['slot_id']
        relative = f'receipts/{slot}.json'
        receipt_path = checked_file(output, relative)
        if journal['events'][index*2:index*2+2] != [
            dict(event='attempt_started', slot_id=slot, attempt_index=0),
            dict(event='attempt_finished', slot_id=slot, attempt_index=0,
                 receipt_path=relative, receipt_sha256=sha(receipt_path))]:
            raise ValueError('attempt order or receipt binding differs')
        saved = read(receipt_path)
        record = saved['record']
        is_a = assignment['phase'] == 'A'
        identity = record['artifact_id'] if is_a else record['run_id']
        if (not isinstance(identity, str) or len(identity) != 32 or
            any(c not in '0123456789abcdef' for c in identity) or identity in seen):
            raise ValueError('invalid or repeated artifact/run identity')
        seen.add(identity)
        folder = output / 'sessions' / slot
        prefix = 'artifacts' if is_a else 'branches'
        names = ('artifact.json', 'A_tests.json', 'patch.diff') if is_a else ('run.json', 'A_tests.json', 'B_tests.json')
        files = {str(p.relative_to(output)) for p in folder.rglob('*')
                 if p.is_file() and 'repo' not in p.relative_to(folder).parts}
        files.update(f'{prefix}/{identity}/{name}' for name in names)
        if (saved['assignment'] != assignment or type(saved['attempt_index']) is not int or
            saved['attempt_index'] != 0 or saved['data_kind'] != 'infrastructure' or
            record['data_kind'] != 'infrastructure' or record['task_class'] != 'unvalidated' or
            set(saved['evidence_sha256']) != files):
            raise ValueError('receipt identity or complete evidence manifest differs')
        for name, digest in saved['evidence_sha256'].items():
            if sha(checked_file(output, name)) != digest:
                raise ValueError('original evidence hash mismatch')
        original = output / prefix / identity
        if (read(original / names[0]) != record or
            tree_digest(original / 'repo') != saved['repo_tree_sha256'] or
            tree_digest(folder / 'repo') != saved['session_tree_sha256'] or
            saved['session_tree_sha256'] != saved['repo_tree_sha256']):
            raise ValueError('original record or repository tree differs')
        inputs = read(folder / 'inputs.json')
        if inputs['assignment'] != assignment or inputs['data_kind'] != 'infrastructure':
            raise ValueError('session assignment differs')
        metrics = read(folder / 'metrics.json')
        if (record['agent_metrics'] != metrics or metrics['model'] != 'offline-scripted-fixture' or
            metrics['usage_kind'] != plan['usage_kind'] or metrics['transport_kind'] != plan['transport'] or
            metrics['sandbox_kind'] != plan['sandbox'] or metrics['seed_sent'] is not False):
            raise ValueError('simulated metrics identity differs')
        mode = injections.get(slot, 'success')
        config = config_for(mode)
        if plan['offline_config_by_slot'][slot] != config:
            raise ValueError('offline configuration differs')
        simulated_requests += audit_session(folder, metrics, mode, plan['prompts_by_slot'][slot], config)
        for key in ('task_id', 'disclosed_future', 'upstream_seed'):
            if record[key] != assignment[key]:
                raise ValueError('original assignment identity differs')
        a_grade = read(original / 'A_tests.json')
        if type(a_grade['pass']) is not bool or type(record['A_pass']) is not bool:
            raise ValueError('invalid A grade')
        if is_a:
            if record['A_pass'] != a_grade['pass'] or record['tree_sha256'] != saved['repo_tree_sha256']:
                raise ValueError('upstream artifact grade or digest differs')
            artifacts[slot] = record
        else:
            upstream = artifacts[assignment['upstream_slot_id']]
            b_grade = read(original / 'B_tests.json')
            missing = 'infrastructure_error' in (upstream['agent_metrics']['status'], metrics['status'])
            success = (upstream['A_pass'] and a_grade['pass'] and b_grade['pass'] and
                       upstream['agent_metrics']['status'] == metrics['status'] == 'submitted')
            if (type(b_grade['pass']) is not bool or record['artifact_id'] != upstream['artifact_id'] or
                record['A_pass'] != upstream['A_pass'] or record['downstream_seed'] != 0 or
                record['realized_future'] != assignment['realized_future'] or
                record['match'] is not (assignment['disclosed_future'] == assignment['realized_future']) or
                record['downstream_A_pass'] is not a_grade['pass'] or record['B_pass'] is not b_grade['pass'] or
                record['trajectory_success'] is not (None if missing else bool(success)) or
                record['agent_status'] != metrics['status'] or
                record['scope_trajectory_success'] is not None or record['architecture_scores'] is not None or
                record['adjudication_status'] != 'pending'):
                raise ValueError('trajectory grade, status or human-score policy differs')
            rows.append(record)
    return dict(pass_=True, data_kind='infrastructure_diagnostic', formal_collection_enabled=False,
                human_gate_satisfied=False, model_execution_enabled=False, model_requests=0,
                GPU_used=False, plan_sha256=expected_plan_sha256, journal_sha256=journal['raw_sha256'],
                task_count=len(tasks), originating_attempts=len(assignments), upstream_artifacts=len(artifacts),
                downstream_assignments=len(rows), simulated_requests=simulated_requests,
                simulated_successes=sum(r['trajectory_success'] is True for r in rows),
                simulated_missing=sum(r['trajectory_success'] is None for r in rows),
                unresolved_attempts=0, automatic_retries=0, diagnostic=estimate(rows, include_infrastructure=True),
                limitations='Fixed offline fixtures exercise the real agent loop and evidence plumbing only. '
                'No model capability, real tokenizer usage, shell isolation, GPU/runtime compatibility, '
                'authenticated attestation, concurrent writer or power-loss certification. '
                'A separate reviewed implementation/release is required for live model collection.')


def run_offline(output, tasks=TASK_IDS, injections=None):
    plan = build_plan(tasks, {} if injections is None else injections)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    immutable_json(output / 'plan.json', plan)
    anchor = sha(output / 'plan.json')
    immutable_json(output / 'plan-anchor.json', dict(plan_sha256=anchor, formal_collection_enabled=False))
    artifacts = {}
    for assignment in plan['assignments']:
        slot = assignment['slot_id']
        append_event(output / 'attempts.jsonl', dict(event='attempt_started', slot_id=slot, attempt_index=0))
        try:
            saved = execute(output, plan, assignment, artifacts)
            relative = f'receipts/{slot}.json'
            immutable_json(output / relative, saved)
            append_event(output / 'attempts.jsonl', dict(event='attempt_finished', slot_id=slot,
                         attempt_index=0, receipt_path=relative, receipt_sha256=sha(output / relative)))
        except Exception as exc:
            append_event(output / 'attempts.jsonl', dict(event='attempt_failed', slot_id=slot,
                         attempt_index=0, error_class=type(exc).__name__))
            raise
    result = audit(output, anchor)
    immutable_json(output / 'audit.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run-offline')
    run.add_argument('--output', type=Path, required=True)
    check = sub.add_parser('audit')
    check.add_argument('--input', type=Path, required=True)
    check.add_argument('--expected-plan-sha256', required=True)
    args = parser.parse_args()
    result = run_offline(args.output) if args.command == 'run-offline' else audit(args.input, args.expected_plan_sha256)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
