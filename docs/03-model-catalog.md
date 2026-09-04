# 3. Model catalog

The catalog lists the models this repository will implement and compare. Each entry gets a
model card (template in `CONTRIBUTING.md`) once implemented. Status values: `planned`,
`in progress`, `done`. Phase numbers refer to `ROADMAP.md`; family codes refer to
`docs/02-estimation-strategies.md`.

## Naive baselines (phase 3)

| Model | Input | Output | Notes | Status |
| --- | --- | --- | --- | --- |
| Last-known SOH | Last label | SOH nowcast or forecast | Lower bound for forecasting | done (`last_known`) |
| Linear extrapolation | Last k labels | Trajectory | Surprisingly strong before the knee | done (`linear_extrapolation`) |
| Dataset mean trajectory | Cycle index | SOH | Shows how much a task depends on cell identity | done (`mean_trajectory`, plus `constant`) |

## Empirical aging models (phase 3, family S7)

| Model | Input | Output | Notes | Status |
| --- | --- | --- | --- | --- |
| Power law in cycles or time | Capacity history | Trajectory | Fitted per cell; exponent near 0.5 when SEI-dominated | done (`empirical_fade`, form `power`) |
| Calendar plus cycling superposition (Arrhenius, SOC and DoD terms) | Stress history | Trajectory | In the style of Schmalstieg et al. (2014) and Wang et al. (2011) | planned |
| Bi-exponential | Capacity history | Trajectory | Common in RUL papers on the NASA data | done (`empirical_fade`, form `biexponential`) |
| Knee detection (Bacon-Watts) | Capacity history | Knee onset and knee point | Used for pre- and post-knee reporting | done (`detect_knee`) |

## Model-based filters (phase 3, family S2)

| Model | Input | Output | Notes | Status |
| --- | --- | --- | --- | --- |
| ECM (1RC) with EKF and recursive capacity estimation | I, V, T streams | SOC, Q, R | `ecm_ekf`; capacity read off the discharge excursion | done |
| ECM with dual EKF | I, V, T streams | SOC, Q, R | Separate time scales for states and parameters | planned |
| ECM with UKF | I, V, T streams | SOC, Q, R | Better with strongly nonlinear OCV curves | planned |
| ECM with particle filter | I, V, T streams | Posterior over Q and R | Native uncertainty | planned |
| Single-particle model with observer | I, V, T streams | Capacity-related parameters | Physics variant | planned (phase 5) |

## Feature-based regressors (phase 3, family S3)

| Model | Input | Output | Notes | Status |
| --- | --- | --- | --- | --- |
| Ridge and elastic net | Feature vector | SOH | Nested cross-validation still to add | done (`feature_regressor`) |
| Gaussian process regression | Feature vector | SOH with variance | Reports `soh_std`; ARD kernels still to add | done (`feature_regressor`) |
| Support vector regression | Feature vector | SOH | ICA features after Weng et al. (2013) | done (`feature_regressor`) |
| Random forest | Feature vector | SOH | Robust default; extra trees still to add | done (`feature_regressor`) |
| Gradient boosting | Feature vector | SOH | scikit-learn for now; LightGBM later | done (`feature_regressor`) |
| Early-life elastic net (Severson features) | First 100 cycles | Cycle life | Reproduction anchor on MATR | planned |
| Relaxation-feature regressor (variance, skewness, maximum) | Rest voltage after charge | Capacity | After Zhu et al. (2022) on the Tongji data | planned |
| EIS-feature Gaussian process | Impedance spectrum | Capacity and RUL | After Zhang et al. (2020) on the Cambridge data | planned |

## Deep learning (phase 4, family S4)

| Model | Input | Output | Notes | Status |
| --- | --- | --- | --- | --- |
| 1D CNN on voltage-indexed charge curve | Resampled partial charge | SOH | After Shen et al. (2020) | planned |
| LSTM or GRU | Cycle sequence | SOH or trajectory | RUL variant after Zhang et al. (2018) | planned |
| Temporal convolutional network | Cycle sequence | SOH | Long receptive field; parallel training | planned |
| Transformer encoder | Multi-cycle window | SOH or trajectory | Attention over cycles | planned |
| Sequence-to-sequence trajectory model | Early cycles | Full trajectory | One-shot style after Li et al. (2021) | planned |
| Self-supervised pretrained encoder plus head | Unlabelled cycles | SOH | For transfer experiments | planned (phase 7) |

