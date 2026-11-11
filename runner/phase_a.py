import uuid
from runner.common import ROOT,TASK,MODEL_TASKS,resolve_task,copy_tree,initialize,save_json
from runner.freeze_artifact import freeze
from runner.agent import run_agent,validate_config


def phase_a(output,future,seed,config,reference=False,study=False,task_id='circuit_001'):
    task=resolve_task(task_id)
    if future not in {"B1","B2"}: raise ValueError("future must be B1 or B2")
    if not reference and task.name not in MODEL_TASKS:
        raise ValueError('task is not released for model runs')
    if not reference: validate_config(config,study)
    if study: raise ValueError("Expert classification is pending; study collection is disabled")
    folder=output/"sessions"/uuid.uuid4().hex
    repo=folder/"repo"
    copy_tree(task/("reference_A" if reference else "base_repo"),repo)
    initialize(repo)
    prompt=(ROOT/"prompts/phase_a.md").read_text().format(
        current_requirement=(task/"A/requirement.md").read_text(),future_disclosure=(task/future/"disclosure.md").read_text())
    (folder/"prompt.txt").write_text(prompt)
    save_json(folder/"config.json",config)
    metrics={"status":"reference_fixture","tokens":None,"input_tokens":None,"tool_calls":None,"wall_seconds":None}
    if not reference: metrics=run_agent(repo,prompt,config,seed,folder/"events.jsonl")
    # The agent's container has ended before grading and artifact freezing.
    artifact,record=freeze(repo,output/"artifacts",future,seed,metrics,
                           "infrastructure" if reference else "model_smoke",None if reference else config,task_id)
    save_json(folder/"assignment.json",{"artifact_id":record["artifact_id"],"disclosed_future":future,"seed":seed})
    return artifact,record
