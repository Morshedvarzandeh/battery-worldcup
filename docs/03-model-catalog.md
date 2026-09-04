# 3. Model catalog

The catalog lists the models this repository will implement and compare. Each entry gets a
model card (template in `CONTRIBUTING.md`) once implemented. Status values: `planned`,
`in progress`, `done`. Phase numbers refer to `ROADMAP.md`; family codes refer to
`docs/02-estimation-strategies.md`.

## Naive baselines (phase 3)

| Model | Input | Output | Notes | Status |
| --- | --- | --- | --- | --- |
| Last-known SOH | Last label | SOH nowcast or forecast | Lower bound for forecasting | planned |
| Linear extrapolation | Last k labels | Trajectory | Surprisingly strong before the knee | planned |
| Dataset mean trajectory | Cycle index | SOH | Shows how much a task depends on cell identity | planned |

## Empirical aging models (phase 3, family S7)

| Model | Input | Output | Notes | Status |
| --- | --- | --- | --- | --- |
| Power law in cycles or time | Capacity history | Trajectory | Fitted per cell; exponent near 0.5 when SEI-dominated | planned |
| Calendar plus cycling superposition (Arrhenius, SOC and DoD terms) | Stress history | Trajectory | In the style of Schmalstieg et al. (2014) and Wang et al. (2011) | planned |
| Bi-exponential | Capacity history | Trajectory | Common in RUL papers on the NASA data | planned |
| Knee detection (Bacon-Watts, double-linear) | Capacity history | Knee onset and knee point | Used for pre- and post-knee reporting | planned |

## Model-based filters (phase 3, family S2)

| Model | Input | Output | Notes | Status |
| --- | --- | --- | --- | --- |
| ECM (1RC or 2RC) with joint EKF | I, V, T streams | SOC, Q, R | Plett formulation | planned |
| ECM with dual EKF | I, V, T streams | SOC, Q, R | Separate time scales for states and parameters | planned |
| ECM with UKF | I, V, T streams | SOC, Q, R | Better with strongly nonlinear OCV curves | planned |
| ECM with particle filter | I, V, T streams | Posterior over Q and R | Native uncertainty | planned |
| Single-particle model with observer | I, V, T streams | Capacity-related parameters | Physics variant | planned (phase 5) |

## Feature-based regressors (phase 3, family S3)

| Model | Input | Output | Notes | Status |
| --- | --- | --- | --- | --- |
| Ridge and elastic net | Feature vector | SOH | With nested cross-validation | planned |
| Gaussian process regression | Feature vector | SOH with variance | ARD kernels; forecasting variant after Richardson et al. (2017) | planned |
| Support vector regression | Feature vector | SOH | ICA features after Weng et al. (2013) | planned |
| Random forest and extra trees | Feature vector | SOH | Robust default | planned |
| Gradient boosting (LightGBM or XGBoost) | Feature vector | SOH | Strong tabular baseline | planned |
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

## Selection criteria for adding a model

A model is added when it is (a) representative of a family or (b) a well-cited published
method whose results can be checked against a public dataset. Marginal variants of an
existing entry are not added unless they change an input requirement, a data requirement or
a transfer property.
