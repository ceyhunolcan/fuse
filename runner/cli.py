import argparse
import itertools
import json
import random
import uuid
from pathlib import Path
from runner.common import ROOT,TASK,resolve_task,append_record,save_json,tree_digest
from runner.phase_a import phase_a
from runner.phase_b import phase_b
from evaluator.correctness import run_suite
from evaluator.leakage import evaluate
from evaluator.contract import invoke
from unittest.mock import patch
from analysis.estimate import estimate,load


def export(records, output):
    import pyarrow as pa
    import pyarrow.parquet as pq
    # Nested empty architecture scores are serialized explicitly for a stable schema.
    flattened=[{**row,"architecture_scores":json.dumps(row["architecture_scores"])} for row in records]
    pq.write_table(pa.Table.from_pylist(flattened),output)


def validate(output,task_id='circuit_001'):
    task=resolve_task(task_id)
    if task!=TASK:
        from evaluator.task_validation import validate_json_task
        return validate_json_task(task,output)
    checks=[]
    with patch.dict("os.environ",{"FUSE_REPO":str(TASK/"base_repo")}):
        result=invoke("INPUT a 1\nINPUT b 1\ny = XOR(a,b)\nOUTPUT y",{"a":"1","b":"0"})
        starter_scalar=(result.returncode==0 and json.loads(result.stdout)=={"y":"1"})
    for name,phase,expected in [("base_repo","A",False),("reference_A","A",True),
                               ("reference_A","B1",False),("reference_A","B2",False),
                               ("reference_B1","A",True),("reference_B1","B1",True),("reference_B1","B2",False),
                               ("reference_B2","A",True),("reference_B2","B2",True),("reference_B2","B1",False)]:
        result=run_suite(TASK/name,TASK,phase)
        checks.append({"repo":name,"suite":phase,"expected_pass":expected,"actual_pass":result["pass"]})
        save_json(output/(name+"_"+phase+".json"),result)
        print(name,phase,"PASS" if result["pass"] else "FAIL", "(expected)",flush=True)
    probes={name:evaluate(TASK/name) for name in ["reference_A","reference_B1","reference_B2"]}
    passed=starter_scalar and all(c["actual_pass"]==c["expected_pass"] for c in checks)
    passed &= not any(probes["reference_A"]["behavioral_observed"].values())
    passed &= probes["reference_B1"]["behavioral_observed"]=={"B1":True,"B2":False}
    passed &= probes["reference_B2"]["behavioral_observed"]=={"B1":False,"B2":True}
    report={"pass":passed,"starter_scalar_pass":starter_scalar,"checks":checks,"leakage_probe_validation":probes,"task_sha256":tree_digest(TASK),
            "type":"reference_contract_validation_not_experiment"}
    save_json(output/"validation.json",report)
    if not passed: raise SystemExit("Reference validation failed")
    return report


def smoke(output,config,reference=True,task_id='circuit_001',export_parquet=True):
    task=resolve_task(task_id)
    output.mkdir(parents=True,exist_ok=False)
    assignment_seed=2041
    future_order=["B1","B2"];random.Random(assignment_seed).shuffle(future_order)
    save_json(output/"assignment_plan.json",{"assignment_seed":assignment_seed,"upstream_seed":0,
              "upstream_order":future_order,"downstream_seed":0,"realized_futures":["B1","B2"],
              "data_kind":"infrastructure" if reference else "model_smoke"})
    save_json(output/"manifest.json",{"config":config,"task_id":task_id,"task_sha256":tree_digest(task),
              "prompts_sha256":tree_digest(ROOT/"prompts"),"scaffold_sha256":tree_digest(ROOT/"runner")})
    records=[]
    for future in future_order:
        artifact,upstream=phase_a(output,future,0,config,reference=reference,task_id=task_id)
        append_record(output/"raw/upstream.jsonl",upstream)
        branches=["B1","B2"];random.Random(assignment_seed+future_order.index(future)+1).shuffle(branches)
        for realized in branches:
            # Assignment is logged before invocation so a crash cannot disappear.
            append_record(output/"raw/attempts.jsonl",{"artifact_id":upstream["artifact_id"],"realized_future":realized,
                                                     "downstream_seed":0,"event":"assigned"})
            result=phase_b(output,artifact,realized,0,config,reference=reference)
            records.append(result);append_record(output/"raw/results.jsonl",result)
            print(f"{future}->{realized}: trajectory={result['trajectory_success']}",flush=True)
    if export_parquet:export(records,output/"results.parquet")
    summary=estimate(records,include_infrastructure=True)
    save_json(output/"summary.json",summary)
    return summary


def main():
    parser=argparse.ArgumentParser(description="FUSE development harness; formal collection is gated")
    parser.add_argument("command",choices=["validate","smoke-reference","smoke-model","plan","analyze"])
    parser.add_argument("--config",type=Path,default=ROOT/"configs/pilot_agent.json")
    parser.add_argument("--output",type=Path)
    parser.add_argument("--input",type=Path)
    parser.add_argument("--include-infrastructure",action="store_true")
    parser.add_argument('--task',default='circuit_001')
    parser.add_argument('--no-parquet',action='store_true',help='Keep JSONL evidence without optional Parquet export')
    args=parser.parse_args()
    output=(args.output or ROOT/"results"/(args.command+"-"+uuid.uuid4().hex[:8])).resolve()
    if args.command=="validate":
        output.mkdir(parents=True,exist_ok=False);validate(output,args.task)
    elif args.command.startswith("smoke"):
        print(json.dumps(smoke(output,json.loads(args.config.read_text()),args.command=="smoke-reference",args.task,not args.no_parquet),indent=2))
    elif args.command=="plan":
        # Authored candidates remain unclassified and unapproved for formal collection.
        tasks=["circuit_001","migration_001","textdrop_001","filequery_001","codesearch_001"]
        upstream=[{"task_id":t,"disclosed_future":f,"upstream_seed":s} for t,f,s in itertools.product(tasks,["B1","B2"],range(5))]
        downstream=[{**a,"realized_future":f,"downstream_seed":s} for a in upstream for f,s in itertools.product(["B1","B2"],range(2))]
        rng=random.Random(2041);rng.shuffle(upstream);rng.shuffle(downstream)
        save_json(output,{"status":"planning_only_not_frozen","upstream":upstream,"downstream":downstream})
        print(f"Saved 50 upstream and 200 downstream planned assignments to {output}")
    else:
        if args.input is None: parser.error("analyze requires --input JSONL")
        result=estimate(load(args.input),include_infrastructure=args.include_infrastructure)
        save_json(output,result);print(json.dumps(result,indent=2))


if __name__=="__main__": main()
