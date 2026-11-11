import shutil
import uuid
from contextlib import nullcontext
from runner.common import TASK,resolve_task,copy_tree,git,initialize,save_json,tree_digest
from evaluator.correctness import run_suite,run_suite_prospective
from evaluator.leakage import evaluate
from evaluator.metrics import changes
from runner.sandbox import grading_environment


def freeze(repo, destination, disclosed_future, seed, agent_metrics, data_kind="infrastructure", config=None, task_id='circuit_001'):
    task=resolve_task(task_id)
    artifact_id=uuid.uuid4().hex
    folder=destination/artifact_id
    snapshot=folder/"repo"
    # Build trusted history afresh rather than trusting agent-edited .git.
    copy_tree(task/"base_repo",snapshot)
    baseline=initialize(snapshot)
    for path in list(snapshot.iterdir()):
        if path.name != ".git":
            shutil.rmtree(path) if path.is_dir() else path.unlink()
    clean=folder/"export"
    copy_tree(repo,clean)
    for path in clean.iterdir(): shutil.move(str(path),snapshot/path.name)
    clean.rmdir()
    git(snapshot,"add","-f",".")
    git(snapshot,"commit","--allow-empty","-qm","Completed current requirement")
    (folder/"patch.diff").write_text(git(snapshot,"diff",baseline,"HEAD")+"\n")
    with grading_environment(snapshot,config) if config else nullcontext():
        assessment=(run_suite_prospective(snapshot,task,'A',config)
                    if config and config.get('outcome_policy')=='submitted-trajectory-v1'
                    else run_suite(snapshot,task,'A'))
        if assessment.get('grade_status')=='unresolved':
            leakage=dict(behavioral_leakage=None,api_leakage=None,breadcrumb_leakage=None,
                scope_clean=None,adjudication_status='pending',probes=[],
                probe_status='not_invoked_after_unresolved_grading')
        else:
            leakage=evaluate(snapshot) if task==TASK else evaluate(snapshot,task)
    record={"artifact_id":artifact_id,"task_id":task_id,"task_class":"unvalidated",
            "disclosed_future":disclosed_future,"upstream_seed":seed,"A_pass":assessment["pass"],
            "commit_sha":git(snapshot,"rev-parse","HEAD"),"tree_sha256":tree_digest(snapshot),
            "agent_metrics":agent_metrics,"metrics":changes(task/"base_repo",snapshot),
            "leakage":leakage,"data_kind":data_kind}
    save_json(folder/"A_tests.json",assessment)
    if config and config.get('outcome_policy')=='submitted-trajectory-v1':
        import hashlib
        record.update(grading_policy=config['grading_policy'],
            A_assessment_sha256=hashlib.sha256((folder/'A_tests.json').read_bytes()).hexdigest())
    save_json(folder/"artifact.json",record)
    return folder,record
