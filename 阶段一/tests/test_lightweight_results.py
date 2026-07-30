import csv
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import check_fds_results as checker


class LightweightResultCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_root = Path(self.tmp.name) / "runs"
        self.chid = "case_a"
        self.case_dir = self.run_root / self.chid
        self.case_dir.mkdir(parents=True)
        (self.case_dir / f"{self.chid}.fds").write_text(
            "\n".join([
                f"&HEAD CHID='{self.chid}', TITLE='test' /",
                "&MESH IJK=4 4 4, XB=0 1 0 1 0 1 /",
                "&TIME T_END=60.0 /",
                "&SURF ID='BURNER', HRRPUA=1000.0, TAU_Q=10.0 /",
                "&VENT XB=0 8 0 5 0 0, SURF_ID='BURNER' /",
                "&TAIL /",
            ]) + "\n",
            encoding="utf-8",
        )
        (self.case_dir / f"{self.chid}.out").write_text(
            "Fire Dynamics Simulator FDS-6.10.1-0-release\nRun completed\n",
            encoding="utf-8",
        )
        (self.case_dir / f"{self.chid}.end").write_text("completed\n", encoding="utf-8")
        self._write_csv(self.case_dir / f"{self.chid}_devc.csv", [
            ["s", "C", "m/s", "kW", "kW/m2"],
            ["Time", "T_5000", "U_5000", "HRR_tot", "Qw_5000"],
            [0, 20, 0, 0, 0],
            [15, 25, 1, 36000, 2],
            [30, 30, 2, 39800, 3],
            [45, 31, 2.5, 40000, 3.5],
            [60, 31, 2.5, 40000, 3.5],
        ])
        self._write_csv(self.case_dir / f"{self.chid}_hrr.csv", [
            ["s", "kW", "kW"],
            ["Time", "HRR", "Q_RADI"],
            [0, 0, 0],
            [15, 36000, -10000],
            [30, 39800, -11500],
            [45, 40000, -12000],
            [60, 40000, -12000],
        ])

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _write_csv(path, rows):
        with Path(path).open("w", newline="", encoding="utf-8") as stream:
            csv.writer(stream).writerows(rows)

    def test_valid_folder_passes(self):
        result = checker.check_case(self.case_dir)
        self.assertEqual("PASS", result["status"], result["issues"])
        self.assertTrue(result["hrr_nonzero"])
        self.assertAlmostEqual(40000.0, result["target_hrr_kW"])
        self.assertLess(result["hrr_closure_rel_error"], 0.01)

    def test_version_mismatch_only_requires_review(self):
        out = self.case_dir / f"{self.chid}.out"
        out.write_text("Fire Dynamics Simulator FDS-6.9.1\nRun completed\n", encoding="utf-8")
        result = checker.check_case(self.case_dir)
        self.assertEqual("REVIEW", result["status"])
        self.assertFalse(result["version_match"])

    def test_any_version_disables_comparison(self):
        out = self.case_dir / f"{self.chid}.out"
        out.write_text("Fire Dynamics Simulator FDS-6.9.1\nRun completed\n", encoding="utf-8")
        result = checker.check_case(self.case_dir, expected_version="any")
        self.assertEqual("PASS", result["status"], result["issues"])
        self.assertTrue(result["version_match"])

    def test_warning_requires_review(self):
        out = self.case_dir / f"{self.chid}.out"
        out.write_text(out.read_text(encoding="utf-8") + "WARNING: inspect me\n", encoding="utf-8")
        result = checker.check_case(self.case_dir)
        self.assertEqual("REVIEW", result["status"])

    def test_rejected_burner_fails(self):
        out = self.case_dir / f"{self.chid}.out"
        out.write_text(
            out.read_text(encoding="utf-8")
            + "WARNING: VENT BURNER overlaps WALL; VENT BURNER rejected.\n",
            encoding="utf-8",
        )
        result = checker.check_case(self.case_dir)
        self.assertEqual("FAIL", result["status"])
        self.assertTrue(result["burner_issue"])

    def test_q_conv_cannot_replace_q_radi(self):
        self._write_csv(self.case_dir / f"{self.chid}_hrr.csv", [
            ["s", "kW", "kW"],
            ["Time", "HRR", "Q_CONV"],
            [0, 0, 0], [15, 36000, 25000], [30, 40000, 28000],
            [45, 40000, 28000], [60, 40000, 28000],
        ])
        result = checker.check_case(self.case_dir)
        self.assertEqual("FAIL", result["status"])
        self.assertFalse(result["required_hrr_ok"])

    def test_check_all_writes_summary(self):
        out = Path(self.tmp.name) / "result_check.csv"
        results = checker.check_all(self.run_root, out)
        self.assertEqual(1, len(results))
        self.assertTrue(out.is_file())

    def test_external_contract_uses_mapped_thermocouples_and_full_field(self):
        devc = self.case_dir / f"{self.chid}_devc.csv"
        self._write_csv(devc, [
            ["s", "C", "C"],
            ["Time", "TC_A", "TC_B"],
            [0, 20, 20], [30, 80, 60], [60, 90, 70],
        ])
        for suffix in (".smv", "_0001.sf", "_0001.bf"):
            (self.case_dir / f"{self.chid}{suffix}").write_text(
                "field\n", encoding="utf-8"
            )
        result = checker.check_case(
            self.case_dir,
            required_temperature_channels=["TC_A", "TC_B"],
            require_full_field=True,
        )
        self.assertEqual("PASS", result["status"], result["issues"])
        self.assertTrue(result["required_devc_ok"])
        self.assertTrue(result["hrr_nonzero"])

        (self.case_dir / f"{self.chid}_0001.bf").unlink()
        missing_field = checker.check_case(
            self.case_dir,
            required_temperature_channels=["TC_A", "TC_B"],
            require_full_field=True,
        )
        self.assertEqual("FAIL", missing_field["status"])
        self.assertIn("全场文件", missing_field["issues"])


if __name__ == "__main__":
    unittest.main()
