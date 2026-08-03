"""Build a self-contained report the owner can save, print or send on.

The point of this file is that it leaves the app. Someone scans their pack on a
phone, then forwards the result to a garage, a recycler or an insurer, who
needs to see the workings rather than just the headline. So the report carries
both: the plain-language answer at the top, and the full audit trail beneath.

The output is a single HTML file with no external references, which means it
opens anywhere, prints to PDF from any browser, and can be attached to a
message without anything breaking.

The renderer works from the *serialised* valuation -- the same payload the API
returns -- rather than from a live object. That means a report rebuilt months
later from a stored record is byte-for-byte what the owner was originally
shown, and nothing can appear in the report that is not also in the API.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone

from typing import Any

from .serialisation import valuation_to_dict
from .valuation.models import ResidualValuation

_STYLE = """
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 28px 20px 48px; background: #fff; color: #16191d;
    font: 15px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    max-width: 780px; margin-inline: auto;
  }
  h1 { font-size: 1.5rem; margin: 0 0 4px; letter-spacing: -0.02em; }
  h2 { font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.08em;
       color: #5d6873; margin: 30px 0 10px; font-weight: 600; }
  .meta { color: #5d6873; font-size: 0.88rem; margin: 0 0 24px; }
  .headline {
    border: 1px solid #dee2e7; border-left: 4px solid #0b6b4f; border-radius: 8px;
    padding: 20px 22px; background: #f7faf9; margin-bottom: 8px;
  }
  .headline.cost { border-left-color: #a3352c; background: #fdf8f7; }
  .amount { font-size: 2.5rem; font-weight: 680; letter-spacing: -0.03em;
            color: #0b6b4f; line-height: 1.1; font-variant-numeric: tabular-nums; }
  .amount.cost { color: #a3352c; }
  .said { font-size: 1.02rem; margin: 10px 0 0; }
  .band { color: #5d6873; font-size: 0.9rem; margin: 12px 0 0; }
  .facts { margin: 16px 0 0; color: #5d6873; font-size: 0.9rem; }
  table { width: 100%; border-collapse: collapse; font-size: 0.88rem; margin-bottom: 6px; }
  th, td { text-align: left; padding: 7px 8px; border-bottom: 1px solid #e6e9ed;
           vertical-align: top; }
  th { color: #5d6873; font-weight: 600; font-size: 0.74rem;
       text-transform: uppercase; letter-spacing: 0.05em; }
  td.num, th.num { text-align: right; white-space: nowrap;
                   font-variant-numeric: tabular-nums; }
  tr.total td { font-weight: 660; background: #f2f4f6; }
  tr.out td { color: #7a828b; }
  .detail { color: #6b7480; font-size: 0.8rem; }
  ul { padding-left: 20px; margin: 0; }
  li { margin-bottom: 7px; }
  .note { border: 1px solid #e9dcb0; background: #fdf7e6; border-radius: 7px;
          padding: 11px 14px; margin-bottom: 8px; font-size: 0.88rem; }
  .wear { border: 1px solid #dee2e7; border-radius: 8px; padding: 16px 18px; }
  .chip { display: inline-block; padding: 3px 10px; border-radius: 999px;
          font-size: 0.76rem; font-weight: 640; letter-spacing: 0.02em;
          border: 1px solid; }
  .chip.good { color: #0b6b4f; border-color: #b8dccd; background: #eef7f3; }
  .chip.fair { color: #7a5b12; border-color: #e9dcb0; background: #fdf7e6; }
  .chip.weak { color: #a3352c; border-color: #eccac5; background: #fdf3f2; }
  .wear p { margin: 11px 0 0; }
  .wear .outlook { font-weight: 560; }
  .chart { width: 100%; height: auto; margin-top: 14px; display: block; }
  .legend { color: #6b7480; font-size: 0.78rem; margin-top: 6px; }
  .legend .key { display: inline-block; width: 22px; height: 0; margin-right: 5px;
                 vertical-align: middle; border-top: 2px solid; }
  footer { margin-top: 34px; padding-top: 16px; border-top: 1px solid #e6e9ed;
           color: #6b7480; font-size: 0.82rem; }
  @media print {
    body { padding: 0; max-width: none; }
    h2 { break-after: avoid; }
    table { break-inside: auto; }
    tr { break-inside: avoid; }
  }
"""


def _e(value: object) -> str:
    """HTML-escape a value. Everything reaching the document goes through this."""
    return html.escape(str(value), quote=True)


def _money(value: dict[str, Any] | None) -> str:
    """Read a serialised Money's display string."""
    return (value or {}).get("formatted", "")


def build_html_report(
    valuation: ResidualValuation | dict[str, Any],
    *,
    include_technical: bool = True,
) -> str:
    """Render a valuation as a standalone HTML document.

    Args:
        valuation: A live result, or the serialised payload of a stored one.
        include_technical: Keep the full audit trail. Turn it off for a
            summary a non-specialist can read at a glance.
    """
    payload = (
        valuation
        if isinstance(valuation, dict)
        else valuation_to_dict(valuation)
    )

    plain = payload.get("plain", {})
    battery = payload.get("battery", {})
    bom = payload.get("bill_of_materials", {})
    residual = payload.get("residual_value", {})
    is_cost = float(residual.get("amount", 0.0)) < 0
    generated = _generated_on(payload)

    parts: list[str] = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>Battery value report - {_e(battery.get('label', 'battery'))}</title>",
        f"<style>{_STYLE}</style></head><body>",
        f"<h1>{_e(battery.get('label', 'Battery'))}</h1>",
        f'<p class="meta">Battery value report &middot; {_e(generated)}'
        + (
            f" &middot; reference {_e(payload['reference'])}"
            if payload.get("reference")
            else ""
        )
        + "</p>",
    ]

    # --- the answer -------------------------------------------------------
    shown = _money(residual).replace("-", "") if is_cost else _money(residual)
    parts.append(f'<div class="headline{" cost" if is_cost else ""}">')
    parts.append(f'<div class="amount{" cost" if is_cost else ""}">{_e(shown)}</div>')
    parts.append(f'<p class="said">{_e(plain.get("headline", ""))}</p>')

    value_range = payload.get("value_range")
    if value_range and not is_cost:
        parts.append(
            '<p class="band">Most likely between '
            f"{_e(_money(value_range.get('low')))} and "
            f"{_e(_money(value_range.get('high')))}.</p>"
        )

    confidence = plain.get("confidence", {})
    parts.append(
        f'<p class="band"><strong>{_e(confidence.get("label", ""))}.</strong> '
        f"{_e(confidence.get('explanation', ''))}</p>"
    )
    parts.append(
        '<p class="facts">'
        f"{_e(battery.get('rated_kwh', ''))} kWh &middot; "
        f"{float(battery.get('state_of_health', 0)) * 100:.0f}% health &middot; "
        f"{_e(plain.get('chemistry', ''))} &middot; "
        f"{_e(bom.get('pack_mass_kg', ''))} kg</p>"
    )
    parts.append("</div>")

    # --- why --------------------------------------------------------------
    if plain.get("why"):
        parts.append("<h2>Why this number</h2><ul>")
        parts.extend(f"<li>{_e(reason)}</li>" for reason in plain["why"])
        parts.append("</ul>")

    # --- how it is wearing ------------------------------------------------
    aging = payload.get("aging")
    if aging:
        parts.append("<h2>How it is wearing</h2>")
        parts.append(_wear_section(aging))

    # --- options ----------------------------------------------------------
    parts.append("<h2>Every option</h2><table>")
    parts.append("<tr><th>What you could do</th><th class='num'>Worth</th></tr>")
    recommended = payload.get("recommended_pathway")
    ordered = sorted(
        payload.get("pathways", []),
        key=lambda p: (not p.get("eligible"), -float(p["net_value"]["amount"])),
    )
    for option in ordered:
        eligible = option.get("eligible")
        classes = "" if eligible else " class='out'"
        amount = _money(option.get("net_value")) if eligible else "not possible"
        reason = (
            option.get("explanation", "")
            if eligible
            else (option.get("blockers") or [""])[0]
        )
        marker = (
            " <strong>(best)</strong>" if option.get("pathway") == recommended else ""
        )
        parts.append(
            f"<tr{classes}><td>{_e(option.get('friendly_label', ''))}{marker}"
            f"<div class='detail'>{_e(reason)}</div></td>"
            f"<td class='num'>{_e(amount)}</td></tr>"
        )
    parts.append("</table>")

    if plain.get("how_to_improve"):
        parts.append("<h2>For a sharper estimate</h2><ul>")
        parts.extend(f"<li>{_e(tip)}</li>" for tip in plain["how_to_improve"])
        parts.append("</ul>")

    if include_technical:
        parts.append(_technical_section(payload))

    parts.append(
        "<footer>This is an estimate produced from the battery's own passport "
        "data and published market prices. It is not an offer. Confirm with a "
        "quote from a buyer or licensed recycler before acting on it."
        "</footer></body></html>"
    )
    return "\n".join(parts)


def _wear_section(aging: dict[str, Any]) -> str:
    """How the battery is ageing, with a chart of where it is heading."""
    parts = [
        '<div class="wear">',
        f'<span class="chip {_e(aging.get("tone", "fair"))}">'
        f'{_e(aging.get("verdict_label", ""))}</span>',
        f'<p>{_e(aging.get("headline", ""))}</p>',
        f'<p class="outlook">{_e(aging.get("outlook", ""))}</p>',
    ]

    chart = _wear_chart(aging)
    if chart:
        parts.append(chart)

    if aging.get("notes"):
        parts.append("<ul class='detail' style='margin-top:14px'>")
        parts.extend(f"<li>{_e(note)}</li>" for note in aging["notes"])
        parts.append("</ul>")

    parts.append("</div>")
    return "\n".join(parts)


def _wear_chart(aging: dict[str, Any]) -> str:
    """An inline SVG of health over time, against the floors that cost money.

    Drawn as SVG rather than an image so the report stays one self-contained
    file that survives being emailed, and stays legible when printed.
    """
    points = aging.get("trajectory") or []
    if len(points) < 2:
        return ""

    width, height = 600.0, 190.0
    left, right, top, bottom = 34.0, 8.0, 12.0, 26.0
    plot_w = width - left - right
    plot_h = height - top - bottom

    ages = [float(point["age_years"]) for point in points]
    x_min, x_max = min(ages), max(ages)
    x_span = (x_max - x_min) or 1.0
    y_min, y_max = 0.4, 1.0

    def x_of(age: float) -> float:
        return left + (age - x_min) / x_span * plot_w

    def y_of(soh: float) -> float:
        clamped = min(max(soh, y_min), y_max)
        return top + (y_max - clamped) / (y_max - y_min) * plot_h

    def path(key: str) -> str:
        return " ".join(
            f"{'M' if index == 0 else 'L'}{x_of(float(point['age_years'])):.1f},"
            f"{y_of(float(point[key])):.1f}"
            for index, point in enumerate(points)
        )

    svg = [
        f'<svg class="chart" viewBox="0 0 {width:.0f} {height:.0f}" '
        'role="img" aria-label="Projected battery health over the next ten years">'
    ]

    for value, label in ((1.0, "100%"), (0.8, "80%"), (0.6, "60%"), (0.4, "40%")):
        y = y_of(value)
        svg.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" '
            'stroke="#e6e9ed" stroke-width="1"/>'
        )
        svg.append(
            f'<text x="{left - 6}" y="{y + 3.5:.1f}" text-anchor="end" '
            f'font-size="10" fill="#8b939c">{label}</text>'
        )

    for key, colour, label in (
        ("resale_floor", "#a3352c", "resale grade"),
        ("storage_floor", "#b8860b", "storage grade"),
    ):
        floor = aging.get(key)
        if floor is None:
            continue
        y = y_of(float(floor))
        svg.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" '
            f'stroke="{colour}" stroke-width="1" stroke-dasharray="3 3" opacity="0.7"/>'
        )
        svg.append(
            f'<text x="{width - right - 2}" y="{y - 4:.1f}" text-anchor="end" '
            f'font-size="9.5" fill="{colour}">{label}</text>'
        )

    svg.append(
        f'<path d="{path("cohort_soh")}" fill="none" stroke="#9aa3ad" '
        'stroke-width="1.6" stroke-dasharray="5 4"/>'
    )
    svg.append(
        f'<path d="{path("projected_soh")}" fill="none" stroke="#0b6b4f" '
        'stroke-width="2.4" stroke-linejoin="round"/>'
    )
    svg.append(
        f'<circle cx="{x_of(ages[0]):.1f}" cy="'
        f'{y_of(float(points[0]["projected_soh"])):.1f}" r="3.5" fill="#0b6b4f"/>'
    )

    for age in (ages[0], ages[len(ages) // 2], ages[-1]):
        svg.append(
            f'<text x="{x_of(age):.1f}" y="{height - 8:.0f}" text-anchor="middle" '
            f'font-size="10" fill="#8b939c">{age:.0f} yr</text>'
        )

    svg.append("</svg>")
    svg.append(
        '<p class="legend">'
        '<span class="key" style="border-color:#0b6b4f"></span>this battery '
        '&nbsp; <span class="key" style="border-color:#9aa3ad"></span>'
        "a typical one of the same model</p>"
    )
    return "\n".join(svg)


def _technical_section(payload: dict[str, Any]) -> str:
    """The audit trail: workings, materials, prices and caveats."""
    parts: list[str] = []
    recommended = payload.get("recommended_pathway")
    best = next(
        (p for p in payload.get("pathways", []) if p.get("pathway") == recommended),
        None,
    )

    if best is not None:
        parts.append(f"<h2>How {_e(best.get('label', '').lower())} adds up</h2><table>")
        for line in best.get("lines", []):
            sign = "+" if line.get("kind") == "revenue" else "&minus;"
            detail = (
                f"<div class='detail'>{_e(line['detail'])}</div>"
                if line.get("detail")
                else ""
            )
            parts.append(
                f"<tr><td>{sign} {_e(line.get('label', ''))}{detail}</td>"
                f"<td class='num'>{_e(_money(line.get('amount')))}</td></tr>"
            )
        parts.append(
            "<tr class='total'><td>Net</td>"
            f"<td class='num'>{_e(_money(best.get('net_value')))}</td></tr></table>"
        )
        if best.get("assumptions"):
            parts.append("<h2>Assumptions</h2><ul class='detail'>")
            parts.extend(f"<li>{_e(a)}</li>" for a in best["assumptions"])
            parts.append("</ul>")

    bom = payload.get("bill_of_materials", {})
    parts.append("<h2>Materials in the pack</h2><table>")
    parts.append(
        "<tr><th>Element</th><th class='num'>kg</th><th>Where this came from</th></tr>"
    )
    for line in bom.get("lines", []):
        parts.append(
            f"<tr><td>{_e(line.get('element', ''))}</td>"
            f"<td class='num'>{float(line.get('mass_kg', 0)):.2f}</td>"
            f"<td class='detail'>{_e(line.get('basis', ''))}</td></tr>"
        )
    parts.append(
        f"<tr><td>inert</td><td class='num'>{float(bom.get('inert_mass_kg', 0)):.2f}</td>"
        "<td class='detail'>separator, binder, electrolyte, plastics</td></tr></table>"
    )

    prices = payload.get("prices", {})
    parts.append("<h2>Market prices used</h2><table>")
    parts.append(
        "<tr><th>Material</th><th class='num'>Price</th>"
        "<th class='num'>Per kg of metal</th><th>Source</th></tr>"
    )
    for quote in prices.get("quotes", []):
        parts.append(
            f"<tr><td>{_e(quote.get('form', ''))}</td>"
            f"<td class='num'>{float(quote.get('price', 0)):,.2f} "
            f"{_e(quote.get('currency', ''))}/{_e(quote.get('unit', ''))}</td>"
            f"<td class='num'>{float(quote.get('price_per_kg_contained', 0)):,.2f}</td>"
            f"<td class='detail'>{_e(quote.get('source', ''))} &middot; "
            f"{_e(quote.get('quality', ''))} &middot; {_e(quote.get('as_of', ''))}</td></tr>"
        )
    parts.append("</table>")

    if payload.get("sensitivity"):
        parts.append("<h2>What moves the number</h2><table>")
        for factor in payload["sensitivity"]:
            parts.append(
                f"<tr><td>{_e(factor.get('name', ''))}</td>"
                f"<td class='num'>{_e(_money(factor.get('low')))} to "
                f"{_e(_money(factor.get('high')))}</td></tr>"
            )
        parts.append("</table>")

    if payload.get("warnings"):
        parts.append("<h2>Caveats</h2>")
        parts.extend(f'<div class="note">{_e(w)}</div>' for w in payload["warnings"])

    return "\n".join(parts)


def _generated_on(payload: dict[str, Any]) -> str:
    """Format the generation timestamp for the report header."""
    raw = payload.get("generated_at")
    if raw:
        try:
            return datetime.fromisoformat(raw).strftime("%d %B %Y")
        except ValueError:
            pass
    return datetime.now(timezone.utc).strftime("%d %B %Y")


def report_filename(
    valuation: ResidualValuation | dict[str, Any], extension: str = "html"
) -> str:
    """A tidy, filesystem-safe filename for the report."""
    payload = (
        valuation if isinstance(valuation, dict) else valuation_to_dict(valuation)
    )
    label = payload.get("battery", {}).get("label", "battery")

    stem = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in label.lower().replace(" ", "-")
    )
    while "--" in stem:
        stem = stem.replace("--", "-")

    raw = payload.get("generated_at")
    try:
        date_part = datetime.fromisoformat(raw).strftime("%Y-%m-%d") if raw else None
    except ValueError:
        date_part = None
    date_part = date_part or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    return f"battery-value-{stem.strip('-') or 'battery'}-{date_part}.{extension}"
