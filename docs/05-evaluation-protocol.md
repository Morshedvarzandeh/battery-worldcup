# 5. Evaluation protocol (the tournament rules)

These rules are fixed before models are ranked. Changing them creates a new leaderboard
version. The aims are comparability across strategy families, honest reporting of data
requirements, and zero leakage.

## 5.1 Tasks

| Task | Input | Output | Primary metric | Notes |
| --- | --- | --- | --- | --- |
| **T1 SOH nowcast** | Signals from the current cycle, optionally a window of the last k cycles | `SOH_C` at that cycle | MAE in SOH points (%) | Variants: T1-full (full cycle), T1-partial (CC charge segment within a fixed voltage window only), T1-relax (rest voltage only), T1-eis (spectrum only) |
| **T2 Trajectory forecast** | Everything up to cycle n, with n in {100, 200, 300} or 10, 20, 30 % of life | `SOH_C` for all future cycles | RMSE over the horizon; error in predicted cycles to 80 % | Reported before and after the knee where one exists |
| **T3 Early-life cycle-life prediction** | First 100 cycles | Cycle life (cycles to 80 %) | MAPE | The MATR protocol; also run on HUST, XJTU and ISU-ILCC |
| **T4 Remaining useful life** | Everything up to cycle t, for t on a grid | Remaining cycles to threshold | MAE in cycles; relative error | Threshold per dataset (80 % by default) |
| **T5 Degradation-mode diagnosis** | Pseudo-OCV or ICA and DVA curve | LLI, LAM_PE, LAM_NE as fractions | MAE per mode | Ground truth from synthetic sets and characterised cells |
| **T6 Transfer** | Train on source dataset(s); test on a target | As T1 or T3 on the target | Same as the parent task | Brackets: zero-shot; k-shot with k in {1, 3, 5, 10} target cells for fine-tuning |

Every task also records the model's **input requirements** (full charge needed? rest needed?
temperature needed? EIS needed?) so that tables can be filtered by what a deployment can
provide.

## 5.2 Splits

- **Cell-level only.** A cell's cycles are entirely in train, validation or test.
- **Stratified** by protocol or condition and by cycle life so that test cells span the range.
- **Frozen.** Split files live in `splits/<dataset>/<version>.json` and are versioned;
  results reference the split version.
- **Repeated.** Five folds for in-distribution tasks; the leaderboard reports mean and
  standard deviation over folds and over at least three seeds for stochastic models.
- **Grouped** variants for robustness: leave-one-protocol-out, leave-one-batch-out,
  leave-one-temperature-out.
- **Time-respecting** for T2 and T4: no information from cycles after the forecast origin.
- **Cross-dataset** for T6: the target dataset's cells are never seen in training except the
  k-shot cells, which are drawn from the target's training fold.

## 5.3 Leakage checks (run in CI)

1. No `cell_id` appears in more than one of train, validation and test.
2. No feature is computed using data from cycles after the prediction cycle (T1 windows are
   causal; T2 and T4 origins are enforced).
3. Labels are read only from the `labels/` module; feature code cannot import label code.
4. Normalisation statistics are computed on the training fold only.
5. Hyper-parameter search touches validation cells only; the runner refuses configs that
   reference the test fold during search.

## 5.4 Metrics

**Point estimates (SOH in percentage points unless stated)**
- MAE, RMSE, MAPE, maximum absolute error, R².
- Error at end of life (at the last labelled cycle), because that is where decisions are made.
- MAE before and after the knee where a knee is detected.

**Trajectories**
- RMSE over the forecast horizon; error in the predicted cycle at which SOH crosses 80 %
  (absolute cycles and percent of true life); dynamic-time-warping distance as an option.

**Probabilistic**
- Negative log-likelihood; continuous ranked probability score; coverage of 90 % intervals;
  mean interval width; calibration curve.

**Cost and requirements**
- Training time, inference time per estimate, parameter count, peak memory.
- Number of training cells and cycles used; label density required.
- Input-requirement flags (see 5.1).

**Data-efficiency curve**
- MAE as a function of the number of training cells (5, 10, 20, 50, all) for T1 and T3.

## 5.5 Reporting

A result is a JSON file with: model name and version, config hash, git commit, dataset and
split version, task and variant, seed, metrics, cost, input-requirement flags, and
environment (Python and library versions, hardware). The leaderboard builder aggregates
result files into Markdown tables:

- **Group stage.** One table per (dataset, task), all models.
- **Knockout.** Cross-dataset transfer tables per (source to target, task), zero-shot and
  k-shot.
- **Special rankings.** Best calibrated model; best model with at most 10 training cells;
  best model using only a partial-charge window; cheapest model within 0.5 SOH points of
  the best.

Rankings use mean rank across datasets, not a single pooled metric, so that one large
dataset cannot dominate.

## 5.6 Required baselines on every table

Every (dataset, task) table must include the naive baselines and at least one model each
from families S2, S3 and S7 before any deep or hybrid result is published on it. A deep
model that does not beat the feature-based baseline is still listed; that is information.

## 5.7 What exists today

Tasks T1 (nowcast) and T2 (trajectory forecast) run end to end: `bwc run <config>` writes a
result file and `bwc leaderboard` renders [the standings](../LEADERBOARD.md) from every result
under `results/`. Splits are cell-level, stratified by lifetime and checked for leakage on
construction. T3 to T6, the frozen split files and the CI leakage tests are still open.

## 5.8 Reproducibility

- `bwc run configs/<file>.yaml --seed 0` must regenerate a result within a tolerance
  documented per model (tolerance is needed for GPU non-determinism).
- Dataset caches are content-addressed; the result records the cache hash.
- Notebooks are not results. Only runner outputs enter `results/`.