## Physics-based (phase 5, family S5)

| Model | Input | Output | Notes | Status |
| --- | --- | --- | --- | --- |
| Degradation-mode fit (LLI, LAM_PE, LAM_NE) via half-cell OCV alignment | Pseudo-OCV or ICA | Mode vector and SOH | After Birkl et al. (2017) and Dubarry et al. (2012) | planned |
| Synthetic-data-trained mode regressor | ICA and DVA curves | Mode vector | After Dubarry and Beck (2020) | planned |
| PyBaMM SPM, SPMe or DFN parameter tracking with PyBOP | Cycle data | Capacity-related parameters | Identifiability reported per parameter | planned |
| Degradation sub-model simulation (SEI, plating, cracking) | Protocol | Synthetic trajectories | Data augmentation, not an estimator | planned |

## Hybrid and physics-informed (phase 5, family S6)

| Model | Input | Output | Notes | Status |
| --- | --- | --- | --- | --- |
| Physics-informed network with degradation-ODE loss | Cycle features and stress | SOH trajectory | After Wang et al. (2024) | planned |
| Residual hybrid (empirical model plus gradient-boosting residual) | Stress history and features | SOH | Simple, strong transfer candidate | planned |
| Physics-feature regressor (mode estimates as features) | Mode vector and usage | SOH and RUL | After Thelen et al. (2022) | planned |
| Monotonic and bounded regressors | Feature vector | SOH | Constraint-based regularisation | planned |

## Probabilistic and transfer wrappers (phase 7, families S8 and S9)

| Wrapper | Applies to | Output | Status |
| --- | --- | --- | --- |
| Deep ensemble | Any deep model | Mean and spread | planned |
| Monte Carlo dropout | Any deep model | Mean and spread | planned |
| Quantile head | Any deep model | Quantiles | planned |
| Conformal prediction | Any model | Calibrated intervals | planned |
| Fine-tuning (k-shot) | Any trainable model | Adapted model | planned |
| Domain-adversarial adaptation | Deep models | Adapted encoder | planned |

## A note on the filter and on synthetic data

`ecm_ekf` tracks the state of charge of one cell with an extended Kalman filter on a Thevenin
circuit and reads capacity from the charge delivered by a discharge divided by the state-of-charge
excursion it caused. It needs no aging dataset, which is why its requirements set
`training_cells` to False.

It scores extremely well on the synthetic cells, and that number must not be read as an
accuracy claim. The synthetic generator produces exactly the circuit the filter assumes, with a
known open-circuit voltage curve and no unmodelled dynamics, so the estimator is being tested
against its own assumptions. On real cells the open-circuit voltage curve drifts with age, the
circuit is only an approximation, and the state of charge is far less observable in shallow
partial cycles. Treat the synthetic result as a check that the estimator is unbiased and stable
when its assumptions hold, and wait for the wave-1 datasets before ranking it against the others.

## The model interface

Every model subclasses `SOHModel` and implements `_fit(data)` and `_predict(data)`, where
`data` is a `ModelData` carrying the targets to predict, the labels the task makes visible,
the feature table, the cycle table and the bundle. What a model may see is decided by the task
and the split, never by the model: `nowcast_views` hides every label of the cells being scored,
and `forecast_views` reveals a target cell's own labels only up to the forecast origin.

Each model declares `InputRequirements` (features, timeseries, history, full charge, rest,
temperature, impedance, and whether it needs other labelled cells at all), so leaderboard tables
can be filtered by what a deployment can actually provide.

## Selection criteria for adding a model

A model is added when it is (a) representative of a family or (b) a well-cited published
method whose results can be checked against a public dataset. Marginal variants of an
existing entry are not added unless they change an input requirement, a data requirement or
a transfer property.
