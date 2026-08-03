# The owner's view

Most people who use this are not battery engineers. They have a pack, a phone,
and one question: what is this worth, and what should I do with it?

So the default view answers exactly that, and everything else is folded away.

## What they see

1. **One number**, large, with a sentence saying what it means and what to do.
2. **A confidence badge in words** — "Good estimate", "Rough estimate" — not a
   percentage they have to interpret.
3. **All four routes**, named for what actually happens to the battery, with a
   plain reason attached to each one that is not available.
4. **Why this number** — two or three sentences, no jargon.
5. **Keep a copy** — share or save the report.
6. **Show the technical detail** — one toggle, closed by default.

The wording lives in `battery_value.valuation.plain` and is served in the
`plain` block of every `/v1/value` response, so a partner app or a chatbot
shows the same phrasing rather than inventing its own.

```python
from battery_value.valuation import plain

plain.headline_sentence(valuation)
# "Your battery is worth about €2,058, and the best way to get that is to
#  take it apart and sell the parts."

plain.confidence_band(valuation.confidence).label     # "Good estimate"
plain.chemistry_in_plain_words(chemistry)             # "Nickel-manganese-cobalt lithium-ion"
plain.health_in_plain_words(0.81)                     # "in good shape for its age"
plain.why_this_value(valuation)                       # 2-3 plain sentences
plain.how_to_improve(valuation)                       # what would sharpen it
```

### Language rules

- Never show a chemistry acronym. `NMC532` becomes "nickel-manganese-cobalt
  lithium-ion".
- Never show "state of health", "payable fraction", "traded form" or "pathway".
- Never show a bare minus sign as the answer. A pack that costs money to
  dispose of reads *"Handling this battery safely costs about €814 more than it
  is worth"*, not *"−€814"*.
- Say why a route is closed in terms the owner can act on: *"Its health is 64%.
  Buyers want at least 75% before fitting a used battery to a vehicle."*

Those rules are enforced by tests in `tests/test_plain_and_report.py`.

## Photographing the code

Phones are the main way this gets used, so the primary button is **Take a photo
of the code**. The file input carries `capture="environment"`, which makes a
phone open the rear camera rather than the photo library.

The photo is decoded in whichever place is cheapest:

1. **In the browser**, via the platform `BarcodeDetector` API where available
   (Chrome, Edge, Android). A few hundred bytes of text go to the server
   instead of a multi-megabyte image, which matters on a workshop's signal.
2. **On the server**, via `POST /v1/decode`, for everything else — notably
   iOS Safari, which has no `BarcodeDetector`.

Server-side decoding does not give up after one attempt. A photo of a sticker
is rarely square-on or well lit, so it retries with greyscale, 2× upscaling,
Otsu and adaptive thresholding, sharpening, and finally a rotation sweep. The
rotation step grows the canvas rather than rotating in place — cropping a QR
code's finder patterns is the one thing that makes it permanently undecodable.

`GET /v1/health` reports `photo_decoding`, so a client can tell whether the
fallback exists before offering it.

Either way the flow converges: decode to a payload, then `POST /v1/value`. That
uniformity is what lets the result be re-priced in another currency or turned
into a report afterwards.

## The report

The technical layer is not hidden, it is *portable*. **Share the report**
fetches `POST /v1/report` and hands the browser a single HTML file:

- Self-contained — no external scripts, styles, fonts or images, so it opens
  offline and survives being emailed.
- Prints to PDF from any browser, which is how most people will forward it.
- Leads with the plain answer, then carries the full audit trail: the
  line-by-line workings, the bill of materials, every price with its source and
  date, the sensitivity analysis and the caveats.

On a phone, `navigator.share` puts it straight into WhatsApp, email or Files.
Elsewhere it downloads.

```bash
bv value --file passport.json --report ./            # writes a dated filename
bv value --file passport.json --report out.html --summary-only
```

```python
from battery_value.report import build_html_report, report_filename

html = build_html_report(valuation)                       # full
brief = build_html_report(valuation, include_technical=False)
name = report_filename(valuation)   # battery-value-nissan-leaf-ze1-40-kwh-2026-08-03.html
```

Passport fields are untrusted input and are HTML-escaped before they reach the
report, so a manufacturer name containing markup cannot become markup.

## Who the report is for

The owner rarely reads it. They forward it. The audience is the garage quoting
for a swap, the dismantler deciding whether to collect, the recycler pricing a
gate fee, or an insurer settling a write-off — all of whom need to see the
workings, not just the headline.

That is why the technical detail is complete rather than summarised, and why it
carries dates and sources on every figure: it has to stand up to someone who
disagrees with it.
