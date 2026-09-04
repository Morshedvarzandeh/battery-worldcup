# Roadmap

This document is the plan for turning `battery-worldcup` into a reference and benchmark for
lithium-ion battery State of Health (SOH) estimation. It is ordered so that every phase
produces something usable on its own, and so that the expensive parts (deep models, physics
models) are built only after the data, labels and evaluation rules they depend on are frozen.

## Guiding principles

1. **Evaluation before models.** Most published SOH results are not comparable because of
   leakage (random cycle-level splits), inconsistent labels, or cherry-picked datasets. The
   rules in `docs/05-evaluation-protocol.md` are fixed before any model is ranked.
2. **Cell-level splits only.** A cell is either in train or in test, never both.
3. **Labels come from reference tests.** SOH labels are derived from reference performance
   tests (or equivalent low-rate full discharges), never from the partial data a model sees
   as input.
4. **Every strategy family gets a fair, tuned baseline** before any "state of the art" model
   is added. A leaderboard containing only deep models teaches nothing.
5. **Reproducibility is a feature.** A result is a JSON file produced by a config, a seed and
   a git commit. If it cannot be regenerated, it is not on the leaderboard.
6. **No raw data in git.** Loaders fetch from original sources, verify checksums and cache
   locally in Parquet. Dataset licences are respected and recorded.
7. **Documentation is a deliverable.** Every dataset has a card, every model has a card,
   every task has a definition.

## Phase overview

| Phase | Name | Outcome | Size |
| --- | --- | --- | --- |
| 0 | Foundation | Docs, scope, conventions, package skeleton, CI | S |
| 1 | Data layer | Canonical schema, loaders for the starter datasets, SOH labels, dataset cards | L |
| 2 | Feature layer | Tested health-indicator library | M |
| 3 | Baselines (group stage) | Naive, empirical, filter-based and feature-based models | M |
| 4 | Deep learning | Sequence models and a training harness | M |
| 5 | Physics-based and hybrid | Degradation-mode diagnosis, parameter tracking, physics-informed models | L |
| 6 | The World Cup | Task runner, split generator, metrics, leaderboard, first public results | M |
| 7 | Uncertainty, transfer, field-readiness | Probabilistic wrappers, transfer tasks, field-like constraints | M |
| 8 | Reference polish | Tutorials, docs site, decision guide, reproduction tracker, v1.0 | M |
| 9 | Community and maintenance | Submission process, versioned leaderboards, releases | ongoing |

Sizes are relative (S < M < L). Phases 2, 3 and 4 can proceed in parallel once phase 1 has
frozen the schema and the labels. Phase 6 needs phases 1, 2 and 3; it should not wait for
phases 4 and 5.

## Milestones

| Milestone | Contents | Version |
| --- | --- | --- |
| M0 | Documentation set, package skeleton, CI | 0.0.x |
| M1 | Wave-1 datasets load into the schema with labels and cards | 0.1 |
| M2 | Feature library and all phase-3 baselines run from configs | 0.2 |
| M3 | First leaderboard (T1, T2, T3 on wave-1 datasets) | 0.3 |
| M4 | Deep and physics-based models on the leaderboard; wave-2 datasets | 0.4 |
| M5 | Transfer and uncertainty brackets; tutorials; docs site | 1.0 |

## Phase 0 — Foundation

Status: complete.

Objective: agree on what the repository is, and set up the conventions everything else uses.

Deliverables
- `README.md` describing purpose, scope, layout and the documentation map.
- Reference documentation set (`docs/01` to `docs/06`, glossary, decision records).
- `CONTRIBUTING.md` with dataset-card and model-card templates.
- Python package skeleton (`src/battery_worldcup`), `pyproject.toml`, ruff, pytest, pre-commit.
- CI workflow: lint and unit tests on synthetic data (no dataset downloads in CI).
- Decision log (`docs/decisions/`) for choices that are hard to reverse (schema, SOH
  definition, split policy).

