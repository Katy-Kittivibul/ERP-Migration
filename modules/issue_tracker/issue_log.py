"""Issue tracker: turns discovery_findings rows into structured, stakeholder-
readable problem statements in CLAUDE.md's fixed format, writes them to an
issue_log table in erp.duckdb, and renders one markdown doc per issue in
docs/problem_statements/.

Run: python modules/issue_tracker/issue_log.py
"""

from pathlib import Path

import duckdb

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT_DIR / "data" / "db" / "erp.duckdb"
DOCS_DIR = ROOT_DIR / "docs" / "problem_statements"

FIELD_ORDER = [
    "Issue ID", "Domain", "Table", "Column", "Issue Type", "Description",
    "Root Cause (hypothesis)", "Business Impact", "Status", "Resolution",
]

# ---------------------------------------------------------------------------
# Root cause hypotheses — keyed by (detection_rule, column_name) for the
# specific cases the profiler actually produces, falling back to a per-rule
# then a per-issue_type generic if a new (rule, column) combination shows up
# later. Deterministic lookup only, no inference.
# ---------------------------------------------------------------------------

ROOT_CAUSE_BY_RULE_COLUMN = {
    ("null_check", "cost_centre"):
        "Expense submission form doesn't enforce cost_centre as a mandatory field, "
        "so a claim can be submitted without one.",
    ("negative_value", "quantity"):
        "Manual quantity entry in the legacy procurement screen allows negative values "
        "with no validation, likely used informally to record returns or credits.",
    ("invalid_currency", "currency"):
        "Currency is entered as free text rather than selected from a controlled list, "
        "allowing typos such as 'GBP' mistyped as 'GPB'.",
    ("cost_centre_format_drift", "cost_centre"):
        "No enforced input format for cost_centre in the legacy system; different "
        "regional offices/business units free-text the code in their own local convention.",
    ("status_date_contradiction", "employment_status"):
        "Termination processing updates termination_date manually but doesn't automatically "
        "flip employment_status, leaving the two fields able to drift out of sync.",
    ("exact_name_match", "name"):
        "Manual re-entry by different HR administrators with no dedup check at source "
        "(e.g. onboarding processed twice before the first record was visible).",
    ("name_abbreviation", "name"):
        "Manual re-entry by different HR administrators with no dedup check at source; "
        "a shortened name variant was used on the second entry.",
    ("fuzzy_name_match", "name"):
        "Manual re-entry by different HR administrators with no dedup check at source; "
        "the second entry has minor spelling differences from the first.",
    ("company_suffix_match", "vendor_name"):
        "Vendor was onboarded independently by different purchasing/AP staff with no shared "
        "vendor master or dedup check, so it exists under two vendor codes with slightly "
        "different legal-name formatting.",
    ("fuzzy_name_match", "vendor_name"):
        "Vendor was onboarded independently by different purchasing/AP staff with no shared "
        "vendor master or dedup check; the second entry has minor spelling differences from "
        "the first.",
    ("orphan_fk", "vendor_id"):
        "Finance and Procurement maintain separate vendor references with no shared "
        "master-data validation at posting time, so a GL entry can be posted against a "
        "vendor_id that no longer exists (or never existed) in Procurement.",
    ("orphan_fk", "employee_id"):
        "Termination process doesn't trigger downstream deactivation in dependent systems, "
        "so a leaver's employee_id can still be used on new expense claims after they've left.",
    ("date_out_of_bounds", None):
        "Date fields accept free-text/manual entry with no range validation against "
        "plausible business dates.",
}

ROOT_CAUSE_BY_ISSUE_TYPE_FALLBACK = {
    "completeness": "Required field was not enforced as mandatory at the point of entry in the legacy system.",
    "accuracy": "No input validation at the point of entry allowed an out-of-range or invalid value to be saved.",
    "consistency": "No enforced format/business rule at the point of entry allowed the same information to be recorded inconsistently.",
    "duplication": "Manual re-entry by different users with no dedup check at source.",
    "obsolete_orphaned": "No referential integrity enforcement between source systems at the point of entry.",
}

# ---------------------------------------------------------------------------
# Business impact — same lookup shape, written for a non-technical reader.
# ---------------------------------------------------------------------------

