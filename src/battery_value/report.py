"""Build a self-contained report the owner can save, print or send on.

The point of this file is that it leaves the app. Someone scans their pack on a
phone, then forwards the result to a garage, a recycler or an insurer, who
needs to see the workings rather than just the headline. So the report carries
both: the plain-language answer at the top, and the full audit trail beneath.

The output is a single HTML file with no external references, which means it
opens anywhere, prints to PDF from any browser, and can be attached to a
message without anything breaking.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone

from .valuation import plain
from .valuation.models import LineKind, ResidualValuation

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
    """HTML-escape a value."""
    return html.escape(str(value), quote=True)


def _rows(pairs: list[tuple[str, str]]) -> str:
    return "".join(
        f"<tr><td>{_e(left)}</td><td class='num'>{_e(right)}</td></tr>"
        for left, right in pairs
    )


def build_html_report(
    valuation: ResidualValuation, *, include_technical: bool = True
) -> str:
    """Render a valuation as a standalone HTML document.

    Args:
        valuation: The result to render.
        include_technical: Keep the full audit trail. Turn it off for a
            summary a non-specialist can read at a glance.
    """
    block = plain.confidence_band(valuation.confidence)
    best = valuation.recommended
    value = valuation.residual_value
    is_cost = value.is_negative
    generated = valuation.generated_at or datetime.now(timezone.utc)

    parts: list[str] = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>Battery value report - {_e(valuation.battery_label)}</title>",
        f"<style>{_STYLE}</style></head><body>",
        f"<h1>{_e(valuation.battery_label)}</h1>",
        f'<p class="meta">Battery value report &middot; '
        f'{_e(generated.strftime("%d %B %Y"))}</p>',
    ]

    # --- the answer -------------------------------------------------------
    shown = value.format(0).replace("-", "") if is_cost else value.format(0)
    parts.append(f'<div class="headline{" cost" if is_cost else ""}">')
    parts.append(f'<div class="amount{" cost" if is_cost else ""}">{_e(shown)}</div>')
    parts.append(f'<p class="said">{_e(plain.headline_sentence(valuation))}</p>')

    if valuation.value_range and not is_cost:
        parts.append(
            f'<p class="band">Most likely between '
            f"{_e(valuation.value_range.low.format(0))} and "
            f"{_e(valuation.value_range.high.format(0))}.</p>"
        )
    parts.append(
        f'<p class="band"><strong>{_e(block.label)}.</strong> '
        f"{_e(block.explanation)}</p>"
    )
    parts.append(
        '<p class="facts">'
        f"{valuation.rated_kwh:g} kWh &middot; "
        f"{valuation.state_of_health:.0%} health &middot; "
        f"{_e(plain.chemistry_in_plain_words(valuation.bom.chemistry))} &middot; "
        f"{valuation.bom.pack_mass_kg:.0f} kg</p>"
    )
    parts.append("</div>")

    # --- why --------------------------------------------------------------
    parts.append("<h2>Why this number</h2><ul>")
    parts.extend(f"<li>{_e(reason)}</li>" for reason in plain.why_this_value(valuation))
    parts.append("</ul>")

    # --- options ----------------------------------------------------------
    parts.append("<h2>Every option</h2><table>")
    parts.append(
        "<tr><th>What you could do</th><th class='num'>Worth</th></tr>"
    )
    ordered = sorted(
        valuation.pathways,
        key=lambda p: (not p.eligible, -p.net_value.amount),
    )
    for option in ordered:
        classes = "" if option.eligible else " class='out'"
        amount = option.net_value.format(0) if option.eligible else "not possible"
        reason = (
            option.pathway.plain_explanation
            if option.eligible
            else (option.blockers[0] if option.blockers else "")
        )
        marker = " <strong>(best)</strong>" if option is best else ""
        parts.append(
            f"<tr{classes}><td>{_e(option.pathway.friendly_label)}{marker}"
            f"<div class='detail'>{_e(reason)}</div></td>"
            f"<td class='num'>{_e(amount)}</td></tr>"
        )
    parts.append("</table>")

    improvements = plain.how_to_improve(valuation)
    if improvements:
        parts.append("<h2>For a sharper estimate</h2><ul>")
        parts.extend(f"<li>{_e(tip)}</li>" for tip in improvements)
        parts.append("</ul>")

    # --- technical --------------------------------------------------------
    if include_technical:
        parts.append(_technical_section(valuation))

    parts.append(
        "<footer>This is an estimate produced from the battery's own passport "
        "data and published market prices. It is not an offer. Confirm with a "
        "quote from a buyer or licensed recycler before acting on it."
        "</footer></body></html>"
    )
    return "\n".join(parts)


def _technical_section(valuation: ResidualValuation) -> str:
    """The audit trail: workings, materials, prices and caveats."""
    parts: list[str] = []
    best = valuation.recommended

    if best is not None:
        parts.append(f"<h2>How {_e(best.label.lower())} adds up</h2><table>")
        for line in best.lines:
            sign = "+" if line.kind is LineKind.REVENUE else "&minus;"
            detail = (
                f"<div class='detail'>{_e(line.detail)}</div>" if line.detail else ""
            )
            parts.append(
                f"<tr><td>{sign} {_e(line.label)}{detail}</td>"
                f"<td class='num'>{_e(line.amount.format(0))}</td></tr>"
            )
        parts.append(
            f"<tr class='total'><td>Net</td>"
            f"<td class='num'>{_e(best.net_value.format(0))}</td></tr></table>"
        )
        if best.assumptions:
            parts.append("<h2>Assumptions</h2><ul class='detail'>")
            parts.extend(f"<li>{_e(a)}</li>" for a in best.assumptions)
            parts.append("</ul>")

    parts.append("<h2>Materials in the pack</h2><table>")
    parts.append(
        "<tr><th>Element</th><th class='num'>kg</th><th>Where this came from</th></tr>"
    )
    for line in valuation.bom.sorted_lines():
        parts.append(
            f"<tr><td>{_e(line.element)}</td>"
            f"<td class='num'>{line.mass_kg:.2f}</td>"
            f"<td class='detail'>{_e(line.basis)}</td></tr>"
        )
    parts.append(
        f"<tr><td>inert</td><td class='num'>{valuation.bom.inert_mass_kg:.2f}</td>"
        "<td class='detail'>separator, binder, electrolyte, plastics</td></tr></table>"
    )

    parts.append("<h2>Market prices used</h2><table>")
    parts.append(
        "<tr><th>Material</th><th class='num'>Price</th>"
        "<th class='num'>Per kg of metal</th><th>Source</th></tr>"
    )
    for form, quote in valuation.prices.quotes.items():
        parts.append(
            f"<tr><td>{_e(form)}</td>"
            f"<td class='num'>{quote.price:,.2f} {_e(quote.currency)}"
            f"/{_e(quote.unit.value)}</td>"
            f"<td class='num'>{quote.price_per_kg_contained():,.2f}</td>"
            f"<td class='detail'>{_e(quote.source)} &middot; {_e(quote.quality.value)}"
            f" &middot; {_e(quote.as_of.isoformat())}</td></tr>"
        )
    parts.append("</table>")

    if valuation.sensitivity:
        parts.append("<h2>What moves the number</h2><table>")
        parts.append(
            _rows(
                [
                    (factor.name, f"{factor.low.format(0)} to {factor.high.format(0)}")
                    for factor in valuation.sensitivity
                ]
            )
        )
        parts.append("</table>")

    if valuation.warnings:
        parts.append("<h2>Caveats</h2>")
        parts.extend(f'<div class="note">{_e(w)}</div>' for w in valuation.warnings)

    return "\n".join(parts)


def report_filename(valuation: ResidualValuation, extension: str = "html") -> str:
    """A tidy, filesystem-safe filename for the report."""
    stem = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in valuation.battery_label.lower().replace(" ", "-")
    )
    while "--" in stem:
        stem = stem.replace("--", "-")
    date = (valuation.generated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    return f"battery-value-{stem.strip('-')}-{date}.{extension}"
