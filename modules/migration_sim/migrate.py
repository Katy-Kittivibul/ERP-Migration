"""
Migration Simulator (Stage 6b)
Extracts legacy data, applies mappings from mapping_spec.yaml, filters unresolvable
records (held-back rows) into held_back_rows table, and writes the final
target tables with migration_status and issue_id for flagged records.
"""

import sys
from pathlib import Path
import duckdb

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from modules.mapping.mapping_validator import (
    load_mapping_spec, build_column_select_sql, DB_PATH, ID_COLUMN_BY_LEGACY_TABLE
)

def run_migration():
    con = duckdb.connect(str(DB_PATH))
    spec = load_mapping_spec()
    
    print("Running migration simulation with strict policy...")
    
    con.execute("DROP TABLE IF EXISTS held_back_rows")
    con.execute("""
    CREATE TABLE held_back_rows (
        domain VARCHAR,
        row_identifier VARCHAR,
        issue_id VARCHAR,
        reason VARCHAR
    )
    """)
    
    for domain_name, domain in spec["domains"].items():
        legacy_table = domain["legacy_table"]
        target_table = domain["target_table"]
        id_col = ID_COLUMN_BY_LEGACY_TABLE[legacy_table]
        
        # 1. Identify Open Issues for this legacy table
        # Held back = obsolete_orphaned, accuracy
        # Flagged = completeness, duplication
        
        # Insert held_back_rows
        held_back_sql = f"""
        INSERT INTO held_back_rows
        SELECT il.domain, df.row_identifier, il.issue_id, il.issue_type || ': ' || df.description
        FROM issue_log il
        JOIN discovery_findings df ON il.issue_id = 'ISSUE-' || lpad(df.finding_id::VARCHAR, 3, '0')
        WHERE il.table_name = '{legacy_table}' AND il.status = 'Open'
          AND il.issue_type IN ('obsolete_orphaned', 'accuracy')
        """
        con.execute(held_back_sql)
        
        # Create target table with new columns
        con.execute(f"DROP TABLE IF EXISTS {target_table}")
        
        migrated_sql = f"""
        CREATE TABLE {target_table} AS 
        WITH raw_mapped AS (
            SELECT {id_col} AS __row_id, {build_column_select_sql(domain)}
            FROM {legacy_table}
        ),
        flagged_issues AS (
            SELECT df.row_identifier, MIN(il.issue_id) as issue_id
            FROM issue_log il
            JOIN discovery_findings df ON il.issue_id = 'ISSUE-' || lpad(df.finding_id::VARCHAR, 3, '0')
            WHERE il.table_name = '{legacy_table}' AND il.status = 'Open'
              AND il.issue_type IN ('completeness', 'duplication')
            GROUP BY df.row_identifier
        )
        SELECT 
            rm.* EXCLUDE(__row_id),
            CASE WHEN fi.issue_id IS NOT NULL THEN 'migrated_with_flag' ELSE 'clean' END AS migration_status,
            fi.issue_id
        FROM raw_mapped rm
        LEFT JOIN flagged_issues fi ON rm.__row_id = fi.row_identifier
        WHERE rm.__row_id NOT IN (
            SELECT row_identifier FROM held_back_rows
        )
        """
        con.execute(migrated_sql)
        print(f"Migrated {target_table}")
        
    print("Migration simulation complete.")
    con.close()

if __name__ == "__main__":
    run_migration()
