"""Synthetic prospective outcomes and opt-in phase integration; no model runs."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from evaluator.grade_policy import POLICY as GRADE_POLICY
from runner.outcome_policy import POLICY,reconcile
from runner.agent import validate_config
from runner.common import TASK,copy_tree,initialize
from runner.freeze_artifact import freeze
from runner.phase_b import phase_b
from tests.test_context_calibration import CONFIG as ENGINE


def grade(status='accepted'):
    return dict(policy=GRADE_POLICY,grade_status=status,**{'pass':{'accepted':True,'rejected':False,'unresolved':None}[status]},
        tests_run=1,assertion_failures=[dict(test_id='owned',error_class='AssertionError')] if status=='rejected' else [],
        suite_complete=status!='unresolved',unresolved_reasons=[dict(reason='command_watchdog')] if status=='unresolved' else [])


CONFIG={**ENGINE,'grading_policy':GRADE_POLICY,'outcome_policy':POLICY,
        'grader_suite_timeout_seconds':90,'grader_command_timeout_seconds':5,'grader_capture_max_bytes_per_stream':2097152}
METRICS=dict(status='submitted',tokens=5,input_tokens=25,tool_calls=0,wall_seconds=1)


class Outcomes(unittest.TestCase):
    def grades(self,**extra):
        return {**{slot:grade() for slot in ('upstream_A','downstream_A','downstream_B')},**extra}

    def test_success_requires_three_accepted_grades_and_both_submissions(self):
        outcome=reconcile(self.grades(),'submitted','submitted')
        self.assertIs(outcome['trajectory_success'],True);self.assertIs(outcome['raw_correctness'],True)
        self.assertTrue(outcome['both_submitted']);self.assertFalse(outcome['formal_collection_authorized'])

    def test_passing_artifacts_do_not_erase_budgeted_non_submission(self):
        for status in ('command_budget_exhausted','time_budget_exhausted','token_budget_exhausted',
                       'context_budget_exhausted','turn_budget_exhausted','invalid_artifact'):
            for phase in ('A','B'):
                with self.subTest(status=status,phase=phase):
                    o=reconcile(self.grades(),status if phase=='A' else 'submitted',status if phase=='B' else 'submitted')
                    self.assertIs(o['raw_correctness'],True);self.assertIs(o['trajectory_success'],False)
                    self.assertEqual(o['outcome_status'],'agent_failure')

    def test_failed_upstream_stays_rejected_after_downstream_repair(self):
        o=reconcile(self.grades(upstream_A=grade('rejected')),'submitted','submitted')
        self.assertIs(o['raw_correctness'],False);self.assertIs(o['trajectory_success'],False)

    def test_any_unresolved_grade_makes_operational_outcome_missing(self):
        for slot in self.grades():
            with self.subTest(slot=slot):
                o=reconcile(self.grades(**{slot:grade('unresolved')}),'submitted','submitted')
                self.assertIsNone(o['trajectory_success']);self.assertIsNone(o['raw_correctness'])
                self.assertEqual(o['outcome_status'],'grading_unresolved')

    def test_known_rejection_remains_visible_alongside_unresolved_grade(self):
        o=reconcile(self.grades(upstream_A=grade('rejected'),downstream_B=grade('unresolved')),'submitted','submitted')
        self.assertIs(o['raw_correctness'],False);self.assertIsNone(o['trajectory_success'])
        self.assertTrue(o['known_failure_reasons']);self.assertTrue(o['unresolved_reasons'])

    def test_accounting_and_provider_integrity_states_are_missing(self):
        for status in ('token_accounting_mismatch','provider_budget_violation'):
            with self.subTest(status=status):
                o=reconcile(self.grades(),status,'submitted')
                self.assertIsNone(o['trajectory_success']);self.assertEqual(o['outcome_status'],'integrity_unresolved')
                self.assertIs(o['raw_correctness'],True)

    def test_infrastructure_and_unknown_statuses_cannot_be_counted_as_failures(self):
        for status,state in [('infrastructure_error','infrastructure_unresolved'),
                             ('sandbox_cleanup_unresolved','infrastructure_unresolved'),('new-unknown','unknown_agent_status'),
                             ('reference_fixture','unknown_agent_status')]:
            with self.subTest(status=status):
                o=reconcile(self.grades(),'submitted',status)
                self.assertIsNone(o['trajectory_success']);self.assertEqual(o['outcome_status'],state)

    def test_all_unresolved_reasons_survive_priority_selection(self):
        o=reconcile(self.grades(downstream_A=grade('unresolved')),'provider_budget_violation','infrastructure_error')
        self.assertEqual(len(o['unresolved_reasons']),3);self.assertEqual(o['outcome_status'],'integrity_unresolved')

    def test_inputs_are_not_mutated_and_scope_is_not_inferred(self):
        grades=self.grades();before=copy.deepcopy(grades);o=reconcile(grades,'submitted','submitted')
        self.assertEqual(grades,before);self.assertIsNone(o['scope_trajectory_success'])

    def test_missing_slots_and_legacy_or_inconsistent_grade_are_rejected(self):
        for grades in ({},self.grades(downstream_A={'pass':True}),self.grades(downstream_A={**grade(), 'pass':1})):
            with self.subTest(grades=grades),self.assertRaises(ValueError):reconcile(grades,'submitted','submitted')

    def test_opt_in_requires_paired_policy_and_explicit_limits(self):
        validate_config(CONFIG)
        for cfg in ({**CONFIG,'grading_policy':None},{**CONFIG,'outcome_policy':None},
                    {**CONFIG,'outcome_policy':'unknown'},{**CONFIG,'grader_command_timeout_seconds':None}):
            with self.subTest(cfg=cfg),self.assertRaises(ValueError):validate_config(cfg)

    def prepare(self,root,assessment=None):
        repo=root/'repo';copy_tree(TASK/'reference_A',repo);initialize(repo)
        with patch('runner.freeze_artifact.grading_environment'), \
                patch('runner.freeze_artifact.run_suite_prospective',return_value=assessment or grade()), \
                patch('runner.freeze_artifact.evaluate',return_value={'behavioral_leakage':None}):
            return freeze(repo,root/'artifacts','B1',0,METRICS,config=CONFIG)

    def branch(self,root,artifact,status='submitted',grades=None):
        with patch('runner.phase_b.run_agent',return_value={**METRICS,'status':status}), \
                patch('runner.phase_b.grading_environment'), \
                patch('runner.phase_b.run_suite_prospective',side_effect=grades or [grade(),grade()]):
            return phase_b(root,artifact,'B1',0,CONFIG)

    def test_phase_integration_retains_grade_file_pins_and_separate_raw_outcome(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);artifact,upstream=self.prepare(root)
            row=self.branch(root,artifact,status='command_budget_exhausted')
            self.assertIs(row['raw_correctness'],True);self.assertIs(row['trajectory_success'],False)
            self.assertEqual(row['operational_outcome']['outcome_status'],'agent_failure')
            self.assertEqual(row['original_grade_sha256']['upstream_A'],hashlib.sha256((artifact/'A_tests.json').read_bytes()).hexdigest())

    def test_phase_integration_preserves_integrity_unknown_and_raw_acceptance(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);artifact,_=self.prepare(root)
            row=self.branch(root,artifact,status='provider_budget_violation')
            self.assertIsNone(row['trajectory_success']);self.assertIs(row['raw_correctness'],True)

    def test_phase_grading_stops_future_after_unresolved_current_suite(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);artifact,_=self.prepare(root)
            row=self.branch(root,artifact,grades=[grade('unresolved')])
            self.assertIsNone(row['trajectory_success']);self.assertIsNone(row['B_pass'])
            reasons=row['operational_outcome']['unresolved_reasons']
            self.assertEqual(len(reasons),2)
            self.assertEqual(reasons[-1]['provenance'][0]['reason'],'not_invoked_after_unresolved_current_grade')

    def test_tampered_or_legacy_A_grade_cannot_enter_prospective_reader(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);artifact,_=self.prepare(root)
            (artifact/'A_tests.json').write_text('{"pass":true}\n')
            with patch('runner.phase_b.run_agent') as request,self.assertRaises(ValueError):self.branch(root,artifact)
            request.assert_not_called()

    def test_unresolved_A_grade_retains_artifact_and_skips_scope_probes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);repo=root/'repo';copy_tree(TASK/'reference_A',repo);initialize(repo)
            with patch('runner.freeze_artifact.grading_environment'), \
                    patch('runner.freeze_artifact.run_suite_prospective',return_value=grade('unresolved')), \
                    patch('runner.freeze_artifact.evaluate') as screen:
                artifact,record=freeze(repo,root/'artifacts','B1',0,METRICS,config=CONFIG)
            self.assertTrue((artifact/'repo').is_dir());self.assertIsNone(record['A_pass']);screen.assert_not_called()
            self.assertEqual(record['leakage']['probe_status'],'not_invoked_after_unresolved_grading')

    def agent_integrity(self,prompt_tokens=25,completion_tokens=5):
        from runner.agent import run_agent
        commands=[]
        class Sandbox:
            def __init__(self,*args):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def run(self,command,timeout):
                commands.append(command);return dict(returncode=0,stdout='',stderr='')
        def response(*args):return dict(choices=[dict(message=dict(content='{"action":"submit_patch"}'),finish_reason='stop')],
            usage=dict(prompt_tokens=prompt_tokens,completion_tokens=completion_tokens))
        with tempfile.TemporaryDirectory() as folder:
            log=Path(folder)/'events.jsonl'
            m=run_agent(Path(folder),'owned integrity fixture',CONFIG,0,log,response,Sandbox,
                        lambda *args:dict(count=25,max_model_len=200))
            events=[json.loads(x) for x in log.read_text().splitlines()]
        return m,events,commands

    def test_actual_loop_token_mismatch_retains_reported_counts_but_null_canonical_usage(self):
        m,e,c=self.agent_integrity(prompt_tokens=26)
        self.assertEqual(m['status'],'token_accounting_mismatch');self.assertEqual(m['usage_integrity'],'unresolved')
        self.assertIsNone(m['tokens']);self.assertIsNone(m['input_tokens']);self.assertEqual(m['observed_prompt_tokens'],26)
        self.assertEqual(m['observed_completion_tokens'],5);self.assertEqual(len(c),1)
        self.assertEqual(len([x for x in e if x['event']=='model_response']),1)

    def test_actual_loop_total_provider_budget_violation_is_integrity_unknown(self):
        m,e,c=self.agent_integrity(completion_tokens=101)
        self.assertEqual(m['status'],'provider_budget_violation');self.assertIsNone(m['tokens'])
        self.assertEqual(m['observed_completion_tokens'],101);self.assertEqual(len(c),1)

    def test_actual_loop_per_turn_provider_overrun_cannot_submit(self):
        m,e,c=self.agent_integrity(completion_tokens=41)
        self.assertEqual(m['status'],'provider_budget_violation');self.assertIsNone(m['tokens'])
        self.assertEqual(m['observed_completion_tokens'],41);self.assertEqual(len(c),1)


if __name__=='__main__':unittest.main()
