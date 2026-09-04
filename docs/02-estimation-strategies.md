# 2. Estimation strategies

A strategy is a way of turning observable signals into an SOH estimate. This chapter
organises the field into ten families, states the assumptions and trade-offs of each, and
ends with a decision guide. Concrete models are listed in `docs/03-model-catalog.md`.

Notation: `SOH(t)` at cycle or time `t`; inputs `x_t` (one cycle's or window's signals);
history `H_t` (everything observed up to `t`).

## S1. Direct measurement

**Idea.** Measure capacity or resistance with a reference test; SOH is the ratio to BOL.

- Inputs: a full low-rate discharge, or a pulse or EIS measurement at controlled SOC and
  temperature.
- Role: produces the labels every other strategy learns from. Not deployable in the field,
  because a full discharge is rarely available, but the only ground truth.
- Pitfalls: rate and temperature dependence, coulomb-counting drift, regeneration after rest.

## S2. Model-based adaptive estimation (filters)

**Idea.** Treat capacity `Q` and resistance `R` as slowly varying parameters of a cell model
and estimate them jointly with SOC from current, voltage and temperature streams.

- Models: equivalent-circuit models (Thevenin with one or two RC pairs, hysteresis variants);
  single-particle and other reduced-order electrochemical models.
- Estimators: extended and unscented Kalman filters, dual and joint filters (separate time
  scales for states and parameters), particle filters, recursive least squares with
  forgetting, moving-horizon estimation.
- Inputs: any usage data. No aging dataset is required.
- Strengths: runs on a BMS; adapts to the individual cell; resistance-based SOH comes for free.
- Weaknesses: capacity is weakly observable from short, shallow cycles; sensitive to OCV-curve
  drift with age and to model mismatch; tuning of noise covariances is an art.
- Benchmark role: the "no training data" contender, evaluated on how fast and how accurately
  it converges per cell.

## S3. Health-indicator engineering plus regression

**Idea.** Extract a small set of physically motivated features from a repeatable segment of
each cycle, then regress SOH.

- Features: ICA and DVA peak heights, positions and areas; time or charge within a voltage
  window during CC charging; CV-phase duration and current tail; relaxation-voltage
  statistics; EIS features; temperature rise; ΔQ(V) statistics between cycles.
- Regressors: linear, ridge and elastic net; Gaussian process regression; support vector
  regression; random forests; gradient boosting; small MLPs.
- Strengths: data-efficient (tens of cells), interpretable, cheap, easy to constrain.
- Weaknesses: the segment must be present in the data (field partial charges may not cover
  the informative voltage window); smoothing choices change ICA peaks; features can encode
  the protocol rather than health.
- Benchmark role: the default strong baseline, and the family used to test transfer across
  chemistries because its features are physically grounded.

## S4. End-to-end deep learning

**Idea.** Learn the mapping from raw or lightly resampled voltage, current and temperature
windows to SOH.

- Architectures: 1D CNNs on voltage-indexed charge curves; LSTM and GRU networks and
  temporal convolutional networks on time sequences; Transformer encoders on multi-cycle
  windows; autoencoders and contrastive pretraining for representation learning;
  sequence-to-sequence models for trajectory forecasting.
- Strengths: best in-distribution accuracy when hundreds of labelled cells exist; no feature
  engineering; handles multi-cycle context.
- Weaknesses: data hungry; brittle under protocol or chemistry shift; hard to audit;
  literature results often rest on cycle-level random splits, which inflate them.
- Benchmark role: judged on in-distribution accuracy and on the transfer and data-efficiency
  tasks, under fixed compute budgets.

## S5. Physics-based diagnosis and parameter tracking

**Idea.** Explain the terminal voltage with an electrochemical description and read SOH off
the fitted parameters.

- Degradation-mode diagnosis: fit LLI, LAM_PE, LAM_NE and resistance by aligning half-cell
  potential curves to the measured pseudo-OCV or ICA/DVA curve.
- Electrochemical parameter tracking: fit single-particle, SPMe or DFN parameters (active
  material fractions, stoichiometry windows, diffusivities, film resistance) over life with
  PyBaMM and PyBOP; use degradation sub-models (SEI, plating, cracking) to simulate futures.
- Strengths: interpretable in terms of mechanisms; can extrapolate to unseen conditions; can
  generate synthetic training data.
- Weaknesses: needs low-rate data or careful excitation; identifiability is poor for many
  parameters; expensive; requires half-cell curves or a parameter set per cell type.
- Benchmark role: the only family evaluated on the degradation-mode task (T5); also the
  source of synthetic data for others.

## S6. Hybrid and physics-informed machine learning

