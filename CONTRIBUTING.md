# Contributing

Contributions are welcome in three forms: datasets, models and results. All three end up in
the reference, so each comes with a card.

## Ground rules

- Read `docs/05-evaluation-protocol.md` before running anything you intend to submit.
- Never commit raw dataset files. Loaders download and verify checksums.
- Cell-level splits only; use the frozen split files under `splits/`.
- One PR per dataset, model or result set. Keep PRs reviewable.
- Code style: ruff (lint and format), type hints on public functions, tests under `tests/`.

## Adding a dataset

1. Add a registry entry: key, name, DOI or URL, checksum, licence, citation.
2. Implement a loader that produces the canonical schema (`cell`, `cycle`, `timeseries`,
   optionally `eis`).
3. Implement label rules in `labels/` (reference-test detection, `Q_BOL`, exclusions).
4. Add integrity tests (cell count, cycle count ranges, monotonic time, plausible capacities).
5. Fill in the dataset card below and add it to `docs/04-datasets.md`.

### Dataset card template

```text
Key:                   <short key>
Name:                  <full name>
Source:                <DOI or URL>
Licence:               <licence text or link>
Citation:              <primary reference>
Chemistry / format:    <e.g. NMC/graphite, 18650>
Cells:                 <count, and how many are complete>
Conditions:            <temperatures, protocols, SOC windows, rates>
Reference tests:       <what and how often; how labels are derived>
Signals available:     <I, V, T, EIS, expansion, ...>
Known issues:          <anomalies, corrupted cells, regeneration events>
Good for:              <tasks and strategy families>
Loader version:        <package version in which the loader first appeared>
```

## Adding a model

1. Implement it under the matching family directory in `src/battery_worldcup/models/`.
2. Provide a config under `configs/` that runs it on at least one wave-1 dataset.
3. Declare input requirements (full charge, rest, temperature, EIS) in the model class.
4. Add a unit test on synthetic data and, if applicable, a reproduction target from the
   literature.
5. Fill in the model card below and add the model to `docs/03-model-catalog.md`.

### Model card template

```text
Name:                  <name and version>
Family:                <S1..S10 from docs/02>
Reference:             <paper, if any>
Inputs:                <signals and windows required>
Outputs:               <SOH point, interval, trajectory, mode vector>
Input requirements:    <full charge / rest / temperature / EIS flags>
Training data needed:  <cells, cycles, label density>
Hyper-parameters:      <list and search space used>
Compute:               <training time, inference time, hardware>
Known limitations:     <where it fails>
Reproduced results:    <published number, our number, tolerance>
```

## Submitting a result

1. Run `bwc run configs/<file>.yaml --seed <s>` for at least three seeds.
2. Commit the result JSON files under `results/<dataset>/<task>/`.
3. Do not edit result files by hand; the leaderboard builder rejects files whose config hash
   does not match.
4. Open a PR with the config, results and (if new) the model card. CI re-runs a reduced
   version of the task to check that the config executes.
