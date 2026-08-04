# Reporting Impact Example: cost_centre Format Drift -> Finance Spend-by-Cost-Centre KPI

**Traced to:** ISSUE-007 (Finance / `legacy_finance` / `cost_centre` / consistency)
**Source finding:** [GL-000029] cost_centre is stored as '009', which doesn't match the standard 'CC-0NN' format used elsewhere - likely the same cost centre recorded in a different format.
**Resolution (Stage 5):** cost_centre normalised via canonical mapping in Stage 5: '009' -> 'CC-009'.

## The KPI

"Total Finance Amount by Cost Centre" - the number a stakeholder reads off a
spend-by-cost-centre report, e.g. to check a budget owner's net spend for the
period.

## Why this example

Cost centre format drift is the cleanest cascade to demonstrate end-to-end: a
single, specific data error (`GL-000029`'s cost_centre stored as
`'009'` instead of `'CC-009'`) has a direct,
provable effect on a rollup KPI, with no other variable involved. (The
alternative candidate - held-back orphaned rows affecting a total PO value KPI
- is already covered by the row-count and business-rule sections of
`docs/reconciliation_report.md`; this report focuses on the one cascade type
reconciliation doesn't already show: values that don't get excluded, they get
silently *misgrouped*.)

## Scope note

Both totals below exclude the 3 Finance row(s) held
back for unrelated reasons (GL-000034 (ISSUE-014), GL-000124 (ISSUE-015), GL-000125 (ISSUE-006) - see
`docs/reconciliation_report.md`), so this comparison isolates the cost_centre
format-drift effect specifically, not mixed with the held-back-row migration
policy.

## Before: legacy `cost_centre`, as entered at source

| cost_centre | rows | total_amount |
|---|---|---|
| 009 | 1 | GBP -2,831.56 |
| CC-001 | 12 | GBP 111,727.77 |
| CC-002 | 13 | GBP 35,883.22 |
| CC-003 | 9 | GBP 80,343.32 |
| CC-004 | 11 | GBP 69,420.87 |
| CC-005 | 16 | GBP 99,744.99 |
| CC-006 | 13 | GBP 112,339.13 |
| CC-007 | 14 | GBP 130,577.59 |
| CC-008 | 11 | GBP 106,498.24 |
| CC-009 | 19 | GBP 141,860.09 |
| CC-010 | 10 | GBP 78,240.27 |
| CC-011 | 14 | GBP 135,250.72 |
| CC-012 | 14 | GBP 51,004.29 |
| CC-013 | 12 | GBP 107,610.16 |
| CC-014 | 15 | GBP 148,752.43 |
| CC-015 | 13 | GBP 94,906.61 |

`CC-009` was split across **two rows** here: `'009'`
(1 row, GBP -2,831.56) and `'CC-009'` (19 rows,
GBP 141,860.09).

## After: target `cost_centre`, normalised in Stage 5

| cost_centre | rows | total_amount |
|---|---|---|
| CC-001 | 12 | GBP 111,727.77 |
| CC-002 | 13 | GBP 35,883.22 |
| CC-003 | 9 | GBP 80,343.32 |
| CC-004 | 11 | GBP 69,420.87 |
| CC-005 | 16 | GBP 99,744.99 |
| CC-006 | 13 | GBP 112,339.13 |
| CC-007 | 14 | GBP 130,577.59 |
| CC-008 | 11 | GBP 106,498.24 |
| CC-009 | 20 | GBP 139,028.53 |
| CC-010 | 10 | GBP 78,240.27 |
| CC-011 | 14 | GBP 135,250.72 |
| CC-012 | 14 | GBP 51,004.29 |
| CC-013 | 12 | GBP 107,610.16 |
| CC-014 | 15 | GBP 148,752.43 |
| CC-015 | 13 | GBP 94,906.61 |

`CC-009` now correctly rolls up to **one row**: 20 rows,
GBP 139,028.53.

## What a stakeholder relying on the "before" report would have gotten wrong

Pre-migration, cost centre CC-009 appeared to have **two**
different budget lines in the legacy report - one under `CC-009`
(GBP 141,860.09 across 19 transactions) and a separate, easy-to-miss
one under the malformed code `009` (GBP -2,831.56, a single credit entry).
A stakeholder searching a report for "CC-009" would only find the first line
and would report net spend for the cost centre as **GBP 141,860.09** -
2,831.56 higher than the true, correctly-consolidated total of
**GBP 139,028.53**, because the credit entry sitting under the
mis-formatted `009` code was effectively invisible to anyone querying by the
canonical cost centre code. Post-migration, cost centre CC-009
correctly shows as one line, with the credit included.

This is not a rounding difference or a missing row - it is the exact same
20 transactions in both cases, 1 of them (the credit
entry) simply invisible to a report keyed on the canonical cost centre code
until the format was normalised.
