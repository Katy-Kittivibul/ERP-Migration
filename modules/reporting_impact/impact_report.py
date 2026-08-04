"""Reporting impact (Stage 7): one concrete, traceable example of a data
quality issue cascading into a reporting KPI, and how fixing it changes the
number a stakeholder would see.

Worked example: cost_centre format drift (ISSUE-007, Finance/GL-000029,
'009' vs 'CC-009') fragments "Total Finance Amount by Cost Centre" into two
rows pre-migration; post-migration normalisation correctly merges them into
one. See "Why this example" in the generated report for why this was chosen
over the held-back-rows alternative.

Writes docs/reporting_impact_example.md and a reporting_impact_example table
in erp.duckdb (so the Stage 8 dashboard can render this without recomputing).

Run: python modules/reporting_impact/impact_report.py
"""

from pathlib import Path

import duckdb

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT_DIR / "data" / "db" / "erp.duckdb"
REPORT_PATH = ROOT_DIR / "docs" / "reporting_impact_example.md"

# The specific cascade being demonstrated - a deliberate editorial choice for
# this worked example, not something to re-derive generically. The numbers
# themselves are computed live from the database, not hardcoded.
ISSUE_ID = "ISSUE-007"
LEGACY_GL_ENTRY = "GL-000029"
LEGACY_VARIANT = "009"
CANONICAL_COST_CENTRE = "CC-009"

REQUIRED_TABLES = ["legacy_finance", "target_finance", "held_back_rows", "issue_log"]


def require_tables(con, names):
    existing = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    missing = [n for n in names if n not in existing]
    if missing:
        raise RuntimeError(
            f"Missing required table(s): {missing}. Run profiler.py, issue_log.py, "
            f"mapping_validator.py and migrate.py before impact_report.py."
        )


def verify_scenario_still_holds(con):
    """Guards against silently producing a stale report if the underlying data
    or issue status ever changes - fail loudly rather than report wrong numbers."""
    legacy_value = con.execute(
        "SELECT cost_centre FROM legacy_finance WHERE gl_entry_id = ?", [LEGACY_GL_ENTRY]
    ).fetchone()
    if not legacy_value or legacy_value[0] != LEGACY_VARIANT:
        raise RuntimeError(
            f"Expected legacy_finance.{LEGACY_GL_ENTRY}.cost_centre == '{LEGACY_VARIANT}', "
            f"got {legacy_value}. The worked example no longer matches the data."
        )
    status = con.execute("SELECT status FROM issue_log WHERE issue_id = ?", [ISSUE_ID]).fetchone()
    if not status or status[0] != "Resolved":
        raise RuntimeError(f"Expected {ISSUE_ID} to be Resolved, got {status}. Run mapping_validator.py first.")


def compute_before(con):
    """Legacy cost_centre, as entered at source - held-back rows (unrelated
    accuracy/orphan issues) excluded from BOTH before and after so this
    isolates the format-drift effect specifically, not mixed with the
    held-back-row migration policy (see docs/reconciliation_report.md for
    that separate concern)."""
    held_back_ids = [
        r[0] for r in con.execute("SELECT row_identifier FROM held_back_rows WHERE domain = 'Finance'").fetchall()
    ]
    placeholders = ", ".join("?" for _ in held_back_ids) or "NULL"
    return con.execute(
        f"""
        SELECT cost_centre, COUNT(*) AS row_count, SUM(amount) AS total_amount
        FROM legacy_finance
        WHERE gl_entry_id NOT IN ({placeholders})
        GROUP BY cost_centre
        ORDER BY cost_centre
        """,
        held_back_ids,
    ).fetchall()


def compute_after(con):
    """Target (normalised) cost_centre - target_finance already excludes
    held-back rows per the Stage 6 migration policy."""
    return con.execute(
        """
        SELECT cost_centre, COUNT(*) AS row_count, SUM(amount) AS total_amount
        FROM target_finance
        GROUP BY cost_centre
        ORDER BY cost_centre
        """
    ).fetchall()


def write_result_table(con, before, after):
    con.execute("DROP TABLE IF EXISTS reporting_impact_example")
    con.execute(
        """
        CREATE TABLE reporting_impact_example (
            scenario VARCHAR,
            cost_centre VARCHAR,
            row_count INTEGER,
            total_amount DOUBLE,
            is_affected_by_cascade BOOLEAN
        )
        """
    )
    rows_out = []
    for cost_centre, row_count, total_amount in before:
        affected = cost_centre in (LEGACY_VARIANT, CANONICAL_COST_CENTRE)
        rows_out.append(("before", cost_centre, row_count, total_amount, affected))
    for cost_centre, row_count, total_amount in after:
        affected = cost_centre == CANONICAL_COST_CENTRE
        rows_out.append(("after", cost_centre, row_count, total_amount, affected))
    con.executemany("INSERT INTO reporting_impact_example VALUES (?, ?, ?, ?, ?)", rows_out)


