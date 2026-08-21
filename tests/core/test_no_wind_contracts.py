import csv
import math
import re
import tempfile
import unittest
from pathlib import Path

from src.fds import generate_fds_case as gen
from src.fds import tunnel_config as cfg


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class NoWindCaseContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = PROJECT_ROOT / "config" / "no_wind" / "首批判伪工况_设计草案.csv"
        with open(path, newline="", encoding="utf-8-sig") as stream:
            cls.rows = list(csv.DictReader(stream))
        fixed_width_path = PROJECT_ROOT / "config" / "no_wind" / "固定宽度诊断块_设计草案.csv"
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

    def test_design_only_csv_cannot_be_batch_generated(self):
        source = PROJECT_ROOT / "config" / "no_wind" / "首批判伪工况_设计草案.csv"
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                ValueError, "DESIGN_ONLY_NOT_AUTHORIZED_FOR_FORMAL_RUN"
            ):
                gen._run_csv(str(source), directory)

    @staticmethod
    def formal_case(**overrides):
        spec = dict(
            chid="g1_fire", case_kind="fire", Q=18, U=0,
            Df=math.sqrt(4 * 16 / math.pi), dx=0.25, L=100, W=10, H=5,
            x_fire=50, T_end=40, rnd_seed=104729,
            sensor_profile="no_wind_global_v1",
            mesh_profile="no_wind_symmetric_v1", output_profile="light",
        )
        spec.update(overrides)
        return gen.normalize_case(spec)

    def test_background_has_no_reaction_burner_or_hrr_device(self):
        case = self.formal_case(chid="g1_bg", case_kind="background", Q=0, Df=0,
                                T_end=5)
        text = gen.render_fds(**{
            "chid": case["chid"], "Q": case["Q"], "U": case["U"],
            "Df": case["Df"], "dx": case["dx"], "_normalized": case,
        })
        self.assertNotIn("&REAC", text)
        self.assertNotIn("BURNER", text)
        self.assertNotIn("HRR_tot", text)
        self.assertEqual(2, text.count("SURF_ID='OPEN'"))

    def test_global_sensors_do_not_move_with_fire(self):
        coordinate_sets = []
        for x_fire in (40, 50, 60):
            case = self.formal_case(chid=f"offset_{x_fire}", x_fire=x_fire)
            text = gen.render_fds(
                chid=case["chid"], Q=case["Q"], U=case["U"], Df=case["Df"],
                dx=case["dx"], _normalized=case,
            )
            coordinates = re.findall(
                r"^&DEVC ID='T(?:85|90|95)_\d+'.*XYZ=([0-9.]+) ",
                text, re.MULTILINE,
            )
            coordinate_sets.append(coordinates)
        self.assertTrue(coordinate_sets[0])
        self.assertEqual(coordinate_sets[0], coordinate_sets[1])
        self.assertEqual(coordinate_sets[1], coordinate_sets[2])

    def test_formal_global_profile_keeps_only_frozen_t90_layer(self):
        case = self.formal_case(
            chid="formal_t90", sensor_profile="no_wind_global_t90_v1",
            output_profile="heavy",
        )
        text = gen.render_fds(
            chid=case["chid"], Q=case["Q"], U=case["U"], Df=case["Df"],
            dx=case["dx"], _normalized=case,
        )
        expected = cfg.no_wind_global_sensor_layout()
        self.assertEqual(len(expected), len(re.findall(r"^&DEVC ID='T90_", text, re.MULTILINE)))
        self.assertEqual(len(expected), len(re.findall(r"^&DEVC ID='U95_", text, re.MULTILINE)))
        self.assertNotIn("ID='T85_", text)
        self.assertNotIn("ID='T95_", text)
        self.assertIn("&SLCF PBZ=4.500, QUANTITY='TEMPERATURE' /", text)

    def test_symmetric_mesh_has_21_meshes_and_central_activity_block(self):
        case = self.formal_case()
        text = gen.render_fds(
            chid=case["chid"], Q=case["Q"], U=case["U"], Df=case["Df"],
            dx=case["dx"], _normalized=case,
        )
        meshes = [line for line in text.splitlines() if line.startswith("&MESH")]
        self.assertEqual(21, len(meshes))
        self.assertEqual(5, sum("XB=36.000 64.000" in line for line in meshes))
        self.assertAlmostEqual(16.0, case["Af_discrete_m2"])
        self.assertAlmostEqual(50.0, case["xf_actual_m"])

    def test_coarse_symmetric_mesh_avoids_two_cell_poisson_partitions(self):
        case = self.formal_case(chid="coarse_mesh", dx=0.5)
        text = gen.render_fds(
            chid=case["chid"], Q=case["Q"], U=case["U"], Df=case["Df"],
            dx=case["dx"], _normalized=case,
        )
        meshes = [line for line in text.splitlines() if line.startswith("&MESH")]
        self.assertEqual(18, len(meshes))
        central = [line for line in meshes if "XB=36.000 64.000" in line]
        self.assertEqual(2, len(central))
        self.assertTrue(all("IJK=56 20 5" in line for line in central))

        cell_count = 0
        for line in meshes:
            match = re.search(r"IJK=(\d+) (\d+) (\d+)", line)
            self.assertIsNotNone(match)
            nx, ny, nz = (int(value) for value in match.groups())
            cell_count += nx * ny * nz
        self.assertEqual(40000, cell_count)

    def test_light_and_heavy_output_profiles_are_distinct(self):
        light = self.formal_case(output_profile="light")
        heavy = self.formal_case(chid="g1_heavy", output_profile="heavy")
        light_text = gen.render_fds(
            chid=light["chid"], Q=light["Q"], U=light["U"], Df=light["Df"],
            dx=light["dx"], _normalized=light,
        )
        heavy_text = gen.render_fds(
            chid=heavy["chid"], Q=heavy["Q"], U=heavy["U"], Df=heavy["Df"],
            dx=heavy["dx"], _normalized=heavy,
        )
        self.assertNotIn("&BNDF", light_text)
        self.assertIn("&BNDF", heavy_text)
        self.assertGreater(heavy_text.count("&SLCF"), light_text.count("&SLCF"))

    def test_primary_documents_and_supporting_contracts(self):
        research_path = PROJECT_ROOT / "docs" / "无风隧道顶棚纵向温度_正向主导双向闭环实施方案_主次定稿版.md"
        with open(research_path, encoding="utf-8-sig") as stream:
            text = stream.read()
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("# "))
        self.assertEqual(0, sum(line == "$$" for line in lines) % 2)
        self.assertIn("正向显式公式重发现", text)
        self.assertIn("G7_SKIPPED_NOT_REQUESTED", text)
        required = (
            PROJECT_ROOT / "docs" / "technical" / "变量与单位合同.md",
            PROJECT_ROOT / "data" / "reference" / "文献证据台账.csv",
            PROJECT_ROOT / "config" / "no_wind" / "固定宽度诊断块_设计草案.csv",
        )
        for path in required:
            self.assertTrue(path.is_file(), str(path))


if __name__ == "__main__":
    unittest.main()
