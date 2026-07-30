"""批量执行回传质量检查、准稳态识别、时间平均和噪声分量汇总。"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path


STAGE3_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = STAGE3_ROOT.parent
STAGE1_SRC = PROJECT_ROOT / "阶段一" / "src"
sys.path.insert(0, str(STAGE1_SRC))

import check_fds_results  # noqa: E402
import fds_io  # noqa: E402
import quasi_steady_detect  # noqa: E402
import time_average_bootstrap  # noqa: E402

import case_registry


DEFAULT_RUN_ROOT = PROJECT_ROOT / "阶段一" / "outputs" / "runs"
DEFAULT_OUTDIR = STAGE3_ROOT / "outputs" / "analysis" / "batch"
COMPLETION_FIELDS = (
    "chid", "subset", "result_version", "quality_status", "steady_status",
    "time_average_status", "overall_status", "adoption_status", "sealed",
    "recompute_required", "failure_reason",
)
STEADY_FIELDS = (
    "chid", "status", "reason", "t_steady_start", "t_steady_end", "avg_t0",
    "avg_t1", "enthalpy_flux_criterion", "window_s", "min_steady_s",
    "thr_hrr_rel", "thr_tmax_C_per_s", "thr_rep_C_per_s",
    "thr_backflow_m_per_s", "thr_enthalpy_rel_per_s",
)
NOISE_COMPONENT_FIELDS = (
    "source_chid", "parent_chid", "sensor", "result_version", "mean_temperature_C",
    "temporal_sd_C", "n_samples", "t0_s", "t1_s",
)
NOISE_SUMMARY_FIELDS = (
    "parent_chid", "sensor", "n_seed_runs", "temporal_sd_median_C",
    "repeat_seed_mean_sd_C", "status", "note",
)


def _write_rows(path, fields, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _steady_row(chid, run_root, window_s, min_steady, avg_duration,
                thr_hrr, thr_tmax, thr_rep, thr_backflow, thr_enthalpy):
    start, end, info = quasi_steady_detect.detect(
        chid, str(run_root), window_s, thr_hrr, thr_tmax, thr_rep,
        thr_backflow, thr_enthalpy, min_steady,
    )
    avg_end = min(end, start + avg_duration) if start is not None else None
    return {
        "chid": chid, "status": info["status"], "reason": info["reason"],
        "t_steady_start": start, "t_steady_end": end, "avg_t0": start,
        "avg_t1": avg_end,
        "enthalpy_flux_criterion": info.get("enthalpy_criterion", "NOT_EVALUATED"),
        "window_s": window_s, "min_steady_s": min_steady,
        "thr_hrr_rel": thr_hrr, "thr_tmax_C_per_s": thr_tmax,
        "thr_rep_C_per_s": thr_rep, "thr_backflow_m_per_s": thr_backflow,
        "thr_enthalpy_rel_per_s": thr_enthalpy,
    }


def temporal_noise_components(run_root, registry_row, t0, t1, result_version):
    chid = registry_row["chid"]
    times, series, units = fds_io.read_devc(str(run_root), chid)
    if times is None:
        raise ValueError(f"{chid}: 缺少 DEVC CSV")
    series = fds_io.normalize_units(series, units)
    indices = time_average_bootstrap.validate_window(times, t0, t1)
    rows = []
    for sensor, values in sorted(series.items()):
        if not (sensor.startswith("T_") and sensor[2:].isdigit()):
            continue
        segment = [values[index] for index in indices if math.isfinite(values[index])]
        if len(segment) < 4:
            raise ValueError(f"{chid}/{sensor}: 稳态窗口有限温度少于 4 点")
        rows.append({
            "source_chid": chid,
            "parent_chid": registry_row.get("parent_chid") or chid,
            "sensor": sensor, "result_version": result_version,
            "mean_temperature_C": f"{statistics.fmean(segment):.6f}",
            "temporal_sd_C": f"{statistics.stdev(segment):.6f}",
            "n_samples": len(segment), "t0_s": f"{t0:.6f}", "t1_s": f"{t1:.6f}",
        })
    if not rows:
        raise ValueError(f"{chid}: 没有 T_* 温度设备")
    return rows


def summarize_noise_components(component_rows, min_seed_runs=2):
    if min_seed_runs < 2:
        raise ValueError("min_seed_runs 至少为 2")
    groups = defaultdict(list)
    for row in component_rows:
        parent = str(row.get("parent_chid") or row.get("source_chid") or "").strip()
        sensor = str(row.get("sensor") or "").strip()
        source = str(row.get("source_chid") or "").strip()
        if not parent or not sensor or not source:
            raise ValueError("噪声分量缺少 parent_chid/source_chid/sensor")
        try:
            temporal_sd = float(row["temporal_sd_C"])
            mean_temperature = float(row["mean_temperature_C"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{parent}/{sensor}: 噪声分量数值无效") from exc
        if not math.isfinite(temporal_sd) or temporal_sd < 0 or not math.isfinite(mean_temperature):
            raise ValueError(f"{parent}/{sensor}: 噪声分量必须有限且标准差非负")
        groups[(parent, sensor)].append((source, temporal_sd, mean_temperature))
    summaries = []
    for (parent, sensor), values in sorted(groups.items()):
        sources = sorted({value[0] for value in values})
        temporal_sd = statistics.median(value[1] for value in values)
        source_means = []
        for source in sources:
            source_means.append(statistics.fmean(value[2] for value in values if value[0] == source))
        ready = len(source_means) >= min_seed_runs
        repeat_sd = statistics.stdev(source_means) if ready else None
        summaries.append({
            "parent_chid": parent, "sensor": sensor, "n_seed_runs": len(source_means),
            "temporal_sd_median_C": f"{temporal_sd:.6f}",
            "repeat_seed_mean_sd_C": "" if repeat_sd is None else f"{repeat_sd:.6f}",
            "status": "READY_FOR_THRESHOLD_REVIEW" if ready else "WAITING_SEED_REPEATS",
            "note": (
                "仅汇总波动分量；Delta T_noise 最终阈值不得由软件测试自动定稿"
                if ready else "重复种子不足，不能确定 Delta T_noise"
            ),
        })
    return summaries


def run_pipeline(registry_path=case_registry.DEFAULT_OUT, run_root=DEFAULT_RUN_ROOT,
                 outdir=DEFAULT_OUTDIR, purpose="model_development",
                 confirm_final_evaluation=False, result_version="run_v1",
                 n_boot=2000, seed=20260729, window_s=20.0, min_steady=30.0,
                 avg_duration=30.0, thr_hrr=0.05, thr_tmax=0.10,
                 thr_rep=0.10, thr_backflow=0.05, thr_enthalpy=0.05):
    if n_boot < 20:
        raise ValueError("n_boot 至少为 20")
    if min(window_s, min_steady, avg_duration) <= 0:
        raise ValueError("窗口和持续时间必须为正")
    if not str(result_version).strip():
        raise ValueError("result_version 不能为空")
    registry_rows = case_registry.read_registry(registry_path)
    selected = case_registry.select_cases(
        registry_rows, purpose, confirm_final_evaluation=confirm_final_evaluation,
    )
    run_root = Path(run_root)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "time_average").mkdir(parents=True, exist_ok=True)
    quality_rows = []
    steady_rows = []
    average_rows = []
    noise_components = []
    completion_rows = []
    for case_index, registry_row in enumerate(selected):
        chid = registry_row["chid"]
        case_dir = run_root / chid
        quality = check_fds_results.check_case(case_dir)
        quality_rows.append(quality)
        completion = {
            "chid": chid, "subset": registry_row["subset"],
            "result_version": result_version, "quality_status": quality["status"],
            "steady_status": "NOT_RUN", "time_average_status": "NOT_RUN",
            "overall_status": "", "adoption_status": "NOT_ADOPTED",
            "sealed": "yes" if registry_row["subset"] == "independent_test" else "no",
            "recompute_required": "no", "failure_reason": "",
        }
        if not case_dir.is_dir():
            completion.update({
                "overall_status": "WAITING_RETURN", "failure_reason": "结果文件夹尚未回传",
            })
            completion_rows.append(completion)
            continue
        if quality["status"] == "REVIEW":
            completion.update({
                "overall_status": "NEEDS_REVIEW", "failure_reason": quality["issues"],
            })
            completion_rows.append(completion)
            continue
        if quality["status"] != "PASS":
            completion.update({
                "overall_status": "NEEDS_RERUN_OR_REPAIR", "recompute_required": "yes",
                "failure_reason": quality["issues"],
            })
            completion_rows.append(completion)
            continue
        steady = _steady_row(
            chid, run_root, window_s, min_steady, avg_duration, thr_hrr,
            thr_tmax, thr_rep, thr_backflow, thr_enthalpy,
        )
        steady_rows.append(steady)
        completion["steady_status"] = steady["status"]
        if steady["status"] != "PASS":
            completion.update({
                "overall_status": "NEEDS_STEADY_EXTENSION_OR_REVIEW",
                "recompute_required": "yes", "failure_reason": steady["reason"],
            })
            completion_rows.append(completion)
            continue
        try:
            rows = time_average_bootstrap.average_case(
                str(run_root), chid, float(steady["avg_t0"]), float(steady["avg_t1"]),
                n_boot=n_boot, seed=seed + case_index * 10000,
            )
            average_rows.extend(rows)
            time_average_bootstrap._write_rows(
                outdir / "time_average" / f"{chid}_Tbar_bootstrap.csv", rows
            )
            noise_components.extend(temporal_noise_components(
                run_root, registry_row, float(steady["avg_t0"]),
                float(steady["avg_t1"]), result_version,
            ))
        except (OSError, ValueError) as exc:
            completion.update({
                "time_average_status": "FAIL", "overall_status": "POSTPROCESS_FAIL",
                "failure_reason": str(exc),
            })
            completion_rows.append(completion)
            continue
        completion["time_average_status"] = "PASS"
        if registry_row["subset"] == "independent_test":
            completion["overall_status"] = "SEALED_READY_FOR_FINAL_EVALUATION"
        else:
            completion["overall_status"] = "READY_FOR_EXPLICIT_ADOPTION"
        completion["adoption_status"] = "PENDING_EXPLICIT_ADOPTION"
        completion_rows.append(completion)

    _write_rows(outdir / "quality" / "result_check.csv",
                check_fds_results.SUMMARY_FIELDS, quality_rows)
    _write_rows(outdir / "steady" / "steady_windows.csv", STEADY_FIELDS, steady_rows)
    time_average_bootstrap._write_rows(
        outdir / "time_average" / "time_average_database.csv", average_rows
    )
    _write_rows(outdir / "noise" / "noise_components.csv",
                NOISE_COMPONENT_FIELDS, noise_components)
    noise_summary = summarize_noise_components(noise_components) if noise_components else []
    _write_rows(outdir / "noise" / "noise_summary.csv",
                NOISE_SUMMARY_FIELDS, noise_summary)
    _write_rows(outdir / "database_completion.csv", COMPLETION_FIELDS, completion_rows)
    return {
        "completion": completion_rows, "quality": quality_rows, "steady": steady_rows,
        "time_average": average_rows, "noise_components": noise_components,
        "noise_summary": noise_summary,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", default=str(case_registry.DEFAULT_OUT))
    parser.add_argument("--rundir", default=str(DEFAULT_RUN_ROOT))
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR))
    parser.add_argument("--purpose", choices=sorted(case_registry.PURPOSE_TO_SUBSET),
                        default="model_development")
    parser.add_argument("--confirm-final-evaluation", action="store_true")
    parser.add_argument("--result-version", default="run_v1")
    parser.add_argument("--n-boot", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20260729)
    parser.add_argument("--window-s", type=float, default=20.0)
    parser.add_argument("--min-steady", type=float, default=30.0)
    parser.add_argument("--avg-duration", type=float, default=30.0)
    args = parser.parse_args()
    try:
        result = run_pipeline(
            args.registry, args.rundir, args.outdir, args.purpose,
            args.confirm_final_evaluation, args.result_version, args.n_boot,
            args.seed, args.window_s, args.min_steady, args.avg_duration,
        )
    except (OSError, ValueError, PermissionError) as exc:
        parser.error(str(exc))
    counts = Counter(row["overall_status"] for row in result["completion"])
    print("批量状态:", dict(counts))
    print(f"完成率: {sum('READY' in row['overall_status'] for row in result['completion'])}/"
          f"{len(result['completion'])}")
    print(f"结果: {Path(args.outdir) / 'database_completion.csv'}")


if __name__ == "__main__":
    main()
