import tempfile
import unittest
from pathlib import Path

from craft_data.experiments import run_experiment


class ExperimentTests(unittest.TestCase):
    def test_comparison_report_and_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            report = run_experiment(str(Path(td) / "exp.db"))
        self.assertIn("results", report)
        self.assertEqual(report["queries"][0]["expected_source_ids"], ("d1",))
        self.assertTrue(report["integrity"]["poly_dangling_allowed"])
        self.assertFalse(report["integrity"]["norm_dangling_allowed"])
        self.assertTrue(report["integrity"]["norm_contract_enforced"])
        self.assertTrue(report["integrity"]["separate_contract_enforced"])

    def test_permissions_and_metrics_present(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            report = run_experiment(str(Path(td) / "exp.db"))
        blocked = report["results"]["norm"]["q_health_blocked"]
        self.assertEqual(blocked["ids"], ())
        for strategy in ("poly", "norm", "separate", "keyword"):
            q = report["results"][strategy]["q_budget"]
            self.assertGreaterEqual(q["duration_ms"], 0.0)
            self.assertIn("precision", q)
            self.assertIn("recall", q)
        self.assertEqual(report["recipe_vector_counts"]["body_v1"], 6)
        self.assertEqual(report["counts"]["norm_embeddings"]["rows"], 12)


if __name__ == "__main__":
    unittest.main()
