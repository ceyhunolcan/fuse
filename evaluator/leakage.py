"""Positive evidence probes and review flags, not a scope certification."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
from evaluator.contract import invoke,run_candidate


TASK = Path(__file__).resolve().parents[1]/"benchmark/tasks/circuit_001"
PROBES = {future:[(row["source"],row["values"],row["expected"],row["format"])
                 for row in json.loads((TASK/future/"leakage_tests/cases.json").read_text())]
          for future in ["B1","B2"]}


def evaluate_json_task(repo,task):
    from evaluator.json_contract import invoke_json
    task=Path(task);metadata=json.loads((task/'metadata.json').read_text())
    def subset(actual,expected):
        if isinstance(expected,dict):
            return isinstance(actual,dict) and all(k in actual and subset(actual[k],v) for k,v in expected.items())
        return actual==expected
    probes=[]
    for future in ('B1','B2'):
        cases=json.loads((task/future/'leakage_tests/cases.json').read_text())
        for index,case in enumerate(cases):
            try:
                result=invoke_json(repo,metadata['module'],case['request'])
                actual=json.loads(result.stdout) if result.returncode==0 else None
                success=actual is not None and subset(actual,case['expected_subset']) and all(k in actual for k in case.get('required_keys',[]))
            except (ValueError,subprocess.TimeoutExpired):success=False
            probes.append({'future':future,'probe':index,'future_behavior_observed':success})
    behavior={future:any(p['future_behavior_observed'] for p in probes if p['future']==future) for future in ('B1','B2')}
    flags=[]
    pattern=re.compile(r'(?i)(TODO.*(?:future|later)|deferred|B[12]\s*(?:requirement|future)|future requirement)')
    for path in Path(repo).rglob('*'):
        if path.is_file() and not set(path.parts)&{'.git','__pycache__'} and path.suffix in {'.py','.md','.txt','.json'}:
            for number,line in enumerate(path.read_text(errors='replace').splitlines(),1):
                if pattern.search(line):flags.append({'path':str(path.relative_to(repo)),'line':number,'text':line[:300]})
    return {'behavioral_observed':behavior,'api_candidates':{'B1':None,'B2':None},
        'breadcrumb_candidates':flags,'behavioral_leakage':True if any(behavior.values()) else None,
        'api_leakage':None,'breadcrumb_leakage':None,'scope_clean':None,
        'adjudication_status':'pending','probes':probes,
        'limitations':'Positive screens only; API and all negative/ambiguous scope judgments require manual review.'}


def evaluate(repo,task=TASK):
    if Path(task)!=TASK:return evaluate_json_task(Path(repo).resolve(),task)
    repo = Path(repo).resolve()
    observed = []
    with patch.dict(os.environ, {"FUSE_REPO": str(repo)}):
        for future, probes in PROBES.items():
            for index, (source, values, expected, fmt) in enumerate(probes):
                try:
                    result = invoke(source, values, fmt)
                    success = result.returncode == 0 and json.loads(result.stdout) == expected
                except (ValueError, subprocess.TimeoutExpired):
                    success = False
                observed.append({"future":future,"probe":index,"future_behavior_observed":success})
    help_command = [sys.executable,"-m","fusecircuit","--help"]
    help_result = run_candidate(help_command,repo)
    api = {"B1": bool(re.search(r"--(?:three[-_]valued|3val|mode)\b", help_result.stdout)),
           "B2": "--format" in help_result.stdout}
    flags = []
    pattern = re.compile(r"(?i)(TODO.*(?:future|unknown|BENCH)|later.*(?:three.valued|BENCH|circuit format)|deferred|B[12]\s*(?:requirement|future)|three.valued|future requirement)")
    for path in repo.rglob("*"):
        if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
            continue
        if path.suffix not in {".py",".md",".txt",".yaml",".json"}:
            continue
        for number,line in enumerate(path.read_text(errors="replace").splitlines(),1):
            if pattern.search(line):
                flags.append({"path":str(path.relative_to(repo)),"line":number,"text":line[:300]})
    behavior = {future:any(p["future_behavior_observed"] for p in observed if p["future"]==future) for future in PROBES}
    return {"behavioral_observed":behavior,"api_candidates":api,"breadcrumb_candidates":flags,
            "behavioral_leakage": True if any(behavior.values()) else None,
            "api_leakage": None,"breadcrumb_leakage": None,"adjudication_status":"pending",
            "scope_clean":None,"probes":observed}
