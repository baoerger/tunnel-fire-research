import csv
import math
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import design_pilot_cases as design
import tunnel_config as cfg


class PilotDesignTests(unittest.TestCase):
    def test_li_correlation_is_monotone_then_capped(self):
        low = design.li_critical_velocity(5)
        medium = design.li_critical_velocity(20)
        high = design.li_critical_velocity(100)
        self.assertLess(low[0], medium[0])
        self.assertLessEqual(medium[0], high[0])
        self.assertAlmostEqual(0.43, high[2])
        self.assertTrue(all(math.isfinite(value) and value > 0 for value in high))

    def test_wu_bakar_hydraulic_height_and_piecewise_branches(self):
        low = design.wu_bakar_critical_velocity(5, W=10, H=5)
        high = design.wu_bakar_critical_velocity(100, W=10, H=5)
        hydraulic_height = 4 * (10 * 5) / (2 * (10 + 5))
        self.assertAlmostEqual(hydraulic_height, low[3])
        self.assertLessEqual(low[1], 0.20)
        self.assertAlmostEqual(0.4 * low[1] ** (1 / 3), low[2])
        self.assertGreater(high[1], 0.20)
        self.assertAlmostEqual(0.30, high[2])
        self.assertTrue(all(math.isfinite(value) and value > 0 for value in high))

    def test_wu_bakar_is_only_an_explicit_alternative(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows, estimates = design.build(
                design.DEFAULT_TARGETS,
                Path(tmp) / "cases.csv",
                Path(tmp) / "uc.csv",
            )
        self.assertTrue(all("Li_2010" in row["critical_velocity_method"] for row in rows))
        self.assertTrue(all("Li_2010" in row["method"] for row in estimates))

    def test_wu_bakar_rejects_nonpositive_geometry(self):
        with self.assertRaisesRegex(ValueError, "必须为正"):
            design.wu_bakar_critical_velocity(40, W=0, H=5)

    def test_candidate_generation_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases_path = Path(tmp) / "cases.csv"
            uc_path = Path(tmp) / "uc.csv"
            rows, estimates = design.build(
                design.DEFAULT_TARGETS, cases_path, uc_path
            )
            self.assertEqual(12, len(rows))
            self.assertEqual(7, len(estimates))
            self.assertTrue(cases_path.is_file() and uc_path.is_file())
        regimes = {row["flow_layer"] for row in rows}
        self.assertEqual({"no_wind", "weak", "near_critical", "strong"}, regimes)
        self.assertEqual(3, sum(bool(row["reuse_chid"]) for row in rows))
        self.assertTrue(all(row["design_status"] == design.DESIGN_STATUS for row in rows))
        self.assertTrue(all(float(row["dx"]) == cfg.WORKING_GRID_DX for row in rows))
        self.assertTrue(all(float(row["L"]) == cfg.L for row in rows))
        lower, upper = cfg.HRRPUA_CANDIDATE_RANGE
        self.assertTrue(all(lower <= float(row["HRRPUA_kW_m2"]) <= upper for row in rows))

    def test_tracked_outputs_match_targets_and_generator(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases_path = Path(tmp) / "cases.csv"
            uc_path = Path(tmp) / "uc.csv"
            design.build(design.DEFAULT_TARGETS, cases_path, uc_path)
            self.assertEqual(
                design.DEFAULT_CASES.read_bytes(), cases_path.read_bytes()
            )
            self.assertEqual(design.DEFAULT_UC.read_bytes(), uc_path.read_bytes())

    def test_conditionally_authorized_cases_generate_valid_fds(self):
        with tempfile.TemporaryDirectory() as tmp:
            outdir = Path(tmp) / "pilot_inputs"
            design.fds_generator._run_csv(design.DEFAULT_CASES, outdir)
            files = sorted(outdir.glob("*.fds"))
            self.assertEqual(12, len(files))
            for path in files:
                text = path.read_text(encoding="utf-8")
                self.assertEqual(22, text.count("&MESH "))
                self.assertIn("&TIME T_END=300.0 /", text)
                self.assertIn("DT_DEVC=1.0, DT_HRR=1.0", text)

    def test_measurement_domain_diagnostics_are_separate_and_generatable(self):
        diagnostic = HERE.parent / "01_先导工况设计" / "pilot_diagnostic_cases.csv"
        with diagnostic.open(newline="", encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(2, len(rows))
        self.assertEqual({"7", "8"}, {row["Df"] for row in rows})
        self.assertTrue(all(row["x_fire"] == "41" for row in rows))
        self.assertTrue(all(row["subset"] == "diagnostic_only" for row in rows))
        with tempfile.TemporaryDirectory() as tmp:
            outdir = Path(tmp)
            design.fds_generator._run_csv(diagnostic, outdir)
            for row in rows:
                text = (outdir / f"{row['chid']}.fds").read_text(encoding="utf-8")
                self.assertIn("&TIME T_END=300.0 /", text)
                self.assertIn("CHID='" + row["chid"] + "'", text)
                self.assertTrue(text.rstrip().endswith("&TAIL /"))

    def test_random_seed_repeats_are_separate_and_generatable(self):
        repeats = HERE.parent / "01_先导工况设计" / "pilot_seed_repeat_cases.csv"
        with repeats.open(newline="", encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(2, len(rows))
        self.assertEqual(
            {"pilot_06_q40_r100_d45", "pilot_11_q100_r160_d70"},
            {row["parent_chid"] for row in rows},
        )
        seeds = {int(row["les_random_seed"]) for row in rows}
        self.assertEqual(2, len(seeds))
        self.assertTrue(all(seed > 0 for seed in seeds))
        with tempfile.TemporaryDirectory() as tmp:
            outdir = Path(tmp)
            design.fds_generator._run_csv(repeats, outdir)
            for row in rows:
                text = (outdir / f"{row['chid']}.fds").read_text(encoding="utf-8")
                self.assertIn(f"RND_SEED={row['les_random_seed']}", text)
                self.assertEqual(22, text.count("&MESH "))
                self.assertIn("&TIME T_END=300.0 /", text)

    def test_fds_override_is_traceable_and_recomputes_ratio(self):
        with tempfile.TemporaryDirectory() as tmp:
            override = Path(tmp) / "override.csv"
            with override.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(["Q_MW", "Uc_FDS_m_s", "source_chid", "evidence_path", "note"])
                writer.writerow([40, 4.0, "pilot_q40", "outputs/analysis/backflow.csv", "test"])
            rows, estimates = design.build(
                design.DEFAULT_TARGETS, Path(tmp) / "cases.csv",
                Path(tmp) / "uc.csv", override,
            )
        q40 = [row for row in rows if float(row["Q"]) == 40]
        self.assertTrue(all(row["critical_velocity_method"] == "fds_backflow_override" for row in q40))
        reused = next(row for row in q40 if row["chid"] == "gsB_m")
        self.assertAlmostEqual(0.625, float(reused["U_over_Uc"]))
        estimate = next(row for row in estimates if float(row["Q_MW"]) == 40)
        self.assertEqual("pilot_q40", estimate["source_chid"])
        self.assertTrue(estimate["evidence_path"])

    def test_invalid_override_without_evidence_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            override = Path(tmp) / "override.csv"
            override.write_text(
                "Q_MW,Uc_FDS_m_s,source_chid,evidence_path,note\n40,3.0,,,bad\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "evidence_path"):
                design.build(
                    design.DEFAULT_TARGETS, Path(tmp) / "cases.csv",
                    Path(tmp) / "uc.csv", override,
                )


if __name__ == "__main__":
    unittest.main()
