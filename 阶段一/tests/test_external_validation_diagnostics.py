import csv
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import diagnose_external_validation as diagnostics


class ExternalValidationDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.external = root / "external"
        self.runs = root / "runs"
        self.out = root / "out"
        self.chid = "case_a"
        self.cases = self.external / "cases.csv"
        self.mapping = self.external / "mapping.csv"
        self.observations = self.external / "observations.csv"
        self.quality = self.external / "quality.csv"
        raw = self.external / "raw" / "experiment.csv"
        case = self.runs / self.chid
        raw.parent.mkdir(parents=True)
        case.mkdir(parents=True)
        self._write(self.cases, [{
            "chid": self.chid, "profile_mode": "fds_timeseries",
            "raw_files": "raw/experiment.csv",
        }])
        self._write(self.mapping, [
            {"chid": self.chid, "raw_channel": "TC1", "x_source_m": 1,
             "x_rel_fire_m": -1, "y_m": 0, "z_m": 1,
             "baseline_t0_s": 0, "baseline_t1_s": 10,
             "window_t0_s": 20, "window_t1_s": 30},
            {"chid": self.chid, "raw_channel": "TC2", "x_source_m": 3,
             "x_rel_fire_m": 1, "y_m": 0, "z_m": 1,
             "baseline_t0_s": 0, "baseline_t1_s": 10,
             "window_t0_s": 20, "window_t1_s": 30},
        ])
        self._write(self.observations, [
            {"chid": self.chid, "sensor_id": "TC1", "dT_mean_C": 10,
             "temperature_sd_C": 1},
            {"chid": self.chid, "sensor_id": "TC2", "dT_mean_C": 20,
             "temperature_sd_C": 1},
        ])
        self._write(self.quality, [{
            "chid": self.chid, "run_chid": self.chid,
            "status": "PASS", "fds_version": "6.9.1",
        }])
        self._write_fds_csv(raw, exp=True)
        self._write_fds_csv(case / f"{self.chid}_devc.csv", exp=False)
        (case / f"{self.chid}.fds").write_text(
            "\n".join([
                f"&HEAD CHID='{self.chid}' /",
                "&DEVC ID='TC1', XYZ=1,0,1, QUANTITY='THERMOCOUPLE' /",
                "&DEVC XYZ=3 0 1, QUANTITY='THERMOCOUPLE', ID='TC2' /",
                "&TAIL /",
            ]) + "\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _write(path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def _write_fds_csv(path, exp):
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(["s", "C", "C"])
            writer.writerow(["Time", "TC1", "TC2"])
            for time in (0, 5, 10):
                writer.writerow([time, 20, 20])
            values = (30, 40) if exp else (40, 50)
            for time in (15, 20, 25, 30, 35):
                writer.writerow([time, *values])

    def _run(self):
        return diagnostics.run_diagnostics(
            self.cases, self.mapping, self.observations, self.quality,
            self.runs, self.out,
        )

    def test_systematic_bias_and_mapping_are_reported(self):
        summaries, points, windows = self._run()
        self.assertEqual(1, len(summaries))
        self.assertEqual("SYSTEMATIC_OVERPREDICTION", summaries[0]["residual_pattern"])
        self.assertEqual("TIME_WINDOW_NOT_PRIMARY", summaries[0]["primary_interpretation"])
        self.assertEqual("true", summaries[0]["mapping_contract_ok"])
        self.assertEqual(2, len(points))
        self.assertEqual(5, len(windows))
        self.assertTrue((self.out / "external_diagnostic_summary.csv").is_file())

    def test_coordinate_mismatch_is_not_silently_accepted(self):
        case = self.runs / self.chid / f"{self.chid}.fds"
        case.write_text(
            case.read_text(encoding="utf-8").replace("XYZ=1,0,1", "XYZ=1,0,0.5"),
            encoding="utf-8",
        )
        summaries, points, _ = self._run()
        self.assertEqual("false", summaries[0]["mapping_contract_ok"])
        self.assertEqual("MAPPING_OR_DEVC_CONTRACT_PROBLEM", summaries[0]["primary_interpretation"])
        self.assertEqual("false", points[0]["coordinate_match"])


if __name__ == "__main__":
    unittest.main()
