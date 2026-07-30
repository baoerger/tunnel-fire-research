"""温度/缺测/偏置/漂移/位置扰动、留区间分组和统一鲁棒性矩阵。"""
from __future__ import annotations

import argparse
import copy
import csv
import math
import random
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


STAGE7_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = STAGE7_ROOT.parent
for source_dir in (
    PROJECT_ROOT / "阶段六" / "src", PROJECT_ROOT / "阶段五" / "src",
    PROJECT_ROOT / "阶段一" / "src",
):
    sys.path.insert(0, str(source_dir))

import physics_training  # noqa: E402
import set_encoder  # noqa: E402
import training_evaluation  # noqa: E402


SYNTHETIC_LABEL = set_encoder.SYNTHETIC_LABEL
NO_ROBUSTNESS_CLAIM = "SYNTHETIC_ROBUSTNESS_TOOL_ONLY_NO_REAL_ROBUSTNESS_CLAIM"
VARIABLE_KEYS = {"power": "Q_MW", "wind": "U", "size": "Df", "location": "x_f"}


@dataclass(frozen=True)
class Perturbation:
    name: str
    temperature_noise_sigma_C: float = 0.0
    missing_fraction: float = 0.0
    temperature_bias_C: float = 0.0
    drift_C_per_m: float = 0.0
    position_sigma_m: float = 0.0

    def validate(self):
        values = asdict(self)
        if not self.name.strip():
            raise ValueError("扰动名称不能为空")
        numeric = [value for key, value in values.items() if key != "name"]
        if any(not math.isfinite(float(value)) for value in numeric):
            raise ValueError("扰动参数必须有限")
        if self.temperature_noise_sigma_C < 0 or self.position_sigma_m < 0:
            raise ValueError("噪声和位置标准差不能为负")
        if not 0 <= self.missing_fraction < 1:
            raise ValueError("缺测比例必须位于 [0,1)")
        return self


def inject_perturbation(sample, perturbation, seed=20260729, min_valid=1):
    perturbation.validate()
    result = copy.deepcopy(sample)
    rng = random.Random(seed)
    sensors = result["sensors"]
    if int(min_valid) != min_valid or not 1 <= min_valid <= len(sensors):
        raise ValueError("min_valid 无效")
    xs = [float(row["x"]) for row in sensors]
    center = sum(xs) / len(xs)
    for row in sensors:
        x_original = float(row["x"])
        row["dT"] = (
            float(row["dT"]) + perturbation.temperature_bias_C
            + perturbation.drift_C_per_m * (x_original - center)
            + rng.gauss(0.0, perturbation.temperature_noise_sigma_C)
        )
        row["x"] = x_original + rng.gauss(0.0, perturbation.position_sigma_m)
    valid_indices = [index for index, row in enumerate(sensors) if float(row["m"]) == 1.0]
    remove_count = min(
        int(round(perturbation.missing_fraction * len(valid_indices))),
        max(0, len(valid_indices) - int(min_valid)),
    )
    for index in rng.sample(valid_indices, remove_count):
        sensors[index]["m"] = 0.0
    result["perturbation"] = asdict(perturbation)
    result["perturbation_seed"] = int(seed)
    result["evidence_label"] = SYNTHETIC_LABEL
    result["decision_status"] = NO_ROBUSTNESS_CLAIM
    return result


def interval_holdout(samples, variable, lower, upper):
    if variable not in VARIABLE_KEYS or not math.isfinite(lower) or not math.isfinite(upper) or lower >= upper:
        raise ValueError("留区间变量或边界无效")
    key = VARIABLE_KEYS[variable]
    groups = {}
    for sample in samples:
        case_id = str(sample.get("case_id") or "").strip()
        if not case_id:
            raise ValueError("留区间样本缺少 case_id")
        groups.setdefault(case_id, []).append(sample)
    test_groups = set()
    for case_id, rows in groups.items():
        values = {float(row[key]) for row in rows}
        if len(values) != 1:
            raise ValueError(f"同一 case_id 的 {key} 不一致")
        value = next(iter(values))
        if lower <= value <= upper:
            test_groups.add(case_id)
    train = [row for row in samples if str(row["case_id"]) not in test_groups]
    test = [row for row in samples if str(row["case_id"]) in test_groups]
    return {
        "variable": variable, "field": key, "lower": lower, "upper": upper,
        "train": train, "test": test, "train_case_ids": sorted(set(str(row["case_id"]) for row in train)),
        "test_case_ids": sorted(test_groups),
    }


