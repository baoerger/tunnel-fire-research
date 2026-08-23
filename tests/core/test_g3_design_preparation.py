import csv
import json
import unittest
from pathlib import Path

from src.fds import prepare_g3_design as g3
from src.fds import run_registry


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class G3DesignPreparationContracts(unittest.TestCase):
    def test_preregistered_counts_batches_and_extension_decision(self):
        development, extension, lockbox = g3.build_design_rows()
        freeze = json.loads(g3.FREEZE_PATH.read_text(encoding="utf-8-sig"))
        development_cases, lockbox_cases = g3.validate_design(
            development, extension, lockbox, freeze)
        self.assertEqual(27, len(development_cases))
        self.assertEqual(14, len(lockbox_cases))
        self.assertEqual(19, len({row["physical_case_id"] for row in development}))
        self.assertEqual({"D1": 5, "D2": 6, "D3": 16}, {
            batch: sum(row["batch"] == batch for row in development)
            for batch in ("D1", "D2", "D3")
        })
        self.assertTrue(all(row["required"] == "no" for row in extension))

    def test_tracked_inputs_match_manifests_and_formal_sensor_contract(self):
        for csv_path, input_dir, expected in (
            (g3.DEVELOPMENT_CSV, g3.DEVELOPMENT_INPUTS, 27),
            (g3.LOCKBOX_CSV, g3.LOCKBOX_INPUTS, 14),
        ):
            with csv_path.open(newline="", encoding="utf-8-sig") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(expected, len(rows))
            for index, row in enumerate(rows, start=2):
                case = g3.generator.normalize_case(row, row_number=index)
                path = input_dir / f"{case['chid']}.fds"
                self.assertTrue(path.is_file(), str(path))
                g3._formal_static_audit(path, case)

    def test_design_approval_and_lockbox_are_sealed(self):
        audit = json.loads(g3.AUDIT_JSON.read_text(encoding="utf-8-sig"))
        self.assertEqual("PASS", audit["decision"])
        self.assertEqual(57, audit["resources"]["accepted_trajectory_budget"]["planned"])
        self.assertEqual(58, audit["resources"]["initial_job_start_budget"]["planned"])
        with g3.APPROVAL_REPORT.open(newline="", encoding="utf-8-sig") as stream:
            approvals = list(csv.DictReader(stream))
        self.assertEqual(4, len(approvals))
        self.assertTrue(all(row["status"] == "PASS" for row in approvals))
        protocol = json.loads(g3.PROTOCOL_PATH.read_text(encoding="utf-8-sig"))
        self.assertEqual("SEALED_UNREAD_UNRUN", protocol["lockbox"]["status"])
        self.assertEqual("G3", protocol["lockbox"]["sealed_at_stage"])
        for operation in ("generate", "prepare", "run", "read", "train"):
            with self.subTest(operation=operation), self.assertRaises(PermissionError):
                run_registry.guard_subset("lockbox", operation, g3.PROTOCOL_PATH)

    def test_frozen_hashes_cover_every_active_input(self):
        freeze = json.loads(g3.G3_FREEZE.read_text(encoding="utf-8-sig"))
        self.assertEqual(27, len(freeze["development_input_sha256"]))
        self.assertEqual(14, len(freeze["lockbox_input_sha256"]))
        for subset, mapping, root in (
            ("development", freeze["development_input_sha256"], g3.DEVELOPMENT_INPUTS),
            ("lockbox", freeze["lockbox_input_sha256"], g3.LOCKBOX_INPUTS),
        ):
            for chid, expected in mapping.items():
                with self.subTest(subset=subset, chid=chid):
                    self.assertEqual(expected, run_registry.sha256_file(root / f"{chid}.fds"))


if __name__ == "__main__":
    unittest.main()
