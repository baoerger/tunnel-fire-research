import csv
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import analyze_boundary_effect as boundary
import analyze_grid_convergence as grid
import check_convection_ratio as convection
import fds_io
import quasi_steady_detect as steady
import time_average_bootstrap as averaging


class StageOneAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_devc(self, chid, fire_x, xs):
        case_dir = self.root / chid
        case_dir.mkdir(parents=True, exist_ok=True)
        headers = ["Time"]
        units = ["s"]
        values = [60.0]
        for x in xs:
            suffix = f"{int(round(x * 100)):04d}"
            headers.append(f"T_{suffix}")
            units.append("C")
            values.append(20.0 + 100.0 * math.exp(-0.10 * abs(x - fire_x)))
        for x in xs:
            suffix = f"{int(round(x * 100)):04d}"
            headers.append(f"U_{suffix}")
            units.append("m/s")
            rel = x - fire_x
            values.append(-1.0 if -10.0 <= rel < 0 else 1.0)
        headers.append(f"Qw_{int(round(fire_x * 100)):04d}")
        units.append("kW/m2")
        values.append(3.0)
        earlier = values.copy()
        earlier[0] = 30.0
        with (case_dir / f"{chid}_devc.csv").open("w", newline="", encoding="utf-8") as stream:
            csv.writer(stream).writerows([units, headers, earlier, values])

    def _write_timeseries_devc(self, chid, times, include_velocity=True):
        case_dir = self.root / chid
        case_dir.mkdir(parents=True, exist_ok=True)
        xs = [15, 25, 35, 45, 50, 55, 65, 75, 85]
        headers = ["Time", "HRR_tot"]
        units = ["s", "kW"]
        for x in xs:
            headers.append(f"T_{x * 100:04d}")
            units.append("C")
        if include_velocity:
            for x in xs:
                headers.append(f"U_{x * 100:04d}")
                units.append("m/s")
        data = [units, headers]
        for time in times:
            row = [time, 40000.0]
            row.extend(20.0 + 100.0 * math.exp(-0.1 * abs(x - 50)) for x in xs)
            if include_velocity:
                row.extend(-1.0 if 40 <= x < 50 else 1.0 for x in xs)
            data.append(row)
        with (case_dir / f"{chid}_devc.csv").open("w", newline="", encoding="utf-8") as stream:
            csv.writer(stream).writerows(data)

    def _write_hrr(self, chid, q_radi=-12000.0):
        case_dir = self.root / chid
        case_dir.mkdir(parents=True, exist_ok=True)
        with (case_dir / f"{chid}_hrr.csv").open("w", newline="", encoding="utf-8") as stream:
            csv.writer(stream).writerows([
                ["s", "kW", "kW"], ["Time", "HRR", "Q_RADI"],
                [0, 0, 0], [30, 40000, q_radi], [60, 40000, q_radi],
            ])

    def test_empty_requested_time_window_fails_instead_of_using_startup(self):
        xs, values = fds_io.extract_T_profile(
            [0.0, 1.0], {"T_5000": [20.0, 21.0]}, 50.0, 20.0,
            t_window=(10.0, 20.0))
        self.assertIsNone(xs)
        self.assertIsNone(values)

    def test_backflow_detects_far_positive_near_fire_negative(self):
        times = [0.0]
        series = {
            "U_2000": [2.0], "U_3000": [1.0], "U_4000": [-1.0],
            "U_4500": [-2.0], "U_5500": [2.0],
        }
        # 零交点在 x=35 m，峰值 x=50 m，因此回流长度 15 m。
        self.assertAlmostEqual(15.0, grid._backflow_length(times, series, 50.0))

    def test_isolated_far_negative_is_not_connected_backflow(self):
        series = {"U_2000": [-1.0], "U_3000": [1.0], "U_4500": [1.0]}
        self.assertEqual(0.0, grid._backflow_length([0.0], series, 50.0))

    def test_zero_relative_change_is_a_real_zero(self):
        self.assertEqual(0.0, grid._rel_change(12.0, 12.0))
        self.assertEqual(0.0, grid._rel_change(0.0, 0.0))

    def test_grid_cli_marks_identical_valid_curves_converged(self):
        xs = list(range(15, 86, 5))
        csv_path = self.root / "cases.csv"
        rows = [
            ["coarse", "g", 40, 2.5, 5, 0.5, 100, 50, 300],
            ["medium", "g", 40, 2.5, 5, 0.25, 100, 50, 300],
            ["fine", "g", 40, 2.5, 5, 0.125, 100, 50, 300],
        ]
        with csv_path.open("w", newline="", encoding="utf-8") as stream:
            csv.writer(stream).writerows([
                ["chid", "case_group", "Q", "U", "Df", "dx", "L", "x_fire", "T_end"],
                *rows,
            ])
        for row in rows:
            self._write_devc(row[0], 50.0, xs)
        outdir = self.root / "out"
        argv = ["grid", "--csv", str(csv_path), "--rundir", str(self.root),
                "--outdir", str(outdir), "--threshold_pct", "5",
                "--t0", "30", "--t1", "60"]
        with mock.patch.object(sys, "argv", argv):
            grid.main()
        with (outdir / "grid_convergence_summary.csv").open(
                newline="", encoding="utf-8-sig") as stream:
            result = next(csv.DictReader(stream))
        self.assertEqual("PASS", result["status"])
        self.assertEqual("0.00", result["dTp_rel(中→细)%"])

    def test_boundary_pair_uses_relative_fire_coordinates(self):
        base_xs = list(range(15, 86, 5))
        ext_xs = list(range(40, 111, 5))
        self._write_devc("base", 50.0, base_xs)
        self._write_devc("extended", 75.0, ext_xs)
        base = {"chid": "base", "Q": "40", "U": "2.5", "Df": "5",
                "L": "100", "x_fire": "50"}
        ext = {"chid": "extended", "Q": "40", "U": "2.5", "Df": "5",
               "L": "150", "x_fire": "75"}
        result, points = boundary.analyze_pair(
            str(self.root), ext, base, 1.5, None, threshold=0.05)
        self.assertEqual("PASS", result["status"], result["reason"])
        self.assertAlmostEqual(0.0, result["delta_p_shift_H"], places=8)
        self.assertGreaterEqual(len(points), 6)

    def test_quasi_steady_checks_all_available_criteria(self):
        self._write_timeseries_devc("steady", list(range(0, 61)))
        start, end, info = steady.detect(
            "steady", str(self.root), window_s=10.0, min_steady=20.0)
        self.assertEqual("PASS", info["status"], info["reason"])
        self.assertIsNotNone(start)
        self.assertEqual(60, end)
        self.assertEqual("UNCHECKED_NO_FIELD_DATA", info["enthalpy_criterion"])
        self.assertIn("representative_temperature", info["criteria"][-1])
        self.assertIn("backflow", info["criteria"][-1])

    def test_quasi_steady_rejects_irregular_time_and_missing_velocity(self):
        irregular = list(range(0, 20)) + list(range(21, 62))
        self._write_timeseries_devc("irregular", irregular)
        _, _, info = steady.detect(
            "irregular", str(self.root), window_s=10.0, min_steady=20.0)
        self.assertEqual("FAIL", info["status"])
        self.assertIn("时间间隔不规则", info["reason"])

        self._write_timeseries_devc("missing_u", list(range(0, 61)), include_velocity=False)
        _, _, info = steady.detect(
            "missing_u", str(self.root), window_s=10.0, min_steady=20.0)
        self.assertEqual("FAIL", info["status"])
        self.assertIn("U_*", info["reason"])

    def test_bootstrap_validates_window_and_is_deterministic(self):
        times = [float(i) for i in range(21)]
        values = [20.0 + 0.1 * math.sin(i) for i in range(21)]
        first = averaging.block_bootstrap_stats(
            values, 0.0, 20.0, times, n_boot=100, seed=17)
        second = averaging.block_bootstrap_stats(
            values, 0.0, 20.0, times, n_boot=100, seed=17)
        self.assertEqual(first, second)
        self.assertGreaterEqual(first["block_len_samples"], 1)
        with self.assertRaisesRegex(ValueError, "短于最小"):
            averaging.block_bootstrap_stats(
                values, 0.0, 5.0, times, n_boot=100, seed=17)
        irregular = times.copy()
        irregular[10] += 0.5
        with self.assertRaisesRegex(ValueError, "时间间隔不规则"):
            averaging.block_bootstrap_stats(
                values, 0.0, 20.0, irregular, n_boot=100, seed=17)

    def test_convection_ratio_requires_valid_window_and_energy_sign(self):
        self._write_hrr("energy")
        metadata = {"Q": 40.0, "U": 2.5, "Df": 5.0}
        result = convection.analyze_case(
            str(self.root), "energy", metadata, window=(30.0, 60.0))
        self.assertEqual("PASS", result["status"], result["reason"])
        self.assertAlmostEqual(0.3, result["chi_r_realized"])
        self.assertAlmostEqual(0.0, result["hrr_closure_rel_error"])

        empty = convection.analyze_case(
            str(self.root), "energy", metadata, window=(100.0, 120.0))
        self.assertEqual("FAIL", empty["status"])
        self.assertIn("不足 2", empty["reason"])

        self._write_hrr("bad_sign", q_radi=1000.0)
        bad = convection.analyze_case(
            str(self.root), "bad_sign", metadata, window=(30.0, 60.0))
        self.assertEqual("FAIL", bad["status"])
        self.assertIn("符号异常", bad["reason"])

    def test_convection_summary_needs_two_pass_cases(self):
        summary = convection.summarize([
            {"status": "PASS", "chi_r_realized": 0.3,
             "target_Q_MW": 40.0, "U_m_per_s": 2.5, "Df_m": 5.0}
        ])
        self.assertEqual("WAITING", summary["status"])
        self.assertEqual("undetermined", summary["candidate_mode"])


if __name__ == "__main__":
    unittest.main()
