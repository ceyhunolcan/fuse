"""Read-only historical Circuit model-smoke usage; never releases or runs a study."""
from collections import Counter
import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import stat
import zipfile


def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
def count(value):return type(value) is int and value>=0


def stream_usage(raw):
    """Keep a valid prefix and corrupt suffix identity; never repair source bytes."""
    events=[];offset=0;errors=[]
    for line in raw.splitlines(keepends=True):
        try:
            if not line.endswith(b'\n'):raise ValueError('incomplete line terminator')
            event=json.loads(line)
            if not isinstance(event,dict):raise ValueError('event is not an object')
        except (ValueError,UnicodeError) as exc:
            errors.append(dict(byte_offset=offset,reason=str(exc)));break
        events.append(event);offset+=len(line)
    responses=[];tools=[];seen_responses=set();seen_tools=set();ends=[]
    for index,event in enumerate(events):
        kind=event.get('event')
        if kind=='session_end':
            if not isinstance(event.get('metrics'),dict):
                raise ValueError('terminal metrics must be an object')
            ends.append(event.get('metrics'))
            if index!=len(events)-1:raise ValueError('event follows terminal session record')
        if kind in ('model_response','tool_call'):
            turn=event.get('turn')
            if not count(turn):raise ValueError('invalid event turn identity')
            seen=seen_responses if kind=='model_response' else seen_tools
            if turn in seen:raise ValueError('duplicate usage event')
            seen.add(turn)
            if kind=='model_response':
                response=event.get('response')
                if not isinstance(response,dict) or not isinstance(response.get('usage'),dict):
                    raise ValueError('response usage must be an object')
                usage=response['usage']
                if any(not count(usage.get(k)) for k in ('prompt_tokens','completion_tokens')):
                    raise ValueError('invalid measured response usage')
                responses.append(usage)
            else:tools.append(event)
    if len(ends)>1:raise ValueError('duplicate terminal metrics')
    observed=dict(tokens=sum(r['completion_tokens'] for r in responses),
                  input_tokens=sum(r['prompt_tokens'] for r in responses))
    terminal=ends[0] if ends else None
    if terminal is not None:
        if (not isinstance(terminal,dict) or not isinstance(terminal.get('status'),str) or
            not terminal['status'] or not count(terminal.get('turns')) or
            terminal['turns']!=len(responses) or not count(terminal.get('tool_calls')) or
            terminal['tool_calls']!=len(tools)):
            raise ValueError('terminal event counts disagree')
        known=[terminal.get(k) is not None for k in ('tokens','input_tokens')]
        if known[0]!=known[1]:raise ValueError('mixed complete token availability')
        for field in ('tokens','input_tokens'):
            value=terminal.get(field)
            if value is not None and (not count(value) or value!=observed[field]):
                raise ValueError('full usage disagrees with observed responses')
        wall=terminal.get('wall_seconds')
        if wall is not None and (type(wall) not in (int,float) or
            type(wall) is float and not math.isfinite(wall) or wall<0):
            raise ValueError('invalid original session wall time')
        if 'usage_complete' in terminal and type(terminal['usage_complete']) is not bool:
            raise ValueError('invalid raw usage-completeness flag')
        for field,key in [('tokens','observed_completion_tokens'),('input_tokens','observed_prompt_tokens')]:
            if key in terminal and (not count(terminal[key]) or terminal[key]!=observed[field]):
                raise ValueError('declared partial usage disagrees')
    intact=not errors
    usable_terminal=terminal if intact else None
    return dict(raw_sha256=sha(raw),raw_bytes=len(raw),valid_prefix_bytes=offset,
        unresolved_bytes=len(raw)-offset,unresolved_suffix_sha256=sha(raw[offset:]),errors=errors,
        state='terminal_recorded' if usable_terminal else 'unterminated_or_corrupt_prefix',
        original_terminal_metrics=terminal,
        metrics={k:usable_terminal.get(k) if usable_terminal else None
                 for k in ('tokens','input_tokens','tool_calls','wall_seconds')},
        full_token_usage_known=bool(usable_terminal and usable_terminal.get('tokens') is not None),
        raw_usage_complete=terminal.get('usage_complete') if terminal else None,
        observed_usage=observed,observed_tool_calls=len(tools),observed_responses=len(responses),
        modified=False)


