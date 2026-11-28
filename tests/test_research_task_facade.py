from __future__ import annotations

import unittest

from research.historical_reports.tasks import load_legacy_task
from research.historical_reports.tasks.legacy import TASK_FILES


class TaskFacadeTests(unittest.TestCase):
    def test_task_registry_is_complete(self):
        self.assertEqual(
            set(TASK_FILES),
            {"multi-event", "stage-c", "stage-d"},
        )

    def test_each_task_loads_and_exposes_main(self):
        for task in TASK_FILES:
            with self.subTest(task=task):
                module = load_legacy_task(task)
                self.assertTrue(callable(module.main))

    def test_multi_event_numeric_parser(self):
        module = load_legacy_task("multi-event")
        self.assertEqual(module.parse_numeric("0.3%"), 0.3)
        self.assertEqual(module.parse_numeric("(0.4%)"), -0.4)
        self.assertEqual(module.parse_numeric("1.2M"), 1_200_000.0)

    def test_stage_d_brier_score_is_zero_for_perfect_predictions(self):
        import numpy as np

        module = load_legacy_task("stage-d")
        score = module.brier_score(["up", "down"], np.array([[1, 0, 0], [0, 1, 0]], dtype=float))
        self.assertAlmostEqual(score, 0.0)


if __name__ == "__main__":
    unittest.main()
