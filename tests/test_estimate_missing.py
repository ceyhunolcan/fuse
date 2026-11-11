import unittest
from analysis.estimate import estimate


def crossover(task, category, values):
    return [dict(task_id=task, task_class=category, artifact_id=task+d,
                 disclosed_future=d, realized_future=r, upstream_seed=0,
                 downstream_seed=0, trajectory_success=value, data_kind='study')
            for (d, r), value in zip([('B1','B1'), ('B1','B2'), ('B2','B1'), ('B2','B2')], values)]


class MissingComparisonTests(unittest.TestCase):
    def test_incomplete_class_member_not_silently_dropped(self):
        rows = crossover('d1', 'divergent', [True, False, False, True])
        rows += crossover('d2', 'divergent', [None, False, False, True])
        rows += crossover('c1', 'convergent', [True, True, True, True])
        result = estimate(rows)
        self.assertIsNone(result['divergent_minus_convergent'])
        self.assertIsNone(result['equal_weight_tau_ID'])
        self.assertEqual(result['tasks']['d2']['tau_ID_missing_bounds'], [.5, 1.0])

    def test_complete_class_comparison_and_absent_class(self):
        rows = crossover('d', 'divergent', [True, False, False, True])
        self.assertIsNone(estimate(rows)['divergent_minus_convergent'])
        rows += crossover('c', 'convergent', [True, True, True, True])
        self.assertEqual(estimate(rows)['divergent_minus_convergent'], 1)

    def test_inconsistent_within_task_class_fails(self):
        rows = crossover('d', 'divergent', [True, False, False, True])
        rows[0]['task_class'] = 'convergent'
        with self.assertRaisesRegex(ValueError, 'inconsistent'): estimate(rows)


if __name__ == '__main__': unittest.main()