BUSINESS_IMPACT_BY_RULE_COLUMN = {
    ("null_check", "cost_centre"):
        "Expense claims may be approved without a cost centre attached, so that spend "
        "won't show up against any budget owner's report.",
    ("negative_value", "quantity"):
        "A negative quantity on a purchase order can understate goods received or "
        "misrepresent a return, throwing off stock and spend reports.",
    ("invalid_currency", "currency"):
        "A mistyped currency code can cause a transaction to be misconverted or dropped "
        "from currency-based reporting, misstating reported spend.",
    ("cost_centre_format_drift", "cost_centre"):
        "A cost centre recorded in a non-standard format won't match up with HR or Finance "
        "records for that cost centre, so spend and headcount reporting for it will look incomplete.",
    ("status_date_contradiction", "employment_status"):
        "An employee shown as still Active despite having left may keep system access or "
        "keep accruing costs (e.g. approvals, payroll) they shouldn't.",
    ("exact_name_match", "name"):
        "A duplicated employee record can split one person's headcount and costs across "
        "two IDs, distorting workforce reporting and risking a duplicate payment.",
    ("name_abbreviation", "name"):
        "A duplicated employee record can split one person's headcount and costs across "
        "two IDs, distorting workforce reporting and risking a duplicate payment.",
    ("fuzzy_name_match", "name"):
        "A duplicated employee record can split one person's headcount and costs across "
        "two IDs, distorting workforce reporting and risking a duplicate payment.",
    ("company_suffix_match", "vendor_name"):
        "A vendor split across two vendor codes fragments spend reporting for that supplier "
        "and increases the risk of paying them twice or missing volume-discount thresholds.",
    ("fuzzy_name_match", "vendor_name"):
        "A vendor split across two vendor codes fragments spend reporting for that supplier "
        "and increases the risk of paying them twice or missing volume-discount thresholds.",
    ("orphan_fk", "vendor_id"):
        "This spend can't be traced back to a valid vendor record, which breaks "
        "spend-by-vendor reporting and leaves a gap in the audit trail.",
    ("orphan_fk", "employee_id"):
        "An expense claim references someone who has already left the company, so a "
        "payment or approval could be processed for a person no longer employed.",
    ("date_out_of_bounds", None):
        "A date outside the plausible business range can distort time-based reporting "
        "(e.g. spend-by-period, headcount-by-hire-date).",
}

BUSINESS_IMPACT_BY_ISSUE_TYPE_FALLBACK = {
    "completeness": "Missing data on this record means it may be excluded from, or misrepresented in, downstream reports.",
    "accuracy": "An invalid value on this record can produce an incorrect figure in downstream reports.",
    "consistency": "Inconsistent formatting on this record may cause it to fail to match against related records in other systems.",
    "duplication": "A duplicate record can double-count activity or split it across two IDs, distorting reporting.",
    "obsolete_orphaned": "This record references something that no longer exists elsewhere in the source data, breaking traceability.",
}


def root_cause_for(detection_rule, column_name, issue_type):
    if (detection_rule, column_name) in ROOT_CAUSE_BY_RULE_COLUMN:
        return ROOT_CAUSE_BY_RULE_COLUMN[(detection_rule, column_name)]
    if (detection_rule, None) in ROOT_CAUSE_BY_RULE_COLUMN:
        return ROOT_CAUSE_BY_RULE_COLUMN[(detection_rule, None)]
    return ROOT_CAUSE_BY_ISSUE_TYPE_FALLBACK.get(
        issue_type, "Root cause not yet determined; needs manual investigation."
    )


def business_impact_for(detection_rule, column_name, issue_type):
    if (detection_rule, column_name) in BUSINESS_IMPACT_BY_RULE_COLUMN:
        return BUSINESS_IMPACT_BY_RULE_COLUMN[(detection_rule, column_name)]
    if (detection_rule, None) in BUSINESS_IMPACT_BY_RULE_COLUMN:
        return BUSINESS_IMPACT_BY_RULE_COLUMN[(detection_rule, None)]
    return BUSINESS_IMPACT_BY_ISSUE_TYPE_FALLBACK.get(
        issue_type, "Downstream impact not yet assessed; needs manual investigation."
    )


# ---------------------------------------------------------------------------
# Build issue records from discovery_findings
# ---------------------------------------------------------------------------


def build_issue_records(con):
    findings = con.execute(
        "SELECT finding_id, domain, table_name, column_name, row_identifier, "
        "issue_type, description, detection_rule "
        "FROM discovery_findings ORDER BY finding_id"
    ).fetchall()

    issues = []
    for finding_id, domain, table_name, column_name, row_identifier, issue_type, description, detection_rule in findings:
        issue_id = f"ISSUE-{finding_id:03d}"
        # Description is prefixed with the row identifier so the fixed field
        # set stays self-contained (which record this is, without adding an
        # eleventh field outside CLAUDE.md's format).
        full_description = f"[{row_identifier}] {description}"
        issues.append(
            {
                "Issue ID": issue_id,
                "Domain": domain,
                "Table": table_name,
                "Column": column_name,
                "Issue Type": issue_type,
                "Description": full_description,
                "Root Cause (hypothesis)": root_cause_for(detection_rule, column_name, issue_type),
                "Business Impact": business_impact_for(detection_rule, column_name, issue_type),
                "Status": "Open",
                "Resolution": "Pending",
            }
        )
    return issues


