import csv
import math
import os
import re
import sys
import tempfile
import unittest
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "src")
sys.path.insert(0, SRC)

import check_convection_ratio as convection
import fds_io
import generate_fds_case as gen
import tunnel_config as cfg


class GenerateFdsTests(unittest.TestCase):
    def render(self, **overrides):
        spec = dict(chid="case", Q=40, U=2.5, Df=5, dx=0.25,
                    L=100, W=10, H=5, x_fire=50, T_end=60)
        spec.update(overrides)
        return gen.render_fds(**spec)

    def parse_meshes(self, text):
        pattern = re.compile(
            r"^&MESH ID='([^']+)', IJK=(\d+) (\d+) (\d+), "
            r"XB=([\d.-]+) ([\d.-]+) ([\d.-]+) ([\d.-]+) "
            r"([\d.-]+) ([\d.-]+) /$",
            re.MULTILINE,
        )
        return [
            {
                "id": match.group(1),
                "ijk": tuple(int(match.group(i)) for i in range(2, 5)),
                "xb": tuple(float(match.group(i)) for i in range(5, 11)),
            }
            for match in pattern.finditer(text)
        ]

    def test_verified_fds_6101_contract(self):
        text = self.render()
        for required in (
            "FUEL='N-HEPTANE'", "TAU_Q=10.0", "DT_HRR=1.0",
            "QUANTITY='U-VELOCITY'", "QUANTITY='HRRPUV'",
            "SPATIAL_STATISTIC='VOLUME INTEGRAL'", "IOR=-3",
        ):
            self.assertIn(required, text)
        for forbidden in (
            "&SPEC", "SIMPLE_CHEMISTRY", " TAU=", "U VELOCITY",
            "STATISTICS=", "CONVECTIVE HRR", "SPECIFIC_HEAT=880",
        ):
            self.assertNotIn(forbidden, text)
        ramps = [line for line in text.splitlines() if line.startswith("&RAMP")]
        self.assertEqual(2, len(ramps))
        self.assertTrue(text.rstrip().endswith("&TAIL /"))

    def test_burner_is_grid_aligned_and_power_closed(self):
        case = gen.normalize_case(dict(chid="burner", Q=40, U=2.5, Df=5,
                                       dx=0.25, L=100, W=10, H=5,
                                       x_fire=50, T_end=60))
        x0, x1, y0, y1 = case["burner_bounds"]
        for value in (x0, x1, y0, y1):
            self.assertAlmostEqual(value / case["dx"], round(value / case["dx"]))
        area = (x1 - x0) * (y1 - y0)
        text = self.render()
        hrrpua = float(re.search(r"HRRPUA=([0-9.]+)", text).group(1))
        self.assertAlmostEqual(hrrpua * area, 40e3, delta=2.0)
        burner = (f"XB={x0:.3f} {x1:.3f} {y0:.3f} {y1:.3f} 0.00 0.00, "
                  "SURF_ID='BURNER'")
        self.assertIn(burner, text)

    def test_zero_wind_has_two_open_portals(self):
        text = self.render(U=0)
        self.assertNotIn("SURF ID='INLET'", text)
        self.assertEqual(2, text.count("SURF_ID='OPEN'"))
        self.assertNotIn("&RAMP", text)

    def test_grid_levels_and_extended_length(self):
        for dx, ijk in ((0.5, "IJK=200 20 10"),
                        (0.25, "IJK=400 40 20"),
                        (0.125, "IJK=800 80 40")):
            meshes = self.parse_meshes(self.render(dx=dx))
            self.assertEqual(1, len(meshes))
            self.assertIn(ijk, self.render(dx=dx))
        self.assertIn("IJK=600 40 20", self.render(L=150, x_fire=75))

    def test_uniform_22_mesh_partition_preserves_domain_and_cells(self):
        cases = (
            (0.5, 3.0, 40000),
            (0.25, 5.0, 320000),
            (0.125, 7.0, 2560000),
        )
        for dx, diameter, expected_cells in cases:
            with self.subTest(dx=dx, diameter=diameter):
                text = self.render(
                    dx=dx, Df=diameter,
                    n_mesh_x=11, n_mesh_y=1, n_mesh_z=2,
                )
                meshes = self.parse_meshes(text)
                self.assertEqual(22, len(meshes))
                self.assertEqual(22, len({mesh["id"] for mesh in meshes}))
                self.assertEqual(
                    expected_cells,
                    sum(math.prod(mesh["ijk"]) for mesh in meshes),
                )

                x_intervals = sorted({(mesh["xb"][0], mesh["xb"][1]) for mesh in meshes})
                z_intervals = sorted({(mesh["xb"][4], mesh["xb"][5]) for mesh in meshes})
                self.assertEqual(11, len(x_intervals))
                self.assertEqual(2, len(z_intervals))
                self.assertEqual((0.0, 100.0), (x_intervals[0][0], x_intervals[-1][1]))
                self.assertEqual((0.0, 5.0), (z_intervals[0][0], z_intervals[-1][1]))
                self.assertTrue(all(
                    left[1] == right[0]
                    for left, right in zip(x_intervals, x_intervals[1:])
                ))
                self.assertTrue(all(
                    left[1] == right[0]
                    for left, right in zip(z_intervals, z_intervals[1:])
                ))
                self.assertTrue(all(mesh["xb"][2:4] == (0.0, 10.0) for mesh in meshes))
                for mesh in meshes:
                    for boundary in mesh["xb"]:
                        self.assertAlmostEqual(boundary / dx, round(boundary / dx))

                burner_x0, burner_x1, burner_y0, burner_y1 = gen.normalize_case(dict(
                    chid="partition", Q=40, U=2.5, Df=diameter, dx=dx,
                    L=100, W=10, H=5, x_fire=50, T_end=60,
                    n_mesh_x=11, n_mesh_y=1, n_mesh_z=2,
                ))["burner_bounds"]
                containing = [
                    interval for interval in x_intervals
                    if interval[0] <= burner_x0 and burner_x1 <= interval[1]
                ]
                self.assertEqual(1, len(containing))
                self.assertTrue(0.0 <= burner_y0 < burner_y1 <= 10.0)

    def test_mesh_partition_validation(self):
        invalid = (
            (dict(n_mesh_x=0), "n_mesh_x 必须大于 0"),
            (dict(n_mesh_z=21), "超过 z 方向单元数"),
            (dict(n_mesh_x=10), "切穿离散燃烧器"),
            (dict(n_mesh_x=1.5), "必须是整数"),
        )
        for overrides, message in invalid:
            spec = dict(chid="partition", Q=40, U=2.5, Df=5, dx=0.25,
                        L=100, W=10, H=5, x_fire=50, T_end=60)
            spec.update(overrides)
            with self.subTest(overrides=overrides), self.assertRaisesRegex(ValueError, message):
                gen.normalize_case(spec)

    def test_custom_geometry_uses_case_height(self):
        text = self.render(L=30, W=4, H=2, x_fire=15, Df=1, dx=0.25)
        self.assertIn("XYZ=", text)
        self.assertIn(" 2.000 1.800 /", text)
        self.assertIn(" 2.000 1.900 /", text)
        self.assertIn("PBY=2.00", text)
        xs = cfg.sensor_layout(height=2, fire_x=15,
                               region=cfg.measurement_region(30, 2))
        self.assertTrue(all(6 <= x <= 24 for x in xs))

    def test_validation_messages(self):
        invalid = [
            (dict(chid="bad id", Q=1, Df=1, dx=0.25), "只能包含"),
            (dict(chid="tbd", Q="TBD", Df=1, dx=0.25), "Q"),
            (dict(chid="grid", Q=1, Df=1, dx=0.3, L=100), "不能被"),
            (dict(chid="fire", Q=1, Df=1, dx=0.25, L=100, x_fire=2), "测量区"),
        ]
        for spec, message in invalid:
            with self.subTest(spec=spec), self.assertRaisesRegex(ValueError, message):
                gen.normalize_case(spec)

    def test_short_run_warns(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            gen.normalize_case(dict(chid="smoke", Q=1, Df=1, dx=0.25,
                                    L=100, x_fire=50, T_end=5))
        self.assertIn("仅适合输入/启动检查", str(caught[0].message))
        self.assertIn("3×TAU_Q", str(caught[0].message))

    def test_csv_duplicate_chid_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cases.csv")
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["chid", "Q", "Df", "dx"])
                writer.writerow(["dup", 1, 1, 0.25])
                writer.writerow(["dup", 2, 1, 0.25])
            with self.assertRaisesRegex(ValueError, "重复"):
                gen._run_csv(path, os.path.join(tmp, "out"))


