import copy
import unittest

from research_data.tennis import evaluate


def match(day, winner, loser, tid="t", score="6-4 6-4", num=None):
    row = {
        "tourney_date": day,
        "tourney_id": tid,
        "winner_id": winner,
        "loser_id": loser,
        "score": score,
        "surface": "Hard",
    }
    if num is not None:
        row["match_num"] = num
    return row


class TennisBaselineTests(unittest.TestCase):
    def test_orientation_and_known_small_elo(self):
        rows = [match("20200101", "2", "1", "old"), match("20210101", "2", "1", "test")]
        report = evaluate(rows, "20210101", availability_days=0)
        p = report["heldout"]["elo"]["calibration"][0]["mean_probability"]
        # player 1 is canonical A; repeated player-2 wins lower its probability.
        self.assertLess(p, 0.5)
        # Reorientation changes the heldout outcome, but not input-order behavior.
        self.assertEqual(
            evaluate(rows, "20210101", 0)["counts"],
            evaluate(list(reversed(rows)), "20210101", 0)["counts"],
        )
        self.assertEqual(report["heldout"]["neutral"]["n"], 1)

    def test_cutoff_gap_and_heldout_cannot_change_fit(self):
        rows = [
            match("20200101", "1", "2", "old"),
            match("20201220", "2", "1", "near"),
            match("20210101", "1", "2", "test"),
        ]
        base = evaluate(rows, "20210101", 21)
        changed = copy.deepcopy(rows)
        changed[-1]["winner_id"], changed[-1]["loser_id"] = "2", "1"
        other = evaluate(changed, "20210101", 21)
        self.assertEqual(base["counts"]["train"], 1)
        self.assertEqual(
            base["heldout"]["elo"]["calibration"][0]["mean_probability"],
            other["heldout"]["elo"]["calibration"][0]["mean_probability"],
        )
        self.assertEqual(base["counts"]["test"], 1)

    def test_same_date_batch_order_invariance_and_reproducibility(self):
        rows = [
            match("20200101", "1", "2", "a"),
            match("20200101", "3", "1", "b"),
            match("20210101", "1", "3", "c"),
        ]
        a = evaluate(rows, "20210101", 0)
        b = evaluate(list(reversed(rows)), "20210101", 0)
        self.assertEqual(a, b)
        self.assertEqual(a, evaluate(rows, "20210101", 0))

    def test_drops_and_no_test(self):
        rows = [
            match("20200101", "1", "2", "a"),
            match("bad", "1", "2", "b"),
            match("20200101", "1", "1", "c"),
            match("20200101", "1", "2", "d", "RET"),
            match("20200101", "1", "2", "a"),
        ]
        report = evaluate(rows, "20220101")
        self.assertEqual(
            report["counts"]["dropped"],
            {
                "duplicate": 1,
                "missing_or_malformed": 1,
                "retirement_or_walkover": 1,
                "self_match": 1,
            },
        )
        self.assertEqual(report["heldout"]["elo"]["n"], 0)
        self.assertIsNone(report["heldout"]["elo"]["brier"])

    def test_ambiguous_tournament_start_date_guard(self):
        with self.assertRaisesRegex(ValueError, "ambiguous start date"):
            evaluate(
                [match("20200101", "1", "2", "same"), match("20200102", "1", "3", "same")],
                "20210101",
            )

    def test_unseen_coverage_and_calibration(self):
        report = evaluate([match("20210101", "9", "8", "new")], "20210101")
        self.assertEqual(report["coverage"]["unseen_players"], 2)
        self.assertEqual(report["heldout"]["elo"]["n"], 1)
        self.assertEqual(report["heldout"]["neutral"]["brier"], 0.25)


if __name__ == "__main__":
    unittest.main()


class ExtraBaselineTests(unittest.TestCase):
    def test_zero_availability_gap_still_never_fits_holdout(self):
        rows = [match("20200101", "1", "2", "old"), match("20210101", "1", "2", "new")]
        changed = [rows[0], dict(rows[1], winner_id="2", loser_id="1")]
        a, b = evaluate(rows, "20210101", 0), evaluate(changed, "20210101", 0)
        self.assertEqual(a["counts"]["train"], 1)
        self.assertEqual(
            a["heldout"]["elo"]["calibration"][0]["mean_probability"],
            b["heldout"]["elo"]["calibration"][0]["mean_probability"],
        )
        self.assertNotEqual(a["input_sha256"], b["input_sha256"])

    def test_nonfinite_or_boolean_settings_rejected(self):
        for value in [float("nan"), float("inf"), True]:
            with self.assertRaises(ValueError):
                evaluate([], "20210101", k=value)
        with self.assertRaises(ValueError):
            evaluate([], "20210101", availability_days=True)
