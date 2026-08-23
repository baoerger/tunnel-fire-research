import json
import tempfile
import unittest
from pathlib import Path

from src.analysis import analyze_g5_forward_models as legacy_analysis
from src.analysis import finalize_g5_freeze_a as legacy_finalizer
from src.analysis.g5_route_guard import LegacyG5RouteDisabledError


class G5LegacyRouteGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / "config").mkdir(parents=True)
        (self.repo / "config" / "protocol_v1.json").write_text(
            json.dumps({
                "status": "G5_SR_PROTOCOL_APPROVED_INPUT_RESTORE_REQUIRED",
                "g5_symbolic_regression": {"status": "SR_PROTOCOL_APPROVED"},
            }),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_legacy_candidate_analysis_is_blocked_before_input_reads(self):
        with self.assertRaisesRegex(
            LegacyG5RouteDisabledError, "A0.*人工候选基准.*A_active"
        ):
            legacy_analysis.analyze(self.repo, n_boot=1)

    def test_legacy_freeze_finalizer_is_blocked_before_writes(self):
        with self.assertRaisesRegex(
            LegacyG5RouteDisabledError, "A0.*人工候选基准.*A_active"
        ):
            legacy_finalizer.finalize(self.repo)
        self.assertFalse((self.repo / "freezes" / "A").exists())


if __name__ == "__main__":
    unittest.main()
