# 4. Public datasets

This catalog lists the public datasets the benchmark will use, what each contains and what
each is good for. Facts were checked against the source publications or hosting pages;
counts marked "(confirm)" must be verified when the loader is written. Nothing is
redistributed in this repository: loaders download from the source and verify checksums.
Always cite the original publication.

## 4.1 Summary table

| Key | Dataset | Institution | Chemistry and format | Cells | Conditions | Best for | Wave |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `nasa` | NASA PCoE Battery Data Set | NASA Ames | 18650, about 2 Ah | about 34 | Several temperatures; various discharge cut-offs; EIS interleaved | Classic RUL and SOH baselines; capacity regeneration; small-data behaviour | 1 |
| `nasa_rw` | NASA Randomized Battery Usage | NASA Ames | 18650 | 28 | Random-walk loads with periodic reference discharges | SOH under non-repetitive usage | 2 |
| `calce` | CALCE CS2 and CX2 | University of Maryland | LCO prismatic; 1.1 Ah (CS2), 1.35 Ah (CX2) | 13 commonly used (more on the CALCE site) | 0.5C CC-CV charge; varied discharge rates | ICA features; filter-based SOH; small data | 1 |
| `oxford` | Oxford Battery Degradation Dataset 1 | University of Oxford | Kokam SLPB533459H4 pouch, 740 mAh | 8 | 40 °C; 1C charge; drive-cycle discharge; characterisation every 100 cycles with a C/25 pseudo-OCV | Degradation-mode diagnosis; ICA and DVA; pseudo-OCV features | 1 |
| `matr` | MATR (Severson 2019, Attia 2020) | MIT, Stanford, Toyota Research Institute | A123 APR18650M1A LFP/graphite, 1.1 Ah | 124 (Severson); about 180 with the Attia cells | 30 °C chamber; 72 fast-charging protocols in Severson, more in Attia; 4C discharge | Early-life cycle-life prediction; large homogeneous population; deep models | 1 |
| `hust` | HUST | Huazhong University of Science and Technology | A123 LFP/graphite, 1.1 Ah | 77 | Identical charge; 77 distinct multi-stage discharge protocols; 30 °C; more than 140,000 cycles | Protocol-shift transfer; RUL | 2 |
| `xjtu` | XJTU | Xi'an Jiaotong University | LISHEN 18650 NCM (LiNi0.5Co0.2Mn0.3O2), 2 Ah | 55 in 6 batches | Different charge and discharge protocols per batch, including random-current and satellite profiles; all cycled below 80 % | Cross-protocol transfer; NCM chemistry | 2 |
| `snl` | Sandia (Preger 2020) | Sandia National Laboratories | LFP (A123, 1.1 Ah), NCA (Panasonic NCR18650B, 3.2 Ah), NMC (LG Chem 18650HG2, 3 Ah) | about 61 on Battery Archive | 15, 25, 35 °C; SOC windows 0-100, 20-80, 40-60 %; 0.5C to 3C discharge | Cross-chemistry and stress-factor studies | 2 |
| `hnei` | HNEI | Hawaii Natural Energy Institute | NMC-LCO 18650, 2.8 Ah | 51 in the study; 14 on Battery Archive | C/2 charge; 1.5C discharge; more than 1000 cycles; room temperature | Cell-to-cell variability; long life | 2 |
| `ulpur` | UL-PUR | Underwriters Laboratories and Purdue | Commercial pouch cells | 10 | 1C cycling between 2.7 and 4.2 V; room temperature | Pouch-format degradation | 2 |
| `mich` | Michigan (MICH, MICH_EXP) | University of Michigan | NMC/graphite pouch | (confirm) | Varied C-rate, temperature and SOC windows; in-situ expansion | Mechanical health indicators | 3 |
| `rwth` | RWTH Aachen ISEA | RWTH Aachen University | Sanyo/Panasonic UR18650E NMC/graphite | 48 | Identical profile for all cells; begin-of-life test; regular reference parameter tests | Cell-to-cell variability; one-shot trajectory prediction | 2 |
| `isu_ilcc` | ISU-ILCC | Iowa State University and Iowa Lakes Community College (now UConn REIL) | NMC/graphite lithium-polymer pouch | 251 (238 in release 2.0) | 63 unique conditions spanning charge rate, discharge rate and depth of discharge | Stress-factor modelling; transfer across DoD and rates | 2 |
| `tongji_relax` | Tongji voltage relaxation (Zhu 2022) | Tongji University and partners | 18650 NCA, NCM and NCM+NCA | 130 across 3 sub-datasets | 25, 35, 45 °C; relaxation after full charge | Relaxation-based estimation; cross-chemistry and cross-temperature | 2 |
| `cam_eis` | Cambridge EIS (Zhang 2020) | University of Cambridge | Eunicell LR2032 LCO coin cells, 45 mAh | 12 | 25, 35, 45 °C; impedance spectra at several stages of every cycle | EIS-based SOH and RUL | 2 |
| `stanford_ev` | Stanford EV driving-profile aging (Pozzato 2022) | Stanford Energy Control Lab | LG INR21700-M50T NMC with graphite-silicon anode | 10 | UDDS discharge; CC-CV charge from C/4 to 3C; 23 months; periodic diagnostics | Realistic load profiles; filter-based SOH | 2 |
| `stanford_2nd` | Stanford second-life grid-storage aging (2024) | Stanford Energy Control Lab | Retired NMC cells | (confirm) | Grid-storage cycling profiles | Second-life SOH | 3 |
| `dubarry_synth` | Synthetic degradation-mode ICA and DVA sets | HNEI (Dubarry and Beck 2020) | Synthetic LFP, NMC, NCA | not applicable | Sweeps of LLI, LAM_PE and LAM_NE | Training and validating mode diagnosis | 2 |

