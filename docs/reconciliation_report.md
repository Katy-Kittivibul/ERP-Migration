======================================================================
Reconciles cleanly against migration policy
======================================================================
POLICY SUMMARY:
- Held back: rows with orphaned/referential-break or accuracy issues.
- Migrated with flag: rows with completeness or duplication issues.

1. ROW COUNT RECONCILIATION
----------------------------------------
[HR]
  Legacy rows: 202 | Target rows: 202 | Delta: 0
  Status: CLEAN

[FINANCE]
  Legacy rows: 200 | Target rows: 197 | Delta: -3
  Status: EXPECTED DELTA (3 rows explicitly held back)
    - GL-000125 (ISSUE-006): accuracy: Currency code 'GPB' is not a recognised code (expected one of ['EUR', 'GBP', 'USD']) - likely a typo.
    - GL-000034 (ISSUE-014): obsolete_orphaned: References vendor_id 'VEND-999', which does not exist anywhere in Procurement - orphaned reference.
    - GL-000124 (ISSUE-015): obsolete_orphaned: References vendor_id 'VEND-999', which does not exist anywhere in Procurement - orphaned reference.

[PROCUREMENT]
  Legacy rows: 201 | Target rows: 199 | Delta: -2
  Status: EXPECTED DELTA (2 rows explicitly held back)
    - PO-00008 (ISSUE-004): accuracy: Quantity is negative (-496) - not a valid PO quantity.
    - PO-00176 (ISSUE-005): accuracy: Quantity is negative (-413) - not a valid PO quantity.

[EXPENSES]
  Legacy rows: 200 | Target rows: 199 | Delta: -1
  Status: EXPECTED DELTA (1 rows explicitly held back)
    - EXP-00017 (ISSUE-016): obsolete_orphaned: References employee_id 'EMP-9999', which does not exist in HR - likely a leaver whose record was never deactivated or reassigned.

2. COLUMN-LEVEL CHECKSUM (Migrated rows only)
----------------------------------------
  Hr: 202/202 rows match (100%)
  Finance: 197/197 rows match (100%)
  Procurement: 199/199 rows match (100%)
  Expenses: 199/199 rows match (100%)
  Status: CLEAN (No unintended data drift)

3. BUSINESS RULE RECONCILIATION
----------------------------------------
[Rule 1] Total Expense Amount by Cost Centre: FAIL (300446.9 legacy vs 300446.8999999999 target)
[Rule 2] Total PO Value by Vendor ID: PASS (Excludes 2 held-back rows with negative quantities or orphaned vendors)
[Rule 3] Total Finance Amount by Account Code: PASS (Excludes 3 held-back orphaned vendor rows)