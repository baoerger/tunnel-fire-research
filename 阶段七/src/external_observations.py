"""外部试验热电偶观测到端到端反演输入的可追溯转换。"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path

import uncertainty_anomaly


STAGE7_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = STAGE7_ROOT.parent
EXTERNAL_ROOT = PROJECT_ROOT / "阶段一" / "05_外部试验复现"
WAITING_STATUS = "WAITING_FORMAL_MODEL_AND_THERMOCOUPLE_TREATMENT_FOR_EXTERNAL_GENERALIZATION"
OOD_SCIENTIFIC_USE = "EXTERNAL_GENERALIZATION_EVALUATION_NO_CORE_DOMAIN_VALIDATION_CLAIM"


def _read_csv(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def build_external_inputs(cases_path=None, profiles_path=None, mapping_path=None):
    cases_path = Path(cases_path or EXTERNAL_ROOT / "external_cases_template.csv")
    profiles_path = Path(profiles_path or EXTERNAL_ROOT / "observations" / "external_temperature_profiles.csv")
    mapping_path = Path(mapping_path or EXTERNAL_ROOT / "measurement_mapping.csv")
    cases = {row["chid"]: row for row in _read_csv(cases_path)}
    profiles = _read_csv(profiles_path)
    mappings = {(row["chid"], row["raw_channel"]): row for row in _read_csv(mapping_path)}
    if not cases or not profiles or len(cases) != 3:
        raise ValueError("外部工况或观测缺失")
    grouped = {}
    for row in profiles:
        grouped.setdefault(row["chid"], []).append(row)
    if set(grouped) != set(cases):
        raise ValueError("外部观测 CHID 与工况表不一致")
    outputs = []
    for chid, case in cases.items():
        rows = grouped[chid]
        sensors, baselines = [], []
        for row in rows:
            key = (chid, row["sensor_id"])
            if key not in mappings:
                raise ValueError(f"外部测点缺少映射: {key}")
            mapping = mappings[key]
            if row["temperature_type"] != "thermocouple" or mapping["temperature_type"] != "thermocouple":
                raise ValueError("当前外部转换只接受明确标识的热电偶观测")
            for field in ("x_source_m", "x_rel_fire_m", "z_m"):
                if abs(float(row[field]) - float(mapping[field])) > 1e-9:
                    raise ValueError(f"外部观测与映射 {field} 不一致: {key}")
            T_mean, dT = float(row["T_mean_C"]), float(row["dT_mean_C"])
            baselines.append(T_mean - dT)
            sensors.append({
                "id": row["sensor_id"], "dT": dT, "x": float(row["x_source_m"]),
                "x_rel_fire_m": float(row["x_rel_fire_m"]), "z_m": float(row["z_m"]),
                "m": 1.0, "sigma_C": float(row["temperature_sd_C"]),
                "temperature_type": "thermocouple", "uncertainty_basis": row["uncertainty_basis"],
                "raw_files": row["raw_files"],
            })
        values = {
            name: float(case[name])
            for name in ("Q", "U", "Df", "dx", "L", "H", "W", "x_fire")
        }
        if not all(math.isfinite(value) for value in values.values()):
            raise ValueError(f"{chid} 已知参数非有限")
        applicability = uncertainty_anomaly.assess_100m_applicability({
            "protocol_version": uncertainty_anomaly.PROTOCOL_VERSION,
            "L": values["L"], "W": values["W"], "H": values["H"],
            "dx": values["dx"],
            "sensor_xs": [sensor["x"] for sensor in sensors],
            "domain_censor_state": "none",
        })
        outputs.append({
            "case_id": chid, "chid": chid, "sensors": sensors,
            "Q_MW": values["Q"], "x_f": values["x_fire"], "U": values["U"],
            "Df": values["Df"], "dx": values["dx"], "L": values["L"],
            "H": values["H"], "W": values["W"],
            "T0_K": statistics.median(baselines) + 273.15,
            **applicability,
            "scientific_use_policy": OOD_SCIENTIFIC_USE,
            "observation_temperature_type": "thermocouple",
            "model_target_temperature_type": "ceiling_gas_temperature",
            "temperature_compatibility": "REQUIRES_RESPONSE_MODEL_OR_EXPLICIT_DISCREPANCY",
            "reference": case["reference"], "source_profile": str(profiles_path),
            "source_mapping": str(mapping_path), "status": WAITING_STATUS,
        })
    return outputs


def evaluation_template(inputs):
    rows = []
    for sample in inputs:
        rows.append({
            "chid": sample["chid"], "Q_true_MW": sample["Q_MW"],
            "x_f_true_m": sample["x_f"], "U_mps": sample["U"], "Df_m": sample["Df"],
            "L_m": sample["L"], "H_m": sample["H"], "W_m": sample["W"],
            "dx_m": sample["dx"], "n_sensors": len(sample["sensors"]),
            "protocol_version": sample["protocol_version"],
            "applicability_status": sample["applicability_status"],
            "ood_reasons": sample["ood_reasons"],
            "scientific_use_policy": sample["scientific_use_policy"],
            "temperature_type": sample["observation_temperature_type"],
            "Q_hat_MW": "", "Q_relative_error": "", "x_f_hat_m": "",
            "x_f_error_m": "", "temperature_reconstruction_rmse_C": "",
            "status": WAITING_STATUS, "reference": sample["reference"],
        })
    return rows


def write_outputs(output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    inputs = build_external_inputs()
    with (output_dir / "external_inversion_inputs.jsonl").open("w", encoding="utf-8") as handle:
        for sample in inputs:
            handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
    rows = evaluation_template(inputs)
    with (output_dir / "external_end_to_end_evaluation.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    return inputs, rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=STAGE7_ROOT / "02_外部端到端反演")
    args = parser.parse_args(argv)
    inputs, _ = write_outputs(args.output_dir)
    print("external inputs prepared:", ", ".join(f"{row['chid']}({len(row['sensors'])})" for row in inputs))
    print(WAITING_STATUS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
