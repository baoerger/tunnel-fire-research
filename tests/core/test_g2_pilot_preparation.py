import csv
import tempfile
import unittest
from pathlib import Path

from src.fds import generate_fds_case as generator
from src.fds import prepare_g2_pilot_runs as pilot


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class G2PilotPreparationContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (PROJECT_ROOT / "config" / "pilot_cases.csv").open(
                newline="", encoding="utf-8-sig") as stream:
            cls.rows = list(csv.DictReader(stream))

    def test_matrix_has_exact_preregistered_blocks_and_stages(self):
        cases = pilot.validate_matrix(self.rows)
        self.assertEqual(16, len(cases))
        self.assertEqual({0.2, 0.25, 0.5},
                         {case["dx"] for case in cases if case["case_kind"] == "fire"})
        self.assertEqual(5, sum(case["output_profile"] == "heavy" for case in cases))
        self.assertTrue(all(case["T_end"] == 300 for case in cases))

    def test_all_inputs_render_and_pass_static_audit(self):
        cases = pilot.validate_matrix(self.rows)
        with tempfile.TemporaryDirectory() as directory:
            for row, case in zip(self.rows, cases):
                with self.subTest(chid=case["chid"]):
                    path = generator.write_fds(row, directory)
                    pilot.static_audit(path, case)
                    text = Path(path).read_text(encoding="utf-8")
                    expected_meshes = 18 if case["dx"] == 0.5 else 21
                    self.assertEqual(expected_meshes, text.count("&MESH ID="))
                    self.assertEqual(2, text.count("SURF_ID='OPEN'"))
                    self.assertNotIn("SURF ID='INLET'", text)

    def test_matrix_rejects_partial_batch(self):
        with self.assertRaisesRegex(ValueError, "恰好 16"):
            pilot.validate_matrix(self.rows[:-1])


if __name__ == "__main__":
    unittest.main()
