import uuid
from contextlib import nullcontext
from runner.common import MODEL_TASKS,ROOT,TASK,resolve_task,install_reference,copy_tree,save_json
from runner.fork_artifact import fork
from runner.agent import run_agent,validate_config
from runner.sandbox import grading_environment
from evaluator.correctness import run_suite,run_suite_prospective,uninvoked_grade
from evaluator.metrics import changes,invalidation


def phase_b(output,artifact,realized,seed,config,reference=False):
    if realized not in {"B1","B2"}: raise ValueError("future must be B1 or B2")
    if not reference: validate_config(config)
    run_id=uuid.uuid4().hex
    folder=output/"branches"/run_id
    repo=folder/"repo"
    upstream=fork(artifact,repo)
    prospective=config.get('outcome_policy')=='submitted-trajectory-v1'
    upstream_grade=None
    if prospective:
        import hashlib,json
        from evaluator.grade_policy import validate_grade
        raw=(artifact/'A_tests.json').read_bytes()
        if (upstream.get('grading_policy')!=config['grading_policy'] or
                hashlib.sha256(raw).hexdigest()!=upstream.get('A_assessment_sha256')):
            raise ValueError('original structured A assessment binding differs')
        upstream_grade=validate_grade(json.loads(raw))
        if upstream['A_pass'] is not upstream_grade['pass']:
            raise ValueError('original A pass differs from structured grade')
    task=resolve_task(upstream['task_id'])
    if not reference and task.name not in MODEL_TASKS:
        raise ValueError('task is not released for model runs')
    prompt=(ROOT/"prompts/phase_b.md").read_text().format(
        current_requirement=(task/"A/requirement.md").read_text(),future_requirement=(task/realized/"full_requirement.md").read_text())
    (folder/"prompt.txt").write_text(prompt)
    save_json(folder/"config.json",config)
    metrics={"status":"reference_fixture","tokens":None,"input_tokens":None,"tool_calls":None,"wall_seconds":None}
    if reference:
        # Explicit fixture replacement tests plumbing, not evolvability or agent skill.
        install_reference(repo,task/("reference_"+realized))
    else:
        metrics=run_agent(repo,prompt,config,seed,folder/"events.jsonl")
    with nullcontext() if reference else grading_environment(repo,config):
        if prospective:
            current=run_suite_prospective(repo,task,'A',config)
            future=(uninvoked_grade(config,'not_invoked_after_unresolved_current_grade')
                    if current['grade_status']=='unresolved'
                    else run_suite_prospective(repo,task,realized,config))
        else:
            current=run_suite(repo,task,'A');future=run_suite(repo,task,realized)
    save_json(folder/"A_tests.json",current);save_json(folder/"B_tests.json",future)
    effort=changes(artifact/"repo",repo)
    provenance=invalidation(task/"base_repo",artifact/"repo",repo)
    infra=metrics["status"]=="infrastructure_error" or upstream["agent_metrics"]["status"]=="infrastructure_error"
    allowed={"submitted","reference_fixture"}
    success=(upstream["A_pass"] and current["pass"] and future["pass"]
             and metrics["status"] in allowed and upstream["agent_metrics"]["status"] in allowed)
    a=upstream["agent_metrics"]
    record={"run_id":run_id,"task_id":upstream["task_id"],"task_class":upstream["task_class"],
            "artifact_id":upstream["artifact_id"],"upstream_seed":upstream["upstream_seed"],"downstream_seed":seed,
            "disclosed_future":upstream["disclosed_future"],"realized_future":realized,
            "match":upstream["disclosed_future"]==realized,"A_pass":upstream["A_pass"],
            "downstream_A_pass":current["pass"],"B_pass":future["pass"],
            "trajectory_success":None if infra else bool(success),"scope_trajectory_success":None,
            "behavioral_leakage":upstream["leakage"]["behavioral_leakage"],
            "api_leakage":None,"breadcrumb_leakage":None,"adjudication_status":"pending",
            "tokens_A":a["tokens"],"tokens_B":metrics["tokens"],
            "input_tokens_A":a.get("input_tokens"),"input_tokens_B":metrics.get("input_tokens"),
            "tool_calls_A":a["tool_calls"],"tool_calls_B":metrics["tool_calls"],
            "wall_time_A":a["wall_seconds"],"wall_time_B":metrics["wall_seconds"],
            "loc_A":upstream["metrics"]["loc_changed"],"loc_B":effort["loc_changed"],
            "files_A":upstream["metrics"]["files_touched"],"files_B":effort["files_touched"],
            **provenance,"architecture_scores":None,"agent_status":metrics["status"],
            "data_kind":"infrastructure" if reference else "model_smoke"}
    if metrics.get('timeout_policy') == 'session-deadline-v1':
        record.update(timeout_policy_B=metrics['timeout_policy'],
                      usage_complete_B=metrics['usage_complete'],
                      observed_tokens_B=metrics['observed_completion_tokens'],
                      observed_input_tokens_B=metrics['observed_prompt_tokens'])
    if prospective:
        from runner.outcome_policy import reconcile
        import hashlib
        outcome=reconcile({'upstream_A':upstream_grade,'downstream_A':current,'downstream_B':future},
                          upstream['agent_metrics']['status'],metrics['status'])
        record.update(outcome_policy=config['outcome_policy'],grading_policy=config['grading_policy'],
            operational_outcome=outcome,trajectory_success=outcome['trajectory_success'],
            raw_correctness=outcome['raw_correctness'],
            original_grade_sha256={'upstream_A':upstream['A_assessment_sha256'],
                'downstream_A':hashlib.sha256((folder/'A_tests.json').read_bytes()).hexdigest(),
                'downstream_B':hashlib.sha256((folder/'B_tests.json').read_bytes()).hexdigest()})
    save_json(folder/"run.json",record)
    return record
