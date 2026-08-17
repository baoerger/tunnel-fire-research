import csv
import math
import os
import re
import sys
import unittest


HERE = os.path.dirname(os.path.abspath(__file__))
NO_WIND_ROOT = os.path.dirname(HERE)
PROJECT_ROOT = os.path.dirname(NO_WIND_ROOT)
STAGE1_SRC = os.path.join(PROJECT_ROOT, "阶段一", "src")
sys.path.insert(0, STAGE1_SRC)

import generate_fds_case as gen
import tunnel_config as cfg


class NoWindCaseContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.path.join(
            NO_WIND_ROOT, "配置", "首批判伪工况_设计草案.csv",
        )
        with open(path, newline="", encoding="utf-8-sig") as stream:
            cls.rows = list(csv.DictReader(stream))
        fixed_width_path = os.path.join(
            NO_WIND_ROOT, "配置", "固定宽度诊断块_设计草案.csv",
        )
        with open(fixed_width_path, newline="", encoding="utf-8-sig") as stream:
            cls.fixed_width_rows = list(csv.DictReader(stream))

    @staticmethod
    def render(case):
        return gen.render_fds(
            chid=case["chid"], Q=case["Q"], U=case["U"], Df=case["Df"],
            dx=case["dx"], L=case["L"], W=case["W"], H=case["H"],
            x_fire=case["x_fire"], T_end=case["T_end"],
            group=case["case_group"], title=case["note"],
            ramp_inlet=case["ramp_inlet"], n_mesh_x=case["n_mesh_x"],
            n_mesh_y=case["n_mesh_y"], n_mesh_z=case["n_mesh_z"],
            sensor_profile=case["sensor_profile"], _normalized=case,
        )

    def test_first_batch_has_expected_design_blocks(self):
        self.assertEqual(16, len(self.rows))
        self.assertEqual(16, len({row["chid"] for row in self.rows}))
        purpose_counts = {}
        for row in self.rows:
            purpose_counts[row["purpose"]] = purpose_counts.get(row["purpose"], 0) + 1
            self.assertEqual("DESIGN_ONLY_NOT_AUTHORIZED_FOR_FORMAL_RUN", row["status"])
            self.assertEqual("no_wind_dense", row["sensor_profile"])
            self.assertEqual(0.0, float(row["U"]))
            self.assertAlmostEqual(float(row["L"]) / float(row["H"]), 20.0)
            self.assertAlmostEqual(
                math.pi * float(row["Df"]) ** 2 / 4.0,
                float(row["Af"]),
                places=5,
            )
        self.assertEqual(
            {"max_temperature_scaling": 12, "half_width_effect": 4},
            purpose_counts,
        )

    def test_all_rows_pass_generator_geometry_and_open_portal_contracts(self):
        for line_number, row in enumerate(self.rows, start=2):
            with self.subTest(chid=row["chid"]):
                case = gen.normalize_case(row, row_number=line_number)
                text = self.render(case)
                self.assertEqual(22, text.count("&MESH ID="))
                self.assertEqual(2, text.count("SURF_ID='OPEN'"))
                self.assertNotIn("SURF ID='INLET'", text)
                self.assertNotIn("RAMP_V", text)
                self.assertNotIn("&RAMP", text)
                self.assertIn(
                    f"&SLCF PBZ={0.90 * case['H']:.3f}, QUANTITY='TEMPERATURE' /",
                    text,
                )

    def test_fixed_width_diagnostic_block_is_separate_and_renderable(self):
        self.assertEqual(4, len(self.fixed_width_rows))
        self.assertEqual(4, len({row["chid"] for row in self.fixed_width_rows}))
        self.assertEqual({20.0, 60.0}, {float(row["Q"]) for row in self.fixed_width_rows})
        self.assertEqual({4.0, 6.0}, {float(row["H"]) for row in self.fixed_width_rows})
        for line_number, row in enumerate(self.fixed_width_rows, start=2):
            with self.subTest(chid=row["chid"]):
                self.assertEqual(10.0, float(row["W"]))
                self.assertEqual(100.0, float(row["L"]))
                self.assertEqual(50.0, float(row["x_fire"]))
                self.assertEqual(
                    "DESIGN_ONLY_NOT_AUTHORIZED_FOR_FORMAL_RUN", row["status"],
                )
                case = gen.normalize_case(row, row_number=line_number)
                text = self.render(case)
                self.assertEqual(2, text.count("SURF_ID='OPEN'"))
                self.assertNotIn("SURF ID='INLET'", text)

    def test_dense_layout_is_symmetric_and_resolves_peak(self):
        row = next(row for row in self.rows if row["chid"] == "nw_p06_q20_h5_ar20")
        case = gen.normalize_case(row)
        text = self.render(case)
        ids = re.findall(r"^&DEVC ID='T_(\d+)'", text, re.MULTILINE)
        xs = sorted(int(value) / 100.0 for value in ids)
        self.assertEqual(35, len(xs))
        self.assertEqual(len(xs), len(set(xs)))
        for x in xs:
            self.assertIn(round(2.0 * case["x_fire"] - x, 8), xs)
        near = [x for x in xs if abs(x - case["x_fire"]) <= 2.0 * case["H"] + 1e-8]
        self.assertGreaterEqual(len(near), 5)
        self.assertLessEqual(
            max(right - left for left, right in zip(near, near[1:])),
            0.25 * case["H"] + 1e-8,
        )

    def test_dense_profile_rejects_wind_offset_and_unknown_profile(self):
        base = dict(
            chid="bad", Q=20, U=0, Df=5.046265, dx=0.25,
            L=100, W=10, H=5, x_fire=50, T_end=60,
            sensor_profile="no_wind_dense",
        )
        for overrides, message in (
            ({"U": 0.1}, "只允许 U=0"),
            ({"x_fire": 45}, "x=L/2"),
            ({"sensor_profile": "dense"}, "仅支持"),
        ):
            spec = dict(base)
            spec.update(overrides)
            with self.subTest(overrides=overrides), self.assertRaisesRegex(ValueError, message):
                gen.normalize_case(spec)

    def test_standard_layout_remains_unchanged(self):
        standard = cfg.sensor_layout(
            height=5, fire_x=50, region=cfg.measurement_region(100, 5),
        )
        self.assertEqual(23, len(standard))

    def test_research_markdown_and_supporting_contracts(self):
        research_path = os.path.join(NO_WIND_ROOT, "研究方案.md")
        with open(research_path, encoding="utf-8-sig") as stream:
            text = stream.read()
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("# "))
        self.assertNotRegex(text, r"(?m)^\s*\\\[\s*$")
        self.assertNotRegex(text, r"(?m)^\s*\\\]\s*$")
        self.assertNotRegex(text, r"(?m)^\\(?:>|-|\d+\.)")
        self.assertEqual(0, sum(line == "$$" for line in lines) % 2)
        self.assertIn("p_2≈1/2", text)
        self.assertIn("已作废的历史误录", text)
        self.assertIn("正式拟合中 `p_1`、`p_2` 仍保持自由", text)
        for name in (
            "变量与单位合同.md",
            "文献证据台账.csv",
            os.path.join("配置", "固定宽度诊断块_设计草案.csv"),
        ):
            self.assertTrue(os.path.isfile(os.path.join(NO_WIND_ROOT, name)), name)


if __name__ == "__main__":
    unittest.main()
