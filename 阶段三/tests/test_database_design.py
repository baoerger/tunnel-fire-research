import csv
import math
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import case_registry
import batch_postprocess
import design_database_cases as design


class DatabaseDesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.summary = design.build()

    def test_exact_subsets_and_development_flow_quotas(self):
        self.assertEqual(
            {"development": 50, "independent_test": 12, "offset_validation": 6},
            dict(Counter(row["subset"] for row in self.rows)),
        )
        development = [row for row in self.rows if row["subset"] == "development"]
        self.assertEqual(design.FLOW_TARGETS, dict(Counter(row["flow_layer"] for row in development)))
        self.assertEqual(12, sum(row["role"] == "pilot" for row in development))
        self.assertEqual(3, sum(bool(row["reuse_chid"]) for row in development))
        self.assertEqual(12, sum(row["external_run_required"] == "no" for row in self.rows))
        self.assertEqual(56, sum(row["external_run_required"] == "yes" for row in self.rows))

    def test_physical_bounds_pairing_offsets_and_100m_v2_status(self):
        self.assertGreaterEqual(self.summary["matched_case_count"], 8)
        self.assertEqual(12, sum(row["design_status"] == design.REUSE_STATUS
                                 for row in self.rows))
        self.assertEqual(56, sum(row["design_status"] == design.DESIGN_STATUS
                                 for row in self.rows))
        self.assertTrue(all(row["protocol_version"] == design.PROTOCOL_VERSION for row in self.rows))
        self.assertTrue(all(float(row["L"]) == 100.0 and float(row["dx"]) == 0.25
                            for row in self.rows))
        self.assertTrue(all(row["parameter_mask_required"] == "yes" for row in self.rows))
        offsets = [row for row in self.rows if row["subset"] == "offset_validation"]
        self.assertEqual({32.5, 67.5}, {float(row["x_fire"]) for row in offsets})
        self.assertTrue(all(float(row["HRRPUA_kW_m2"]) <= 3000 for row in self.rows))

    def test_independent_test_roles_and_seal(self):
        tests = [row for row in self.rows if row["subset"] == "independent_test"]
        self.assertEqual(
            {"unseen_power": 3, "unseen_wind": 3, "near_critical_difficult": 2,
             "size_boundary": 2, "high_power_strong": 2},
            dict(Counter(row["test_role"] for row in tests)),
        )
        with self.assertRaisesRegex(PermissionError, "封存"):
            case_registry.select_cases(self.rows, "final_evaluation")
        released = case_registry.select_cases(
            self.rows, "final_evaluation", confirm_final_evaluation=True
        )
        self.assertEqual(12, len(released))

    def test_default_reader_exposes_only_development_when_selected(self):
        read_rows = case_registry.read_registry()
        selected = case_registry.select_cases(read_rows)
        self.assertEqual(50, len(selected))
        self.assertEqual({"development"}, {row["subset"] for row in selected})

    def test_derived_samples_cannot_cross_subset_or_physical_group(self):
        source = self.rows[0]
        valid = [{
            "source_chid": source["chid"], "dataset_group_id": source["chid"],
            "subset": source["subset"], "result_version": "run_v1", "is_adopted": "true",
        }]
        summary = case_registry.validate_case_linkage(valid, self.rows)
        self.assertEqual(1, summary["n_source_cases"])
        invalid = [dict(valid[0], subset="independent_test")]
        with self.assertRaisesRegex(ValueError, "不得跨子集"):
            case_registry.validate_case_linkage(invalid, self.rows)
        invalid = [dict(valid[0], dataset_group_id="another_case")]
        with self.assertRaisesRegex(ValueError, "dataset_group_id"):
            case_registry.validate_case_linkage(invalid, self.rows)

    def test_only_one_adopted_result_version_per_chid(self):
        source = self.rows[0]
        rows = [
            {"source_chid": source["chid"], "dataset_group_id": source["chid"],
             "subset": source["subset"], "result_version": version, "is_adopted": "true"}
            for version in ("run_v1", "run_v2")
        ]
        with self.assertRaisesRegex(ValueError, "多套 adopted"):
            case_registry.validate_case_linkage(rows, self.rows)

    def test_committed_output_is_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidate.csv"
            design.build(out_path=path)
            self.assertEqual(design.DEFAULT_OUT.read_bytes(), path.read_bytes())
            with path.open(newline="", encoding="utf-8-sig") as stream:
                self.assertEqual(68, len(list(csv.DictReader(stream))))

    def test_external_manifest_maps_every_chid_and_required_return(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.csv"
            manifest = design.write_external_run_manifest(self.rows, path)
            self.assertEqual(68, len(manifest))
            self.assertEqual({row["chid"] for row in self.rows},
                             {row["chid"] for row in manifest})
            for row in manifest:
                self.assertIn(row["chid"] + "_devc.csv", row["required_return_files"])
                self.assertIn(row["chid"] + "_hrr.csv", row["required_return_files"])
                self.assertEqual("END_OR_SUCCESSFUL_OUT_AND_BOTH_CSV_AT_T_END",
                                 row["completion_evidence_policy"])
                if row["external_run_required"] == "yes":
                    self.assertTrue(row["server_workdir"].endswith("/" + row["chid"]))
                else:
                    self.assertEqual("", row["server_workdir"])
                    self.assertTrue(row["existing_result_path"])

    def test_invalid_registry_or_linkage_fails_loudly(self):
        broken = [dict(row) for row in self.rows]
        broken[0]["dataset_group_id"] = "wrong"
        with self.assertRaisesRegex(ValueError, "最小数据分组"):
            design.validate_design(broken)
        with self.assertRaisesRegex(ValueError, "明确布尔值"):
            case_registry.validate_case_linkage([{
                "source_chid": self.rows[0]["chid"],
                "dataset_group_id": self.rows[0]["chid"],
                "subset": "development", "result_version": "v1",
                "is_adopted": "maybe",
            }], self.rows)

    @staticmethod
    def _write_csv(path, rows):
        with Path(path).open("w", newline="", encoding="utf-8") as stream:
            csv.writer(stream).writerows(rows)

    def _write_synthetic_pass_case(self, run_root):
        chid = self.rows[0]["chid"]
        case_dir = Path(run_root) / chid
        case_dir.mkdir(parents=True)
        (case_dir / f"{chid}.fds").write_text("\n".join([
            f"&HEAD CHID='{chid}', TITLE='synthetic software test' /",
            "&MESH IJK=4 4 4, XB=0 1 0 1 0 1 /",
            "&TIME T_END=80.0 /",
            "&SURF ID='BURNER', HRRPUA=1250.0, TAU_Q=10.0 /",
            "&VENT XB=0 2 0 2 0 0, SURF_ID='BURNER' /",
            "&TAIL /",
        ]) + "\n", encoding="utf-8")
        (case_dir / f"{chid}.out").write_text(
            "Fire Dynamics Simulator FDS-6.9.1-0-release\nRun completed\n",
            encoding="utf-8",
        )
        (case_dir / f"{chid}.end").write_text("completed\n", encoding="utf-8")
        devc = [
            ["s", "C", "C", "C", "m/s", "m/s", "m/s", "kW", "kW/m2"],
            ["Time", "T_4500", "T_5000", "T_5500", "U_4500", "U_5000",
             "U_5500", "HRR_tot", "Qw_5000"],
        ]
        hrr = [["s", "kW", "kW"], ["Time", "HRR", "Q_RADI"]]
        for time in range(81):
            factor = min(1.0, time / 20.0)
            fluctuation = 0.02 * math.sin(time / 2.0)
            hrr_value = 5000.0 * factor
            devc.append([
                time, 20 + 20 * factor + fluctuation,
                20 + 40 * factor + fluctuation,
                20 + 25 * factor + fluctuation,
                0.0, 0.0, 0.0, hrr_value, 2.0 * factor,
            ])
            hrr.append([time, hrr_value, -0.30 * hrr_value])
        self._write_csv(case_dir / f"{chid}_devc.csv", devc)
        self._write_csv(case_dir / f"{chid}_hrr.csv", hrr)
        return chid

    def test_batch_pipeline_waits_for_missing_results_and_processes_pass_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_root = root / "runs"
            chid = self._write_synthetic_pass_case(run_root)
            isolated_registry = [dict(row) for row in self.rows]
            for row in isolated_registry:
                if row["role"] == "pilot":
                    row["existing_result_path"] = str(root / "isolated_pilots" / row["chid"])
            first = next(row for row in isolated_registry if row["chid"] == chid)
            first["existing_result_path"] = str(run_root / chid)
            registry_path = root / "registry.csv"
            with registry_path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=design.CASE_FIELDS)
                writer.writeheader(); writer.writerows(isolated_registry)
            result = batch_postprocess.run_pipeline(
                registry_path=registry_path, run_root=run_root,
                outdir=root / "analysis", n_boot=20,
                window_s=20.0, avg_duration=20.0,
            )
            completion = {row["chid"]: row for row in result["completion"]}
            self.assertEqual("READY_FOR_EXPLICIT_ADOPTION", completion[chid]["overall_status"])
            self.assertEqual(
                49, sum(row["overall_status"] == "WAITING_RETURN" for row in result["completion"])
            )
            self.assertTrue((root / "analysis" / "database_completion.csv").is_file())
            self.assertTrue((root / "analysis" / "time_average" / "time_average_database.csv").is_file())
            self.assertTrue(result["noise_components"])
            self.assertTrue(all(
                row["status"] == "WAITING_SEED_REPEATS" for row in result["noise_summary"]
            ))

    def test_noise_summary_requires_repeat_seeds(self):
        one = [{
            "source_chid": "seed_a", "parent_chid": "base", "sensor": "T_5000",
            "mean_temperature_C": "80", "temporal_sd_C": "1.5",
        }]
        waiting = batch_postprocess.summarize_noise_components(one)
        self.assertEqual("WAITING_SEED_REPEATS", waiting[0]["status"])
        two = one + [{
            "source_chid": "seed_b", "parent_chid": "base", "sensor": "T_5000",
            "mean_temperature_C": "84", "temporal_sd_C": "2.0",
        }]
        ready = batch_postprocess.summarize_noise_components(two)
        self.assertEqual("READY_FOR_THRESHOLD_REVIEW", ready[0]["status"])
        self.assertAlmostEqual(2.828427, float(ready[0]["repeat_seed_mean_sd_C"]), places=5)

    def test_batch_defaults_match_frozen_stage1_steady_protocol(self):
        self.assertEqual(0.15, batch_postprocess.quasi_steady_detect.DEFAULT_THR_HRR)
        self.assertEqual(0.15, batch_postprocess.quasi_steady_detect.DEFAULT_THR_TMAX_SLOPE)
        self.assertEqual(0.15, batch_postprocess.quasi_steady_detect.DEFAULT_THR_REP_SLOPE)


if __name__ == "__main__":
    unittest.main()