Definition of done
- A new contributor can read the docs and understand the taxonomy, the datasets and the rules.
- `pip install -e .` and `pytest` work on a clean machine.

## Phase 1 — Data layer

Status: in progress (schema, labels, registry and the Oxford loader exist; see the checklist at the end).

Objective: one schema, many datasets, trustworthy labels.

Deliverables
- Canonical schema (below) with Parquet storage and a `Cell` / `Cycle` / `Step` API.
- Registry with download URL or DOI, checksum, licence and citation per dataset.
- Loaders, in two waves:
  - Wave 1 (starter set): NASA PCoE, CALCE CS2/CX2, Oxford Battery Degradation Dataset 1,
    MATR (Severson 2019 and Attia 2020).
  - Wave 2: HUST, XJTU, Sandia (SNL), HNEI, UL-PUR, RWTH Aachen, ISU-ILCC, Tongji voltage
    relaxation, Cambridge EIS, Stanford EV driving-profile, synthetic degradation-mode sets.
- Reference-test detection and SOH label construction (`labels/`), with per-dataset rules
  documented and tested.
- Dataset cards in `docs/04-datasets.md` completed from the loaded data (cell counts, cycle
  counts, label density, coverage plots).
- A data-quality report per dataset: missing cycles, capacity-regeneration events, outliers,
  temperature drift.

Canonical schema (minimum)
- `cell`: dataset, cell_id, chemistry, format, nominal_capacity_Ah, nominal_voltage_V,
  manufacturer and part number, test temperature, protocol description, licence.
- `cycle`: cell_id, cycle_index, start_time, charge and discharge capacity_Ah, energy_Wh,
  coulombic efficiency, mean and max temperature, is_reference_test, soh_capacity (when a
  label exists at this cycle), soh_resistance (when available), interpolated flag.
- `timeseries`: cell_id, cycle_index, step_index, time_s, current_A, voltage_V,
  temperature_C, step_type (cc_charge, cv_charge, discharge, rest, pulse, eis).
- `eis` (optional): cell_id, cycle_index, soc, frequency_Hz, z_real_Ohm, z_imag_Ohm.

Definition of done
- Every loaded dataset round-trips through the schema, passes the integrity tests and has a card.
- SOH labels are plotted per cell and reviewed once; anomalies are documented in the card.

## Phase 2 — Feature layer

Status: in progress (ICA/DVA with peak extraction, partial-charge windows, CV phase,
relaxation and per-cycle extraction exist; EIS, thermal, ΔQ(V) and efficiency features are
open).

Objective: a tested library of health indicators, because most feature-based papers are not
reproducible at the feature-extraction step.

Deliverables
- Incremental capacity (dQ/dV) and differential voltage (dV/dQ) curves with configurable
  smoothing (Savitzky-Golay, Gaussian, spline) and peak and valley extraction.
- Partial-charge features: time to traverse a voltage window, charge within a window, CC and
  CV durations, curve slopes and curvature, voltage vectors resampled on capacity.
- Relaxation features after charge and discharge (variance, skewness, maximum, fitted time
  constants of the rest-voltage curve).
- Statistical features on ΔQ(V) between cycles (the early-life features of Severson et al.).
- EIS features: raw spectrum vectors, equivalent-circuit fits (via `impedance.py`),
  distribution of relaxation times (optional).
- Thermal features: temperature rise per step, differential thermal voltammetry.
- Coulombic efficiency and capacity-throughput features.
- Feature registry: name, required inputs, availability mask per dataset, unit tests on
  synthetic curves with known peaks.

Definition of done
- Each feature has a test on a synthetic signal and a notebook cell showing it on two real
  datasets.
- A feature-availability matrix (dataset by feature) is generated automatically.

## Phase 3 — Baselines (the group stage)

Objective: honest, tuned baselines for each family before any deep model exists.

Deliverables
- Naive baselines: last-known SOH, linear extrapolation, per-dataset mean trajectory.
- Empirical aging models fitted per cell and per condition: power law, square-root-of-time,
  exponential and bi-exponential, calendar-plus-cycling superposition; knee detection
  (Bacon-Watts and double-linear fits).
