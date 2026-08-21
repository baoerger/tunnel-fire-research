"""Finalize the passed G5 candidate as freeze A and update protocol state."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from src.fds import tunnel_config as cfg
from src.fds.project_paths import PROJECT_ROOT


def _read_csv(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        return [dict(row) for row in csv.DictReader(stream)]


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_manifest(repo):
    rows = _read_csv(repo / "reports" / "g5_database_manifest.csv")
    if len(rows) != 9:
        raise ValueError(f"G5 派生表应为 9 张，实际 {len(rows)}")
    for row in rows:
        path = repo / row["relative_path"]
        if not path.is_file() or _sha256(path) != row["sha256"]:
            raise ValueError(f"G5 派生表哈希失配: {row['relative_path']}")
    return rows


def _analysis_markdown(decision, peak_evidence, shape_summary, systematic):
    metrics = decision["selected_outer_metrics"]
    peak = decision["final_peak_parameters"]
    shape = decision["final_shape_parameters"]
    size = peak_evidence[0]
    s3 = decision["s3_evidence"]
    return rf"""# G5 正向公式选择与冻结 A

## 结论

G5 通过父组留一的 9 折外层验证，正向门为 `PASS`。峰值选择
`M1`（功率+火源尺寸），形状选择 `S1`（平滑单区幂律），组合为
`M1_S1`。公式、参数、适用域和误差模型封存在
`freezes/A/forward_model_v1.json`。

## 冻结公式

\[
\frac{{\Delta T_p}}{{T_0}}
= {peak['C_m']:.6f}
\left(\frac{{Q_{{actual}}}}{{27\,\mathrm{{MW}}}}\right)^{{{peak['a']:.6f}}}
\left(\frac{{D_f}}{{{peak['D_ref_m']:.6f}\,\mathrm{{m}}}}\right)^{{{peak['b_d']:.6f}}}
\]

\[
\Theta(\xi)=\left[1+({shape['A_s']:.6f}\xi)^2\right]^{{-{shape['p_s']:.6f}/2}},
\qquad
\Delta T(x)=\Delta T_p\Theta\!\left(\frac{{|x-x_f|}}{{5\,\mathrm{{m}}}}\right).
\]

这里的 `Q_actual` 是 60 s 准稳态窗实际平均 HRR，不是请求值。

## 候选取舍

- 尺寸指数 `b_d={peak['b_d']:.3f}`，父组 Bootstrap 95% 区间为
  `[{float(size['b_d_cluster_bootstrap_ci95_low']):.3f}, {float(size['b_d_cluster_bootstrap_ci95_high']):.3f}]`，
  整体位于 `[-0.10,0.10]` 无实质效应区间之外；9 个外层拟合方向全部一致，因此保留 M1。
- S2 拉伸指数的折外曲线 NRMSE 明显高于 S1，且参数触及边界，拒绝。
- S3 相对 S1 的平均 NLL 改善只有 `{s3['s3_mean_nll_relative_improvement']:.2%}`，
  中位 NRMSE 改善只有 `{s3['s3_median_nrmse_improvement']:.2%}`，且外层参数触界；
  未达到 10%/2 个百分点的结构门，因此不声称两区机制。

## 折外性能

| 指标 | 结果 | 门限 |
|---|---:|---:|
| 峰值相对误差中位数 | {metrics['peak_relative_error_median']:.2%} | ≤15% |
| 峰值相对误差 90% 分位 | {metrics['peak_relative_error_p90']:.2%} | ≤25% |
| 曲线 NRMSE 中位数 | {metrics['curve_nrmse_median']:.2%} | ≤15% |
| 曲线 NRMSE 90% 分位 | {metrics['curve_nrmse_p90']:.2%} | ≤25% |
| 90% 预测区间覆盖率 | {metrics['pi90_coverage']:.2%} | ≥80% |
| 最不利父组曲线 NRMSE | {metrics['worst_parent_curve_nrmse']:.2%} | 单独报告 |

非负、峰值归一和单调约束 100% 满足；`log(Q)`、`log(Df)`、`xf-50`
和左右分支的 Holm 校正父组 Bootstrap 系统残差门全部通过。

## 边界

