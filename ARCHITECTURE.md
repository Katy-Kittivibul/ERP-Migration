# ERP Migration Intelligence Platform — Architecture & Design

## Purpose

Portfolio project simulating the SAP ECC → target platform migration work described in
[Data Analyst — Enterprise Transformation Programme]. Demonstrates: data discovery,
issue structuring (cause/impact), cleansing, mapping, mock migration + reconciliation,
and downstream reporting-impact awareness — across HR, finance, procurement, and expenses.

Timeline: 1 week, part-time. MVP scope. A shared engine runs against all four domains
rather than four bespoke pipelines — do not build domain-specific logic unless a domain
genuinely requires it (documented exception, not default).

## Core Design Principles

1. **Storage**: DuckDB. Single file at `data/db/erp.duckdb`. No Postgres — adds ops
   overhead with zero portfolio value for this project.
2. **Legacy schema**: synthetic, SAP ECC-*flavoured* (table names like `BUT000`-style
   business partner, `MSEG`-style material movements) but simplified — not a full
   IDoc/BAPI replica. Realism over completeness.
3. **Domains**: HR, Finance, Procurement, Expenses. Each domain = one legacy table +
   one target table + a mapping spec between them.
4. **Issue injection**: issues are injected programmatically with a manifest
   (`data/raw/injected_issues.json`) recording ground truth — every issue the discovery
   module *should* find. This is what lets you score recall/precision of your own
   detection logic in the CV/interview ("caught 47 of 50 injected issues, missed 3 due to X").
5. **Problem statement format** (fixed, do not vary):
   ```
   Issue ID | Domain | Table | Column | Issue Type | Description |
   Root Cause (hypothesis) | Business Impact | Status | Resolution
   ```
