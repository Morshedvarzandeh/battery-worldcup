# 0002. Split policy

Status: accepted (phase 0)

## Context

The single most common flaw in SOH papers is a random split at the cycle level, which places
cycles of the same cell in both train and test. Because consecutive cycles of a cell are
almost identical, this turns estimation into interpolation and inflates accuracy.

## Decision

- Splits are made at the cell level only. A cell belongs to exactly one of train,
  validation and test.
- Splits are stratified by condition (protocol, temperature, batch) and by cycle life, and
  are frozen in versioned files under `splits/`.
- Forecasting tasks additionally forbid any information from cycles after the forecast
  origin.
- Transfer tasks keep the target dataset entirely unseen except for the declared k-shot cells,
  which are taken from the target's training fold.
- A CI test fails if any result file references a split in which a `cell_id` occurs in more
  than one fold.

## Consequences

- Results will be lower than many published numbers. That is expected and is the point.
- Datasets with very few cells (for example NASA PCoE) give high-variance results; they are
  reported but excluded from aggregate rankings.
