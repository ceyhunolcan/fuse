"""Trusted authored-reference checks; neither expert validation nor model evidence."""
import json
import re
from evaluator.correctness import run_suite
from evaluator.json_contract import invoke_json
from evaluator.leakage import evaluate
from runner.common import save_json,tree_digest

MATRIX=[('base_repo','A',False),('reference_A','A',True),
 ('reference_A','B1',False),('reference_A','B2',False),
 ('reference_B1','A',True),('reference_B1','B1',True),('reference_B1','B2',False),
 ('reference_B2','A',True),('reference_B2','B2',True),('reference_B2','B1',False)]

def validate_json_task(task,output):
    metadata=json.loads((task/'metadata.json').read_text());checks=[]
    starter=metadata['starter_probe']
    response=invoke_json(task/'base_repo',metadata['module'],starter['request'])
    try:starter_pass=response.returncode==0 and json.loads(response.stdout)==starter['expected']
    except ValueError:starter_pass=False
    for name,phase,expected in MATRIX:
        result=run_suite(task/name,task,phase)
        loaded=re.search(r'Ran (\d+) tests? in',result['stderr'])
        suite_loaded=bool(loaded) and int(loaded.group(1))>0 and 'Failed to import test module' not in result['stderr']
        checks.append({'reference':name,'suite':phase,'expected_pass':expected,
                       'actual_pass':result['pass'],'suite_loaded':suite_loaded,
                       'tests_run':int(loaded.group(1)) if loaded else None})
        save_json(output/(name+'_'+phase+'.json'),result)
    probes={name:evaluate(task/name,task) for name in ('reference_A','reference_B1','reference_B2')}
    probe_checks=[]
    for name,result in probes.items():
        for future in ('B1','B2'):
            probe_checks.append({'reference':name,'future':future,
                'expected':name=='reference_'+future,'actual':result['behavioral_observed'][future]})
    passed=starter_pass and all(c['suite_loaded'] and c['actual_pass']==c['expected_pass'] for c in checks)
    passed &= all(c['actual']==c['expected'] for c in probe_checks)
    report={'pass':passed,'type':'authored_reference_contract_validation_not_experiment',
        'task_id':metadata['task_id'],'task_class':'unvalidated','independent_expert_review':'pending',
        'starter_works':starter_pass,'checks':checks,'probe_checks':probe_checks,
        'leakage_probe_validation':probes,'task_sha256':tree_digest(task),'formal_study_runs':0}
    save_json(output/'validation.json',report)
    if not passed:raise ValueError('Authored task reference validation failed: '+metadata['task_id'])
    return report