公式只适用于本项目固定的水平、无机械纵向风、两端开放、
`L×W×H=100×10×5 m`、单一撞顶/近撞顶火焰层级，且 `0≤xi≤5`。
峰值误差带仍保留 13% 网格不确定性，不声称严格网格无关。
锁箱仍未运行、未读取；下一步进入 G6 直接反演。
"""


def finalize(repo):
    repo = Path(repo).resolve()
    decision_path = repo / "reports" / "g5_forward_model_decision.json"
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    if decision.get("decision") != "PASS_FREEZE_A":
        raise ValueError("G5 正向门未通过，不得冻结 A")
    if decision.get("lockbox_status") != "SEALED_UNREAD_UNRUN":
        raise ValueError("锁箱已被触及，不得冻结 A")
    manifest = _verify_manifest(repo)
    metrics = _read_csv(repo / "derived" / "development" / "case_metrics.csv")
    curves = _read_csv(repo / "derived" / "development" / "curve_points.csv")
    peak_evidence = _read_csv(repo / "reports" / "g5_peak_model_evidence.csv")
    shape_summary = _read_csv(repo / "reports" / "g5_shape_summary.csv")
    systematic = _read_csv(repo / "reports" / "g5_systematic_residuals.csv")
    if any(row["status"] != "PASS" for row in systematic):
        raise ValueError("系统残差门未通过")

    report_path = repo / "reports" / "g5_forward_model_analysis.md"
    report_path.write_text(
        _analysis_markdown(decision, peak_evidence, shape_summary, systematic),
        encoding="utf-8",
    )
    q_values = [float(row["Q_actual_kW"]) for row in metrics]
    d_values = [float(row["Df_discrete_m"]) for row in metrics]
    x_values = [float(row["xf_actual_m"]) for row in metrics]
    forward_callable = {
        "callable": "src.analysis.forward_temperature.forward_temperature",
        "peak_model": decision["selected_peak_model"],
        "shape_model": decision["selected_shape_model"],
        "peak_parameters": decision["final_peak_parameters"],
        "shape_parameters": decision["final_shape_parameters"],
        "prediction_error": decision["prediction_error"],
        "flame_regime_layer": "CEILING_IMPINGING_OR_NEAR_IMPINGING",
        "support": {
            "Q_actual_kW": [min(q_values), max(q_values)],
            "Df_discrete_m": [min(d_values), max(d_values)],
            "xf_actual_m": [min(x_values), max(x_values)],
            "H_eff_m": cfg.H,
            "xi_h": 5.0,
            "T0_K": cfg.T_AMBIENT_K,
            "mechanical_longitudinal_wind_m_s": 0.0,
            "geometry_m": {"L": cfg.L, "W": cfg.W, "H": cfg.H},
        },
    }
    evidence_paths = [
        "reports/g5_forward_model_decision.json",
        "reports/g5_candidate_summary.csv", "reports/g5_shape_summary.csv",
        "reports/g5_peak_model_evidence.csv", "reports/g5_systematic_residuals.csv",
        "reports/g5_final_parameters.csv", "reports/g5_database_manifest.csv",
        "reports/g5_forward_model_analysis.md",
        "src/analysis/analyze_g5_forward_models.py",
        "src/analysis/forward_temperature.py",
        "src/analysis/finalize_g5_freeze_a.py",
    ]
    freeze = {
        "schema_version": "1.0", "freeze_id": "forward_model_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "decision": "PASS_FREEZE_A", "selected_model_id": decision["selected_model_id"],
        "formula": {
            "peak": "DeltaT_p/T0=C_m*(Q_actual/Q_ref)^a*(Df/D_ref)^b_d",
            "shape": "Theta(xi)=[1+(A_s*xi)^2]^(-p_s/2)",
            "combined": "DeltaT(x)=DeltaT_p*Theta(abs(x-xf)/H_eff)",
        },
        "forward_callable": forward_callable,
        "outer_validation": decision["selected_outer_metrics"],
        "candidate_decisions": {
            "M0": "REJECT_SIZE_TERM_IS_MATERIAL_AND_STABLE",
            "M1": "SELECT",
            "S1": "SELECT",
            "S2": "REJECT_WORSE_OUTER_ERROR_AND_BOUND_HIT",
            "S3": decision["s3_evidence"]["s3_status"],
        },
        "uncertainty_contract": {
            "time_block_bootstrap": "G4 pointwise synchronized blocks",
            "seed_difference": "retained in development repeats and model residual",
            "spatial_covariance": "exponential kernel plus shrinkage nugget",
            "peak_grid_uncertainty_fraction": 0.13,
            "strict_grid_independence_claim_allowed": False,
        },
        "development_runs": sorted({row["run_chid"] for row in metrics}),
        "development_parent_groups": sorted({row["parent_case_id"] for row in metrics}),
        "database_sha256": {row["relative_path"]: row["sha256"] for row in manifest},
        "evidence_sha256": {path: _sha256(repo / path) for path in evidence_paths},
        "inverse_search_space": {
            "Q_actual_kW": forward_callable["support"]["Q_actual_kW"],
            "xf_actual_m": forward_callable["support"]["xf_actual_m"],
            "Df_is_known_not_inverted": True,
            "xi_h": 5.0,
        },
        "lockbox_status": "SEALED_UNREAD_UNRUN",
        "next_action": "G6_DIRECT_INVERSION",
    }
    freeze_path = repo / "freezes" / "A" / "forward_model_v1.json"
    freeze_path.parent.mkdir(parents=True, exist_ok=True)
    freeze_path.write_text(json.dumps(freeze, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")

    protocol_path = repo / "config" / "protocol_v1.json"
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    protocol["updated_at"] = datetime.now(timezone.utc).astimezone().isoformat()
    protocol["status"] = "G5_PASS_FREEZE_A_G6_DIRECT_INVERSION_REQUIRED"
    protocol["g5_forward_model"] = {
        "decision": "PASS_FREEZE_A",
        "selected_model_id": decision["selected_model_id"],
        "outer_validation": decision["selected_outer_metrics"],
        "freeze_file": "freezes/A/forward_model_v1.json",
        "analysis": "reports/g5_forward_model_analysis.md",
        "candidate_summary": "reports/g5_candidate_summary.csv",
        "systematic_residuals": "reports/g5_systematic_residuals.csv",
        "lockbox_status": "SEALED_UNREAD_UNRUN",
        "next_action": "G6_DIRECT_INVERSION",
    }
    protocol_path.write_text(json.dumps(protocol, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    return freeze


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(PROJECT_ROOT))
    args = parser.parse_args()
    print(json.dumps(finalize(args.repo), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
