"""Discovery module: profiles the four legacy CSVs for data-quality issues in
CLAUDE.md's closed issue-type set (completeness, accuracy, consistency,
duplication, obsolete_orphaned), writes findings to data/db/erp.duckdb, and
scores those findings against data/raw/injected_issues.json (ground truth).

Run: python modules/discovery/profiler.py
"""

import difflib
import json
import re
from datetime import date
from itertools import combinations
from pathlib import Path

import duckdb

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT_DIR / "data" / "db" / "erp.duckdb"
RAW_DIR = ROOT_DIR / "data" / "raw"
MANIFEST_PATH = RAW_DIR / "injected_issues.json"

ISSUE_TYPES = ["completeness", "accuracy", "consistency", "duplication", "obsolete_orphaned"]

VALID_CURRENCIES = {"GBP", "USD", "EUR"}
COST_CENTRE_RE = "^CC-[0-9]{3}$"
MIN_PLAUSIBLE_DATE = date(2010, 1, 1)

# Fuzzy-match threshold for the generic near-duplicate-name fallback (see
# _find_person_duplicates / _find_company_duplicates). Calibrated against
# this dataset: genuinely different people who happen to share a first name
# or surname ("Bruce Smith" vs "Bruce Shaw", "Damian Morton" vs "Damian
# Brown") score between 0.55 and 0.79 on difflib's ratio. The two specific
# duplicate patterns actually injected - whitespace variants and abbreviated
# surnames ("Ashleigh S." vs "Ashleigh Smith") - are caught by dedicated
# exact-match / abbreviation rules below, not by this fallback. 0.90 keeps
# the fallback well clear of the same-surname noise band while still
# catching plain typos/spelling drift it wasn't specifically built for.
NAME_SIMILARITY_THRESHOLD = 0.90

TITLES = {"mr", "mrs", "ms", "miss", "dr"}
COMPANY_SUFFIXES = {"limited", "ltd", "plc", "llc", "inc", "co"}

TABLE_NAME = {
    "hr": "legacy_hr",
    "finance": "legacy_finance",
    "procurement": "legacy_procurement",
    "expenses": "legacy_expenses",
}
DOMAIN_LABEL = {"hr": "HR", "finance": "Finance", "procurement": "Procurement", "expenses": "Expenses"}
ID_COLUMN = {"hr": "employee_id", "finance": "gl_entry_id", "procurement": "po_id", "expenses": "expense_id"}

REQUIRED_COLUMNS = {
    "hr": ["employee_id", "name", "dept_code", "job_title", "hire_date", "employment_status", "cost_centre"],
    "finance": ["gl_entry_id", "cost_centre", "account_code", "amount", "currency", "posting_date",
                "document_type", "vendor_id"],
    "procurement": ["po_id", "vendor_id", "vendor_name", "material_code", "quantity", "unit_price",
                     "po_date", "delivery_status", "cost_centre"],
    "expenses": ["expense_id", "employee_id", "cost_centre", "category", "amount", "submitted_date",
                 "approval_status", "currency"],
}
# termination_date and manager_id are legitimately blank (active employees /
# top-of-hierarchy managers) so they're excluded from HR's required columns.

DATE_COLUMNS = {
    "hr": ["hire_date", "termination_date"],
    "finance": ["posting_date"],
    "procurement": ["po_date"],
    "expenses": ["submitted_date"],
}

_findings = []


def add_finding(domain, table, column, row_identifier, issue_type, description, confidence, rule):
    if issue_type not in ISSUE_TYPES:
        raise ValueError(f"Unknown issue_type: {issue_type}")
    _findings.append(
        {
            "domain": domain,
            "table": table,
            "column": column,
            "row_identifier": row_identifier,
            "issue_type": issue_type,
            "description": description,
            "confidence": confidence,
            "detection_rule": rule,
        }
    )


# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------


def load_staging_tables(con):
    for key, table in TABLE_NAME.items():
        csv_path = (RAW_DIR / f"{table}.csv").as_posix()
        con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM read_csv_auto('{csv_path}')")


# ---------------------------------------------------------------------------
# Completeness: null/blank rate per column, row-level findings on required fields
# ---------------------------------------------------------------------------


