"""Machine-readable + human-readable research report rendering."""

from __future__ import annotations

from typing import Any, Mapping


def _f(value: Any, digits: int = 3) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def render_markdown_report(report: Mapping[str, Any]) -> str:
    """Render a research summary as Markdown. Mirrors the JSON artifact."""
    lines = [
        f"# Strategy Research Report — {report['family_name']}",
        "",
        f"- family_id: `{report['family_id']}`",
        f"- source_commit: `{report['source_commit']}`",
        f"- dataset_hash: `{report['dataset_hash']}`",
        *([f"- dataset_fingerprint: `{report['dataset_fingerprint']}`"] if report.get("dataset_fingerprint") else []),
        *(
            [
                f"- run_id: `{report['run_id']}`",
                f"- data_preflight_verdict: {report.get('data_preflight_verdict', 'n/a')}"
                f" (accepted warnings: {len(report.get('accepted_warnings', []))})",
            ]
            if report.get("run_id")
            else []
        ),
        f"- universe: {', '.join(report['universe'])}",
        f"- observations: {report['n_observations']}",
        f"- folds: {len(report['folds'])} (embargo={report['embargo']}, anchored={report['anchored']})",
        f"- trial_count: {report['trial_count']}",
        f"- generated_at: {report['generated_at']}",
        f"- evidence_ceiling: **{report['evidence_ceiling']}**",
        "",
    ]
    if report.get("known_biases"):
        lines.append("## Known biases")
        lines.extend(f"- {b}" for b in report["known_biases"])
        lines.append("")
    lines.append("## Promotion summary")
    promo = report.get("promotion_summary", {})
    lines.append(f"- APPROVED: {promo.get('approved', []) or 'none'}")
    lines.append(f"- REJECTED: {promo.get('rejected', []) or 'none'}")
    lines.append("")
    lines.append("## Per-configuration evidence")
    lines.append("")
    header = (
        "| config | trades | expectancy | Sharpe | MaxDD | profit factor | "
        "PSR | DSR | PBO | cost 2x survives | promotion |"
    )
    lines.append(header)
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for cfg in report.get("config_reports", []):
        m = cfg.get("metrics", {})
        sig = cfg.get("significance", {})
        pbo = cfg.get("pbo", {})
        cs = cfg.get("cost_stress", {})
        promo_cfg = cfg.get("promotion", {})
        row = "| {cfg} | {trades} | {exp} | {sharpe} | {mdd} | {pf} | {psr} | {dsr} | {pbo} | {stress} | {promo} |"
        lines.append(
            row.format(
                cfg=cfg.get("config_id"),
                trades=int(m.get("trade_count", 0)),
                exp=_f(m.get("expectancy", 0.0), 2),
                sharpe=_f(m.get("sharpe", 0.0)),
                mdd=_f(m.get("max_drawdown", 0.0)),
                pf=_f(m.get("profit_factor", 0.0)),
                psr=_f(sig.get("psr", {}).get("psr", 0.0)),
                dsr=_f(sig.get("dsr", {}).get("dsr", 0.0)),
                pbo=_f(pbo.get("pbo", 1.0)),
                stress=str(cs.get("survives_2x", False)),
                promo=promo_cfg.get("status", "UNKNOWN"),
            )
        )
    lines.append("")
    for cfg in report.get("config_reports", []):
        promo_cfg = cfg.get("promotion", {})
        reasons = promo_cfg.get("reasons", [])
        if reasons:
            lines.append(f"### {cfg.get('config_id')} rejection reasons")
            lines.extend(f"- {r}" for r in reasons)
            lines.append("")
    fam = report.get("family_diagnostics", {})
    if fam.get("white_reality_check"):
        wrc = fam["white_reality_check"]
        lines.append("## White reality check")
        lines.append(f"- p_value: {_f(wrc.get('p_value'))}")
        lines.append(f"- observed best mean excess: {_f(wrc.get('observed_best_mean_excess'), 6)}")
        lines.append("")
    return "\n".join(lines)