- Equivalent-circuit model (Thevenin, 1RC and 2RC) with EKF, UKF and dual estimation of
  capacity and internal resistance; a particle-filter variant.
- Feature-based regressors with nested cross-validation: ridge and elastic net, Gaussian
  process regression (with uncertainty), support vector regression, random forest, gradient
  boosting.
- Early-life cycle-life predictor (Severson-style features and elastic net) as an anchor
  against the literature.
- Model cards for each.

Definition of done
- Every baseline runs from a config on every wave-1 dataset and writes a result file.
- At least one published number is reproduced within its reported tolerance (for example
  early-life prediction error on MATR).

## Phase 4 — Deep learning

Objective: strong sequence models under the same rules as the baselines.

Deliverables
- Standard input windows: partial-charge segment resampled on voltage, full-cycle sequences,
  multi-cycle windows.
- Architectures: 1D CNN, LSTM and GRU, temporal convolutional network, Transformer encoder;
  sequence-to-one (SOH now) and sequence-to-sequence (trajectory).
- Training harness: PyTorch, configs, seeds, early stopping on validation cells,
  checkpoints, deterministic data pipeline.
- Self-supervised pretraining option (masked reconstruction or contrastive) for the transfer
  experiments in phase 7.
- Model cards with data requirements and compute cost.

Definition of done
- Each architecture trains on MATR and HUST within a compute budget documented in its card.
- Results are reported over at least three seeds with mean and standard deviation.

## Phase 5 — Physics-based and hybrid models

Objective: models that explain degradation, not just fit it.

Deliverables
- Degradation-mode diagnosis: fit LLI, LAM_PE and LAM_NE from pseudo-OCV or ICA/DVA using
  half-cell curves (the approach of Dubarry et al. 2012 and Birkl et al. 2017); synthetic
  training data from degradation-mode sweeps.
- PyBaMM single-particle and DFN models with parameter estimation (PyBOP) to track
  capacity-related parameters over life; degradation sub-models (SEI growth, lithium
  plating, particle cracking) for simulation-based augmentation.
- Physics-informed neural networks and physics-regularised regressors (monotonic fade,
  Arrhenius-like temperature dependence, capacity bounds).
- Residual hybrids: a physics or empirical model plus an ML correction.

Definition of done
- Degradation-mode diagnosis is validated on synthetic data with known modes and inspected
  on the Oxford dataset.
- At least one hybrid beats both its pure-ML and pure-physics parents on a transfer task.

## Phase 6 — The World Cup

Objective: the benchmark itself, and the first public results.

Deliverables
- Task definitions (`tasks/`): T1 SOH nowcast (full-cycle, partial-charge, relaxation-only,
  EIS-only variants), T2 trajectory forecasting, T3 early-life cycle-life prediction, T4
  remaining useful life, T5 degradation-mode diagnosis, T6 transfer (zero-shot and few-shot).
- Split generator with frozen split files, stratification and leakage checks that fail loudly.
- Metrics module: point, trajectory, probabilistic and cost metrics.
- Runner: `bwc run config.yaml` writes a result JSON with config hash, git commit, seed and
  environment.
- Leaderboard builder: results to Markdown tables, with per-dataset "group stage" tables and
  cross-dataset "knockout" tables.
- First leaderboard covering all phase-3 baselines and phase-4 models on wave-1 datasets.

Definition of done
- Anyone can regenerate the leaderboard from the `results/` directory with one command.
- Leakage tests run in CI.

## Phase 7 — Uncertainty, transfer and field-readiness

Objective: the properties that decide whether a model is usable outside the lab.

Deliverables
- Probabilistic wrappers: deep ensembles, Monte Carlo dropout, quantile heads, conformal
  prediction; calibration reporting (coverage, interval width, CRPS).
- Transfer protocol: source-to-target fine-tuning, domain-adaptation baselines, few-shot with
  k target cells; cross-chemistry (LFP, NMC, NCA), cross-protocol, cross-temperature.