def check_completeness(con):
    print("Completeness - null/blank rate per column:")
    for key, cols in REQUIRED_COLUMNS.items():
        table, id_col = TABLE_NAME[key], ID_COLUMN[key]
        total = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for col in cols:
            missing = con.execute(
                f"SELECT {id_col} FROM {table} "
                f"WHERE {col} IS NULL OR trim(CAST({col} AS VARCHAR)) = ''"
            ).fetchall()
            rate = len(missing) / total * 100 if total else 0
            print(f"  {table}.{col}: {len(missing)}/{total} missing ({rate:.1f}%)")
            for (row_id,) in missing:
                add_finding(
                    domain=DOMAIN_LABEL[key], table=table, column=col, row_identifier=row_id,
                    issue_type="completeness",
                    description=f"{col.replace('_', ' ')} is missing (blank) on this record.",
                    confidence="high", rule="null_check",
                )


# ---------------------------------------------------------------------------
# Accuracy: out-of-range values
# ---------------------------------------------------------------------------


def check_accuracy(con):
    # Negative PO quantity - physically impossible.
    rows = con.execute("SELECT po_id, quantity FROM legacy_procurement WHERE quantity < 0").fetchall()
    for po_id, qty in rows:
        add_finding(
            DOMAIN_LABEL["procurement"], TABLE_NAME["procurement"], "quantity", po_id, "accuracy",
            f"Quantity is negative ({qty}) - not a valid PO quantity.",
            confidence="high", rule="negative_value",
        )

    # Currency code not in the fixed valid-currency list.
    valid_list = ", ".join(f"'{c}'" for c in VALID_CURRENCIES)
    for key in ("finance", "expenses"):
        table, id_col = TABLE_NAME[key], ID_COLUMN[key]
        rows = con.execute(
            f"SELECT {id_col}, currency FROM {table} "
            f"WHERE currency IS NOT NULL AND currency NOT IN ({valid_list})"
        ).fetchall()
        for row_id, currency in rows:
            add_finding(
                DOMAIN_LABEL[key], table, "currency", row_id, "accuracy",
                f"Currency code '{currency}' is not a recognised code "
                f"(expected one of {sorted(VALID_CURRENCIES)}) - likely a typo.",
                confidence="high", rule="invalid_currency",
            )

    # Dates outside a plausible range for this legacy system's data.
    for key, cols in DATE_COLUMNS.items():
        table, id_col = TABLE_NAME[key], ID_COLUMN[key]
        for col in cols:
            rows = con.execute(
                f"SELECT {id_col}, {col} FROM {table} "
                f"WHERE {col} IS NOT NULL "
                f"AND ({col} < DATE '{MIN_PLAUSIBLE_DATE.isoformat()}' OR {col} > CURRENT_DATE)"
            ).fetchall()
            for row_id, bad_date in rows:
                add_finding(
                    DOMAIN_LABEL[key], table, col, row_id, "accuracy",
                    f"{col.replace('_', ' ')} ({bad_date}) falls outside the plausible range "
                    f"({MIN_PLAUSIBLE_DATE.isoformat()} to today).",
                    confidence="medium", rule="date_out_of_bounds",
                )


# ---------------------------------------------------------------------------
# Consistency: cost_centre format drift + HR status/date contradiction
# ---------------------------------------------------------------------------


def check_consistency(con):
    for key in ("hr", "finance", "procurement", "expenses"):
        table, id_col = TABLE_NAME[key], ID_COLUMN[key]
        rows = con.execute(
            f"SELECT {id_col}, cost_centre FROM {table} "
            f"WHERE cost_centre IS NOT NULL AND cost_centre <> '' "
            f"AND NOT regexp_matches(cost_centre, '{COST_CENTRE_RE}')"
        ).fetchall()
        for row_id, cc in rows:
            add_finding(
                DOMAIN_LABEL[key], table, "cost_centre", row_id, "consistency",
                f"cost_centre is stored as '{cc}', which doesn't match the standard 'CC-0NN' "
                f"format used elsewhere - likely the same cost centre recorded in a different format.",
                confidence="high", rule="cost_centre_format_drift",
            )

    # employment_status says Active but termination_date is a past date -
    # ground truth (injected_issues.json) classes this contradiction as
    # 'consistency' rather than 'completeness' or 'obsolete_orphaned', since
    # nothing is missing or unreferenced - two present fields disagree.
    rows = con.execute(
        "SELECT employee_id, termination_date FROM legacy_hr "
        "WHERE employment_status = 'Active' AND termination_date IS NOT NULL "
        "AND termination_date < CURRENT_DATE"
    ).fetchall()
    for emp_id, term_date in rows:
        add_finding(
            "HR", "legacy_hr", "employment_status", emp_id, "consistency",
            f"Employee is marked 'Active' but has a termination_date in the past ({term_date}) "
            f"- contradictory record.",
            confidence="high", rule="status_date_contradiction",
        )


