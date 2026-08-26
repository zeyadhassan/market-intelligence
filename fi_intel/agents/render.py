"""Static HTML brief rendering with claim-level source evidence."""

import html as html_mod

from fi_intel.agents.brief import Brief, BriefItem


def _highlight(excerpt: str, needle: str) -> str:
    """Wrap the first case-insensitive occurrence of needle in ``mark``."""
    escaped = html_mod.escape(excerpt)
    index = excerpt.lower().find(needle.lower())
    if index < 0:
        return escaped
    return (
        escaped[:index]
        + "<mark>"
        + escaped[index : index + len(needle)]
        + "</mark>"
        + escaped[index + len(needle) :]
    )


def _render_empty(brief: Brief) -> str:
    parts: list[str] = []
    if brief.coverage_complete:
        parts.append(
            "<p class='empty'>No supported material developments for this desk "
            "in the completed coverage window.</p>"
        )
    else:
        parts.append(
            "<p class='empty'>Brief incomplete: research capacity was exhausted "
            "before all eligible signals were assessed.</p>"
        )
    if brief.abstained_signals:
        parts.append(
            f"<p class='meta'>Evidence-insufficient signals: {len(brief.abstained_signals)}.</p>"
        )
    if brief.deferred_signals:
        parts.append(
            f"<p class='meta'>Signals deferred by capacity: {len(brief.deferred_signals)}.</p>"
        )
    return "".join(parts)


def _render_claims(item: BriefItem) -> str:
    escape = html_mod.escape
    if not item.opportunity.claims:
        return f"<p>{escape(item.opportunity.summary)}</p>"
    parts = ["<h3>Supported claims</h3><ul>"]
    for claim in item.opportunity.claims:
        parts.append(
            f"<li><strong>{escape(str(claim.claim_type).replace('_', ' '))}:</strong> "
            f"{escape(claim.text)} "
            f"<span class='meta'>(confidence {claim.confidence:.0%})</span></li>"
        )
    parts.append("</ul>")
    return "".join(parts)


def _render_evidence(item: BriefItem) -> str:
    if not item.evidence:
        return ""
    escape = html_mod.escape
    parts = ["<h3>Evidence</h3><ul>"]
    for evidence in item.evidence:
        shown = _highlight(evidence.excerpt, item.signal.entity_name)
        if evidence.source_url:
            reference = (
                f"<a href='{escape(evidence.source_url)}' rel='noopener noreferrer'>"
                f"{escape(evidence.evidence_id)}</a>"
            )
        else:
            reference = f"<code>{escape(evidence.evidence_id)}</code>"
        parts.append(f"<li>{reference}: {shown}</li>")
    parts.append("</ul>")
    return "".join(parts)


def _render_item(item: BriefItem) -> str:
    escape = html_mod.escape
    return "".join(
        [
            "<div class='item'>",
            f"<h2>{escape(item.opportunity.title)}</h2>",
            f"<p class='meta'>{escape(item.signal.entity_name)} - pattern "
            f"{escape(item.signal.pattern)} (priority {item.signal.priority})</p>",
            _render_claims(item),
            f"<p class='meta'><em>Falsifier:</em> {escape(item.opportunity.falsifier)}</p>",
            _render_evidence(item),
            "</div>",
        ]
    )


def _render_funnel(brief: Brief) -> str:
    looked_at = (
        len(brief.items)
        + len(brief.abstained_signals)
        + len(brief.deferred_signals)
        + len(brief.unresearched_signals)
    )
    return (
        "<p class='meta coverage-funnel'>"
        f"Coverage funnel: looked at {looked_at} situations; published {len(brief.items)}; "
        f"abstained for insufficient citable evidence {len(brief.abstained_signals)}; "
        f"deferred on capacity {len(brief.deferred_signals)}; "
        f"below triage threshold {len(brief.unresearched_signals)}."
        "</p>"
    )


def render_html(brief: Brief) -> str:
    """Render the brief to a standalone HTML page."""
    escape = html_mod.escape
    date = brief.as_of.date().isoformat()
    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>FI Brief - {escape(brief.desk)} - {date}</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:60em;margin:2em auto;color:#111}"
        ".item{border:1px solid #ccc;border-radius:6px;padding:1em;margin:1em 0}"
        ".meta{color:#555;font-size:.9em}mark{background:#ffe58f}"
        ".empty{color:#555}</style></head><body>",
        f"<h1>FI Daily Brief - {escape(brief.desk)}</h1>",
        f"<p class='meta'>As of {date}</p>",
    ]

    if brief.nothing_material or not brief.items:
        parts.extend([_render_empty(brief), _render_funnel(brief), "</body></html>"])
        return "".join(parts)

    parts.extend(_render_item(item) for item in brief.items)
    parts.append(_render_funnel(brief))
    if brief.unresearched_signals or brief.deferred_signals or brief.abstained_signals:
        parts.append(
            "<p class='meta'>"
            f"Below-threshold: {len(brief.unresearched_signals)}; "
            f"deferred: {len(brief.deferred_signals)}; "
            f"evidence-insufficient: {len(brief.abstained_signals)}.</p>"
        )
    parts.extend(
        [
            "<footer class='meta'>Generated by fi-intel. Research capacity used: "
            f"{brief.research_usage.calls} calls, "
            f"{brief.research_usage.total_tokens:,} tokens, "
            f"{brief.research_usage.latency_ms / 1000.0:.1f}s model latency, "
            f"${brief.research_usage.cost_usd:.2f} metered spend.</footer>",
            "</body></html>",
        ]
    )
    return "".join(parts)
