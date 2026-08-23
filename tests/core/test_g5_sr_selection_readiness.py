import unittest

from src.analysis.check_g5_sr_selection_readiness import (
    assess_shape_selection_readiness,
)


def _row(fold, *, stage="INNER_SEARCH", eligible=True, family="STRUCT_x"):
    return {
        "campaign_id": "shape_xi_only",
        "stage": stage,
        "outer_fold_index": str(fold),
        "freeze_eligible": str(eligible),
        "structure_family_id": family,
    }


class G5SRSelectionReadinessTests(unittest.TestCase):
    def test_missing_outer_folds_fail_closed(self):
        rows = [_row(fold) for fold in range(9) if fold not in {3, 7}]
        result = assess_shape_selection_readiness(rows, outer_fold_count=9)
        self.assertEqual(
            "SHAPE_EXPANSION_DECISION_NOT_EVALUABLE", result["status"]
        )
        self.assertEqual([3, 7], result["missing_outer_folds"])
        self.assertFalse(result["can_create_shape_expansion_decision"])
        self.assertEqual("PROTOCOL_AUTHORIZATION_REQUIRED", result["next_action"])

    def test_all_outer_folds_are_ready(self):
        rows = [_row(fold, family=f"STRUCT_{fold}") for fold in range(9)]
        result = assess_shape_selection_readiness(rows, outer_fold_count=9)
        self.assertEqual("READY_FOR_UNIQUE_OUTER_SELECTION", result["status"])
        self.assertEqual([], result["missing_outer_folds"])
        self.assertTrue(result["can_create_shape_expansion_decision"])

    def test_wrong_campaign_and_ineligible_rows_do_not_count(self):
        rows = [_row(fold) for fold in range(9)]
        rows[4]["campaign_id"] = "peak_q_d"
        rows[6]["freeze_eligible"] = "False"
        rows.append(_row(8, stage="FINAL_SEARCH"))
        result = assess_shape_selection_readiness(rows, outer_fold_count=9)
        self.assertEqual([4, 6], result["missing_outer_folds"])


if __name__ == "__main__":
    unittest.main()
