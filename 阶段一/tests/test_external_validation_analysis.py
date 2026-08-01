import csv
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import analyze_external_validation as analysis


class ExternalValidationAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.run_root = root / "runs"
        self.outdir = root / "analysis"
        self.mapping = root / "mapping.csv"
        self.observations = root / "observations.csv"
        self.quality = root / "quality.csv"
        self.chid = "external_a"
        case = self.run_root / self.chid
        case.mkdir(parents=True)
        self._write(self.mapping, [
            {
                "chid": self.chid, "raw_channel": "TC1", "x_source_m": "1",
                "x_rel_fire_m": "-1", "baseline_t0_s": "0", "baseline_t1_s": "10",
                "window_t0_s": "20", "window_t1_s": "30",
            },
            {
                "chid": self.chid, "raw_channel": "TC2", "x_source_m": "3",
                "x_rel_fire_m": "1", "baseline_t0_s": "0", "baseline_t1_s": "10",
                "window_t0_s": "20", "window_t1_s": "30",
            },
        ])
        self._write(self.observations, [
            {"chid": self.chid, "sensor_id": "TC1", "x_rel_fire_m": "-1", "dT_mean_C": "20"},
            {"chid": self.chid, "sensor_id": "TC2", "x_rel_fire_m": "1", "dT_mean_C": "30"},
        ])
        self._write(self.quality, [{
            "chid": self.chid, "run_chid": self.chid, "status": "PASS", "fds_version": "6.10.1",
        }])
        with (case / f"{self.chid}_devc.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerows([
                ["s", "C", "C"], ["Time", "TC1", "TC2"],
                [0.01, 20, 20], [10, 20, 20], [20, 40, 50], [30, 40, 50],
            ])

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _write(path, rows):
        fields = list(rows[0])
        with Path(path).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_pass_quality_extracts_traceable_exact_profile(self):
        summaries, profiles, path = analysis.run_analysis(
            self.mapping, self.observations, self.quality,
            self.run_root, self.outdir,
        )
        self.assertTrue(path.is_file())
        self.assertEqual(2, len(profiles))
        self.assertEqual("REAL_FDS", profiles[0]["data_origin"])
        self.assertEqual("true", profiles[0]["decision_eligible"])
        self.assertAlmostEqual(0.0, summaries[0]["nrmse"], places=12)

    def test_review_is_rejected_by_default_and_explicitly_preliminary(self):
        self._write(self.quality, [{
            "chid": self.chid, "run_chid": self.chid, "status": "REVIEW", "fds_version": "6.9.1",
        }])
        with self.assertRaises(ValueError):
            analysis.run_analysis(
                self.mapping, self.observations, self.quality,
                self.run_root, self.outdir,
            )
        summaries, profiles, _ = analysis.run_analysis(
            self.mapping, self.observations, self.quality,
            self.run_root, self.outdir, allow_review=True,
        )
        self.assertEqual("false", profiles[0]["decision_eligible"])
        self.assertEqual("PRELIMINARY_REVIEW_NOT_FORMAL", summaries[0]["evidence_status"])

    def test_missing_window_or_channel_fails(self):
        with self.mapping.open(encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        rows[0]["raw_channel"] = "MISSING"
        self._write(self.mapping, rows)
        with self.assertRaises(ValueError):
            analysis.run_analysis(
                self.mapping, self.observations, self.quality,
                self.run_root, self.outdir,
            )


if __name__ == "__main__":
    unittest.main()
