# 1. SOH fundamentals

This chapter defines what this repository estimates, why it is hard to measure, and how the
underlying degradation shows up in the signals a model can see.

## 1.1 Definitions

**State of Health (SOH)** is a normalised measure of how much of a cell's original
performance remains. There is no single agreed definition; the two dominant ones are:

- **Capacity-based SOH.** `SOH_C = Q_now / Q_ref`, where `Q_now` is the capacity measured
  under a defined reference condition and `Q_ref` is either the nominal (datasheet)
  capacity or the capacity measured at beginning of life (BOL) under the same reference
  condition. *This repository uses the measured BOL capacity as `Q_ref` by default*
  (see `docs/decisions/0001-primary-soh-definition.md`). Nominal capacities are set
  conservatively by manufacturers and are defined under different conditions, so using them
  makes fresh cells appear to exceed 100 % and makes datasets incomparable.
- **Resistance-based (power) SOH.** `SOH_R = (R_EOL - R_now) / (R_EOL - R_BOL)` or, more
  simply, `R_BOL / R_now`, where `R` is a defined internal resistance (a DC pulse resistance at
  fixed SOC and temperature, or an EIS-derived value). Power fade and capacity fade are
  correlated but not identical, and they matter differently for energy and power applications.

Quantities that are often bundled with SOH but are distinct:

| Quantity | What it is | Relation to SOH |
| --- | --- | --- |
| State of Charge (SOC) | Fraction of the *current* capacity that is available | Depends on SOH; estimated on much faster time scales |
| State of Function (SOF) | Whether the cell can deliver a requested power or energy now | A function of SOC, SOH and temperature |
| Remaining Useful Life (RUL) | Cycles or time until SOH reaches an end-of-life threshold | A forecast of the SOH trajectory |
| Cycle life | Total cycles from BOL to end of life | RUL evaluated at cycle 0 |
| End of life (EOL) | An application-defined threshold, commonly 80 % of BOL capacity for automotive first life; 70 % to 60 % is used for second-life storage | Sets the target of RUL |

**Time scales.** SOC changes within seconds to hours; SOH changes over weeks to years. This
is why SOH can be treated as a slowly varying parameter inside SOC estimators, and why SOH
problems are framed per cycle or per reference test rather than per sample.

## 1.2 Why SOH is hard to measure

Capacity is not a fixed number. It depends on:

- **Rate.** Higher C-rates deliver less capacity because of transport and kinetic limits.
- **Temperature.** Cold cells deliver less capacity, and the effect grows with age.
- **Voltage limits.** Cut-off voltages define which fraction of the electrodes is used.
- **Path history.** A recent rest or partial cycle changes the next measured capacity (the
  "capacity regeneration" seen in the NASA data after rest periods).
- **Measurement drift.** Coulomb counting accumulates current-sensor bias.

For this reason SOH labels must come from a **reference performance test (RPT)**: a standard
sequence (for example a full CC-CV charge, a rest, and a low-rate full discharge at a
controlled temperature) repeated periodically through life. Datasets differ in how often and
how consistently they run RPTs; `docs/04-datasets.md` records this per dataset, and the
`labels/` module implements per-dataset rules.

Three practical consequences:

1. **Labels are sparse.** Many datasets have an RPT every 50 to 100 cycles. In between, the
   cycling capacity is only a proxy.
2. **Labels are noisy.** RPT capacity typically scatters by a fraction of a percent; a model
   that claims 0.1 % error is fitting that noise.
3. **Labels are protocol-dependent.** SOH from a 1C discharge is not the same number as SOH
   from a C/25 discharge. Cross-dataset comparisons must respect this.

## 1.3 How cells degrade

### Mechanisms (the physics)