def build_holdout_schemes(samples, intervals):
    schemes = []
    for variable in ("power", "wind", "size", "location"):
        if variable not in intervals:
            raise ValueError(f"缺少 {variable} 留区间定义")
        lower, upper = intervals[variable]
        schemes.append(interval_holdout(samples, variable, float(lower), float(upper)))
    return schemes


def default_perturbations():
    return [
        Perturbation("baseline"),
        Perturbation("temperature_noise", temperature_noise_sigma_C=2.0),
        Perturbation("random_missing", missing_fraction=0.30),
        Perturbation("temperature_bias", temperature_bias_C=2.0),
        Perturbation("spatial_drift", drift_C_per_m=0.05),
        Perturbation("position_error", position_sigma_m=0.20),
    ]


def robustness_matrix(predictors, samples, perturbations=None, decoder=None, seed=20260729):
    if not predictors or not samples:
        raise ValueError("鲁棒性评价模型或样本为空")
    perturbations = list(perturbations or default_perturbations())
    rows = []
    for perturb_index, perturbation in enumerate(perturbations):
        perturbed = []
        for sample_index, sample in enumerate(samples):
            row = inject_perturbation(
                sample, perturbation, seed + 1000 * perturb_index + sample_index
            )
            row["subset"] = "synthetic_software_evaluation"
            perturbed.append(row)
        for model_name, predictor in predictors.items():
            metrics = training_evaluation.evaluate_model(
                model_name, predictor, perturbed, decoder=decoder
            )
            rows.append({
                "perturbation": perturbation.name, **metrics,
                "evidence_label": SYNTHETIC_LABEL,
                "decision_status": NO_ROBUSTNESS_CLAIM,
            })
    return rows


def _constant_model():
    return {
        "family": "power_law", "epsilon": 1e-6,
        "coefficients": {name: [0.0, 0.0, 0.0, 0.0]
                         for name in ("Pe_e", "Da_e", "Pi_S", "delta_over_H")},
    }


def run_synthetic_software_check(output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    samples = physics_training.generate_synthetic_samples(
        8, seed=20260730, sensor_counts=(8, 12), missing_probability=0.0
    )
    for sample in samples:
        sample["case_id"] = sample["sample_id"]
        sample["subset"] = "synthetic_software_evaluation"
    decoder = physics_training.ConstantClosurePhysicsDecoder()
    network = set_encoder.SetEncoder(seed=104)
    direct = training_evaluation.make_direct_predictor(
        _constant_model(), {"q_grid": 8, "x_grid": 11, "n_starts": 2, "local_iterations": 6}
    )
    rows = robustness_matrix(
        {"direct_optimization": direct, "physics_decoder_set_encoder": network.predict},
        samples[:4], decoder=decoder,
    )
    with (output_dir / "synthetic_robustness_matrix.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    ranges = {
        "power": (20.0, 50.0), "wind": (1.5, 3.5),
        "size": (3.5, 6.5), "location": (45.0, 55.0),
    }
    schemes = build_holdout_schemes(samples, ranges)
    split_rows = []
    for scheme in schemes:
        for case_id in scheme["train_case_ids"]:
            split_rows.append({
                "scheme": f"leave_{scheme['variable']}_interval", "case_id": case_id,
                "role": "train", "lower": scheme["lower"], "upper": scheme["upper"],
                "evidence_label": SYNTHETIC_LABEL, "decision_status": NO_ROBUSTNESS_CLAIM,
            })
        for case_id in scheme["test_case_ids"]:
            split_rows.append({
                "scheme": f"leave_{scheme['variable']}_interval", "case_id": case_id,
                "role": "holdout", "lower": scheme["lower"], "upper": scheme["upper"],
                "evidence_label": SYNTHETIC_LABEL, "decision_status": NO_ROBUSTNESS_CLAIM,
            })
    with (output_dir / "synthetic_holdout_schemes.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(split_rows[0]))
        writer.writeheader(); writer.writerows(split_rows)
    return {"matrix": rows, "schemes": schemes}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=STAGE7_ROOT / "01_鲁棒性工具")
    args = parser.parse_args(argv)
    result = run_synthetic_software_check(args.output_dir)
    print(f"synthetic robustness rows={len(result['matrix'])}, holdout schemes={len(result['schemes'])}")
    print(NO_ROBUSTNESS_CLAIM)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
