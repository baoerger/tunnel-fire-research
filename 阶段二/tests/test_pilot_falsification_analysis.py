import csv
import math
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import analyze_pilot_falsification as analysis


class PilotFalsificationAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.design = root / "design.csv"
        self.quality = root / "quality.csv"
        self.windows = root / "windows.csv"
        self.profiles = root / "profiles.csv"
        self.noise = root / "noise.csv"
        self.evidence = root / "sensor_evidence.csv"
        self._write(self.evidence, [{
            "sensor_threshold_C": 5.0,
            "source_type": "project_engineering_requirement",
            "source_reference": "approved test requirement R-1",
            "decision_basis": "minimum useful engineering temperature rise",
            "approval_status": "APPROVED",
        }])
        self.runs = root / "runs"
        self.outdir = root / "out"
        self.chids = ("pilot_a", "pilot_b")
        self.xs = [20.0 + 5.0 * index for index in range(13)]
        design_rows = []
        quality_rows = []
        window_rows = []
        profile_rows = []
        noise_rows = []
        for case_index, chid in enumerate(self.chids):
            design_rows.append({
                "chid": chid, "Q": 20 + 20 * case_index, "U_over_Uc": 0.5 + case_index * 0.4,
                "Df": 4 + case_index, "flow_layer": "weak",
            })
            quality_rows.append({
                "chid": chid, "run_chid": chid, "status": "PASS", "fds_version": "6.10.1",
            })
            window_rows.append({"chid": chid, "status": "PASS", "avg_t0": 0, "avg_t1": 39})
            noise_rows.append({
                "chid": chid, "DeltaT_noise_C": 1.0,
                "source": "repeated_seed_test", "evidence_path": "repeat.csv",
            })
            values = analysis.tm.point_profile(
                self.xs, 160 + 20 * case_index, 50.0, 0.10 + 0.01 * case_index, 0.09
            )
            for sensor_index, (x, value) in enumerate(zip(self.xs, values)):
                profile_rows.append({
                    "chid": chid, "sensor": f"T_{int(x * 100):04d}", "x": x,
                    "t0": 0, "t1": 39, "T_bar": value, "T_mean_C": value + 20,
                    "sd": 0.2, "ci_lo": value - 0.4, "ci_hi": value + 0.4,
                    "block_len_samples": 2, "n_samples": 40, "n_boot": 100,
                    "seed": sensor_index,
                })
            self._write_devc(chid, values)
        self._write(self.design, design_rows)
        self._write(self.quality, quality_rows)
        self._write(self.windows, window_rows)
        self._write(self.profiles, profile_rows)
        self._write(self.noise, noise_rows)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _write(path, rows):
        with Path(path).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def _write_devc(self, chid, values):
        case = self.runs / chid
        case.mkdir(parents=True)
        path = case / f"{chid}_devc.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            sensors = [f"T_{int(x * 100):04d}" for x in self.xs]
            writer.writerow(["s"] + ["C"] * len(sensors))
            writer.writerow(["Time"] + sensors)
            time = 0.0
            for index in range(40):
                # 小幅自适应步变化应被 FDS 时序检查接受。
                if index:
                    time += 1.0 + (0.02 if index % 2 else -0.02)
                writer.writerow([
                    time,
                    *[20 + value + 0.1 * math.sin(index / 4 + sensor)
                      for sensor, value in enumerate(values)],
                ])

    def _run(self, **overrides):
        kwargs = dict(
            design_path=self.design, quality_path=self.quality,
            windows_path=self.windows, profiles_path=self.profiles,
            run_root=self.runs, outdir=self.outdir,
            noise_thresholds_path=self.noise,
            threshold_evidence_path=self.evidence, n_boot=30,
        )
        kwargs.update(overrides)
        return analysis.run_analysis(**kwargs)

    def test_formal_inputs_produce_traceable_outputs(self):
        summary, rows = self._run()
        self.assertEqual("FORMAL_QUALITY_PASS", summary["evidence_status"])
        self.assertEqual(2, len(rows))
        self.assertTrue((self.outdir / "pilot_case_metrics.csv").is_file())
        self.assertTrue(all(row["data_origin"] == "REAL_FDS" for row in rows))

    def test_review_is_rejected_by_default_and_never_formal(self):
        with self.quality.open(encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        for row in rows:
            row["status"] = "REVIEW"
            row["fds_version"] = "6.9.1"
        self._write(self.quality, rows)
        with self.assertRaises(ValueError):
            self._run()
        summary, case_rows = self._run(
            allow_review=True, noise_thresholds_path=None,
            threshold_evidence_path=None,
        )
        self.assertEqual("PRELIMINARY_REVIEW_NOT_FORMAL", summary["evidence_status"])
        self.assertEqual("NOT_EVALUATED_REVIEW_DATA", summary["formal_gate_status"])
        self.assertTrue(all(row["decision_eligible"] == "false" for row in case_rows))

    def test_formal_analysis_requires_threshold_evidence(self):
        with self.assertRaisesRegex(ValueError, "threshold-evidence"):
            self._run(threshold_evidence_path=None)
        with self.assertRaisesRegex(ValueError, "噪声阈值"):
            self._run(noise_thresholds_path=None)

    def test_threshold_evidence_must_match_value_and_be_approved(self):
        with self.evidence.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        rows[0]["sensor_threshold_C"] = "4.0"
        self._write(self.evidence, rows)
        with self.assertRaisesRegex(ValueError, "不一致"):
            self._run()
        rows[0]["sensor_threshold_C"] = "5.0"
        rows[0]["approval_status"] = "WAITING_USER_EVIDENCE"
        self._write(self.evidence, rows)
        with self.assertRaisesRegex(ValueError, "APPROVED"):
            self._run()

    def test_pass_quality_can_be_explored_without_threshold_evidence(self):
        summary, case_rows = self._run(
            noise_thresholds_path=None,
            threshold_evidence_path=None,
            exploratory_thresholds=True,
        )
        self.assertEqual(
            "FORMAL_QUALITY_PASS_THRESHOLD_CANDIDATE",
            summary["evidence_status"],
        )
        self.assertEqual(
            "NOT_EVALUATED_THRESHOLD_EVIDENCE",
            summary["formal_gate_status"],
        )
        self.assertTrue(all(row["decision_eligible"] == "false" for row in case_rows))


if __name__ == "__main__":
    unittest.main()
