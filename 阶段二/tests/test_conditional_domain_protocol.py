import csv
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

import conditional_domain_protocol as protocol


class ConditionalDomainProtocolTests(unittest.TestCase):
    def test_real_v1_failure_is_preserved_and_v2_is_confirmation_pending(self):
        with tempfile.TemporaryDirectory() as temporary:
            rows, summary = protocol.build(outdir=temporary)
            self.assertEqual(12, len(rows))
            self.assertEqual("FAIL", summary["legacy_v1_formal_gate_status"])
            self.assertEqual(protocol.DEVELOPMENT_STATUS, summary["development_gate_status"])
            self.assertEqual(12, summary["n_protocol_compatible"])
            self.assertTrue((Path(temporary) / "protocol_v2_case_targets.csv").is_file())

    def test_downstream_shortage_becomes_domain_censor_without_kd(self):
        rows, _ = protocol.build()
        target = next(row for row in rows if row["chid"] == "pilot_12_q100_r160_d80")
        self.assertEqual("insufficient_downstream", target["legacy_observation_category"])
        self.assertEqual("downstream_domain_censored", target["v2_observation_category"])
        self.assertEqual("false", target["k_d_target_permitted"])
        self.assertEqual("", target["k_d_1_m"])
        self.assertEqual("true", target["profile_target_available"])

    def test_unstable_parameters_are_blank_not_forced(self):
        rows, _ = protocol.build()
        unstable = next(row for row in rows if row["chid"] == "pilot_01_q05_u000_d20")
        self.assertEqual("", unstable["k_u_1_m"])
        self.assertEqual("", unstable["k_d_1_m"])
        self.assertIn("拟合区间不稳定", unstable["note"])

    def test_non_fail_v1_summary_cannot_be_silently_rewritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            summary_path = Path(temporary) / "summary.csv"
            with protocol.DEFAULT_V1_SUMMARY.open(newline="", encoding="utf-8-sig") as stream:
                row = next(csv.DictReader(stream))
            row["formal_gate_status"] = "PASS"
            with summary_path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(row))
                writer.writeheader(); writer.writerow(row)
            with self.assertRaisesRegex(ValueError, "保留协议 V1"):
                protocol.build(v1_summary=summary_path, outdir=temporary)


if __name__ == "__main__":
    unittest.main()
