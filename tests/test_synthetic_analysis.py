import copy
import unittest
from analysis.assignment_audit import audit_assignments, audited_estimate
from analysis.synthetic_rehearsal import fixtures
from analysis.estimate import estimate


class SyntheticAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.ledger, self.scenarios = fixtures()
        self.rows = self.scenarios['complete']

    def test_hand_calculated_complete_and_shared_block_interval(self):
        result = audited_estimate(self.rows, self.ledger)
        self.assertEqual(result['assignment_audit']['assigned'], 32)
        self.assertEqual(result['assignment_audit']['artifacts'], 8)
        summary = result['estimate']
        self.assertEqual(summary['equal_weight_tau_ID'], .25)
        self.assertEqual(summary['divergent_minus_convergent'], .5)
        self.assertEqual(summary['equal_weight_tau_ID_missing_bounds'], [.25,.25])
        # Each seed block has tau .5 despite heterogeneous rows. A row bootstrap
        # would vary; retaining whole paired blocks must give exactly [.5,.5].
        self.assertEqual(summary['tasks']['synthetic-divergent']['conditional_seed_block_bootstrap_95'], [.5,.5])
        self.assertFalse(result['formal_collection_enabled'])

    def test_missing_placeholder_preserves_assigned_denominator(self):
        result = audited_estimate(self.scenarios['missing'], self.ledger)['estimate']
        task = result['tasks']['synthetic-divergent']
        self.assertEqual(task['cells']['11']['n'], 4)
        self.assertEqual(task['cells']['11']['assigned_success_lower'], .5)
        self.assertEqual(task['cells']['11']['assigned_success_upper'], .75)
        self.assertEqual(task['tau_ID_missing_bounds'], [.375,.5])
        self.assertEqual(result['equal_weight_tau_ID_missing_bounds'], [.1875,.25])
        self.assertIsNone(result['equal_weight_tau_ID'])
        self.assertIsNone(result['divergent_minus_convergent'])
        self.assertIsNone(task['conditional_seed_block_bootstrap_95'])

    def test_deadline_is_failure_and_upstream_repair_does_not_rescue(self):
        deadline = audited_estimate(self.scenarios['deadline'], self.ledger)['estimate']
        self.assertEqual(deadline['tasks']['synthetic-divergent']['tau_ID'], .375)
        self.assertEqual(deadline['tasks']['synthetic-divergent']['cells']['11']['missing'], 0)
        repaired = audited_estimate(self.scenarios['upstream_failure'], self.ledger)['estimate']
        self.assertEqual(repaired['tasks']['synthetic-divergent']['tau_ID'], .375)
        rows = copy.deepcopy(self.scenarios['upstream_failure'])
        rows[0]['trajectory_success'] = True
        with self.assertRaisesRegex(ValueError, 'preserve upstream'): audit_assignments(rows, self.ledger)

    def test_negative_premium_not_rectified(self):
        result = audited_estimate(self.scenarios['negative'], self.ledger)['estimate']
        self.assertEqual(result['equal_weight_tau_ID'], -.25)
        self.assertEqual(result['divergent_minus_convergent'], -.5)

    def test_missing_duplicate_and_unassigned_records_rejected(self):
        for rows, message in [(self.rows[1:], 'missing assigned'),
                              (self.rows+self.rows[:1], 'duplicate outcome')]:
            with self.assertRaisesRegex(ValueError, message): audit_assignments(rows, self.ledger)
        rows = copy.deepcopy(self.rows)
        rows[0]['artifact_id'] = 'unassigned'
        with self.assertRaisesRegex(ValueError, 'unassigned'): audit_assignments(rows, self.ledger)

    def test_invalid_schema_types_and_ledger_metadata_rejected(self):
        for field, value in [('trajectory_success', 'true'), ('trajectory_success', 1),
                             ('task_class', 'convergent'), ('upstream_seed', True)]:
            rows = copy.deepcopy(self.rows); rows[0][field] = value
            with self.assertRaises(ValueError): audit_assignments(rows, self.ledger)
        rows = copy.deepcopy(self.rows); rows[0]['trajectory_success'] = 1
        with self.assertRaisesRegex(ValueError, 'boolean or null'):
            estimate(rows, include_infrastructure=True)

    def test_ledger_balance_and_artifact_identity_checked(self):
        ledger = copy.deepcopy(self.ledger); ledger['assignments'].pop()
        with self.assertRaisesRegex(ValueError, 'unbalanced'): audit_assignments(self.rows, ledger)
        ledger = copy.deepcopy(self.ledger)
        ledger['assignments'][8]['artifact_id'] = ledger['assignments'][0]['artifact_id']
        with self.assertRaisesRegex(ValueError, 'artifact reused'): audit_assignments(self.rows, ledger)

    def test_rehearsal_cannot_accept_study_records_or_release(self):
        with self.assertRaisesRegex(ValueError, 'non-study'): estimate(self.rows)
        rows = copy.deepcopy(self.rows); rows[0]['data_kind'] = 'study'
        with self.assertRaisesRegex(ValueError, 'non-synthetic'): audit_assignments(rows, self.ledger)
        ledger = copy.deepcopy(self.ledger); ledger['data_kind'] = 'study'
        with self.assertRaisesRegex(ValueError, 'only synthetic'): audit_assignments(self.rows, ledger)

    def test_one_block_has_no_interval_and_unclear_class_is_retained(self):
        rows = [r for r in self.rows if r['upstream_seed'] == 0]
        for row in rows:
            if row['task_id'] == 'synthetic-convergent': row['task_class'] = 'unclear'
        result = estimate(rows, include_infrastructure=True)
        self.assertEqual(len(result['tasks']), 2)
        self.assertIsNone(result['divergent_minus_convergent'])
        self.assertTrue(all(t['conditional_seed_block_bootstrap_95'] is None for t in result['tasks'].values()))


if __name__ == '__main__': unittest.main()