# ---------------------------------------------------------------------------
# Duplication: near-duplicate name detection
# ---------------------------------------------------------------------------


def _normalize_tokens(name, drop_words):
    tokens = [t.strip(".,").lower() for t in name.strip().split()]
    return [t for t in tokens if t not in drop_words]


def _find_person_duplicates(rows, domain, table):
    """rows: list of (row_identifier, name), ordered by row_identifier ascending."""
    flagged = set()
    normalized = [(rid, name, _normalize_tokens(name, TITLES)) for rid, name in rows]

    # Rule 1 - exact match once whitespace/case/titles are normalised away
    # (catches e.g. a trailing-space re-entry of an identical name).
    by_norm = {}
    for rid, name, toks in normalized:
        by_norm.setdefault(" ".join(toks), []).append((rid, name))
    for group in by_norm.values():
        if len(group) < 2:
            continue
        base_id, base_name = group[0]
        for rid, name in group[1:]:
            key = tuple(sorted((base_id, rid)))
            flagged.add(key)
            add_finding(
                domain, table, "name", rid, "duplication",
                f"Name '{name.strip()}' matches {base_id} ('{base_name.strip()}') exactly once "
                f"whitespace/case differences are ignored - likely the same person entered twice.",
                confidence="high", rule="exact_name_match",
            )

    # Rule 2 - surname abbreviated to a single initial ("Ashleigh S." vs
    # "Ashleigh Smith"): same first name, and one record's last token is a
    # single letter that starts the other's last token.
    by_first_token = {}
    for rid, name, toks in normalized:
        if toks:
            by_first_token.setdefault(toks[0], []).append((rid, name, toks))
    for group in by_first_token.values():
        for (id1, n1, t1), (id2, n2, t2) in combinations(group, 2):
            key = tuple(sorted((id1, id2)))
            if key in flagged or len(t1) < 2 or len(t2) < 2:
                continue
            last1, last2 = t1[-1], t2[-1]
            if last1 == last2:
                continue
            is_abbreviation = (len(last1) == 1 and last2.startswith(last1)) or (
                len(last2) == 1 and last1.startswith(last2)
            )
            if is_abbreviation:
                flagged.add(key)
                add_finding(
                    domain, table, "name", id2, "duplication",
                    f"Name '{n2.strip()}' appears to abbreviate the surname on {id1} "
                    f"('{n1.strip()}') to an initial - likely the same person entered twice.",
                    confidence="medium", rule="name_abbreviation",
                )

    # Rule 3 - fuzzy fallback for anything else, blocked by the first three
    # letters of the first name to keep the comparison space (and false-
    # positive surface) small.
    by_block = {}
    for rid, name, toks in normalized:
        if toks:
            by_block.setdefault(toks[0][:3], []).append((rid, name))
    for group in by_block.values():
        for (id1, n1), (id2, n2) in combinations(group, 2):
            key = tuple(sorted((id1, id2)))
            if key in flagged:
                continue
            ratio = difflib.SequenceMatcher(None, n1.strip().lower(), n2.strip().lower()).ratio()
            if ratio >= NAME_SIMILARITY_THRESHOLD:
                flagged.add(key)
                add_finding(
                    domain, table, "name", id2, "duplication",
                    f"Name '{n2.strip()}' is a {ratio:.0%} textual match to {id1} "
                    f"('{n1.strip()}') - possible duplicate entry.",
                    confidence="medium", rule="fuzzy_name_match",
                )


