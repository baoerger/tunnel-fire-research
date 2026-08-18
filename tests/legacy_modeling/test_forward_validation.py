import math
import csv
import tempfile
import unittest
from pathlib import Path

import numpy as np


from extras.legacy_modeling import empirical_baselines as empirical
from extras.legacy_modeling import forward_validation as validation
from src.analysis import extract_cross_section_integrals as extractor
from src.analysis import field_integrals as field

PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
        catalog = PROJECT_ROOT / "data" / "reference" / "empirical_formula_catalog.csv"
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
        self.assertTrue(all((PROJECT_ROOT / row["implementation"]).is_file() for row in rows))

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

    def test_nodal_cross_section_trapezoid_matches_constant_field(self):
        y = np.linspace(0, 10, 21)
        z = np.linspace(0, 5, 11)
        shape = (y.size, z.size)
        result = field.integrate_nodal_cross_section(
            y, z, np.full(shape, 1.2), 1000, np.full(shape, 303.15), 293.15,
            np.full(shape, 2.0),
        )
        self.assertAlmostEqual(result["area_m2"], 50.0)
        self.assertAlmostEqual(result["C_T_J_per_m"], 600000.0)
        self.assertAlmostEqual(result["J_T_W"], 1200000.0)
        self.assertAlmostEqual(result["U_e_field_mps"], 2.0)

    def test_nodal_cross_section_rejects_axis_and_field_mismatch(self):
        y = np.array([0.0, 1.0])
        z = np.array([0.0, 1.0])
        values = np.ones((2, 2))
        with self.assertRaisesRegex(ValueError, "严格递增"):
            field.integrate_nodal_cross_section(
                [0, 0], z, values, 1005, values + 293.15, 293.15, values
            )
        with self.assertRaisesRegex(ValueError, "形状"):
            field.integrate_nodal_cross_section(
                y, z, values, 1005, values + 293.15, 293.15, np.ones((2, 3))
            )

    def test_multimesh_paired_integration_counts_each_subdomain_once(self):
        class Mesh:
            def __init__(self, mesh_id):
                self.id = mesh_id

        class Subslice:
            def __init__(self, mesh_id, z, data, vector=False):
                self.mesh = Mesh(mesh_id)
                self._z = np.asarray(z)
                self.data = np.asarray(data, dtype=float)
                self.filename = f"{mesh_id}.sf"
                self.vector_filenames = {"u": f"{mesh_id}_u.sf"} if vector else {}
                self.vector_data = {"u": np.asarray(data, dtype=float)} if vector else {}

            def get_coordinates(self, ignore_cell_centered=False):
                return {"x": np.array([50.0]), "y": np.array([0.0, 1.0]), "z": self._z}

        class Slice:
            orientation = 1
            cell_centered = False
            times = np.array([0.0])

            def __init__(self, values, vector=False):
                self.subslices = [
                    Subslice("lower", [0.0, 0.5], values[0], vector),
                    Subslice("upper", [0.5, 1.0], values[1], vector),
                ]

        temperature = Slice(([[[30.0, 30.0], [30.0, 30.0]]],
                             [[[30.0, 30.0], [30.0, 30.0]]]))
        density = Slice(([[[1.0, 1.0], [1.0, 1.0]]],
                         [[[1.0, 1.0], [1.0, 1.0]]]))
        velocity = Slice(([[[2.0, 2.0], [2.0, 2.0]]],
                          [[[4.0, 4.0], [4.0, 4.0]]]), vector=True)
        times, results = extractor.integrate_paired_slices(
            temperature, density, velocity, 1000, 293.15
        )
        self.assertEqual([0.0], times.tolist())
        self.assertAlmostEqual(results[0]["area_m2"], 1.0)
        self.assertAlmostEqual(results[0]["C_T_J_per_m"], 10000.0)
        self.assertAlmostEqual(results[0]["J_T_W"], 30000.0)
        self.assertAlmostEqual(results[0]["U_e_field_mps"], 3.0)

    def test_axis_mismatch_and_manifest_hash_are_explicit(self):
        extractor._assert_same_axis("time", np.array([0.0, 5.0]), np.array([0.0, 5.0]))
        with self.assertRaisesRegex(ValueError, "time"):
            extractor._assert_same_axis("time", np.array([0.0, 5.0]), np.array([0.0, 6.0]))
        row = {field_name: "value" for field_name in extractor.OUTPUT_FIELDS}
        first = extractor._canonical_rows_sha256([row])
        second = extractor._canonical_rows_sha256([dict(row)])
        self.assertEqual(first, second)
        self.assertIn("cp_model", extractor.OUTPUT_FIELDS)
        self.assertIn("data_csv_sha256", extractor.MANIFEST_FIELDS)

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
