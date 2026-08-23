import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.analysis import capture_g5_sr_environment as environment


class G5SREnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / "config").mkdir(parents=True)
        for index, relative in enumerate(environment.FORMAL_EXECUTION_ARTIFACTS):
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"artifact-{index}\n".encode())
        self.machine = {
            "schema_version": "1.0",
            "protocol_id": "g5_symbolic_regression_v1",
            "status": "SR_PROTOCOL_APPROVED",
            "approval": {"decision": "SR_PROTOCOL_APPROVED"},
            "data_contract": {
                "forbidden_subsets": ["lockbox"],
                "required_sources": [
                    {
                        "relative_path": "derived/development/case_metrics.csv"
                    },
                    {
                        "relative_path": "derived/development/curve_points.csv"
                    },
                ],
            },
        }
        self.machine_path = self.repo / environment.MACHINE_PROTOCOL
        self.machine_path.write_text(json.dumps(self.machine), encoding="utf-8")
        self.machine_hash = hashlib.sha256(self.machine_path.read_bytes()).hexdigest()
        self.study = {
            "g5_symbolic_regression": {
                "status": "SR_PROTOCOL_APPROVED",
                "machine_protocol": environment.MACHINE_PROTOCOL.as_posix(),
                "machine_protocol_sha256": self.machine_hash,
                "execution_supplement": {
                    "status": "DRAFT_REQUIRES_USER_APPROVAL",
                    "draft_path": (
                        environment.protocol_guard.EXECUTION_SUPPLEMENT_DRAFT.as_posix()
                    ),
                    "approved_path": (
                        environment.protocol_guard.EXECUTION_SUPPLEMENT.as_posix()
                    ),
                    "approved_sha256": None,
                },
            },
            "lockbox": {
                "status": "SEALED_UNREAD_UNRUN",
                "sealed_at_stage": "G3",
                "generation_allowed": False,
                "preparation_allowed": False,
                "run_allowed": False,
                "read_allowed": False,
            },
        }
        (self.repo / environment.STUDY_PROTOCOL).write_text(
            json.dumps(self.study), encoding="utf-8"
        )
        draft_path = self.repo / environment.protocol_guard.EXECUTION_SUPPLEMENT_DRAFT
        draft_path.write_text(
            json.dumps(
                {
                    "supplement_id": "g5_sr_execution_supplement_v1",
                    "status": "DRAFT_REQUIRES_USER_APPROVAL",
                    "execution_state": "NOT_AUTHORIZED_FOR_FORMAL_SEARCH",
                    "parent_protocol": {
                        "relative_path": environment.MACHINE_PROTOCOL.as_posix(),
                        "protocol_id": self.machine["protocol_id"],
                        "required_status": "SR_PROTOCOL_APPROVED",
                        "sha256": self.machine_hash,
                    },
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _kwargs(self):
        return {
            "repo": self.repo,
            "julia_version": "1.12.7",
            "symbolic_regression_version": "1.11.3",
            "smoke_status": "SMOKE_PASS",
            "smoke_equation": "x*(x*1.0 - 8.281593e-9)",
            "smoke_max_abs_error": 1.6563186378e-08,
            "smoke_fit_seconds": 20.01,
            "cold_start_seconds": 781.08,
            "generated_at": "2026-08-23T07:00:00+00:00",
            "package_versions": {
                "mpmath": "1.3.0",
                "numpy": "2.4.6",
                "scipy": "1.17.1",
                "sympy": "1.14.0",
                "pysr": "1.5.10",
            },
            "uv_info": {
                "available": True,
                "version": "0.11.25",
                "raw": "uv 0.11.25",
            },
            "python_info": {
                "version": "3.11.15",
                "implementation": "CPython",
                "system": "Windows",
            },
        }

    def _approve_execution_supplement(self):
        supplement = {
            "supplement_id": "g5_sr_execution_supplement_v1",
            "status": environment.protocol_guard.APPROVED_EXECUTION_SUPPLEMENT_STATUS,
            "execution_state": (
                environment.protocol_guard.APPROVED_EXECUTION_SUPPLEMENT_STATE
            ),
            "approval": {
                "decision": (
                    environment.protocol_guard.APPROVED_EXECUTION_SUPPLEMENT_STATUS
                ),
                "approved_by": "USER",
                "approved_date": "2026-08-23",
            },
            "parent_protocol": {
                "relative_path": environment.MACHINE_PROTOCOL.as_posix(),
                "protocol_id": self.machine["protocol_id"],
                "required_status": self.machine["status"],
                "sha256": self.machine_hash,
            },
        }
        approved_path = self.repo / environment.protocol_guard.EXECUTION_SUPPLEMENT
        approved_path.write_text(json.dumps(supplement), encoding="utf-8")
        approved_hash = hashlib.sha256(approved_path.read_bytes()).hexdigest()
        self.study["g5_symbolic_regression"]["execution_supplement"].update(
            status=environment.protocol_guard.APPROVED_EXECUTION_SUPPLEMENT_STATUS,
            approved_sha256=approved_hash,
        )
        (self.repo / environment.STUDY_PROTOCOL).write_text(
            json.dumps(self.study), encoding="utf-8"
        )
        return approved_hash

    def test_report_records_approved_hash_runtime_and_current_blocker(self):
        report = environment.build_report(**self._kwargs())

        self.assertEqual(
            "ENVIRONMENT_SMOKE_PASS_DATA_BLOCKED", report["status"]
        )
        self.assertFalse(report["formal_search_authorized"])
        self.assertEqual(
            [
                environment.G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED,
                environment.MISSING_G4_DERIVED_INPUTS,
            ],
            report["blocker_codes"],
        )
        self.assertEqual(
            environment.G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED,
            report["blocker_code"],
        )
        self.assertFalse(report["execution_supplement"]["approved"])
        self.assertEqual(
            "DRAFT_REQUIRES_USER_APPROVAL",
            report["execution_supplement"]["status"],
        )
        self.assertEqual(
            self.machine_hash, report["approved_protocol"]["approved_sha256"]
        )
        self.assertTrue(report["approved_protocol"]["hash_match"])
        self.assertEqual("1.3.0", report["python_packages"]["mpmath"])
        self.assertEqual("1.5.10", report["python_packages"]["pysr"])
        self.assertEqual("1.14.0", report["python_packages"]["sympy"])
        self.assertEqual("1.12.7", report["julia_backend"]["julia_version"])
        self.assertEqual(
            "1.11.3", report["julia_backend"]["symbolic_regression_version"]
        )
        self.assertEqual("SMOKE_PASS", report["smoke_test"]["status"])
        artifacts = report["formal_execution_artifacts"]
        self.assertEqual("SHA-256", artifacts["hash_algorithm"])
        self.assertEqual(
            list(environment.FORMAL_EXECUTION_ARTIFACTS),
            [item["relative_path"] for item in artifacts["files"]],
        )
        for item in artifacts["files"]:
            path = self.repo / item["relative_path"]
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), item["sha256"])
            self.assertEqual(path.stat().st_size, item["bytes"])
        self.assertFalse(report["ai_feynman"]["blocks_primary_pysr_search"])
        self.assertFalse(report["lockbox"]["used_by_environment_capture"])
        self.assertEqual(
            environment.MISSING_G4_DERIVED_INPUTS,
            report["data_preflight"]["blocker_code"],
        )
        self.assertEqual(2, len(report["data_preflight"]["missing_inputs"]))

    def test_capture_never_opens_development_or_lockbox_data(self):
        for relative in environment.EXPECTED_DEVELOPMENT_INPUTS:
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"not a readable research table")
        lockbox = self.repo / "derived/lockbox/forbidden.bin"
        lockbox.parent.mkdir(parents=True)
        lockbox.write_bytes(b"sealed")

        original_open = Path.open

        def guarded_open(path, *args, **kwargs):
            lowered = {part.lower() for part in Path(path).resolve().parts}
            if "derived" in lowered or "lockbox" in lowered:
                raise AssertionError(f"research data was opened: {path}")
            return original_open(path, *args, **kwargs)

        with mock.patch.object(Path, "open", new=guarded_open):
            report = environment.build_report(**self._kwargs())

        self.assertEqual("AVAILABLE_NOT_READ", report["data_preflight"]["status"])
        self.assertEqual(
            "ENVIRONMENT_SMOKE_PASS_EXECUTION_SUPPLEMENT_BLOCKED",
            report["status"],
        )
        self.assertEqual(
            [environment.G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED],
            report["blocker_codes"],
        )
        self.assertFalse(report["formal_search_authorized"])
        self.assertFalse(report["data_preflight"]["content_read"])
        self.assertEqual([], report["lockbox"]["paths_accessed"])

    def test_approved_supplement_and_present_inputs_clear_known_blockers(self):
        for relative in environment.EXPECTED_DEVELOPMENT_INPUTS:
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"presence only")
        approved_hash = self._approve_execution_supplement()

        report = environment.build_report(**self._kwargs())

        self.assertEqual(
            "ENVIRONMENT_SMOKE_PASS_INPUTS_AVAILABLE_NOT_READ", report["status"]
        )
        self.assertTrue(report["formal_search_authorized"])
        self.assertIsNone(report["blocker_code"])
        self.assertEqual([], report["blocker_codes"])
        self.assertTrue(report["execution_supplement"]["approved"])
        self.assertEqual(
            approved_hash, report["execution_supplement"]["approved_sha256"]
        )

    def test_hash_mismatch_and_missing_explicit_versions_are_rejected(self):
        study_path = self.repo / environment.STUDY_PROTOCOL
        self.study["g5_symbolic_regression"]["machine_protocol_sha256"] = "0" * 64
        study_path.write_text(json.dumps(self.study), encoding="utf-8")
        with self.assertRaisesRegex(
            environment.G5SREnvironmentError, "批准协议哈希失配"
        ):
            environment.build_report(**self._kwargs())

        self.study["g5_symbolic_regression"][
            "machine_protocol_sha256"
        ] = self.machine_hash
        study_path.write_text(json.dumps(self.study), encoding="utf-8")
        kwargs = self._kwargs()
        kwargs["julia_version"] = ""
        with self.assertRaisesRegex(
            environment.G5SREnvironmentError, "必须来自已验证 smoke"
        ):
            environment.build_report(**kwargs)

    def test_missing_formal_execution_artifact_is_rejected(self):
        (self.repo / environment.FORMAL_EXECUTION_ARTIFACTS[-1]).unlink()
        with self.assertRaisesRegex(
            environment.G5SREnvironmentError, "缺少正式执行关键文件"
        ):
            environment.build_report(**self._kwargs())

    def test_missing_mpmath_version_is_rejected(self):
        kwargs = self._kwargs()
        kwargs["package_versions"].pop("mpmath")
        with self.assertRaisesRegex(
            environment.G5SREnvironmentError, "正式 Python 环境缺少版本: mpmath"
        ):
            environment.build_report(**kwargs)

    def test_cli_is_stdout_only_unless_output_is_explicit(self):
        output = self.repo / environment.DEFAULT_OUTPUT
        args = [
            "--repo",
            str(self.repo),
            "--julia-version",
            "1.12.7",
            "--symbolic-regression-version",
            "1.11.3",
            "--smoke-status",
            "SMOKE_PASS",
            "--smoke-equation",
            "x*x",
            "--smoke-max-abs-error",
            "0",
            "--smoke-fit-seconds",
            "1",
            "--cold-start-seconds",
            "2",
            "--generated-at",
            "2026-08-23T07:00:00+00:00",
        ]
        with (
            mock.patch.object(
                environment,
                "_package_versions",
                return_value={
                    "mpmath": "m",
                    "numpy": "n",
                    "scipy": "s",
                    "sympy": "y",
                    "pysr": "p",
                },
            ),
            mock.patch.object(
                environment,
                "_uv_version",
                return_value={"available": True, "version": "u", "raw": "uv u"},
            ),
            mock.patch.object(
                environment,
                "_python_platform",
                return_value={"version": "3.11", "system": "Windows"},
            ),
            contextlib.redirect_stdout(io.StringIO()) as stdout,
        ):
            environment.main(args)

        self.assertFalse(output.exists())
        self.assertEqual("SMOKE_PASS", json.loads(stdout.getvalue())["smoke_test"]["status"])


if __name__ == "__main__":
    unittest.main()
