======================================================================
MAPPING VALIDATION REPORT (Stage 5)
======================================================================

Target table build
----------------------------------------
  target_hr: 202 rows
  target_finance: 200 rows
  target_procurement: 201 rows
  target_expenses: 200 rows
  target_vendor_master: 41 rows

Vendor consolidation (target_vendor_master)
----------------------------------------
  40 vendors consolidated from 41 raw name variants
    merged: VEND-040, VEND-041

cost_centre normalisation (per target table)
----------------------------------------
  target_hr: 202 already canonical, 0 fixed, 0 left null, 0 unrecognised
  target_finance: 199 already canonical, 1 fixed, 0 left null, 0 unrecognised
  target_procurement: 200 already canonical, 1 fixed, 0 left null, 0 unrecognised
  target_expenses: 197 already canonical, 0 fixed, 3 left null, 0 unrecognised

Referential integrity: target_finance.vendor_id -> target_vendor_master
----------------------------------------
  FAIL: 2 orphaned reference(s)
    GL-000034 -> VEND-999 not found (see ISSUE-014, remains Open)
    GL-000124 -> VEND-999 not found (see ISSUE-015, remains Open)

Referential integrity: target_expenses.employee_id -> target_hr.employee_id
----------------------------------------
  FAIL: 1 orphaned reference(s)
    EXP-00017 -> EMP-9999 not found (see ISSUE-016, remains Open)

cost_centre cross-domain consistency (post-normalisation)
----------------------------------------
  PASS: all non-null cost_centre values conform to CC-0NN

Resolved this stage (0)
----------------------------------------

Still open (12)
----------------------------------------
  accuracy - invalid/out-of-range value - no correction logic in scope for this stage.
    ISSUE-004 (Procurement.quantity)
    ISSUE-005 (Procurement.quantity)
    ISSUE-006 (Finance.currency)
  completeness - blank value at source - no imputation/rejection logic in scope for this stage.
    ISSUE-001 (Expenses.cost_centre)
    ISSUE-002 (Expenses.cost_centre)
    ISSUE-003 (Expenses.cost_centre)
  duplication - employee-level duplicate - no merge/dedup mechanism built for HR at this stage.
    ISSUE-010 (HR.name)
    ISSUE-011 (HR.name)
    ISSUE-012 (HR.name)
  obsolete_orphaned - references a vendor/employee id that never existed or was never deactivated - no valid target to map to.
    ISSUE-014 (Finance.vendor_id)
    ISSUE-015 (Finance.vendor_id)
    ISSUE-016 (Expenses.employee_id)

0 issue(s) resolved this stage, 12 remain Open.

## HR DUPLICATE REVIEW (added after Stage 5 - see CLAUDE.md's target_hr_master note)
----------------------------------------------------------------------
Vendor duplicates (target_vendor_master) and HR employee duplicates are handled
differently on purpose, not inconsistently. A vendor name is just a display
label restated inconsistently across records - collapsing two spellings into
one canonical string carries no real-world consequence beyond a tidier report.
An employee_id is a person's identity: payroll, system access, approvals and
headcount all key off it, so silently merging two employee_ids risks paying,
deactivating, or misreporting a real person. target_hr_master therefore never
designates a canonical employee_id or merges records - it only tags an
employee with a cluster_id when their name looks like a possible duplicate of
another employee's; canonical_name is always the employee's own name. Every
flagged pair is written to hr_duplicate_review for a human (HR) to review, and
only pairs where dept_code, cost_centre, AND hire_date all line up (within
30 days) are marked Resolved in issue_log - with
a note that consolidation still requires HR sign-off, not that it happened
automatically. Every other flagged pair stays Open with a note pointing back
to this review table.

Clusters found: 3 (3 pair(s))
  likely_duplicate:   2 (Resolved in issue_log; HR sign-off still required to actually merge)
  needs_human_review: 1 (left Open - see hr_duplicate_review for detail)

See the hr_duplicate_review and target_hr_master tables in erp.duckdb for full detail.