- Field-like constraints: partial windows only, missing temperature, irregular sampling, no
  reference tests (pseudo-labels from partial charges), long calendar gaps.
- Robustness tests: sensor noise, voltage offset, sampling-rate changes.

Definition of done
- Every leaderboard entry that outputs uncertainty reports calibration.
- Transfer results show, per family, how accuracy decays with distribution shift.

## Phase 8 — Reference polish and v1.0

Deliverables
- Tutorials: one notebook per strategy family, runnable end to end on a wave-1 dataset.
- Docs site (MkDocs Material) built from `docs/`, with API reference.
- Decision guide: given inputs X and constraints Y, start from strategy Z.
- Paper-reproduction tracker: which published results were reproduced, to what tolerance,
  and what differed.
- `CITATION.cff`, versioned release, changelog.

## Phase 9 — Community and maintenance

- Submission process for external results (a PR with config, result JSON and model card; CI
  re-runs a small task).
- Leaderboard versioning tied to dataset and split versions.
- Issue templates for dataset requests and model requests.
- Quarterly refresh of the dataset registry (new public datasets, broken links, licence
  changes).

## Design decisions (recorded in `docs/decisions/`)

| Decision | Choice | Why |
| --- | --- | --- |
| Language and stack | Python 3.11+; pandas or polars with Parquet; scikit-learn; PyTorch; PyBaMM and PyBOP | Standard in the field; every referenced toolkit is Python |
| Primary SOH definition | Capacity ratio to the measured beginning-of-life capacity under the dataset's reference test | Nominal capacities are set conservatively and inconsistently; measured BOL keeps datasets comparable |
| Secondary SOH definitions | Resistance-based SOH and a degradation-mode vector (LLI, LAM_PE, LAM_NE) | Capacity alone hides power fade and mechanism |
| Split policy | Cell-level, stratified, frozen files; time-respecting for forecasting | Prevents the most common leakage |
| Config system | Plain YAML with a small loader (Hydra optional later) | Low entry barrier |
| Result format | JSON, one file per (config, seed) | Diffable, mergeable, leaderboard-friendly |
| Data policy | Never commit raw data; checksum-verified downloads; licence recorded per dataset | Legal and size reasons |

## Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Dataset links rot or licences change | Registry stores DOI or URL, checksum and licence; quarterly check; mirror only where the licence allows |
| Label inconsistency across datasets | Per-dataset label rules documented and tested; SOH always relative to measured BOL |
| Leaderboard dominated by one dataset | Rank by mean rank across datasets; always show per-dataset tables |
| Deep models overfit to a protocol | Mandatory transfer task; cross-dataset results printed next to in-distribution results |
| Scope creep into SOC and general BMS work | Non-goals stated in the README; SOC appears only as a component of filter-based SOH |

## Immediate next steps (phase 0 to phase 1)

- [x] `pyproject.toml`, the `src/battery_worldcup` skeleton, ruff and pytest configuration, and
      a CI workflow.
- [x] Canonical schema as column specifications with a Parquet writer, plus a synthetic-cell
      generator with known ground truth for the tests.
- [x] Dataset registry, checksum-verified download cache, SOH label rules and cell-level split
      generation with leakage checks.
- [x] First loader (Oxford) written against the published file structure. Validation against
      the real file is pending: the hosting site could not be reached from the development
      sandbox, so the loader is tested on a structurally identical fake file.
- [ ] NASA, CALCE and MATR loaders, each validated on the real files.
- [ ] First dataset cards generated from real loaded data.
- [ ] Frozen wave-1 split files.

## What "reference for SOH" means at v1.0

- A reader can learn the field from `docs/` alone, with citations to primary sources.
- Every public SOH dataset worth using has a card and a loader.
- Every strategy family has a tuned, documented, reproducible implementation.
- A single command reproduces the leaderboard.
- Published results from key papers are either reproduced or the discrepancy is documented.
