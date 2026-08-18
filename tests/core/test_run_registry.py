import tempfile
import unittest
from pathlib import Path

from src.fds import run_registry as registry


class RunRegistryContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cases = self.root / "case_registry.csv"
        self.attempts = self.root / "job_attempts.csv"
        self.protocol = self.root / "protocol.json"
        self.protocol.write_text(
            '{"lockbox":{"read_allowed":false,"run_allowed":false}}',
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _input(self, chid="case_a", t_end=300, restart=False, q=18):
        path = self.root / f"{chid}_{t_end}_{int(restart)}_{q}.fds"
        path.write_text("\n".join([
            f"&HEAD CHID='{chid}', TITLE='test' /",
            "&MESH IJK=4 4 4, XB=0 1 0 1 0 1 /",
            f"&TIME T_END={t_end}.0 /",
            f"&MISC RESTART={'.TRUE.' if restart else '.FALSE.'}, TMPA=20.0 /",
            f"&SURF ID='BURNER', HRRPUA={q * 1000}.0 /",
            "&TAIL /",
        ]) + "\n", encoding="utf-8")
        return path

    def _named_input(self, **kwargs):
        source = self._input(**kwargs)
        target = source.with_name(f"{registry.input_chid(source)}.fds")
        source.replace(target)
        return target

    def _prepare(self, source, job_attempt_id="case_a_a01", return_name="a01"):
        return registry.prepare_attempt(
            source_input=source,
            return_dir=self.root / "runs" / return_name,
            subset="pilot", purpose="g1_short", case_kind="fire",
            parent_case_id="g1", physical_case_id="g1_q18",
            job_attempt_id=job_attempt_id,
            protocol_path=self.protocol,
            case_registry_path=self.cases, attempts_path=self.attempts,
        )

    def test_t_end_and_restart_are_the_only_current_hash_whitelist(self):
        first = self._input(t_end=300, restart=False)
        continued = self._input(t_end=450, restart=True)
        changed_q = self._input(t_end=300, restart=False, q=27)
        self.assertEqual(registry.run_core_sha256(first),
                         registry.run_core_sha256(continued))
        self.assertNotEqual(registry.run_core_sha256(first),
                            registry.run_core_sha256(changed_q))

    def test_attempt_copy_and_registration_are_idempotent_but_id_cannot_change(self):
        source = self._named_input()
        row = self._prepare(source)
        again = self._prepare(source)
        self.assertEqual(row["attempt_input_sha256"], again["attempt_input_sha256"])
        self.assertTrue(Path(row["attempt_input_path"]).is_file())

        source.write_text(source.read_text(encoding="utf-8").replace(
            "HRRPUA=18000.0", "HRRPUA=27000.0"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "禁止复用或覆盖"):
            self._prepare(source)

    def test_same_run_chid_rejects_non_whitelist_core_change(self):
        source = self._named_input()
        self._prepare(source)
        source.write_text(source.read_text(encoding="utf-8").replace(
            "HRRPUA=18000.0", "HRRPUA=27000.0"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "不同 run_core_sha256"):
            self._prepare(source, job_attempt_id="case_a_a02", return_name="a02")

    def test_lockbox_is_denied_until_protocol_unlock(self):
        for operation in ("generate", "read", "train"):
            with self.subTest(operation=operation), self.assertRaises(PermissionError):
                registry.guard_subset("lockbox", operation, self.protocol)
        registry.guard_subset("pilot", "prepare", self.protocol)

    def test_restart_transition_requires_next_registered_end_and_same_core(self):
        parent = self._input(t_end=300, restart=False)
        child = self._input(t_end=450, restart=True)
        registry.validate_restart_transition(parent, child)
        skipped = self._input(t_end=600, restart=True)
        with self.assertRaisesRegex(ValueError, "续接一段"):
            registry.validate_restart_transition(parent, skipped)