**Idea.** Combine the flexibility of S3 and S4 with the structure of S5.

- Physics-informed neural networks: loss terms enforcing degradation ODEs, monotonic fade,
  Arrhenius temperature dependence, capacity bounds.
- Residual hybrids: a physics or empirical model predicts the bulk; ML corrects the residual.
- ML-parameterised physics: networks predict ECM or physics parameters from usage.
- Feature hybrids: physics-derived features (mode estimates, fitted parameters) as inputs to
  regressors.
- Strengths: better extrapolation and sample efficiency than pure ML; more flexible than pure
  physics.
- Weaknesses: engineering effort; constraints can be wrong for a new chemistry.
- Benchmark role: expected to win the transfer and small-data brackets. That is the
  hypothesis the benchmark tests.

## S7. Empirical aging models, trajectory forecasting and early prediction

**Idea.** Model SOH as a function of time, cycles and stress; forecast rather than nowcast.

- Closed-form laws: power laws in time or cycles, square-root-of-time terms, Arrhenius and
  SOC-dependent rate terms; calendar-plus-cycling superposition; knee models.
- Sequence forecasting: fit early cycles and extrapolate; ML on early-life features to predict
  cycle life (the MATR early-prediction problem); knee-onset prediction.
- Strengths: tiny, interpretable, decades of validation for calendar aging.
- Weaknesses: fixed shapes fail at knees; parameters are condition-specific.
- Benchmark role: tasks T2, T3 and T4.

## S8. Transfer learning and domain adaptation

**Idea.** Reuse what was learned on one population (chemistry, protocol, temperature, lab) on
another.

- Techniques: fine-tuning with a few target cells; feature-space alignment;
  domain-adversarial training; meta-learning across datasets; pretraining on many datasets
  and then adapting.
- Benchmark role: task T6 with zero-shot and k-shot brackets. Every family is evaluated
  through this lens, not only deep models.

## S9. Probabilistic estimation and uncertainty quantification

**Idea.** Output a distribution, not a point.

- Native: Gaussian process regression, Bayesian linear models, particle filters.
- Wrappers: deep ensembles, Monte Carlo dropout, quantile heads, conformal prediction for
  distribution-free intervals.
- Metrics: calibration (coverage of nominal intervals), sharpness (interval width), negative
  log-likelihood, CRPS.
- Benchmark role: any model may submit intervals; calibrated models get a separate ranking.

## S10. Field and fleet-scale estimation

**Idea.** Estimate SOH when there are no reference tests, only partial charges, irregular
sampling and long gaps.

- Pseudo-labels from repeatable partial charges; per-unit drift models; hierarchical models
  across a fleet; cloud-side aggregation on top of on-board filters.
- Benchmark role: simulated in phase 7 by degrading lab datasets (partial windows, dropped
  temperature, subsampling); real field datasets are added as they become public.

## Decision guide

| You have | You need | Start with |
| --- | --- | --- |
| Only on-board current, voltage and temperature; no aging dataset | Online SOH on a BMS | S2 (ECM plus dual EKF), then S6 to correct bias |
| Repeatable CC charge segments and 20 to 100 labelled cells | Accurate, interpretable nowcast | S3 (ICA or partial-charge features with GPR or gradient boosting) |
| Hundreds of labelled cells from one protocol | Best in-distribution accuracy | S4 (CNN or Transformer), reported next to an S3 baseline |
| Low-rate characterisation data | To know why capacity was lost | S5 (degradation-mode diagnosis) |
| A different chemistry or protocol in deployment than in training | Robust transfer | S6, or S3 with physically grounded features, evaluated via S8 |
| Early cycles only | Cycle life or knee prediction | S7 (early-life features with regularised regression) |
| Safety-relevant decisions | Calibrated uncertainty | An S9 wrapper on whichever model above |
| Fleet data with no reference tests | Scalable field SOH | S10 patterns on top of S3 or S2 |

## Recurring mistakes the benchmark is designed to catch

1. **Cycle-level random splits.** Cycles from the same cell in train and test make any model
   look excellent. Only cell-level splits are allowed.
2. **Labels derived from the inputs.** Using the same partial charge to compute both the
   feature and the "true" capacity.
3. **Ignoring temperature and calendar time.** Two cells with identical cycle counts can have
   very different ages.
4. **Evaluating only on smooth datasets.** A method must be shown on at least one dataset
   with knees or protocol variety.
5. **Reporting a single seed.** Deep models vary more across seeds than the differences
   between papers.
6. **Tuning on the test cells.** Hyper-parameters are chosen on validation cells inside the
   training fold.