def members(path,pin):
    raw=Path(path).read_bytes()
    if sha(raw)!=pin:raise ValueError('independent archive hash mismatch')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names=archive.namelist()
        if len(names)!=len(set(names)):raise ValueError('duplicate archive member')
        for info in archive.infolist():
            name=PurePosixPath(info.filename)
            if (name.is_absolute() or '..' in name.parts or '\\' in info.filename or
                stat.S_ISLNK(info.external_attr>>16)):
                raise ValueError('invalid archive member')
        if archive.testzip() is not None:raise ValueError('archive CRC failure')
        return {n:archive.read(n) for n in names if not n.endswith('/')}


def lines(raw):
    if raw and not raw.endswith(b'\n'):raise ValueError('truncated structured record file')
    return [json.loads(line) for line in raw.splitlines()]


def tree(files,prefix):
    digest=hashlib.sha256()
    for name in sorted(n for n in files if n.startswith(prefix+'/')):
        relative=name[len(prefix)+1:]
        if set(PurePosixPath(relative).parts)&{'.git','__pycache__','.pytest_cache'}:continue
        digest.update(relative.encode()+b'\0'+files[name]+b'\0')
    return digest.hexdigest()


def inspect(raw_archive,source_archive,expected_raw_sha256,expected_source_sha256):
    raw=members(raw_archive,expected_raw_sha256);source=members(source_archive,expected_source_sha256)
    get=lambda name:json.loads(raw[name])
    freeze=get('development_freeze.json');config=get('frozen_config.json');completion=get('completion.json')
    frozen_plan=freeze['assignment_plan'];plan=get('crossover/assignment_plan.json')
    if (config.get('data_kind')!='model_smoke' or config.get('status')!='development_frozen' or
        frozen_plan.get('data_kind')!='model_smoke' or plan.get('data_kind')!='model_smoke' or
        completion.get('formal_study_runs')!=0 or type(completion.get('formal_study_runs')) is not int):
        raise ValueError('only historical model-smoke development accepted')
    if completion.get('completed') is not True:
        raise ValueError('incomplete legacy export needs independently bound start evidence; no inferred uninvoked slots')
    if (raw['frozen_config.json']!=source['configs/circuit_tokenized_development_frozen.json'] or
        raw['development_freeze.json']!=source['docs/circuit_tokenized_development_freeze.json'] or
        sha(raw['frozen_config.json'])!=freeze['config_sha256'] or
        canonical(get('prospective_freeze.json'))!=canonical(freeze)):
        raise ValueError('original configuration/freeze binding mismatch')
    if sha(source['protocol/circuit_tokenized_controller.py'])!=freeze['controller_sha256']:
        raise ValueError('original controller hash mismatch')
    for folder,key in [('benchmark/tasks/circuit_001','task_sha256'),('prompts','prompts_sha256'),
                       ('runner','scaffold_sha256'),('evaluator','evaluator_sha256')]:
        if tree(source,folder)!=freeze[key]:raise ValueError('original source group hash mismatch')
    if (frozen_plan.get('sessions')!=6 or type(frozen_plan['sessions']) is not int or
        sorted(frozen_plan['upstream_order'])!=['B1','B2'] or
        any(sorted(v)!=['B1','B2'] for v in frozen_plan['downstream_order'].values()) or
        set(frozen_plan['downstream_order'])!={'B1','B2'} or
        frozen_plan.get('no_success_based_retries') is not True or
        plan['upstream_order']!=frozen_plan['upstream_order'] or
        plan['realized_futures']!=['B1','B2'] or
        any(not count(frozen_plan[k]) or not count(plan[k]) or plan[k]!=frozen_plan[k]
            for k in ('assignment_seed','upstream_seed','downstream_seed'))):
        raise ValueError('unsupported historical assignment plan')
    if canonical(get('crossover/manifest.json')['config'])!=canonical(config):
        raise ValueError('crossover configuration mismatch')
    upstream=lines(raw['crossover/raw/upstream.jsonl']);rows=lines(raw['crossover/raw/results.jsonl'])
    attempts=lines(raw['crossover/raw/attempts.jsonl'])
    if len(upstream)!=2 or len(rows)!=4 or len(attempts)!=4:
        raise ValueError('missing, duplicate or retried original assignment')
    if [u['disclosed_future'] for u in upstream]!=frozen_plan['upstream_order']:
        raise ValueError('upstream order mismatch')
    logs={n:stream_usage(data) for n,data in raw.items() if n.endswith('/events.jsonl')}
    if len(logs)!=6 or any(v['state']!='terminal_recorded' for v in logs.values()):
        raise ValueError('completed archive contains missing/interrupted session logs')
    for name,usage in logs.items():
        if canonical(json.loads(raw[name.rsplit('/',1)[0]+'/config.json']))!=canonical(config):
            raise ValueError('original session configuration mismatch')
        if usage['original_terminal_metrics']['model_revision']!=config['model_revision']:
            raise ValueError('original checkpoint mismatch')
    sessions=[];branches=[];used_logs=set();used_rows=set();artifacts=set()
    for u in upstream:
        artifact=u['artifact_id'];disclosed=u['disclosed_future']
        if (artifact in artifacts or u['data_kind']!='model_smoke' or u['task_id']!='circuit_001' or
            u['task_class']!='unvalidated' or type(u['upstream_seed']) is not int or
            u['upstream_seed']!=plan['upstream_seed']):raise ValueError('invalid original A assignment')
        artifacts.add(artifact)
        artifact_path=f'crossover/artifacts/{artifact}/artifact.json'
        if canonical(get(artifact_path))!=canonical(u) or tree(raw,f'crossover/artifacts/{artifact}/repo')!=u['tree_sha256']:
            raise ValueError('original artifact binding mismatch')
        assigned=[n for n in raw if n.startswith('crossover/sessions/') and n.endswith('/assignment.json')
                  and json.loads(raw[n]).get('artifact_id')==artifact]
        if len(assigned)!=1:raise ValueError('upstream original session binding is not unique')
        assignment=get(assigned[0]);a_log=assigned[0].rsplit('/',1)[0]+'/events.jsonl'
        if (assignment['disclosed_future']!=disclosed or type(assignment['seed']) is not int or
            assignment['seed']!=plan['upstream_seed'] or a_log not in logs or a_log in used_logs):
            raise ValueError('upstream original session mismatch')
        if canonical(logs[a_log]['original_terminal_metrics'])!=canonical(u['agent_metrics']):
            raise ValueError('upstream metrics differ from original log')
        used_logs.add(a_log)
        a_id=a_log.rsplit('/',1)[0]
        sessions.append(dict(phase='A',session_id=a_id,artifact_id=artifact,
            disclosed_future=disclosed,realized_future=None,log_path=a_log,usage=logs[a_log]))
        expected=[dict(event='assigned',artifact_id=artifact,realized_future=f,
            downstream_seed=plan['downstream_seed']) for f in frozen_plan['downstream_order'][disclosed]]
        actual=[x for x in attempts if x.get('artifact_id')==artifact]
        if canonical(actual)!=canonical(expected):raise ValueError('downstream original attempt mismatch')
        for future in frozen_plan['downstream_order'][disclosed]:
            matching=[r for r in rows if r['artifact_id']==artifact and r['realized_future']==future]
            if len(matching)!=1:raise ValueError('downstream original result is missing or duplicated')
            row=matching[0];run=row['run_id'];b_path=f'crossover/branches/{run}';b_log=b_path+'/events.jsonl'
            if (run in used_rows or b_log in used_logs or b_log not in logs or
                row['data_kind']!='model_smoke' or row['task_class']!='unvalidated' or
                row['task_id']!='circuit_001' or row['disclosed_future']!=disclosed or
                type(row['upstream_seed']) is not int or row['upstream_seed']!=plan['upstream_seed'] or
                type(row['downstream_seed']) is not int or row['downstream_seed']!=plan['downstream_seed'] or
                canonical(get(b_path+'/run.json'))!=canonical(row)):
                raise ValueError('downstream original row binding mismatch')
            b_metrics=logs[b_log]['original_terminal_metrics'];a_metrics=u['agent_metrics']
            for field,target in [('tokens','tokens'),('input_tokens','input_tokens'),
                                 ('tool_calls','tool_calls'),('wall_seconds','wall_time')]:
                for phase,metrics in [('A',a_metrics),('B',b_metrics)]:
                    if canonical(row[target+'_'+phase])!=canonical(metrics[field]):
                        raise ValueError('copied branch usage differs from originating session')
            if row['agent_status']!=b_metrics['status']:
                raise ValueError('original B status changed')
            used_rows.add(run);used_logs.add(b_log)
            sessions.append(dict(phase='B',session_id=b_path,artifact_id=artifact,
                disclosed_future=disclosed,realized_future=future,log_path=b_log,usage=logs[b_log]))
            branches.append(dict(artifact_id=artifact,session_A=a_id,session_B=b_path,
                disclosed_future=disclosed,realized_future=future,
                original_trajectory_success=row['trajectory_success'],original_agent_status=row['agent_status']))
    if used_logs!=set(logs):raise ValueError('unbound original session log')
    def totals(items):
        stats=dict(sessions=len(items),status_counts=dict(Counter(r['usage']['original_terminal_metrics']['status'] for r in items)))
        for field in ('tokens','input_tokens'):
            usages=[r['usage'] for r in items];known=[u['metrics'][field] for u in usages if u['metrics'][field] is not None]
            stats[field]=dict(known_full_sessions=len(known),unknown_full_sessions=len(items)-len(known),
                known_full_subtotal=sum(known),full_total=sum(known) if len(known)==len(items) else None,
                observed_response_lower_bound=sum(u['observed_usage'][field] for u in usages))
        stats['tool_calls']=sum(r['usage']['observed_tool_calls'] for r in items)
        walls=[r['usage']['metrics']['wall_seconds'] for r in items]
        stats['session_wall_seconds']=math.fsum(walls) if all(v is not None for v in walls) else None
        return stats
    report=dict(schema_version=1,data_kind='historical_model_smoke_usage',
        raw_archive_sha256=expected_raw_sha256,source_archive_sha256=expected_source_sha256,
        original_config_sha256=freeze['config_sha256'],original_timeout_policy=config.get('timeout_policy','legacy-v0'),
        assigned_sessions=6,unique_original_sessions=len(sessions),phases={p:totals([r for r in sessions if r['phase']==p]) for p in ('A','B')},
        overall=totals(sessions),original_classifications_and_outcomes_preserved=True,
        source_members_executed=0,new_model_requests=0,new_GPU_sessions=0,
        formal_observations=0,formal_collection_enabled=False,human_gate_satisfied=False,
        monetary_cost=None,GPU_charge=None,
        limitation='Read-only historical completed development batch. Partial response usage is a lower bound, not full server cost. No formal reader, inferred uninvoked slots, scope ratings, server cancellation or runtime forecast.')
    return report,sessions,branches,{name:sha(data) for name,data in raw.items()}


