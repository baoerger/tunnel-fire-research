"""Protect the approved G5-SR route from legacy G5 entry points."""

from __future__ import annotations

import json
from pathlib import Path


SR_PROTOCOL_APPROVED = "SR_PROTOCOL_APPROVED"


class LegacyG5RouteDisabledError(RuntimeError):
    """Raised when an obsolete Freeze-A route is used after G5-SR approval."""


def refuse_legacy_route_after_sr_approval(repo, entrypoint):
    """Fail before writes when the project has approved the G5-SR route.

    The finite-candidate scripts created the immutable A0 evidence before the
    symbolic-regression protocol was approved.  They must not be able to
    restore the obsolete ``A0 -> G6`` transition in the current project state.
    """
    repo = Path(repo).resolve()
    protocol_path = repo / "config" / "protocol_v1.json"
    if not protocol_path.is_file():
        return
    protocol = json.loads(protocol_path.read_text(encoding="utf-8-sig"))
    sr = protocol.get("g5_symbolic_regression", {})
    if sr.get("status") == SR_PROTOCOL_APPROVED:
        raise LegacyG5RouteDisabledError(
            f"{entrypoint} 是 G5-A0 历史入口，已被 SR_PROTOCOL_APPROVED 禁用；"
            "A0 仅为人工候选基准。请先运行 src.analysis.run_g5_symbolic_search，"
            "完成 G5-SR、联合重拟合并生成 A_active 后，再进入 G6。"
        )