Wave is the roadmap phase-1 wave in which the loader is planned; 3 means later.

## 4.2 Aggregators and related benchmarks

- **Battery Archive** (batteryarchive.org, maintained by Sandia) hosts Sandia, HNEI, UL-PUR,
  Oxford, CALCE and Michigan data in a common CSV format (time series and per-cycle files).
  A good first target for a shared loader.
- **BatteryML** (Microsoft) provides preprocessing and standard splits for CALCE, HUST, MATR,
  RWTH, SNL, UL-PUR and HNEI, with combined sets (CRUH, CRUSH, MIX). Useful for cross-checking
  our splits and baselines.
- **BatteryLife** (Tan et al., KDD 2025) integrates 16 datasets, 990 cells, 8 formats, 59
  chemical systems, 9 temperatures and 421 protocols for life prediction, including zinc-ion,
  sodium-ion and industrial large-format cells. The natural reference for the transfer tasks.
- **"Lithium-ion battery data and where to find it"** (dos Reis et al., 2021) is the standard
  survey of public data; re-check it when adding wave-3 datasets.

## 4.3 Notes per dataset

### NASA PCoE
Charge is CC 1.5 A to 4.2 V then CV until the current drops to 20 mA; discharge is CC 2 A to
cut-off voltages that differ per cell; EIS runs are interleaved. The original end-of-life
criterion was 30 % capacity fade. Known issues: few cells, capacity regeneration after rests,
inconsistent cycle counts, some corrupted files. Use it for illustration and small-data
behaviour, not for ranking models.

### CALCE CS2 and CX2
Prismatic LCO cells charged at 0.5C CC-CV (CV to 0.05 A) and discharged at constant current
to 2.7 V at various rates. Widely used in ICA and filter papers, so it is the natural place
to reproduce classic feature-based results.

### Oxford Battery Degradation Dataset 1
Eight Kokam 740 mAh pouch cells at 40 °C, charged at 1C CC-CV and discharged with an urban
drive cycle; every 100 cycles a characterisation with a 1C discharge and a C/25 pseudo-OCV
charge and discharge. The pseudo-OCV data make it the reference dataset for degradation-mode
diagnosis (Birkl et al., 2017).