def _find_company_duplicates(con, vendor_rows):
    domain, table = DOMAIN_LABEL["procurement"], TABLE_NAME["procurement"]
    flagged = set()
    normalized = [(vid, name, _normalize_tokens(name, COMPANY_SUFFIXES)) for vid, name in vendor_rows]

    def first_po_for(vendor_id):
        row = con.execute(
            "SELECT po_id FROM legacy_procurement WHERE vendor_id = ? ORDER BY po_id LIMIT 1", [vendor_id]
        ).fetchone()
        return row[0] if row else vendor_id

    # Rule 1 - exact match once common company suffixes (Ltd/PLC/Inc/...) are
    # stripped (catches e.g. "Green-Kelly" vs "Green-Kelly Ltd").
    by_norm = {}
    for vid, name, toks in normalized:
        by_norm.setdefault(" ".join(toks), []).append((vid, name))
    for group in by_norm.values():
        if len(group) < 2:
            continue
        base_id, base_name = group[0]
        for vid, name in group[1:]:
            key = tuple(sorted((base_id, vid)))
            flagged.add(key)
            add_finding(
                domain, table, "vendor_name", first_po_for(vid), "duplication",
                f"Vendor '{name}' ({vid}) matches '{base_name}' ({base_id}) once common company "
                f"suffixes (Ltd/PLC/Inc/etc.) are ignored - likely the same vendor entered as two "
                f"master records.",
                confidence="high", rule="company_suffix_match",
            )

    # Rule 2 - fuzzy fallback across all vendor pairs (pool is small enough
    # that no blocking is needed).
    for (id1, n1, _), (id2, n2, _) in combinations(normalized, 2):
        key = tuple(sorted((id1, id2)))
        if key in flagged:
            continue
        ratio = difflib.SequenceMatcher(None, n1.lower(), n2.lower()).ratio()
        if ratio >= NAME_SIMILARITY_THRESHOLD:
            flagged.add(key)
            add_finding(
                domain, table, "vendor_name", first_po_for(id2), "duplication",
                f"Vendor '{n2}' ({id2}) is a {ratio:.0%} textual match to '{n1}' ({id1}) - "
                f"possible duplicate vendor master record.",
                confidence="medium", rule="fuzzy_name_match",
            )


def check_duplication(con):
    hr_rows = con.execute("SELECT employee_id, name FROM legacy_hr ORDER BY employee_id").fetchall()
    _find_person_duplicates(hr_rows, "HR", "legacy_hr")

    vendor_rows = con.execute(
        "SELECT DISTINCT vendor_id, vendor_name FROM legacy_procurement ORDER BY vendor_id"
    ).fetchall()
    _find_company_duplicates(con, vendor_rows)


# ---------------------------------------------------------------------------
# Obsolete/orphaned: referential checks
# ---------------------------------------------------------------------------


def check_obsolete_orphaned(con):
    rows = con.execute(
        "SELECT gl_entry_id, vendor_id FROM legacy_finance "
        "WHERE vendor_id NOT IN (SELECT DISTINCT vendor_id FROM legacy_procurement)"
    ).fetchall()
    for gl_id, vendor_id in rows:
        add_finding(
            "Finance", "legacy_finance", "vendor_id", gl_id, "obsolete_orphaned",
            f"References vendor_id '{vendor_id}', which does not exist anywhere in Procurement "
            f"- orphaned reference.",
            confidence="high", rule="orphan_fk",
        )

    rows = con.execute(
        "SELECT expense_id, employee_id FROM legacy_expenses "
        "WHERE employee_id NOT IN (SELECT DISTINCT employee_id FROM legacy_hr)"
    ).fetchall()
    for exp_id, emp_id in rows:
        add_finding(
            "Expenses", "legacy_expenses", "employee_id", exp_id, "obsolete_orphaned",
            f"References employee_id '{emp_id}', which does not exist in HR - likely a leaver "
            f"whose record was never deactivated or reassigned.",
            confidence="high", rule="orphan_fk",
        )


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_findings(con):
    con.execute("DROP TABLE IF EXISTS discovery_findings")
    con.execute(
        """
        CREATE TABLE discovery_findings (
            finding_id INTEGER,
            domain VARCHAR,
            table_name VARCHAR,
            column_name VARCHAR,
            row_identifier VARCHAR,
            issue_type VARCHAR,
            description VARCHAR,
            confidence VARCHAR,
            detection_rule VARCHAR
        )
        """
    )
    for i, f in enumerate(_findings, start=1):
        con.execute(
            "INSERT INTO discovery_findings VALUES (?,?,?,?,?,?,?,?,?)",
            [i, f["domain"], f["table"], f["column"], f["row_identifier"], f["issue_type"],
             f["description"], f["confidence"], f["detection_rule"]],
        )