| Mechanism | Where | Driven by | Main effect |
| --- | --- | --- | --- |
| Solid electrolyte interphase (SEI) growth | Negative-electrode surface | Time, temperature, high SOC | Consumes cyclable lithium; raises resistance; often grows with the square root of time |
| Lithium plating | Negative electrode | Low temperature, high charge rate, high SOC | Consumes lithium, partly reversible (stripping); a safety risk |
| Particle cracking and loss of contact | Both electrodes | High rates, large SOC swings, mechanical stress | Loss of active material; fresh surface for SEI |
| Transition-metal dissolution and cathode surface reconstruction | Positive electrode | High voltage, high temperature | Loss of active material; resistance rise |
| Electrolyte decomposition and gas evolution | Everywhere | High voltage, high temperature | Resistance rise; dry-out |
| Binder decomposition, current-collector corrosion | Electrodes and collectors | Over-discharge, high temperature | Contact loss; resistance rise |

### Modes (what a model can identify)

Mechanisms are not observable at the terminals. **Degradation modes** are the level at which
diagnosis is possible from voltage data:

- **LLI, loss of lithium inventory.** Lithium tied up in SEI, plating or side reactions.
- **LAM_PE and LAM_NE, loss of active material** at the positive and negative electrode.
- **Conductivity loss and ohmic resistance increase (CL, ORI).**

Each mode shifts and scales the electrode potential curves relative to each other in a
characteristic way, so the full-cell open-circuit voltage curve, its derivative (incremental
capacity, dQ/dV) and the differential voltage (dV/dQ) carry a fingerprint of the mode mix.
This is the basis of ICA/DVA diagnosis and of the synthetic-data approach to training
diagnostic models. See Dubarry et al. (2012), Birkl et al. (2017) and Edge et al. (2021) in
the reading list.

### Stress factors

| Stress factor | Typical effect |
| --- | --- |
| Temperature | Arrhenius-like acceleration of SEI growth when hot; plating when cold; both ends of the range are harmful |
| SOC window and depth of discharge | High average SOC accelerates calendar aging; large swings accelerate mechanical damage |
| Charge rate | The main driver of plating; discharge rate matters less until heating dominates |
| Calendar time | Aging continues at rest; datasets that report only cycle counts lose this variable |
| Cell-to-cell variability | Nominally identical cells differ in cycle life by tens of percent under identical conditions (see the RWTH Aachen and HNEI datasets) |

## 1.4 Trajectory shapes

SOH-versus-cycle curves do not share one shape. Common patterns:

- **Square-root-of-time** decay (SEI-dominated, typical of calendar aging).
- **Near-linear** fade (cycling under mild conditions).
- **Knee.** An abrupt acceleration of fade, often linked to plating or electrolyte depletion;
  predicting knee onset is a task of its own (see the review by Attia et al., 2022).
- **Plateaus and regeneration** after rest periods.

Models that assume one functional form fail at knees; models trained on smooth datasets may
never see a knee until deployment. The benchmark therefore reports errors separately before
and after the knee where one exists.

## 1.5 Observable health indicators

Signals available to an estimator, in decreasing order of information and increasing order
of field availability:

1. Full low-rate reference discharge capacity (lab only).
2. Full constant-current charge or discharge curve.
3. Constant-current partial-charge segment (common in vehicles, because charging is more
   repeatable than driving).
4. Constant-voltage phase duration and current tail.
5. Voltage relaxation after charge or discharge.
6. Electrochemical impedance spectroscopy (lab; on-board variants exist).
7. Pulse resistance (HPPC) at fixed SOC.
8. Temperature rise during a step.
9. Coulombic efficiency, capacity throughput, calendar time and average SOC (usage history).

The estimation strategies in the next chapter differ mostly in which of these they need.

## 1.6 Labels in this repository

For each dataset, the `labels/` module defines:

- how reference tests are detected (explicit RPT flags, protocol pattern matching, or
  low-rate discharge steps);
- `Q_ref` as the first reference-test capacity, or the median of the first few where the
  first cycle is a formation artefact;
- `SOH_C` at every reference cycle, with optional interpolation between them flagged as
  `interpolated = true`;
- `SOH_R` where pulse or EIS data exist;
- exclusion rules for corrupted cycles, and a list of known anomalies.

Nothing in the label pipeline uses partial-cycle features, so features and labels are
independent by construction.
