"""Fail-closed readiness audit before G5-SR outer-fold model selection.

This step does not select a formula.  It verifies that every frozen outer
fold has at least one shape candidate that the preceding structural and
continuous-domain audit declared eligible for later selection/refit.  The
approved execution supplement requires an unambiguous, leakage-free selected
xi-only structure in all nine outer folds; a missing fold therefore stops the
route before a shape-expansion decision can be created.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from src.fds.project_paths import PROJECT_ROOT


TRIALS = Path("reports/g5_sr_search_trials.csv")
CANDIDATES = Path("reports/g5_sr_pareto_front.csv")
FAMILIES = Path("reports/g5_sr_candidate_families.csv")
MACHINE_PROTOCOL = Path("config/symbolic_regression_v1.json")
EXECUTION_SUPPLEMENT = Path("config/g5_sr_execution_supplement_v1.json")
OUTPUT = Path("reports/g5_sr_selection_readiness.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise ValueError(f"CSV 缺少表头: {path}")
        return list(reader)


def _is_true(value: object) -> bool:
    return str(value).strip().lower() == "true"


def assess_shape_selection_readiness(
    family_rows: list[dict[str, str]], *, outer_fold_count: int
) -> dict:
    """Return the fail-closed per-fold candidate-availability assessment."""
    if outer_fold_count <= 0:
        raise ValueError("outer_fold_count 必须为正整数")

    relevant = [
        row for row in family_rows
        if row.get("campaign_id") == "shape_xi_only"
        and row.get("stage") in {"INNER_SEARCH", "OUTER_REFIT_SEARCH"}
        and _is_true(row.get("freeze_eligible"))
    ]
    per_fold = []
    for fold in range(outer_fold_count):
        rows = [
            row for row in relevant
            if int(row.get("outer_fold_index", -1)) == fold
        ]
        inner = [row for row in rows if row.get("stage") == "INNER_SEARCH"]
        refit = [row for row in rows if row.get("stage") == "OUTER_REFIT_SEARCH"]
        per_fold.append({
            "outer_fold_index": fold,
            "eligible_inner_candidate_instances": len(inner),
            "eligible_outer_refit_candidate_instances": len(refit),
            "eligible_candidate_instances_total": len(rows),
            "eligible_structure_families": len({
                row.get("structure_family_id", "") for row in rows
                if row.get("structure_family_id", "")
            }),
            "selection_candidate_available": bool(rows),
        })

    missing = [
        row["outer_fold_index"] for row in per_fold
        if not row["selection_candidate_available"]
    ]
    if missing:
        status = "SHAPE_EXPANSION_DECISION_NOT_EVALUABLE"
        next_action = "PROTOCOL_AUTHORIZATION_REQUIRED"
    else:
        status = "READY_FOR_UNIQUE_OUTER_SELECTION"
        next_action = "IMPLEMENT_AND_RUN_UNIQUE_OUTER_SELECTION"
    return {
        "status": status,
        "next_action": next_action,
        "outer_fold_count": outer_fold_count,
        "eligible_candidate_instances": len(relevant),
        "missing_outer_folds": missing,
        "per_outer_fold": per_fold,
        "can_create_shape_expansion_decision": not missing,
        "can_launch_shape_xi_q_d": False,
        "A_active_created": False,
        "lockbox_used": False,
    }


def run(repo: Path = PROJECT_ROOT, output: Path | None = None) -> dict:
    repo = Path(repo).resolve()
    machine_path = repo / MACHINE_PROTOCOL
    supplement_path = repo / EXECUTION_SUPPLEMENT
    machine = json.loads(machine_path.read_text(encoding="utf-8-sig"))
    supplement = json.loads(supplement_path.read_text(encoding="utf-8-sig"))
    expected_folds = int(machine["search_budget"]["outer_folds"]["count"])
    if expected_folds != int(
        supplement["stability_accounting"]["outer_fold_denominator"]
    ):
        raise ValueError("父协议与执行补充协议的外层折数不一致")

    paths = {
        "search_trials": repo / TRIALS,
        "pareto_front": repo / CANDIDATES,
        "candidate_families": repo / FAMILIES,
    }
    for label, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"缺少 G5-SR {label}: {path}")

    trials = _read_csv(paths["search_trials"])
    if len(trials) != int(supplement["trial_budget"]["initial_trial_count"]):
        raise ValueError("初始 trial 台账数量不完整")
    terminal = {"SUCCESS", "FAILED", "TIMEOUT", "FAILED_NO_CANDIDATES", "FAILED_INTERRUPTED"}
    if any(row.get("status") not in terminal for row in trials):
        raise ValueError("初始 trial 台账仍含非终态记录")

    family_rows = _read_csv(paths["candidate_families"])
    result = assess_shape_selection_readiness(
        family_rows,
        outer_fold_count=expected_folds,
    )
    campaign_coverage = {}
    final_denominator = int(
        machine["stability_gate"]["core_variable_support_final_seeds_denominator"]
    )
    final_threshold = int(
        machine["stability_gate"]["core_variable_support_final_seeds_min"]
    )
    for campaign_id in ("peak_q_d", "shape_xi_only"):
        eligible = [
            row for row in family_rows
            if row.get("campaign_id") == campaign_id
            and _is_true(row.get("freeze_eligible"))
        ]
        outer_folds = sorted({
            int(row["outer_fold_index"])
            for row in eligible
            if row.get("stage") in {"INNER_SEARCH", "OUTER_REFIT_SEARCH"}
        })
        final_seeds = sorted({
            int(row["seed"])
            for row in eligible if row.get("stage") == "FINAL_SEARCH"
        })
        campaign_coverage[campaign_id] = {
            "eligible_candidate_instances_all_stages": len(eligible),
            "outer_folds_with_eligible_candidate": outer_folds,
            "outer_fold_coverage": f"{len(outer_folds)}/{expected_folds}",
            "final_seeds_with_eligible_candidate": final_seeds,
            "final_seed_coverage": f"{len(final_seeds)}/{final_denominator}",
            "maximum_possible_final_support_frequency": len(final_seeds),
            "final_support_stability_threshold": final_threshold,
            "final_support_stability_still_possible": (
                len(final_seeds) >= final_threshold
            ),
        }
    result.update({
        "schema_version": "1.0",
        "stage": "G5_SR3_SELECTION_READINESS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "campaign_id": "shape_xi_only",
        "eligibility_source": (
            "reports/g5_sr_candidate_families.csv freeze_eligible=True; "
            "STRUCTURE_NUMERIC_AND_CONTINUOUS_DOMAIN_PASS_ONLY"
        ),
        "decision_rule": (
            "Every one of the 9 outer folds must have at least one leakage-free "
            "eligible xi-only candidate before a unique selected structure and "
            "held-out prediction can be produced"
        ),
        "campaign_coverage": campaign_coverage,
        "machine_protocol_sha256": _sha256(machine_path),
        "execution_supplement_sha256": _sha256(supplement_path),
        "input_artifacts": {
            label: {
                "relative_path": path.relative_to(repo).as_posix(),
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
            for label, path in paths.items()
        },
        "prohibited_without_new_authorization": [
            "create reports/g5_sr_shape_expansion_decision.json",
            "launch shape_xi_q_d conditional campaign",
            "change search seeds, budget, grammar, normalization, or physics gates",
            "create A_SR or A_active",
            "start G6",
            "read or run the lockbox",
        ],
    })
    destination = repo / (output or OUTPUT)
    if destination.exists():
        raise FileExistsError(f"选择就绪审计已存在，禁止覆盖: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(PROJECT_ROOT))
    parser.add_argument("--output", default=str(OUTPUT))
    args = parser.parse_args()
    print(json.dumps(run(args.repo, Path(args.output)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