def print_findings_summary():
    by_domain_type = {}
    for f in _findings:
        key = (f["domain"], f["issue_type"])
        by_domain_type[key] = by_domain_type.get(key, 0) + 1

    domains = sorted({d for d, _ in by_domain_type})
    header = f"{'Domain':<12}" + "".join(f"{t:<18}" for t in ISSUE_TYPES) + "Total"
    print("\nDiscovery findings summary")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    grand_total = 0
    for d in domains:
        counts = [by_domain_type.get((d, t), 0) for t in ISSUE_TYPES]
        total = sum(counts)
        grand_total += total
        print(f"{d:<12}" + "".join(f"{c:<18}" for c in counts) + f"{total}")
    print("-" * len(header))
    col_totals = [sum(by_domain_type.get((d, t), 0) for d in domains) for t in ISSUE_TYPES]
    print(f"{'Total':<12}" + "".join(f"{c:<18}" for c in col_totals) + f"{grand_total}")
    print()


# ---------------------------------------------------------------------------
# Scoring against ground truth
# ---------------------------------------------------------------------------


def score_against_manifest(con):
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    findings = con.execute(
        "SELECT row_identifier, issue_type, domain, detection_rule, description FROM discovery_findings"
    ).fetchall()

    # An injected issue counts as "caught" if some finding names the same
    # row and the same issue_type - the exact wording of the description
    # doesn't have to match, just the identification of the problem.
    manifest_keys = {(i["row_identifier"], i["issue_type"]) for i in manifest}
    finding_keys = {(r[0], r[1]) for r in findings}

    matched = [i for i in manifest if (i["row_identifier"], i["issue_type"]) in finding_keys]
    missed = [i for i in manifest if (i["row_identifier"], i["issue_type"]) not in finding_keys]
    false_positives = [r for r in findings if (r[0], r[1]) not in manifest_keys]

    recall = len(matched) / len(manifest) * 100 if manifest else 0.0
    fp_rate = len(false_positives) / len(findings) * 100 if findings else 0.0

    print("=" * 70)
    print("SCORING vs data/raw/injected_issues.json")
    print("=" * 70)
    print(f"Recall:          {len(matched)}/{len(manifest)} injected issues caught ({recall:.1f}%)")
    print(f"False positives: {len(false_positives)}/{len(findings)} findings ({fp_rate:.1f}%)")
    gate = "PASS" if recall >= 80 else "FAIL"
    print(f"Stage 2 gate (>= 80% recall): {gate}")

    print("\nRecall by issue_type:")
    for itype in ISSUE_TYPES:
        total_t = [i for i in manifest if i["issue_type"] == itype]
        if not total_t:
            continue
        matched_t = [i for i in matched if i["issue_type"] == itype]
        print(f"  {itype:<20}{len(matched_t)}/{len(total_t)}")

    if missed:
        print("\nMissed injected issues:")
        for i in missed:
            print(f"  {i['issue_id']} {i['domain']} {i['row_identifier']} ({i['issue_type']}): {i['description']}")

    if false_positives:
        print(f"\nFindings with no matching injected issue ({len(false_positives)}):")
        for row_id, issue_type, domain, rule, description in false_positives:
            print(f"  {domain} {row_id} ({issue_type}) via {rule}: {description}")
    print()

    return recall, fp_rate


def main():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))

    load_staging_tables(con)

    check_completeness(con)
    check_accuracy(con)
    check_consistency(con)
    check_duplication(con)
    check_obsolete_orphaned(con)

    write_findings(con)
    print_findings_summary()
    print(f"Wrote {len(_findings)} findings to discovery_findings in {DB_PATH}\n")

    score_against_manifest(con)
    con.close()


if __name__ == "__main__":
    main()
