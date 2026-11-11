"""Prospective submitted-trajectory outcomes; no historical recoding or authority."""
from evaluator.grade_policy import POLICY as GRADE_POLICY, validate_grade

POLICY='submitted-trajectory-v1'
INTEGRITY={'token_accounting_mismatch','provider_budget_violation'}
INFRASTRUCTURE={'infrastructure_error','sandbox_cleanup_unresolved'}
AGENT_FAILURE={'time_budget_exhausted','command_budget_exhausted','token_budget_exhausted',
    'context_budget_exhausted','turn_budget_exhausted','invalid_artifact'}
KNOWN=INTEGRITY|INFRASTRUCTURE|AGENT_FAILURE|{'submitted'}


def validate_config(config):
    policy=config.get('outcome_policy')
    if policy is not None and (policy!=POLICY or config.get('grading_policy')!=GRADE_POLICY):
        raise ValueError('submitted outcome policy requires structured-grading-v1')
    if config.get('grading_policy')==GRADE_POLICY and policy!=POLICY:
        raise ValueError('agent phases require paired explicit grading and outcome policies')


def reconcile(grades,status_A,status_B):
    if set(grades)!={'upstream_A','downstream_A','downstream_B'}:
        raise ValueError('three original grade slots required')
    for grade in grades.values():validate_grade(grade)
    if any(not isinstance(s,str) or not s for s in (status_A,status_B)):
        raise ValueError('original nonempty agent statuses required')
    passes={slot:g['pass'] for slot,g in grades.items()}
    raw_correctness=(False if any(v is False for v in passes.values())
                    else None if any(v is None for v in passes.values()) else True)
    statuses={'A':status_A,'B':status_B}
    reasons=[]
    for phase,status in statuses.items():
        if status in INTEGRITY:reasons.append(dict(phase=phase,reason='integrity_unresolved',agent_status=status))
        elif status in INFRASTRUCTURE:reasons.append(dict(phase=phase,reason='infrastructure_unresolved',agent_status=status))
        elif status not in KNOWN:reasons.append(dict(phase=phase,reason='unknown_agent_status',agent_status=status))
    for slot,grade in grades.items():
        if grade['grade_status']=='unresolved':
            reasons.append(dict(grade_slot=slot,reason='grading_unresolved',provenance=grade['unresolved_reasons']))
    failure_reasons=[dict(phase=p,reason='agent_not_submitted',agent_status=s)
                     for p,s in statuses.items() if s in AGENT_FAILURE]
    failure_reasons += [dict(grade_slot=slot,reason='correctness_rejection') for slot,g in grades.items()
                       if g['grade_status']=='rejected']
    if reasons:
        priority=['integrity_unresolved','infrastructure_unresolved','unknown_agent_status','grading_unresolved']
        state=next(x for x in priority if any(r['reason']==x for r in reasons));success=None
    elif failure_reasons:
        state='agent_failure' if any(s in AGENT_FAILURE for s in statuses.values()) else 'correctness_failure'
        success=False
    else:
        state='success';success=True
    return dict(policy=POLICY,outcome_status=state,trajectory_success=success,
        raw_correctness=raw_correctness,raw_grade_passes=passes,agent_statuses=statuses,
        both_submitted=all(s=='submitted' for s in statuses.values()),
        unresolved_reasons=reasons,known_failure_reasons=failure_reasons,
        scope_trajectory_success=None,formal_collection_authorized=False)