6. **Issue types** (closed set, from JD's own language):
   `completeness`, `accuracy`, `consistency`, `duplication`, `obsolete_orphaned`
7. **Reconciliation method**: row count deltas + column-level checksum (hash of
   normalised row) + business-rule checks (e.g. "total expense amount by cost centre
   must match pre/post migration"). No fuzzy matching in MVP — flag as future work.
8. **Dashboard**: Streamlit, single page with tabs (Discovery / Issue Log / Mapping /
   Migration Reconciliation / Reporting Impact). Non-technical-stakeholder-readable —
   plain language issue descriptions, not raw stack traces.
9. **No ML/GNN components.** This project is deliberately NOT a modelling showcase —
   it demonstrates ERP/migration domain fluency. 

## Architecture

```
erp-migration-platform/
├── CLAUDE.md
├── data/
│   ├── raw/
│   │   ├── legacy_hr.csv
│   │   ├── legacy_finance.csv
│   │   ├── legacy_procurement.csv
│   │   ├── legacy_expenses.csv
│   │   └── injected_issues.json       # ground truth manifest
│   ├── target_schema/
│   │   └── *.sql                      # target table DDL, one per domain
│   └── db/
│       └── erp.duckdb
├── modules/
│   ├── generate_synthetic_data.py     # builds legacy_*.csv + injects issues
│   ├── discovery/
│   │   └── profiler.py                # completeness/accuracy/consistency/dup/orphan checks
│   ├── issue_tracker/
│   │   └── issue_log.py               # writes structured issues to DuckDB + docs/problem_statements/
│   ├── mapping/
│   │   ├── mapping_spec.yaml          # legacy col -> target col, one per domain
│   │   └── mapping_validator.py       # referential integrity + type/format checks
│   ├── migration_sim/
│   │   ├── migrate.py                 # applies mapping, writes target tables
│   │   └── reconcile.py               # pre/post checksum + business rule reconciliation
│   └── reporting_impact/
│       └── impact_report.py           # shows a KPI before/after a specific DQ fix
├── dashboard/
│   └── app.py
├── tests/
│   ├── test_profiler.py
│   ├── test_mapping_validator.py
│   └── test_reconcile.py
└── docs/
    ├── problem_statements/            # auto-generated, one .md per issue
    └── mapping_spec_notes.md
```

## Domain schemas (MVP scope — keep columns minimal)

**HR** (`legacy_hr.csv`): employee_id, name, dept_code, job_title, hire_date,
termination_date, employment_status, cost_centre, manager_id

**Finance** (`legacy_finance.csv`): gl_entry_id, cost_centre, account_code, amount,
currency, posting_date, document_type, vendor_id

**Procurement** (`legacy_procurement.csv`): po_id, vendor_id, vendor_name, material_code,
quantity, unit_price, po_date, delivery_status, cost_centre

**Expenses** (`legacy_expenses.csv`): expense_id, employee_id, cost_centre, category,
amount, submitted_date, approval_status, currency

Shared join key across domains: `cost_centre`. Shared identity key HR↔Expenses:
`employee_id`. Shared identity key Finance↔Procurement: `vendor_id`.
This is what makes the "downstream reporting impact" module possible — a bad
cost_centre in HR cascades into a finance/expenses rollup KPI.

**Target-only addition — `target_vendor_master`** (vendor_id, canonical_vendor_name):
identified as a gap during mapping (Stage 3 sign-off). `Procurement.vendor_name` is
dropped from the target transactional table (denormalised, duplicate-prone — see
injected duplication issues) and replaced by this master table, built by
de-duplicating the vendor_name variants seen in legacy Procurement using the same
near-duplicate logic already implemented in the discovery profiler (Stage 2).
This is a 5th target table; there is no corresponding legacy table for it.

**Target-only addition — `target_hr_master`** (employee_id, canonical_name,
duplicate_of / cluster_id): added after Stage 5 for consistency with
target_vendor_master, to resolve the HR duplication issues (near-duplicate
employee names, e.g. "J. Smith" vs "John Smith") the same way vendor duplicates
were resolved. IMPORTANT — this is higher-risk than vendor consolidation and must
NOT silently merge employee_ids the way vendor_ids were merged:
- Cluster near-duplicate names using the existing similarity logic, but do NOT
  auto-designate a canonical employee_id or merge records.
- Instead, record each cluster as a *flagged pair* (employee_id_a, employee_id_b,
  similarity_score, shared_attributes e.g. same cost_centre/dept) for human
  review — output a `hr_duplicate_review` table/report, not an auto-resolution.
- Only mark the corresponding issue_log entries "Resolved" if a cluster is
  clearly a data-entry duplicate (e.g. identical dept_code, cost_centre, and
  near-identical hire_date) — otherwise leave "Open" with resolution_note
  explaining it's flagged for HR business review, not resolvable by the pipeline
  alone. This distinction (name-string merge vs identity merge) should be stated
  explicitly in mapping_validation_report.md and the README.

## Injected issue plan (~12-15 issues total, ground truth in manifest)

- Duplication: 3-4 duplicate vendor/employee records with minor name variants
- Completeness: missing cost_centre in ~5% of expense rows; missing termination_date
  logic errors (active status but termination_date in past)
- Accuracy: negative quantities in procurement; currency code typos (GBP vs GPB)
- Consistency: cost_centre format drift (CC-001 vs 001 vs C001) across domains
- Obsolete/orphaned: finance entries referencing vendor_ids not in procurement;
  expenses referencing employee_ids not in HR (leavers not deactivated)

## Migration policy for unresolved-issue rows (decided Stage 6)

Not every row with an open issue is handled the same way at migration time —
this distinction matters and must be visible in the reconciliation report,
not just asserted as a row-count delta:

- **Held back (excluded from target tables entirely)**: rows with orphaned/
  referential-break issues — legacy row references a vendor_id/employee_id
  that doesn't exist in the corresponding master table, or contains a value
  invalid on its face (e.g. negative quantity). These cannot be migrated
  meaningfully without another system correcting the source reference first.
  Write these to a `held_back_rows` table in erp.duckdb (domain, row
  identifier, issue_id, reason) — this is the traceability artefact a
  stakeholder/auditor would ask for.
- **Migrated with flag**: rows with completeness issues that don't break
  referential integrity — e.g. missing cost_centre. These migrate into the
  target table as normal, but get a `migration_status` column value of
  `migrated_with_flag` (vs `clean` for issue-free rows). The missing value
  itself is NOT imputed or guessed — it stays NULL, flagged for correction
  by a human post-migration.
- Every held-back or flagged row must trace back to its issue_log entry
  (issue_id), so the reconciliation report can say *why*, not just *how many*.
- The reconciliation report's row-count section must list held-back rows
  explicitly (table, not just a count), and business-rule checks (e.g. total
  expense amount by cost_centre) must clearly state whether flagged/held-back
  rows are included or excluded from the totals being compared.
- Report framing: avoid "SUCCESS" as an unqualified top-line status. Use
  "Reconciles cleanly against migration policy" and state the policy above it,
  so it's clear reconciliation is against a defined policy, not a claim that
  nothing was excluded.

## Manual SQL investigation layer (added Stage 7.5)

The JD explicitly lists SQL as an essential tool, and "investigate and interpret
data issues" as an essential skill. Everything built so far (profiler.py,
mapping_validator.py, etc.) demonstrates this only indirectly, wrapped inside
Python. This stage adds a `sql/investigation/` directory of hand-written,
readable SQL queries — the kind you'd actually run live against a database to
find each issue type, written to read as genuine investigative SQL, not derived
mechanically from the Python logic.

Requirements:
- One .sql file per issue type (completeness.sql, duplication.sql,
  consistency.sql, accuracy.sql, orphaned_referential.sql), each containing
  2-4 standalone queries a human would run to manually investigate that issue
  type across the four domains — plain SQL, DuckDB dialect, commented to
  explain what each query is checking and why.
- GROUP BY/HAVING for duplicates, LEFT JOIN/IS NULL for orphans, simple
  range/format checks for accuracy — the manual equivalent a data analyst
  would reach for first, before any pipeline exists.
- A short docs/sql_vs_pipeline.md note explaining the relationship: manual SQL
  for ad hoc investigation and one-off checks, the Python pipeline for
  repeatable checks that need to run every migration cycle. This is the
  answer to "why do you have both" if asked.
- No new tables, no changes to existing modules — this is a standalone,
  additive artefact.



## Definition of done (MVP)

- [ ] All 4 legacy CSVs generated with documented injected issues
- [ ] Discovery module finds ≥80% of injected issues, near-zero false positives on clean rows
- [ ] Issue log produces one problem-statement doc per issue, fixed format
- [ ] Mapping validator catches all injected referential/format breaks
- [ ] Migration simulation + reconciliation report shows discrepancies pre-fix, clean post-fix
- [ ] Reporting impact module shows one concrete before/after KPI cascade example
- [ ] Streamlit dashboard ties all five views together
- [ ] README with architecture diagram + "how this maps to the JD" table
