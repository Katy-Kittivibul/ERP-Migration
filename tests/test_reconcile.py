"""
Tests for Migration Reconciliation Policy (Stage 6b)
"""

import sys
from pathlib import Path
import duckdb
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from modules.mapping.mapping_validator import DB_PATH

@pytest.fixture
def db():
    con = duckdb.connect(str(DB_PATH))
    yield con
    con.close()

def test_held_back_rows_excluded(db):
    """Verify that held-back rows are explicitly excluded from checksum logic and target tables"""
    # Procurement PO-00008 was held back due to negative quantity.
    in_target = db.execute("SELECT COUNT(*) FROM target_procurement WHERE po_id = 'PO-00008'").fetchone()[0]
    assert in_target == 0, "Held-back row must not be in target table"
    
    in_held_back = db.execute("SELECT COUNT(*) FROM held_back_rows WHERE row_identifier = 'PO-00008'").fetchone()[0]
    assert in_held_back == 1, "Held-back row must be in held_back_rows table"

def test_flagged_row_status_and_nulls(db):
    """Verify flagged rows carry migration_status='migrated_with_flag', issue_id, and don't impute NULLs"""
    # Expense EXP-00012 was a missing cost_centre (completeness). It should be migrated with flag.
    row = db.execute("SELECT migration_status, issue_id, cost_centre FROM target_expenses WHERE expense_id = 'EXP-00012'").fetchone()
    
    assert row is not None, "Flagged row must be migrated"
    status, issue_id, cost_centre = row
    
    assert status == 'migrated_with_flag', "Status must be migrated_with_flag"
    assert issue_id == 'ISSUE-001', "Traceability issue_id must match"
    assert cost_centre is None, "Missing value must remain NULL, not imputed"

def test_business_rules_reflect_exclusions(db):
    """Verify that aggregate calculations naturally exclude held-back rows but include flagged rows."""
    target_total = db.execute("SELECT SUM(amount) FROM target_expenses").fetchone()[0]
    
    legacy_total = db.execute("""
        SELECT SUM(amount) FROM legacy_expenses 
        WHERE expense_id NOT IN (SELECT row_identifier FROM held_back_rows WHERE domain='Expenses')
    """).fetchone()[0]
    
    assert abs(target_total - legacy_total) < 0.01, "Target total must exactly match legacy total minus exclusions"