### MATR (Severson 2019, Attia 2020)
A123 LFP/graphite 18650 cells cycled in a 30 °C chamber with one- and two-step fast-charging
protocols and 4C discharge, to 80 % capacity. Cycle lives span roughly 150 to 2300 cycles.
The first 100 cycles predict cycle life well (the variance of ΔQ(V) between cycles 100 and
10), which makes this the reference for early-life prediction. The homogeneous chemistry and
temperature make transfer out of MATR hard, which is exactly what task T6 needs.

### HUST
77 A123 LFP cells with the same charging protocol and 77 different multi-stage discharge
protocols at 30 °C, more than 140,000 cycles in total, published with the deep transfer
learning paper of Ma et al. (2022). The discharge-protocol diversity makes it the main
dataset for protocol-shift experiments alongside MATR.

### XJTU
55 LISHEN NCM 18650 cells (2 Ah) in six batches; charging is fixed for the first five batches
while discharge varies across fixed-current, random-current and satellite (GEO) profiles; all
cells run to below 80 %. The dataset authors maintain a list of papers that use it, which
helps with reproduction targets.

### Sandia (SNL)
Three chemistries under a factorial design of temperature, SOC window and discharge rate. It
is the only wave-2 dataset with LFP, NCA and NMC from one lab under one protocol family, so
it anchors the cross-chemistry bracket. Preger et al. report that the fade rate increases
with temperature for LFP, decreases for NMC and shows no strong dependence for NCA.

### HNEI
Nominally identical NMC-LCO cells under one protocol for more than 1000 cycles: cell-to-cell
variability under identical conditions.

### RWTH Aachen
48 Sanyo/Panasonic UR18650E cells aged with the same profile, with begin-of-life tests and
regular reference parameter tests. Used for one-shot trajectory prediction from
manufacturing variability (Li et al., 2021).

### ISU-ILCC
251 NMC/graphite pouch cells under 63 conditions crossing charge rate, discharge rate and
depth of discharge (release 2.0 contains 238 completed cells). A companion LFP/graphite
dataset from the same group exists. This is the largest designed stress-factor grid
available, ideal for empirical and hybrid stress models.

### Tongji voltage relaxation
130 18650 cells (NCA, NCM and NCM+NCA sub-datasets) cycled at 25, 35 and 45 °C. Zhu et al.
(2022) showed that three statistics of the rest voltage after a full charge (variance,
skewness, maximum) predict capacity across chemistries. Data are on Zenodo.

### Cambridge EIS
12 LCO coin cells at three temperatures with impedance spectra recorded at several stages of
every cycle (Zhang et al., 2020). Small cells, but the only dense EIS-over-life dataset.

### Stanford EV driving profile
LG INR21700-M50T cells with UDDS discharge and CC-CV charging at rates from C/4 to 3C over 23
months, with periodic diagnostics (Pozzato et al., 2022). The most realistic load profile
among public lab datasets, and the natural test for filter-based estimators.

### Synthetic degradation-mode sets
Dubarry and Beck (2020) published large synthetic ICA and DVA sets generated by sweeping LLI
and LAM for several chemistries. They are the training ground for mode diagnosis (task T5).

## 4.4 What is still missing publicly

- **Field data** from vehicles or stationary storage with occasional reference tests. Sulzer
  et al. (2021) describe the challenge; Aitio and Howey (2021) used off-grid solar
  home-system data. Phase 7 simulates field constraints on lab data until real field datasets
  are usable.
- **Calendar-aging series** with matched cycling data for the same cell type.
- **Large-format and pack-level data** with cell-level labels (BatteryLife includes some
  industrial large-format cells).

## 4.5 Data policy

- Raw data are never committed. Loaders fetch from the DOI or URL in the registry and verify
  a checksum.
- The registry stores licence and citation for each dataset; the leaderboard prints the
  citation for every dataset used.
- Processed Parquet caches live outside the repository (by default under
  `~/.cache/battery_worldcup`).
- A dataset whose licence forbids redistribution of derived data is still usable, but its
  cached files are never uploaded anywhere.
