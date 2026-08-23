import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.analysis import g5_sr_protocol as sr


def _write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class G5SRProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        for relative in (
            "config",
            "reports",
            "derived/development",
            "freezes/A",
            "freezes/A0",
        ):
            (self.repo / relative).mkdir(parents=True, exist_ok=True)
        for index, relative in enumerate(sr.FORMAL_EXECUTION_ARTIFACTS):
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"formal-artifact-{index}\n".encode())

        a0_bytes = b'{"model":"M1_S1"}\n'
        (self.repo / "freezes/A/forward_model_v1.json").write_bytes(a0_bytes)
        (self.repo / "freezes/A0/forward_model_v1.json").write_bytes(a0_bytes)
        self.a0_hash = hashlib.sha256(a0_bytes).hexdigest()
        (self.repo / "freezes/A0/manifest.json").write_text(
            json.dumps({
                "identity": "A0",
                "role": "IMMUTABLE_HUMAN_CANDIDATE_BASELINE",
                "canonical_sha256": self.a0_hash,
                "legacy_sha256": self.a0_hash,
            }),
            encoding="utf-8",
        )

        self.registry = [
            {
                "chid": "d_run_1",
                "job_attempt_id": "d_run_1_a01",
                "subset": "development",
                "parent_case_id": "p1",
                "physical_case_id": "pc1",
            },
            {
                "chid": "d_run_2",
                "job_attempt_id": "d_run_2_a01",
                "subset": "development",
                "parent_case_id": "p2",
                "physical_case_id": "pc2",
            },
        ]
        _write_csv(self.repo / "config/development_cases.csv", self.registry)

        self.metrics = []
        self.curves = []
        for index, registry in enumerate(self.registry, start=1):
            common = {
                "physical_case_id": registry["physical_case_id"],
                "parent_case_id": registry["parent_case_id"],
                "run_chid": registry["chid"],
                "job_attempt_id": registry["job_attempt_id"],
                "window_id": "w1",
                "batch": "D1",
            }
            self.metrics.append({
                **common,
                "Q_actual_kW": str(17000 + index * 1000),
                "Q_star": "0.1",
                "Df_discrete_m": "4.0",
                "xf_actual_m": "50.0",
                "deltaT_max_K": "100.0",
                "deltaT_peak_ci95_low_K": "90.0",
                "deltaT_peak_ci95_high_K": "110.0",
                "censored_fraction": "0.0",
                "bootstrap_n": "2000",
                "bootstrap_success_rate": "1.0",
                "bootstrap_block_len_samples": "3",
                "quality_status": "PASS",
                "exclusion_reason": "",
            })
            self.curves.append({
                **common,
                "x_coord_m": "50.0",
                "xf_actual_m": "50.0",
                "xi": "0.0",
                "deltaT_mean_K": "100.0",
                "deltaT_ci95_low_K": "90.0",
                "deltaT_ci95_high_K": "110.0",
                "Theta": "1.0",
                "point_role": "ACTIVE",
                "detection_limit_K": "0.5",
                "bootstrap_n": "2000",
                "bootstrap_block_len_samples": "3",
            })
        self.metrics_path = self.repo / "derived/development/case_metrics.csv"
        self.curves_path = self.repo / "derived/development/curve_points.csv"
        _write_csv(self.metrics_path, self.metrics)
        _write_csv(self.curves_path, self.curves)

        def source(path, required_columns, rows):
            return {
                "relative_path": path.relative_to(self.repo).as_posix(),
                "sha256": sr.sha256_file(path),
                "bytes": path.stat().st_size,
                "row_count": len(rows),
                "required_columns": required_columns,
            }

        sources = [
            source(self.metrics_path, list(self.metrics[0]), self.metrics),
            source(self.curves_path, list(self.curves[0]), self.curves),
        ]
        _write_csv(
            self.repo / "reports/g4_development_database_manifest.csv",
            [{
                "relative_path": item["relative_path"],
                "sha256": item["sha256"],
                "bytes": item["bytes"],
                "row_count": item["row_count"],
            } for item in sources],
        )
        (self.repo / "reports/g4_development_decision.json").write_text(
            json.dumps({
                "decision": "PASS_WITH_USER_VERSION_OVERRIDE_READY_FOR_G5",
                "lockbox_status": "SEALED_UNREAD_UNRUN",
            }),
            encoding="utf-8",
        )

        self.machine = {
            "schema_version": "1.0",
            "protocol_id": "g5_symbolic_regression_v1",
            "status": "SR_PROTOCOL_APPROVED",
            "execution_state": "APPROVED_NOT_RUN",
            "approval": {"decision": "SR_PROTOCOL_APPROVED"},
            "data_contract": {
                "allowed_subset": "development",
                "forbidden_subsets": ["lockbox"],
                "development_registry": "config/development_cases.csv",
                "g4_decision": "reports/g4_development_decision.json",
                "g4_required_decision": "PASS_WITH_USER_VERSION_OVERRIDE_READY_FOR_G5",
                "g4_manifest": "reports/g4_development_database_manifest.csv",
                "expected_counts": {
                    "runs": 2,
                    "parent_groups": 2,
                    "physical_cases": 2,
                    "curve_rows": 2,
                },
                "required_sources": sources,
                "effective_domain": {
                    "xi_max": 5.0,
                    "include_point_roles": ["ACTIVE", "CENSORED_LOW_SIGNAL"],
                },
            },
            "normalization": {"H_eff_m": 5.0},
            "search_budget": {"outer_folds": {"count": 9}},
            "freeze_identity": {
                "A0": {
                    "role": "IMMUTABLE_HUMAN_CANDIDATE_BASELINE",
                    "canonical_file": "freezes/A0/forward_model_v1.json",
                    "legacy_compatibility_file": "freezes/A/forward_model_v1.json",
                    "sha256": self.a0_hash,
                },
                "A_SR": {"state": "NOT_CREATED"},
                "A_active": {"state": "PENDING_G5_SR_DECISION"},
            },
        }
        self.machine_path = self.repo / "config/symbolic_regression_v1.json"
        self._write_protocols()
        self.protocol_hash = sr.sha256_file(self.machine_path)
        self.approved_hash_patch = mock.patch.object(
            sr, "APPROVED_PROTOCOL_SHA256", self.protocol_hash
        )
        self.approved_hash_patch.start()
        self._write_environment()

    def tearDown(self):
        self.approved_hash_patch.stop()
        self.tmp.cleanup()

    def _write_protocols(self):
        self.machine_path.write_text(
            json.dumps(self.machine, sort_keys=True), encoding="utf-8"
        )
        parent_hash = sr.sha256_file(self.machine_path)
        supplement = {
            "schema_version": "1.0",
            "supplement_id": "g5_sr_execution_supplement_v1",
            "status": sr.APPROVED_EXECUTION_SUPPLEMENT_STATUS,
            "execution_state": sr.APPROVED_EXECUTION_SUPPLEMENT_STATE,
            "approval": {
                "decision": sr.APPROVED_EXECUTION_SUPPLEMENT_STATUS,
                "approved_by": "USER",
                "approved_date": "2026-08-23",
            },
            "parent_protocol": {
                "relative_path": sr.MACHINE_PROTOCOL.as_posix(),
                "protocol_id": self.machine["protocol_id"],
                "required_status": "SR_PROTOCOL_APPROVED",
                "sha256": parent_hash,
            },
            "shape_search_sequence": {
                "phase_1": {
                    "campaign_id": "shape_xi_only",
                    "required_first": True,
                },
                "expansion_trigger": {
                    "logic": "OR",
                    "conditions": [
                        {
                            "condition_id": (
                                "XI_ONLY_Q_OR_D_SYSTEMATIC_RESIDUAL_FAIL"
                            )
                        },
                        {
                            "condition_id": "XI_ONLY_ABSOLUTE_SHAPE_GATE_FAIL"
                        },
                    ],
                    "decision_artifact_required_before_phase_2": (
                        sr.SHAPE_EXPANSION_DECISION.as_posix()
                    ),
                },
                "phase_2": {
                    "campaign_id": "shape_xi_q_d",
                    "conditional": True,
                    "launch_requires_trigger_pass": True,
                },
            },
        }
        supplement_path = self.repo / sr.EXECUTION_SUPPLEMENT
        supplement_path.write_text(
            json.dumps(supplement, sort_keys=True), encoding="utf-8"
        )
        study = {
            "lockbox": {
                "status": "SEALED_UNREAD_UNRUN",
                "sealed_at_stage": "G3",
                "generation_allowed": False,
                "preparation_allowed": False,
                "run_allowed": False,
                "read_allowed": False,
            },
            "g5_symbolic_regression": {
                "status": "SR_PROTOCOL_APPROVED",
                "machine_protocol": "config/symbolic_regression_v1.json",
                "machine_protocol_sha256": parent_hash,
                "execution_supplement": {
                    "status": sr.APPROVED_EXECUTION_SUPPLEMENT_STATUS,
                    "draft_path": sr.EXECUTION_SUPPLEMENT_DRAFT.as_posix(),
                    "approved_path": sr.EXECUTION_SUPPLEMENT.as_posix(),
                    "approved_sha256": sr.sha256_file(supplement_path),
                },
            },
        }
        (self.repo / "config/protocol_v1.json").write_text(
            json.dumps(study), encoding="utf-8"
        )

    def _set_draft_supplement(self):
        approved_path = self.repo / sr.EXECUTION_SUPPLEMENT
        approved_path.unlink(missing_ok=True)
        draft = {
            "schema_version": "1.0-draft",
            "supplement_id": "g5_sr_execution_supplement_v1",
            "status": "DRAFT_REQUIRES_USER_APPROVAL",
            "execution_state": "NOT_AUTHORIZED_FOR_FORMAL_SEARCH",
            "parent_protocol": {
                "relative_path": sr.MACHINE_PROTOCOL.as_posix(),
                "protocol_id": self.machine["protocol_id"],
                "required_status": "SR_PROTOCOL_APPROVED",
                "sha256": sr.sha256_file(self.machine_path),
            },
        }
        draft_path = self.repo / sr.EXECUTION_SUPPLEMENT_DRAFT
        draft_path.write_text(json.dumps(draft), encoding="utf-8")
        study_path = self.repo / sr.STUDY_PROTOCOL
        study = json.loads(study_path.read_text(encoding="utf-8"))
        study["g5_symbolic_regression"]["execution_supplement"] = {
            "status": "DRAFT_REQUIRES_USER_APPROVAL",
            "draft_path": sr.EXECUTION_SUPPLEMENT_DRAFT.as_posix(),
            "approved_path": sr.EXECUTION_SUPPLEMENT.as_posix(),
            "approved_sha256": None,
        }
        study_path.write_text(json.dumps(study), encoding="utf-8")

    def _mutate_approved_supplement(self, mutate):
        supplement_path = self.repo / sr.EXECUTION_SUPPLEMENT
        supplement = json.loads(supplement_path.read_text(encoding="utf-8"))
        mutate(supplement)
        supplement_path.write_text(
            json.dumps(supplement, sort_keys=True), encoding="utf-8"
        )
        study_path = self.repo / sr.STUDY_PROTOCOL
        study = json.loads(study_path.read_text(encoding="utf-8"))
        study["g5_symbolic_regression"]["execution_supplement"][
            "approved_sha256"
        ] = sr.sha256_file(supplement_path)
        study_path.write_text(json.dumps(study), encoding="utf-8")

    def _write_shape_expansion_decision(self, mutate=None):
        supplement_path = self.repo / sr.EXECUTION_SUPPLEMENT
        supplement = json.loads(supplement_path.read_text(encoding="utf-8"))
        decision = {
            "schema_version": "1.0",
            "stage": "G5_SR_SHAPE_EXPANSION_DECISION",
            "status": "SHAPE_EXPANSION_TRIGGERED",
            "decision": "RUN_SHAPE_XI_Q_D",
            "launch_authorized": True,
            "source_campaign": "shape_xi_only",
            "target_campaign": "shape_xi_q_d",
            "machine_protocol_sha256": sr.sha256_file(self.machine_path),
            "execution_supplement_sha256": sr.sha256_file(supplement_path),
            "lockbox_used": False,
            "outer_holdout_used_for_selection": False,
            "outer_fold_selected_count": 9,
            "trigger_logic": "OR",
            "trigger_tests": [
                {
                    "condition_id": condition["condition_id"],
                    "evaluated": True,
                    "triggered": index == 0,
                }
                for index, condition in enumerate(
                    supplement["shape_search_sequence"]["expansion_trigger"][
                        "conditions"
                    ]
                )
            ],
        }
        if mutate is not None:
            mutate(decision)
        path = self.repo / sr.SHAPE_EXPANSION_DECISION
        path.write_text(json.dumps(decision, sort_keys=True), encoding="utf-8")
        return decision, path

    def _write_environment(self, mutate=None):
        protocol_hash = sr.sha256_file(self.machine_path)
        runtime_versions = sr._current_runtime_versions()
        report = {
            "schema_version": "1.0",
            "stage": "G5_SR_ENVIRONMENT",
            "status": "ENVIRONMENT_SMOKE_PASS_INPUTS_AVAILABLE_NOT_READ",
            "approved_protocol": {
                "path": "config/symbolic_regression_v1.json",
                "approved_sha256": protocol_hash,
                "declared_sha256": protocol_hash,
                "hash_match": True,
            },
            "python": {"version": runtime_versions["python"]},
            "python_packages": {
                "mpmath": runtime_versions["mpmath"],
                "numpy": runtime_versions["numpy"],
                "scipy": runtime_versions["scipy"],
                "sympy": runtime_versions["sympy"],
                "pysr": runtime_versions["pysr"],
            },
            "formal_execution_artifacts": {
                "hash_algorithm": "SHA-256",
                "files": [
                    {
                        "relative_path": relative,
                        "sha256": sr.sha256_file(self.repo / relative),
                        "bytes": (self.repo / relative).stat().st_size,
                    }
                    for relative in sr.FORMAL_EXECUTION_ARTIFACTS
                ],
            },
            "julia_backend": {
                "julia_version": "1.12.7",
                "symbolic_regression_version": "1.11.3",
            },
            "smoke_test": {
                "status": "SMOKE_PASS",
                "project_or_lockbox_data_read": False,
            },
            "lockbox": {
                "protocol_status": "SEALED_UNREAD_UNRUN",
                "sealed_at_stage": "G3",
                "used_by_environment_capture": False,
                "used_by_g5_sr": False,
                "paths_accessed": [],
            },
            "data_preflight": {
                "status": "AVAILABLE_NOT_READ",
                "content_read": False,
                "missing_inputs": [],
                "required_inputs": [
                    {
                        "relative_path": source["relative_path"],
                        "present": True,
                    }
                    for source in self.machine["data_contract"]["required_sources"]
                ],
            },
        }
        if mutate is not None:
            mutate(report)
        (self.repo / sr.ENVIRONMENT_REPORT).write_text(
            json.dumps(report), encoding="utf-8"
        )

    def test_static_and_full_preflight_pass_for_development_only_data(self):
        static = sr.validate_static(self.repo)
        self.assertEqual("STATIC_G5_SR_PROTOCOL_PASS", static["status"])
        self.assertTrue(static["execution_supplement"]["approved"])
        self.assertTrue(static["formal_search_authorized"])
        self.assertEqual([], static["blocker_codes"])
        self.assertFalse(static["A_active_created"])

        result = sr.preflight(self.repo)
        self.assertEqual("READY_FOR_G5_SR_SEARCH", result["status"])
        self.assertEqual(2, result["counts"]["development_runs"])
        self.assertEqual(2, result["counts"]["curve_rows_in_formal_domain"])
        self.assertEqual(
            "ENVIRONMENT_SMOKE_PASS_INPUTS_AVAILABLE_NOT_READ",
            result["environment"]["status"],
        )

    def test_draft_supplement_reports_both_blockers_and_stops_before_data_read(self):
        self._set_draft_supplement()
        self.metrics_path.unlink()
        self.curves_path.unlink()

        static = sr.validate_static(self.repo)
        self.assertEqual("STATIC_G5_SR_PROTOCOL_PASS", static["status"])
        self.assertFalse(static["formal_search_authorized"])
        self.assertEqual(
            [
                sr.G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED,
                sr.MISSING_G4_DERIVED_INPUTS,
            ],
            static["blocker_codes"],
        )
        self.assertFalse(static["execution_supplement"]["approved"])
        self.assertEqual(2, len(static["data_availability"]["missing_inputs"]))

        with (
            mock.patch.object(
                sr,
                "_verify_source_file",
                side_effect=AssertionError("development data must not be opened"),
            ),
            self.assertRaisesRegex(
                sr.G5SRExecutionSupplementNotApprovedError,
                sr.G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED,
            ),
        ):
            sr.preflight(self.repo)

    def test_approved_supplement_requires_hash_parent_binding_and_approval_record(self):
        study_path = self.repo / sr.STUDY_PROTOCOL
        study = json.loads(study_path.read_text(encoding="utf-8"))
        study["g5_symbolic_regression"]["execution_supplement"][
            "approved_sha256"
        ] = "0" * 64
        study_path.write_text(json.dumps(study), encoding="utf-8")
        with self.assertRaisesRegex(
            sr.G5SRExecutionSupplementNotApprovedError,
            sr.G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED,
        ):
            sr.preflight(self.repo)

        self._write_protocols()
        self._mutate_approved_supplement(lambda supplement: supplement.pop("approval"))
        with self.assertRaisesRegex(
            sr.G5SRExecutionSupplementNotApprovedError,
            sr.G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED,
        ):
            sr.preflight(self.repo)

        self._write_protocols()
        self._mutate_approved_supplement(
            lambda supplement: supplement["parent_protocol"].update(
                sha256="0" * 64
            )
        )
        with self.assertRaisesRegex(
            sr.G5SRExecutionSupplementNotApprovedError,
            sr.G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED,
        ):
            sr.preflight(self.repo)

    def test_strict_supplement_loader_returns_exact_body_and_hashes(self):
        loaded = sr.load_approved_execution_supplement(self.repo)
        approved_path = self.repo / sr.EXECUTION_SUPPLEMENT
        self.assertEqual(
            json.loads(approved_path.read_text(encoding="utf-8")),
            loaded["record"],
        )
        self.assertEqual(sr.sha256_file(approved_path), loaded["sha256"])
        self.assertEqual(self.protocol_hash, loaded["parent_protocol_sha256"])

    def test_shape_expansion_authorization_accepts_only_bound_triggered_decision(self):
        _, path = self._write_shape_expansion_decision()
        loaded = sr.load_approved_execution_supplement(self.repo)
        result = sr.require_shape_expansion_authorized(
            self.repo,
            machine_protocol_sha256=self.protocol_hash,
            execution_supplement_sha256=loaded["sha256"],
            supplement=loaded["record"],
        )
        self.assertTrue(result["authorized"])
        self.assertEqual(sr.sha256_file(path), result["decision_sha256"])
        self.assertEqual("shape_xi_only", result["source_campaign"])
        self.assertEqual("shape_xi_q_d", result["target_campaign"])
        self.assertEqual(
            ["XI_ONLY_Q_OR_D_SYSTEMATIC_RESIDUAL_FAIL"],
            result["triggered_condition_ids"],
        )

    def test_shape_expansion_missing_decision_fails_closed(self):
        with self.assertRaisesRegex(
            sr.G5SRShapeExpansionNotAuthorizedError,
            sr.G5_SR_SHAPE_EXPANSION_NOT_AUTHORIZED,
        ):
            sr.require_shape_expansion_authorized(self.repo)

    def test_shape_expansion_illegal_state_hash_or_provenance_fails_closed(self):
        mutations = {
            "status": lambda value: value.update(status="SHAPE_EXPANSION_NOT_TRIGGERED"),
            "decision": lambda value: value.update(decision="DO_NOT_RUN_SHAPE_XI_Q_D"),
            "launch": lambda value: value.update(launch_authorized=False),
            "source": lambda value: value.update(source_campaign="shape_xi_q_d"),
            "target": lambda value: value.update(target_campaign="shape_xi_only"),
            "parent_hash": lambda value: value.update(
                machine_protocol_sha256="0" * 64
            ),
            "supplement_hash": lambda value: value.update(
                execution_supplement_sha256="0" * 64
            ),
            "lockbox": lambda value: value.update(lockbox_used=True),
            "outer_holdout": lambda value: value.update(
                outer_holdout_used_for_selection=True
            ),
            "outer_fold_count": lambda value: value.update(
                outer_fold_selected_count=8
            ),
            "unevaluated": lambda value: value["trigger_tests"][0].update(
                evaluated=False
            ),
            "no_trigger": lambda value: [
                item.update(triggered=False) for item in value["trigger_tests"]
            ],
            "missing_condition": lambda value: value.update(
                trigger_tests=value["trigger_tests"][:1]
            ),
            "duplicate_condition": lambda value: value["trigger_tests"][1].update(
                condition_id=value["trigger_tests"][0]["condition_id"]
            ),
        }
        for label, mutation in mutations.items():
            with self.subTest(label=label):
                self._write_shape_expansion_decision(mutation)
                with self.assertRaisesRegex(
                    sr.G5SRShapeExpansionNotAuthorizedError,
                    sr.G5_SR_SHAPE_EXPANSION_NOT_AUTHORIZED,
                ):
                    sr.require_shape_expansion_authorized(self.repo)

    def test_shape_expansion_rejects_stale_caller_protocol_context(self):
        self._write_shape_expansion_decision()
        loaded = sr.load_approved_execution_supplement(self.repo)
        calls = (
            {
                "machine_protocol_sha256": "0" * 64,
                "execution_supplement_sha256": loaded["sha256"],
                "supplement": loaded["record"],
            },
            {
                "machine_protocol_sha256": self.protocol_hash,
                "execution_supplement_sha256": "0" * 64,
                "supplement": loaded["record"],
            },
            {
                "machine_protocol_sha256": self.protocol_hash,
                "execution_supplement_sha256": loaded["sha256"],
                "supplement": {**loaded["record"], "tampered": True},
            },
        )
        for kwargs in calls:
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(
                    sr.G5SRShapeExpansionNotAuthorizedError,
                    sr.G5_SR_SHAPE_EXPANSION_NOT_AUTHORIZED,
                ):
                    sr.require_shape_expansion_authorized(self.repo, **kwargs)

    def test_static_validation_does_not_require_environment_evidence(self):
        (self.repo / sr.ENVIRONMENT_REPORT).unlink()
        result = sr.validate_static(self.repo)
        self.assertEqual("STATIC_G5_SR_PROTOCOL_PASS", result["status"])

    def test_static_validation_does_not_check_execution_artifact_hashes(self):
        (self.repo / sr.FORMAL_EXECUTION_ARTIFACTS[1]).unlink()
        result = sr.validate_static(self.repo)
        self.assertEqual("STATIC_G5_SR_PROTOCOL_PASS", result["status"])

    def test_full_preflight_requires_environment_evidence(self):
        (self.repo / sr.ENVIRONMENT_REPORT).unlink()
        with self.assertRaisesRegex(sr.G5SRProtocolError, "缺少 G5-SR 环境证据"):
            sr.preflight(self.repo)

    def test_stale_data_blocked_environment_evidence_is_rejected(self):
        def stale(report):
            report["status"] = "ENVIRONMENT_SMOKE_PASS_DATA_BLOCKED"
            report["data_preflight"]["status"] = "BLOCKED"

        self._write_environment(stale)
        with self.assertRaisesRegex(sr.G5SRProtocolError, "重新捕获环境 JSON"):
            sr.preflight(self.repo)

    def test_environment_protocol_hash_must_match_approved_hash(self):
        def wrong_hash(report):
            report["approved_protocol"]["approved_sha256"] = "0" * 64

        self._write_environment(wrong_hash)
        with self.assertRaisesRegex(sr.G5SRProtocolError, "approved_sha256"):
            sr.preflight(self.repo)

    def test_environment_requires_smoke_versions_and_lockbox_isolation(self):
        mutations = {
            "smoke": lambda report: report["smoke_test"].update(status="FAILED"),
            "python": lambda report: report["python"].update(version=" "),
            "mpmath": lambda report: report["python_packages"].update(mpmath=""),
            "numpy": lambda report: report["python_packages"].update(numpy=""),
            "scipy": lambda report: report["python_packages"].update(scipy=None),
            "sympy": lambda report: report["python_packages"].pop("sympy"),
            "pysr": lambda report: report["python_packages"].update(pysr=None),
            "julia": lambda report: report["julia_backend"].update(julia_version=""),
            "symbolic": lambda report: report["julia_backend"].update(
                symbolic_regression_version=""
            ),
            "environment_lockbox": lambda report: report["lockbox"].update(
                used_by_environment_capture=True
            ),
            "g5_lockbox": lambda report: report["lockbox"].update(used_by_g5_sr=True),
            "paths": lambda report: report["lockbox"].update(
                paths_accessed=["forbidden"]
            ),
            "status": lambda report: report["lockbox"].update(
                protocol_status="SEALED_G3"
            ),
            "sealed_stage": lambda report: report["lockbox"].update(
                sealed_at_stage="G2"
            ),
        }
        for label, mutation in mutations.items():
            with self.subTest(label=label):
                self._write_environment(mutation)
                with self.assertRaises((sr.G5SRProtocolError, PermissionError)):
                    sr.preflight(self.repo)

    def test_current_python_versions_must_exactly_match_environment_report(self):
        locations = {
            "python": ("python", "version"),
            "mpmath": ("python_packages", "mpmath"),
            "numpy": ("python_packages", "numpy"),
            "scipy": ("python_packages", "scipy"),
            "sympy": ("python_packages", "sympy"),
            "pysr": ("python_packages", "pysr"),
        }
        for label, (section, key) in locations.items():
            with self.subTest(label=label):
                self._write_environment(
                    lambda report, section=section, key=key: report[section].update(
                        {key: "0.0-environment-drift"}
                    )
                )
                with self.assertRaisesRegex(sr.G5SRProtocolError, "环境漂移"):
                    sr.preflight(self.repo)

    def test_formal_execution_artifact_drift_is_rejected(self):
        path = self.repo / sr.FORMAL_EXECUTION_ARTIFACTS[1]
        path.write_bytes(path.read_bytes() + b"tampered\n")
        with self.assertRaisesRegex(sr.G5SRProtocolError, "正式执行文件环境漂移"):
            sr.preflight(self.repo)

    def test_missing_development_data_remains_prior_to_environment_drift(self):
        self.metrics_path.unlink()
        path = self.repo / sr.FORMAL_EXECUTION_ARTIFACTS[1]
        path.write_bytes(path.read_bytes() + b"tampered\n")
        with self.assertRaisesRegex(
            sr.G5SRDataUnavailableError, sr.MISSING_G4_DERIVED_INPUTS
        ):
            sr.preflight(self.repo)

    def test_unapproved_protocol_is_rejected(self):
        self.machine["status"] = "RESEARCH_REVIEWED_NOT_APPROVED"
        self._write_protocols()
        with self.assertRaises(PermissionError):
            sr.validate_static(self.repo)

    def test_any_lockbox_permission_rejects_preflight(self):
        study_path = self.repo / "config/protocol_v1.json"
        study = json.loads(study_path.read_text(encoding="utf-8"))
        study["lockbox"]["read_allowed"] = True
        study_path.write_text(json.dumps(study), encoding="utf-8")
        with self.assertRaises(PermissionError):
            sr.validate_static(self.repo)

    def test_modified_source_is_rejected_by_hash_before_training(self):
        with self.metrics_path.open("a", encoding="utf-8") as stream:
            stream.write("tampered\n")
        with self.assertRaisesRegex(sr.G5SRProtocolError, "输入哈希失配"):
            sr.preflight(self.repo)

    def test_unregistered_run_is_rejected_even_with_valid_csv_structure(self):
        curves = [dict(row) for row in self.curves]
        curves[0]["run_chid"] = "lockbox_or_unknown_run"
        with self.assertRaises(PermissionError):
            sr._validate_development_rows(self.repo, self.machine, self.metrics, curves)

    def test_repository_a0_is_an_exact_legacy_copy(self):
        repo = Path(__file__).resolve().parents[2]
        canonical = repo / "freezes/A0/forward_model_v1.json"
        legacy = repo / "freezes/A/forward_model_v1.json"
        self.assertEqual(legacy.read_bytes(), canonical.read_bytes())
        self.assertEqual(
            "f4e90939fcf3507930bbbe883a9888d7e92b58eeb63b24de07b12ad8663e509b",
            sr.sha256_file(canonical),
        )

    def test_repository_static_contract_passes_without_opening_derived_data(self):
        repo = Path(__file__).resolve().parents[2]
        result = sr.validate_static(repo)
        self.assertEqual("STATIC_G5_SR_PROTOCOL_PASS", result["status"])
        expected_blockers = []
        if not result["execution_supplement"]["approved"]:
            expected_blockers.append(sr.G5_SR_EXECUTION_SUPPLEMENT_NOT_APPROVED)
        if result["data_availability"]["missing_inputs"]:
            expected_blockers.append(sr.MISSING_G4_DERIVED_INPUTS)
        self.assertEqual(expected_blockers, result["blocker_codes"])
        self.assertFalse(result["data_availability"]["content_read"])
        self.assertEqual(not expected_blockers, result["formal_search_authorized"])
        self.assertFalse(result["search_results_created"])


if __name__ == "__main__":
    unittest.main()
