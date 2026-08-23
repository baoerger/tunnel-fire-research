import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from src.analysis import build_g5_sr_time_blocks as blocks


class G5SRSynchronizedTimeBlockTests(unittest.TestCase):
    columns = [
        "parent_case_id", "physical_case_id", "run_chid", "job_attempt_id",
        "window_id", "rnd_seed", "time_block_id", "block_start_sample_index",
        "block_start_s", "block_end_s", "block_len_samples", "x_coord_m",
        "xf_actual_m", "xi", "deltaT_block_mean_K", "point_role",
        "detection_limit_K",
    ]

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)
        self.run_chid = "d_test_r000001"
        self.attempt_id = f"{self.run_chid}_a01"
        self._write_inputs()

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def _write_csv(path, fieldnames, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    def _write_inputs(self):
        self._write_csv(
            self.repo / blocks.REGISTRY,
            ["chid", "job_attempt_id", "parent_case_id", "physical_case_id", "rnd_seed", "status"],
            [{
                "chid": self.run_chid,
                "job_attempt_id": self.attempt_id,
                "parent_case_id": "parent_1",
                "physical_case_id": "physical_1",
                "rnd_seed": "123",
                "status": "COMPLETED_ACCEPTED_G4",
            }],
        )
        self._write_csv(
            self.repo / blocks.CASE_METRICS,
            [
                "physical_case_id", "parent_case_id", "run_chid", "job_attempt_id",
                "window_id", "xf_actual_m", "window_start_s", "window_end_s",
                "bootstrap_block_len_samples", "quality_status",
            ],
            [{
                "physical_case_id": "physical_1",
                "parent_case_id": "parent_1",
                "run_chid": self.run_chid,
                "job_attempt_id": self.attempt_id,
                "window_id": "QS60_V1",
                "xf_actual_m": "1",
                "window_start_s": "0",
                "window_end_s": "60",
                "bootstrap_block_len_samples": "2",
                "quality_status": "PASS",
            }],
        )
        curve_fields = [
            "physical_case_id", "parent_case_id", "run_chid", "job_attempt_id",
            "window_id", "x_coord_m", "xf_actual_m", "xi", "point_role",
            "deltaT_mean_K", "detection_limit_K", "bootstrap_block_len_samples",
        ]
        common = {
            "physical_case_id": "physical_1",
            "parent_case_id": "parent_1",
            "run_chid": self.run_chid,
            "job_attempt_id": self.attempt_id,
            "window_id": "QS60_V1",
            "xf_actual_m": "1",
            "point_role": "ACTIVE",
            "detection_limit_K": "0.5",
            "bootstrap_block_len_samples": "2",
        }
        self._write_csv(
            self.repo / blocks.CURVE_POINTS,
            curve_fields,
            [
                {**common, "x_coord_m": "0", "xi": "1", "deltaT_mean_K": "3"},
                {**common, "x_coord_m": "1", "xi": "0", "deltaT_mean_K": "16"},
            ],
        )
        devc = (
            self.repo / "runs/development" / self.run_chid / "attempts"
            / self.attempt_id / f"{self.run_chid}_devc.csv"
        )
        devc.parent.mkdir(parents=True, exist_ok=True)
        devc.write_text(
            "s,C,C\n"
            "Time,T90_0000,T90_0100\n"
            "0,20,30\n"
            "20,22,34\n"
            "40,24,38\n"
            "60,26,42\n",
            encoding="utf-8",
        )
        generator = self.repo / blocks.GENERATOR_PATH
        generator.parent.mkdir(parents=True, exist_ok=True)
        generator.write_text("frozen generator bytes\n", encoding="utf-8")

    def _approval_loader(self, repo):
        self.assertEqual(Path(repo), self.repo.resolve())
        return {
            "record": {
                "status": "G5_SR_EXECUTION_SUPPLEMENT_APPROVED",
                "execution_state": "AUTHORIZED_FOR_FORMAL_SEARCH",
                "approval": {"approved_date": "2026-08-23"},
                "sr5_synchronized_time_block_input": {
                    "relative_path": blocks.OUTPUT_TABLE.as_posix(),
                    "manifest_path": blocks.OUTPUT_MANIFEST.as_posix(),
                    "construction": {
                        "method": "circular moving source blocks with stride one sample and the per-run frozen bootstrap_block_len_samples",
                        "same_time_indices_for_all_spatial_points": True,
                        "wraparound": True,
                        "contains_source_blocks_not_bootstrap_replicates": True,
                    },
                    "required_columns": list(self.columns),
                },
            },
            "path": "config/g5_sr_execution_supplement_v1.json",
            "sha256": "a" * 64,
            "parent_protocol_sha256": "b" * 64,
        }

    def _build(self):
        return blocks.build_synchronized_time_blocks(
            self.repo, approval_loader=self._approval_loader
        )

    def test_builds_all_circular_stride_one_blocks_with_synchronized_bounds(self):
        manifest = self._build()
        table_path = self.repo / blocks.OUTPUT_TABLE
        with table_path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))

        self.assertEqual(8, len(rows))
        self.assertEqual(4, manifest["table"]["time_block_count"])
        self.assertEqual(8, manifest["table"]["row_count"])
        by_block = {}
        for row in rows:
            by_block.setdefault(row["time_block_id"], []).append(row)
        self.assertEqual(["TB00000", "TB00001", "TB00002", "TB00003"], list(by_block))
        for grouped in by_block.values():
            self.assertEqual({"0", "1"}, {row["x_coord_m"] for row in grouped})
            self.assertEqual(1, len({row["block_start_s"] for row in grouped}))
            self.assertEqual(1, len({row["block_end_s"] for row in grouped}))

        x0_means = [
            float(row["deltaT_block_mean_K"])
            for row in rows if row["x_coord_m"] == "0"
        ]
        self.assertEqual([1.0, 3.0, 5.0, 3.0], x0_means)
        self.assertEqual("60", by_block["TB00003"][0]["block_start_s"])
        self.assertEqual("0", by_block["TB00003"][0]["block_end_s"])
        self.assertTrue(manifest["scope"]["lockbox_used"] is False)
        self.assertTrue(manifest["scope"]["new_fds_generated_or_run"] is False)
        self.assertLessEqual(
            float(
                manifest["validation"]["all_block_means_vs_frozen_curve_means"]
                ["maximum_absolute_difference_K"]
            ),
            1e-9,
        )

    def test_output_is_byte_deterministic_and_manifest_binds_source_hash(self):
        first = self._build()
        first_bytes = (self.repo / blocks.OUTPUT_TABLE).read_bytes()
        second = self._build()
        second_bytes = (self.repo / blocks.OUTPUT_TABLE).read_bytes()
        self.assertEqual(first_bytes, second_bytes)
        self.assertEqual(first, second)
        source = (
            self.repo / "runs/development" / self.run_chid / "attempts"
            / self.attempt_id / f"{self.run_chid}_devc.csv"
        )
        self.assertEqual(
            hashlib.sha256(source.read_bytes()).hexdigest(),
            first["source_runs"][0]["sha256"],
        )
        saved = json.loads((self.repo / blocks.OUTPUT_MANIFEST).read_text(encoding="utf-8"))
        self.assertEqual(first, saved)

    def test_rejects_curve_and_t90_coordinate_mismatch(self):
        curve_path = self.repo / blocks.CURVE_POINTS
        rows, fields = blocks._read_csv(curve_path)
        rows[1]["x_coord_m"] = "2"
        self._write_csv(curve_path, fields, rows)
        with self.assertRaisesRegex(blocks.G5SRTimeBlockError, "坐标不一致"):
            self._build()


if __name__ == "__main__":
    unittest.main()