def render(raw_archive,source_archive,output,expected_raw_sha256,expected_source_sha256):
    output=Path(output).resolve()
    if output.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError('report must be outside the running source root')
    report,sessions,branches,pins=inspect(raw_archive,source_archive,expected_raw_sha256,expected_source_sha256)
    output.mkdir(parents=True,exist_ok=False)
    for name,value in [('summary.json',report),('sessions.json',sessions),('branches.json',branches),('original_member_sha256.json',pins)]:
        (output/name).write_text(json.dumps(value,indent=2)+'\n')
    with (output/'sessions.csv').open('x',newline='') as stream:
        fields=['data_kind','phase','session_id','artifact_id','disclosed_future','realized_future','original_status',
            'tokens','input_tokens','observed_tokens','observed_input_tokens','tool_calls','wall_seconds','raw_usage_complete']
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        for r in sessions:
            u=r['usage'];writer.writerow(dict(data_kind='historical_model_smoke',
                **{k:r[k] for k in ('phase','session_id','artifact_id','disclosed_future','realized_future')},
                original_status=u['original_terminal_metrics']['status'],**u['metrics'],
                observed_tokens=u['observed_usage']['tokens'],observed_input_tokens=u['observed_usage']['input_tokens'],
                raw_usage_complete=u['raw_usage_complete']))
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('raw-archive','source-archive','output'):p.add_argument('--'+name,required=True,type=Path)
    for name in ('expected-raw-sha256','expected-source-sha256'):p.add_argument('--'+name,required=True)
    args=p.parse_args();render(args.raw_archive,args.source_archive,args.output,args.expected_raw_sha256,args.expected_source_sha256)


if __name__=='__main__':main()