# ---------------------------------------------------------------------------
# Persistence: DuckDB table
# ---------------------------------------------------------------------------


def write_issue_log(con, issues):
    con.execute("DROP TABLE IF EXISTS issue_log")
    con.execute(
        """
        CREATE TABLE issue_log (
            issue_id VARCHAR,
            domain VARCHAR,
            table_name VARCHAR,
            column_name VARCHAR,
            issue_type VARCHAR,
            description VARCHAR,
            root_cause VARCHAR,
            business_impact VARCHAR,
            status VARCHAR,
            resolution VARCHAR
        )
        """
    )
    for issue in issues:
        con.execute(
            "INSERT INTO issue_log VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                issue["Issue ID"], issue["Domain"], issue["Table"], issue["Column"],
                issue["Issue Type"], issue["Description"], issue["Root Cause (hypothesis)"],
                issue["Business Impact"], issue["Status"], issue["Resolution"],
            ],
        )


# ---------------------------------------------------------------------------
# Persistence: one markdown problem statement per issue
# ---------------------------------------------------------------------------


def render_markdown(issue):
    lines = [f"# {issue['Issue ID']}", "", "| Field | Value |", "|---|---|"]
    for field in FIELD_ORDER:
        value = str(issue[field]).replace("|", "\\|")
        lines.append(f"| {field} | {value} |")
    lines.append("")
    return "\n".join(lines)


def write_markdown_file(issue):
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    path = DOCS_DIR / f"{issue['Issue ID']}.md"
    path.write_text(render_markdown(issue), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Called by later modules (mapping_validator.py, migrate.py, ...) once an
# issue is actually fixed.
# ---------------------------------------------------------------------------


def mark_resolved(issue_id, resolution_note, db_path=DB_PATH):
    con = duckdb.connect(str(db_path))
    try:
        exists = con.execute(
            "SELECT COUNT(*) FROM issue_log WHERE issue_id = ?", [issue_id]
        ).fetchone()[0]
        if not exists:
            raise ValueError(f"No issue_log row found for issue_id={issue_id!r}")

        con.execute(
            "UPDATE issue_log SET status = 'Resolved', resolution = ? WHERE issue_id = ?",
            [resolution_note, issue_id],
        )

        row = con.execute(
            "SELECT issue_id, domain, table_name, column_name, issue_type, description, "
            "root_cause, business_impact, status, resolution FROM issue_log WHERE issue_id = ?",
            [issue_id],
        ).fetchone()
        issue = dict(zip(
            ["Issue ID", "Domain", "Table", "Column", "Issue Type", "Description",
             "Root Cause (hypothesis)", "Business Impact", "Status", "Resolution"],
            row,
        ))
        write_markdown_file(issue)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def print_summary(issues):
    by_domain_type = {}
    issue_types = sorted({i["Issue Type"] for i in issues})
    for i in issues:
        key = (i["Domain"], i["Issue Type"])
        by_domain_type[key] = by_domain_type.get(key, 0) + 1

    domains = sorted({i["Domain"] for i in issues})
    header = f"{'Domain':<12}" + "".join(f"{t:<18}" for t in issue_types) + "Total"
    print("\nIssue log summary")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    grand_total = 0
    for d in domains:
        counts = [by_domain_type.get((d, t), 0) for t in issue_types]
        total = sum(counts)
        grand_total += total
        print(f"{d:<12}" + "".join(f"{c:<18}" for c in counts) + f"{total}")
    print("-" * len(header))
    col_totals = [sum(by_domain_type.get((d, t), 0) for d in domains) for t in issue_types]
    print(f"{'Total':<12}" + "".join(f"{c:<18}" for c in col_totals) + f"{grand_total}")
    print()


def main():
    con = duckdb.connect(str(DB_PATH))

    issues = build_issue_records(con)
    write_issue_log(con, issues)

    for issue in issues:
        write_markdown_file(issue)

    print(f"Logged {len(issues)} issues to issue_log in {DB_PATH}")
    print(f"Wrote {len(issues)} problem-statement docs to {DOCS_DIR}")
    print_summary(issues)

    con.close()


if __name__ == "__main__":
    main()