class FdsIoTests(unittest.TestCase):
    def write_csv(self, rows):
        tmp = tempfile.TemporaryDirectory()
        path = os.path.join(tmp.name, "sample.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)
        return tmp, path

    def test_units_first_and_header_first(self):
        layouts = [
            [["s", "K", "W"], ["Time", "T_0100", "HRR"], [0, 293.15, 1000]],
            [["Time", "T_0100", "HRR"], ["s", "K", "W"], [0, 293.15, 1000]],
        ]
        for rows in layouts:
            tmp, path = self.write_csv(rows)
            try:
                times, series, units = fds_io._read_fds_csv(path)
                normalized = fds_io.normalize_units(series, units)
                self.assertEqual([0.0], times)
                self.assertAlmostEqual(20.0, normalized["T_0100"][0])
                self.assertEqual(1.0, normalized["HRR"][0])
            finally:
                tmp.cleanup()

    def test_reads_per_case_run_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            chid = "nested_case"
            run_dir = os.path.join(tmp, chid)
            os.makedirs(run_dir)
            path = os.path.join(run_dir, f"{chid}_devc.csv")
            with open(path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows([
                    ["Time", "T_0100"], ["s", "C"], [0, 20],
                ])
            times, series, _ = fds_io.read_devc(tmp, chid)
            self.assertEqual([0.0], times)
            self.assertEqual([20.0], series["T_0100"])

    def test_hrr_key_matching_does_not_substitute_q_conv(self):
        series = {"HRR": [1000], "Q_CONV": [500]}
        self.assertEqual("HRR", convection._find_key(series, ["HRR"]))
        self.assertIsNone(convection._find_key(series, ["Q_RADI"]))

    def test_engineering_unit_conversions(self):
        series = {"mw": [2], "flux": [3000], "speed": [4]}
        units = {"mw": "MW", "flux": "W/m^2", "speed": "m/s"}
        normalized = fds_io.normalize_units(series, units)
        self.assertEqual([2000.0], normalized["mw"])
        self.assertEqual([3.0], normalized["flux"])
        self.assertEqual([4], normalized["speed"])


if __name__ == "__main__":
    unittest.main()
