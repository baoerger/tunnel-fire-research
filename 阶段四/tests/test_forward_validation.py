import math
import csv
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
import field_integrals as field
import forward_validation as validation
import empirical_baselines as empirical


class ForwardValidationTests(unittest.TestCase):
    def setUp(self):
        self.case = {
            "case_id": "dev_a", "x": [-10, -5, 0, 5, 10],
            "dT": [5, 20, 50, 20, 5], "x_fire": 0, "U": 0,
            "subset": "development",
        }

    def test_exact_profile_metrics_are_zero(self):
        metrics = validation.profile_metrics(self.case, self.case["dT"])
        for name in ("nrmse", "peak_relative_error", "peak_position_error_m",
                     "upstream_nrmse", "downstream_nrmse", "mean_bias_C"):
            self.assertAlmostEqual(metrics[name], 0.0)

    def test_censored_exceedance_is_explicit(self):
        case = {**self.case, "censor_mask": [True, True, False, False, False],
                "detection_threshold_C": 2.0}
        prediction = [1.0, 3.0, 50, 20, 5]
        self.assertEqual(validation.profile_metrics(case, prediction)["strong_wind_censored_exceedance_C"], 1.0)

    def test_kurioka_source_units_and_near_continuous_branch(self):
        Q_MW, H = 40.0, 5.0
        rho0, cp_J, T0, g = 1.2, 1005.0, 293.15, 9.81
        q_star = Q_MW * 1e6 / (rho0 * cp_J * T0 * math.sqrt(g) * H ** 2.5)

        def velocity_for_driving(driving):
            froude = q_star ** 2 / driving ** 3
            return math.sqrt(froude * g * H)

        below = empirical.kurioka_2003_max_temperature_rise(
            Q_MW, velocity_for_driving(1.35 * (1 - 1e-9)), H
        )
        above = empirical.kurioka_2003_max_temperature_rise(
            Q_MW, velocity_for_driving(1.35 * (1 + 1e-9)), H
        )
        self.assertEqual("driving_parameter_lt_1.35", below["branch"])
        self.assertEqual("driving_parameter_ge_1.35", above["branch"])
        self.assertAlmostEqual(q_star, below["Q_star"])
        relative_jump = abs(below["delta_T_max_K"] - above["delta_T_max_K"]) / above["delta_T_max_K"]
        self.assertLess(relative_jump, 0.002)

    def test_li_2011_low_and_high_velocity_branches(self):
        low = empirical.li_2011_max_temperature_rise(40, 0, 5, 5)
        expected_low = 17.5 * 40000 ** (2 / 3) / 5 ** (5 / 3)
        self.assertEqual("u_prime_le_0.19", low["branch"])
        self.assertAlmostEqual(expected_low, low["delta_T_max_K"])
        self.assertAlmostEqual(2.5, low["fire_radius_m"])
        high_velocity = 0.20 * low["w_star_m_s"]
        high = empirical.li_2011_max_temperature_rise(40, high_velocity, 5, 5)
        expected_high = 40000 / (high_velocity * 2.5 ** (1 / 3) * 5 ** (5 / 3))
        self.assertEqual("u_prime_gt_0.19", high["branch"])
        self.assertAlmostEqual(expected_high, high["delta_T_max_K"])

    def test_empirical_formulas_reject_wrong_domains(self):
        with self.assertRaisesRegex(ValueError, "U_m_s"):
            empirical.kurioka_2003_max_temperature_rise(40, 0, 5)
        with self.assertRaisesRegex(ValueError, "chi_r"):
            empirical.li_2011_max_temperature_rise(40, 2, 5, 5, chi_r=1.0)

    def test_machine_readable_formula_catalog_is_complete_and_traceable(self):
        project_root = HERE.parent.parent
        catalog = project_root / "阶段一" / "01_文献调研" / "empirical_formula_catalog.csv"
        with catalog.open(newline="", encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
        keys = {(row["formula_id"], row["branch"]) for row in rows}
        self.assertEqual(8, len(rows))
        self.assertIn(
            ("kurioka_2003_max_ceiling_gas_temperature", "low_driving"), keys
        )
        self.assertIn(
            ("li_2011_max_ceiling_gas_temperature", "high_velocity"), keys
        )
        self.assertIn(("wu_bakar_2000_critical_velocity", "high_Qprime"), keys)
        self.assertTrue(all(row["original_doi"] and row["evidence_level"] for row in rows))
        self.assertTrue(all((project_root / row["implementation"]).is_file() for row in rows))

    def test_peak_only_empirical_evaluation_and_temperature_discipline(self):
        rows = validation.evaluate_empirical_peak_baselines(
            [self.case], {"kurioka_2003": {"dev_a": 45.0}}
        )
        self.assertEqual(1, len(rows))
        self.assertEqual("maximum_ceiling_gas_temperature_only", rows[0]["prediction_scope"])
        self.assertAlmostEqual(0.1, rows[0]["peak_relative_error"])
        thermocouple = {**self.case, "temperature_type": "thermocouple_response_temperature"}
        with self.assertRaisesRegex(ValueError, "ceiling_gas_temperature"):
            validation.evaluate_empirical_peak_baselines(
                [thermocouple], {"li_2011": {"dev_a": 45.0}}
            )

    def test_five_models_required_and_independent_test_sealed(self):
        predictions = {name: {"dev_a": self.case["dT"]} for name in validation.MODEL_FAMILIES}
        rows = validation.evaluate_models([self.case], predictions)
        self.assertEqual(len(rows), 5)
        bad = dict(predictions); bad.pop(validation.MODEL_FAMILIES[-1])
        with self.assertRaisesRegex(ValueError, "五类"):
            validation.evaluate_models([self.case], bad)
        sealed = {**self.case, "subset": "independent_test"}
        with self.assertRaises(PermissionError):
            validation.evaluate_models([sealed], predictions)

    def test_zero_wind_symmetry_and_summary_holdouts(self):
        predictions = {name: {"dev_a": self.case["dT"]} for name in validation.MODEL_FAMILIES}
        rows = validation.evaluate_models([{**self.case, "holdout_power": True}], predictions)
        self.assertTrue(all(row["zero_wind_symmetry_error"] == 0 for row in rows))
        summary = validation.summarize(rows)
        self.assertTrue(all(row["holdout_power_nrmse"] == 0 for row in summary))

    def test_constant_cross_section_matches_analytic_units(self):
        cells = [{"rho": 1.2, "cp": 1000, "T": 303.15, "T0": 293.15,
                  "u_x": 2.0, "dA": 2.5} for _ in range(4)]
        result = field.integrate_cross_section(cells, field.EXPECTED_UNITS)
        self.assertAlmostEqual(result["C_T_J_per_m"], 120000.0)
        self.assertAlmostEqual(result["J_T_W"], 240000.0)
        self.assertAlmostEqual(result["U_e_field_mps"], 2.0, places=10)
        self.assertEqual(result["area_m2"], 10.0)

    def test_wrong_units_and_bad_cells_fail(self):
        cells = [{"rho": 1.2, "cp": 1000, "T": 303, "T0": 293,
                  "u_x": 2, "dA": 1}]
        with self.assertRaisesRegex(ValueError, "单位"):
            field.integrate_cross_section(cells, {**field.EXPECTED_UNITS, "cp": "kJ/(kg K)"})
        cells[0]["rho"] = -1
        with self.assertRaisesRegex(ValueError, "必须为正"):
            field.integrate_cross_section(cells, field.EXPECTED_UNITS)

    def test_station_order_and_duplicate_rejection(self):
        cell = {"rho": 1.2, "cp": 1000, "T": 303, "T0": 293, "u_x": 2, "dA": 1}
        rows = field.integrate_stations(
            [{"x_m": 2, "cells": [cell]}, {"x_m": 1, "cells": [cell]}], field.EXPECTED_UNITS
        )
        self.assertEqual([row["x_m"] for row in rows], [1.0, 2.0])
        with self.assertRaisesRegex(ValueError, "重复"):
            field.integrate_stations(
                [{"x_m": 1, "cells": [cell]}, {"x_m": 1, "cells": [cell]}], field.EXPECTED_UNITS
            )

    def test_strict_si_cell_csv_reader(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cells.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle); writer.writerow(field.CELL_CSV_FIELDS)
                writer.writerow([0, 1.2, 1000, 303.15, 293.15, 2, 1])
                writer.writerow([0, 1.2, 1000, 303.15, 293.15, 2, 1])
            stations = field.read_cell_csv(path)
            self.assertEqual(len(stations), 1)
            self.assertEqual(len(stations[0]["cells"]), 2)
            path.write_text("x,bad\n0,1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "字段"):
                field.read_cell_csv(path)

    def test_synthetic_exports_are_not_scientific_conclusions(self):
        with tempfile.TemporaryDirectory() as temporary:
            rows, summaries = validation.run_synthetic_check(temporary)
            field_rows = field.run_synthetic_check(Path(temporary) / "field.csv")
            self.assertEqual(len(rows), 15)
            self.assertEqual(len(summaries), 5)
            self.assertTrue(all(row["decision_status"] == "NO_FORMAL_MODEL_SELECTION" for row in rows))
            self.assertTrue(all(row["decision_status"] == "NO_FIELD_PHYSICS_CONCLUSION" for row in field_rows))


if __name__ == "__main__":
    unittest.main()
