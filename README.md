# battery-worldcup

**A reference and open benchmark for lithium-ion battery State of Health (SOH) estimation.**

SOH models come from very different traditions: empirical fade laws, equivalent-circuit
models tracked with Kalman filters, hand-engineered health indicators fed to regressors,
deep sequence models on raw cycling data, electrochemical models with degradation
sub-models, and hybrids of all of these. They are rarely compared under identical rules.

This repository organises them like a tournament. Every strategy plays on the same public
datasets, with the same cell-level splits and the same metrics, and results are published
on a versioned leaderboard. The point is not only to crown a winner but to make the
trade-offs visible: how much data each approach needs, which inputs it requires (a full
charge? a rest period? an impedance spectrum?), how it degrades under distribution shift,
whether it knows when it is wrong, and what it costs to run.

## What you will find here

| Goal | What it means in practice |
| --- | --- |
| **Reference** | Curated, citable documentation: what SOH is, how cells degrade, which estimation strategies exist, which public datasets are usable, and how methods must be evaluated. |
| **Benchmark** | One canonical data schema, fixed tasks and splits, one metrics module, leakage checks, and a leaderboard generated from result files. |
| **Model zoo** | Reproducible implementations of representative models from every strategy family, each with a model card. |

## Status

Phase 0 (foundation). The documentation set below is in place; code lands phase by phase as
described in [ROADMAP.md](ROADMAP.md).

## Documentation map

| Document | Question it answers |
| --- | --- |
| [docs/01-soh-fundamentals.md](docs/01-soh-fundamentals.md) | What is SOH, how do cells degrade, and how are SOH labels obtained? |
| [docs/02-estimation-strategies.md](docs/02-estimation-strategies.md) | Which families of estimation strategies exist, and when should each be used? |
| [docs/03-model-catalog.md](docs/03-model-catalog.md) | Which concrete models will be implemented and compared? |
| [docs/04-datasets.md](docs/04-datasets.md) | Which public datasets exist, what is in them, and what is each good for? |
| [docs/05-evaluation-protocol.md](docs/05-evaluation-protocol.md) | Tournament rules: tasks, splits, metrics, leakage rules, leaderboard format |
| [docs/06-reading-list.md](docs/06-reading-list.md) | Key papers, reviews, datasets and tools |
| [docs/glossary.md](docs/glossary.md) | Terms and abbreviations |
| [docs/decisions/](docs/decisions/) | Records of decisions that are hard to reverse |
| [ROADMAP.md](ROADMAP.md) | The phased plan to build all of the above |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to add a dataset, a model, or a result |

## Strategy map at a glance

| Family | Core idea | Needs | Typical strength | Typical weakness |
| --- | --- | --- | --- | --- |
| Empirical aging models | Fit capacity fade against time, cycles and stress with closed-form laws | Capacity history | Interpretable, tiny | Fails at knees; parameters are condition-specific |
| Model-based (ECM or physics + filter) | Track capacity and resistance as slowly varying parameters online | Current, voltage, temperature streams | Runs on a BMS; needs no aging dataset | Sensitive to model mismatch and OCV drift with age |
| Health indicators + regression | Extract features (ICA/DVA peaks, partial charge, relaxation, EIS) and regress SOH | A repeatable segment per cycle | Data-efficient, interpretable | Segment must exist in the data; smoothing choices matter |
| End-to-end deep learning | Learn from raw voltage/current/temperature windows | Many labelled cells | Best in-distribution accuracy | Data hungry; brittle under shift |
| Physics-based diagnosis | Fit degradation modes (LLI, LAM) via OCV/half-cell models or DFN | Low-rate OCV or well-characterised cells | Explains why capacity was lost | Needs characterisation; identifiability limits |
| Hybrid / physics-informed | Constrain or augment ML with physics | A mix of the above | Better extrapolation, fewer samples | Engineering effort; wrong constraints hurt |
| Transfer and probabilistic wrappers | Adapt across chemistries and protocols; quantify uncertainty | Source data plus a few target cells | Field readiness | Extra tuning and validation |

See [docs/02-estimation-strategies.md](docs/02-estimation-strategies.md) for the full treatment.

## Planned repository layout

```text
battery-worldcup/
├── README.md, ROADMAP.md, CONTRIBUTING.md, LICENSE
├── docs/                      # the reference (this documentation set; later a docs site)
├── src/battery_worldcup/
│   ├── data/                  # canonical schema, dataset loaders, registry, caching
│   ├── labels/                # reference-test detection and SOH label construction
│   ├── features/              # ICA/DVA, partial-charge, relaxation, EIS, statistical features
│   ├── models/
│   │   ├── empirical/         # fade laws, knee models
│   │   ├── filters/           # ECM + EKF/UKF/PF, dual estimation
│   │   ├── ml/                # ridge, GPR, SVR, tree ensembles
│   │   ├── deep/              # CNN, RNN, TCN, Transformer
│   │   ├── physics/           # PyBaMM-based diagnosis and parameter tracking
│   │   └── hybrid/            # physics-informed and residual hybrids
│   ├── tasks/                 # task definitions and split generation
│   ├── metrics/               # point, trajectory, probabilistic and cost metrics
│   ├── benchmark/             # runner, result schema, leaderboard builder
│   └── cli.py                 # `bwc data ...`, `bwc run ...`, `bwc leaderboard`
├── configs/                   # experiment configs (dataset x task x model x split x seed)
├── splits/                    # frozen, versioned split files
├── results/                   # result JSON files and generated leaderboards
├── notebooks/                 # tutorials, one per strategy family
└── tests/                     # unit tests on synthetic cells, leakage tests, smoke tests
```

## Scope and non-goals

In scope: cell-level SOH estimation, SOH trajectory forecasting, remaining useful life,
early-life prediction, degradation-mode diagnosis, transfer across datasets, uncertainty
quantification.

Out of scope for now: state-of-charge estimation as an end in itself, pack-level balancing,
thermal runaway and safety, manufacturing quality, non-lithium chemistries (welcome later).

## License

MIT for the code and documentation. Datasets keep their own licences; nothing is
redistributed here. Loaders download from the original sources and verify checksums.