def render_table(rows):
    lines = ["| cost_centre | rows | total_amount |", "|---|---|---|"]
    for cost_centre, row_count, total_amount in rows:
        lines.append(f"| {cost_centre} | {row_count} | GBP {total_amount:,.2f} |")
    return "\n".join(lines)


def build_report(con, before, after):
    before_fragment = next(r for r in before if r[0] == LEGACY_VARIANT)
    before_main = next(r for r in before if r[0] == CANONICAL_COST_CENTRE)
    after_merged = next(r for r in after if r[0] == CANONICAL_COST_CENTRE)

    _, frag_n, frag_total = before_fragment
    _, main_n, main_total = before_main
    _, merged_n, merged_total = after_merged

    # How the stakeholder's "before" number (main_total, the CC-009-only
    # fragment) compares to the true, correctly-consolidated "after" total.
    delta = main_total - merged_total
    direction = "higher" if delta > 0 else "lower"

    issue = con.execute(
        "SELECT domain, table_name, column_name, description, resolution FROM issue_log WHERE issue_id = ?",
        [ISSUE_ID],
    ).fetchone()
    domain, table_name, column_name, description, resolution = issue

    held_back_finance = con.execute(
        "SELECT row_identifier, issue_id FROM held_back_rows WHERE domain = 'Finance' ORDER BY row_identifier"
    ).fetchall()
    held_back_note = ", ".join(f"{rid} ({iid})" for rid, iid in held_back_finance)

    return f"""# Reporting Impact Example: cost_centre Format Drift -> Finance Spend-by-Cost-Centre KPI

**Traced to:** {ISSUE_ID} ({domain} / `{table_name}` / `{column_name}` / consistency)
**Source finding:** {description}
**Resolution (Stage 5):** {resolution}

## The KPI

"Total Finance Amount by Cost Centre" - the number a stakeholder reads off a
spend-by-cost-centre report, e.g. to check a budget owner's net spend for the
period.

## Why this example

Cost centre format drift is the cleanest cascade to demonstrate end-to-end: a
single, specific data error (`{LEGACY_GL_ENTRY}`'s cost_centre stored as
`'{LEGACY_VARIANT}'` instead of `'{CANONICAL_COST_CENTRE}'`) has a direct,
provable effect on a rollup KPI, with no other variable involved. (The
alternative candidate - held-back orphaned rows affecting a total PO value KPI
- is already covered by the row-count and business-rule sections of
`docs/reconciliation_report.md`; this report focuses on the one cascade type
reconciliation doesn't already show: values that don't get excluded, they get
silently *misgrouped*.)

## Scope note

Both totals below exclude the {len(held_back_finance)} Finance row(s) held
back for unrelated reasons ({held_back_note} - see
`docs/reconciliation_report.md`), so this comparison isolates the cost_centre
format-drift effect specifically, not mixed with the held-back-row migration
policy.

## Before: legacy `cost_centre`, as entered at source

{render_table(before)}

`{CANONICAL_COST_CENTRE}` was split across **two rows** here: `'{LEGACY_VARIANT}'`
({frag_n} row, GBP {frag_total:,.2f}) and `'{CANONICAL_COST_CENTRE}'` ({main_n} rows,
GBP {main_total:,.2f}).

## After: target `cost_centre`, normalised in Stage 5

{render_table(after)}

`{CANONICAL_COST_CENTRE}` now correctly rolls up to **one row**: {merged_n} rows,
GBP {merged_total:,.2f}.

## What a stakeholder relying on the "before" report would have gotten wrong

Pre-migration, cost centre {CANONICAL_COST_CENTRE} appeared to have **two**
different budget lines in the legacy report - one under `CC-009`
(GBP {main_total:,.2f} across {main_n} transactions) and a separate, easy-to-miss
one under the malformed code `009` (GBP {frag_total:,.2f}, a single credit entry).
A stakeholder searching a report for "CC-009" would only find the first line
and would report net spend for the cost centre as **GBP {main_total:,.2f}** -
{abs(delta):,.2f} {direction} than the true, correctly-consolidated total of
**GBP {merged_total:,.2f}**, because the credit entry sitting under the
mis-formatted `009` code was effectively invisible to anyone querying by the
canonical cost centre code. Post-migration, cost centre {CANONICAL_COST_CENTRE}
correctly shows as one line, with the credit included.

This is not a rounding difference or a missing row - it is the exact same
{merged_n} transactions in both cases, {merged_n - main_n} of them (the credit
entry) simply invisible to a report keyed on the canonical cost centre code
until the format was normalised.
"""


def main():
    con = duckdb.connect(str(DB_PATH))

    require_tables(con, REQUIRED_TABLES)
    verify_scenario_still_holds(con)

    before = compute_before(con)
    after = compute_after(con)

    write_result_table(con, before, after)

    report_text = build_report(con, before, after)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report_text, encoding="utf-8")

    print(report_text)
    print(f"Wrote {REPORT_PATH}")
    print(f"Wrote reporting_impact_example table ({len(before) + len(after)} rows) to {DB_PATH}")

    con.close()


if __name__ == "__main__":
    main()
