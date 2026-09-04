# 0001. Primary SOH definition

Status: accepted (phase 0)

## Context

Published SOH numbers use different denominators: the datasheet nominal capacity, the
capacity measured at beginning of life under the dataset's reference test, or the capacity
of the first cycling cycle. Datasets also differ in the rate and temperature of the reference
test. Mixing these makes cross-dataset tables meaningless and makes fresh cells appear to
exceed 100 %.

## Decision

- The primary label is `SOH_C = Q_now / Q_BOL`, where both capacities come from the dataset's
  reference test (or, if none exists, from the lowest-rate full discharge available), and
  `Q_BOL` is the first reference-test capacity or the median of the first few when the first
  cycle is a formation artefact. The rule is written per dataset in the `labels/` module.
- The nominal capacity is stored in the `cell` table for reference but is never used as a
  denominator in results.
- Secondary labels are `SOH_R` (resistance-based, where pulse or EIS data exist) and the
  degradation-mode vector (LLI, LAM_PE, LAM_NE) where it can be derived.
- End of life defaults to 80 % of `Q_BOL`; tasks may override it per dataset.

## Consequences

- Cross-dataset comparisons remain approximate because reference-test conditions differ; the
  dataset card records the conditions, and transfer results state them.
- Datasets without any reference test (field data) need pseudo-labels, handled in phase 7,
  and are flagged as such on the leaderboard.
