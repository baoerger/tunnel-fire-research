import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import external_observations as external


class ExternalObservationTests(unittest.TestCase):
    def test_three_cases_and_known_parameters_are_complete(self):
        rows = external.build_external_inputs()
        self.assertEqual({row["chid"] for row in rows}, {"Arup_Tunnel", "CSTB_Tunnel_Test_2", "IFAB-07"})
        self.assertEqual({row["chid"]: len(row["sensors"]) for row in rows},
                         {"Arup_Tunnel": 9, "CSTB_Tunnel_Test_2": 6, "IFAB-07": 11})
        for row in rows:
            self.assertGreater(row["Q_MW"], 0)
            self.assertGreater(row["Df"], 0)
            self.assertGreater(row["L"], 0)
            self.assertGreater(row["H"], 0)
            self.assertGreater(row["T0_K"], 0)
            self.assertEqual("OOD_EXPLORATORY", row["applicability_status"])
            self.assertTrue(row["ood_reasons"])
            self.assertEqual(external.OOD_SCIENTIFIC_USE, row["scientific_use_policy"])

    def test_thermocouples_are_not_relabelled_as_gas_temperature(self):
        for row in external.build_external_inputs():
            self.assertEqual(row["observation_temperature_type"], "thermocouple")
            self.assertEqual(row["model_target_temperature_type"], "ceiling_gas_temperature")
            self.assertIn("REQUIRES", row["temperature_compatibility"])
            self.assertTrue(all(sensor["temperature_type"] == "thermocouple" for sensor in row["sensors"]))

    def test_sources_and_uncertainty_are_traceable(self):
        for row in external.build_external_inputs():
            self.assertTrue(row["reference"])
            for sensor in row["sensors"]:
                self.assertTrue(sensor["raw_files"])
                self.assertTrue(sensor["uncertainty_basis"])
                self.assertGreater(sensor["sigma_C"], 0)

    def test_evaluation_template_is_blank_and_waiting(self):
        rows = external.evaluation_template(external.build_external_inputs())
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["Q_hat_MW"] == "" and row["x_f_hat_m"] == "" for row in rows))
        self.assertTrue(all(row["status"] == external.WAITING_STATUS for row in rows))
        self.assertTrue(all(row["applicability_status"] == "OOD_EXPLORATORY" for row in rows))
        self.assertTrue(all(row["scientific_use_policy"] == external.OOD_SCIENTIFIC_USE
                            for row in rows))

    def test_written_outputs_preserve_types_and_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            external.write_outputs(temporary)
            lines = (Path(temporary) / "external_inversion_inputs.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(len(lines), 3)
            self.assertTrue(all(json.loads(line)["observation_temperature_type"] == "thermocouple"
                                for line in lines))
            self.assertTrue(all(json.loads(line)["applicability_status"] == "OOD_EXPLORATORY"
                                for line in lines))
            with (Path(temporary) / "external_end_to_end_evaluation.csv").open(
                encoding="utf-8", newline=""
            ) as handle:
                rows = list(csv.DictReader(handle))
            self.assertTrue(all(row["status"] == external.WAITING_STATUS for row in rows))


if __name__ == "__main__":
    unittest.main()
