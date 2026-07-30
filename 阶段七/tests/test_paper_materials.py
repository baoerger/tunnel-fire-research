import csv
import math
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
STAGE = HERE.parent
MATERIALS = STAGE / "04_论文与验收"


def read_csv(name):
    with (MATERIALS / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


class PaperMaterialsTests(unittest.TestCase):
    def test_t2_candidates_cover_three_rates_and_two_peaks(self):
        rows = read_csv("t2_transient_candidates.csv")
        self.assertEqual(len(rows), 6)
        self.assertEqual(
            {(row["growth_class"], float(row["Q_max_MW"])) for row in rows},
            {(rate, power) for rate in ("slow", "medium", "fast")
             for power in (20.0, 60.0)},
        )
        self.assertEqual(
            {row["growth_class"]: float(row["alpha_kW_s2"]) for row in rows},
            {"slow": 0.00293, "medium": 0.01172, "fast": 0.0469},
        )
        for row in rows:
            expected = math.sqrt(float(row["Q_max_MW"]) * 1000.0 /
                                 float(row["alpha_kW_s2"]))
            self.assertAlmostEqual(float(row["t_to_Qmax_s"]), expected, places=3)

    def test_t2_candidates_remain_blocked_without_fds_inputs(self):
        for row in read_csv("t2_transient_candidates.csv"):
            self.assertEqual(row["status"], "CANDIDATE_ONLY_WAITING_STAGE6_GATE")
            self.assertEqual(row["required_gate"], "STAGE6_COMPARISON_COMPLETE")
            self.assertEqual(row["fds_input_path"].strip(), "")
            self.assertEqual(row["data_source"], "CANDIDATE_DESIGN_NO_FDS")
        self.assertEqual(list(STAGE.rglob("*.fds")), [])

    def test_acceptance_register_has_all_eleven_predeclared_criteria(self):
        rows = read_csv("验收复核表.csv")
        self.assertEqual(
            {row["criterion_id"] for row in rows},
            {f"FWD_{index:02d}" for index in range(1, 7)}
            | {f"INV_{index:02d}" for index in range(1, 5)}
            | {"NET_01"},
        )
        self.assertTrue(all(row["decision"] == "NOT_EVALUATED" for row in rows))
        self.assertTrue(all(row["evidence_path"].strip() == "" for row in rows))

    def test_evidence_register_has_four_innovations_and_twelve_outputs(self):
        rows = read_csv("创新点与预期成果证据表.csv")
        innovations = [row for row in rows if row["record_type"] == "innovation"]
        outputs = [row for row in rows if row["record_type"] == "expected_output"]
        self.assertEqual({row["record_id"] for row in innovations},
                         {f"I{index}" for index in range(1, 5)})
        self.assertEqual({row["record_id"] for row in outputs},
                         {f"O{index}" for index in range(1, 13)})
        self.assertTrue(all(row["evidence_path"].strip() == "" for row in rows))
        self.assertTrue(all("WAITING" in row["status"] or
                            "FRAMEWORK" in row["status"] or
                            "SYNTHETIC" in row["status"] or
                            "CANDIDATE" in row["status"] for row in rows))

    def test_every_csv_has_traceability_columns(self):
        alternatives = {
            "data source": {"data_source"},
            "subset": {"subset"},
            "version": {"model_version", "model_or_data_version"},
            "script": {"analysis_script"},
            "metric": {"metric", "metric_or_figure"},
            "evidence path": {"evidence_path"},
        }
        for path in sorted(MATERIALS.glob("*.csv")):
            with path.open(encoding="utf-8", newline="") as handle:
                header = set(next(csv.reader(handle)))
            for label, names in alternatives.items():
                self.assertTrue(header & names, f"{path.name} lacks {label}")

    def test_figure_register_and_templates_do_not_claim_results(self):
        rows = read_csv("论文图表清单.csv")
        self.assertEqual(sum(row["item_type"] == "figure" for row in rows), 12)
        self.assertEqual(sum(row["item_type"] == "table" for row in rows), 3)
        self.assertEqual(len({row["output_path"] for row in rows}), len(rows))
        self.assertTrue(all(row["evidence_status"] != "PASS" for row in rows))

        methods = (MATERIALS / "论文方法与数据流模板.md").read_text(encoding="utf-8")
        results = (MATERIALS / "论文结果表模板.md").read_text(encoding="utf-8")
        for field in ("data_source", "subset", "model_or_data_version",
                      "analysis_script", "metric_or_figure", "evidence_path"):
            self.assertIn(field, methods)
        self.assertIn("不得作为科学结论", methods)
        self.assertIn("WAITING", results)


if __name__ == "__main__":
    unittest.main()
