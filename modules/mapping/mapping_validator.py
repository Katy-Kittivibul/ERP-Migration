"""Mapping validator (Stage 5): applies mapping_spec.yaml's column mappings and
cost_centre normalisation to all four legacy tables, builds target_vendor_master
by de-duplicating vendor names (reusing profiler.py's near-duplicate logic),
validates referential integrity, and resolves the issue_log entries that the
mapping stage actually fixes.

Run: python modules/mapping/mapping_validator.py
"""

import json
import re
import sys
from itertools import combinations
from pathlib import Path

import duckdb
import yaml
import difflib

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from modules.discovery.profiler import (  # noqa: E402
    NAME_SIMILARITY_THRESHOLD, COMPANY_SUFFIXES, COST_CENTRE_RE,
    TABLE_NAME, DOMAIN_LABEL, ID_COLUMN, load_staging_tables, _normalize_tokens,
)
from modules.issue_tracker.issue_log import mark_resolved  # noqa: E402

DB_PATH = ROOT_DIR / "data" / "db" / "erp.duckdb"
RAW_DIR = ROOT_DIR / "data" / "raw"
MANIFEST_PATH = RAW_DIR / "injected_issues.json"
MAPPING_SPEC_PATH = ROOT_DIR / "modules" / "mapping" / "mapping_spec.yaml"
REPORT_PATH = ROOT_DIR / "docs" / "mapping_validation_report.md"

ID_COLUMN_BY_LEGACY_TABLE = {TABLE_NAME[k]: ID_COLUMN[k] for k in TABLE_NAME}

REQUIRED_TABLES = [
    "legacy_hr", "legacy_finance", "legacy_procurement", "legacy_expenses",
    "discovery_findings", "issue_log",
]

# cost_centre normalisation, per docs/mapping_spec_notes.md section 1:
#   CC-0NN            -> unchanged (already canonical)
#   NNN (3 digits)     -> 'CC-' + NNN (missing prefix)
#   CNNN (C + 3 digit) -> 'CC-' + NNN (malformed prefix)
#   NULL / blank       -> stays NULL (a completeness issue, not a format one)
#   anything else      -> left unchanged (unrecognised pattern)
# Built as a plain (non f-string) string: the '{3}' regex quantifiers below would
# otherwise collide with Python's str.format/f-string interpolation syntax.
COST_CENTRE_CASE_SQL = """CASE
    WHEN cost_centre IS NULL OR trim(cost_centre) = '' THEN NULL
    WHEN regexp_matches(cost_centre, '^CC-[0-9]{3}$') THEN cost_centre
    WHEN regexp_matches(cost_centre, '^[0-9]{3}$') THEN 'CC-' || cost_centre
    WHEN regexp_matches(cost_centre, '^C[0-9]{3}$') THEN 'CC-' || substr(cost_centre, 2)
    ELSE cost_centre
END"""

CANONICAL_CC_RE = re.compile(COST_CENTRE_RE)


def normalize_cost_centre_py(value):
    """Pure-Python mirror of COST_CENTRE_CASE_SQL, used only to check whether a
    specific row's value actually changed (for the resolution logic below).
    KEEP THIS IN SYNC WITH COST_CENTRE_CASE_SQL — the resolver cross-checks the
    two against each other and warns loudly if they ever disagree."""
    if value is None or str(value).strip() == "":
        return None
    value = str(value).strip()
    if CANONICAL_CC_RE.match(value):
        return value
    if re.match(r"^[0-9]{3}$", value):
        return f"CC-{value}"
    if re.match(r"^C[0-9]{3}$", value):
        return f"CC-{value[1:]}"
    return value


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def require_tables(con, names):
    existing = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    missing = [n for n in names if n not in existing]
    if missing:
        raise RuntimeError(
            f"Missing required table(s): {missing}. Run modules/discovery/profiler.py "
            f"and modules/issue_tracker/issue_log.py before mapping_validator.py."
        )


def load_mapping_spec():
    return yaml.safe_load(MAPPING_SPEC_PATH.read_text(encoding="utf-8"))


def load_manifest_by_row_id():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {i["row_identifier"]: i["issue_type"] for i in manifest}


def row_identifier_lookup(con):
    """issue_id -> (row_identifier, table_name, detection_rule), joining issue_log
    to discovery_findings via the issue_id <-> finding_id numbering scheme
    (issue_log itself has no row_identifier column of its own)."""
    rows = con.execute(
        """
        SELECT il.issue_id, df.row_identifier, il.table_name, df.detection_rule, il.status
        FROM issue_log il
        JOIN discovery_findings df
          ON il.issue_id = 'ISSUE-' || lpad(df.finding_id::VARCHAR, 3, '0')
        """
    ).fetchall()
    return {issue_id: (row_id, table_name, rule, status) for issue_id, row_id, table_name, rule, status in rows}


def ground_truth_blocks(row_identifier, manifest_by_row_id):
    """Safety guard shared by every resolver: refuse to resolve anything the
    ground-truth manifest says is a completeness issue. A row not present in the
    manifest at all (e.g. the un-seeded HR name collision) never blocks."""
    return manifest_by_row_id.get(row_identifier) == "completeness"


# ---------------------------------------------------------------------------
# Part A: target_vendor_master
# ---------------------------------------------------------------------------


def cluster_vendors(vendor_rows):
    """Union-find clustering across ALL vendor_id/vendor_name pairs (not scoped to
    a single vendor_id) using the same two rules profiler.py's
    _find_company_duplicates applies: suffix-stripped exact match, then a fuzzy
    fallback. Reimplemented (not called directly) because that function is wired
    to profiler's private findings list, not reusable as a clustering utility.
    Returns {root_index: [member indices into vendor_rows]}.
    """
    n = len(vendor_rows)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    normalized = [_normalize_tokens(name, COMPANY_SUFFIXES) for _, name in vendor_rows]

    # Rule 1 - exact match once common company suffixes are stripped.
    by_norm_key = {}
    for idx, toks in enumerate(normalized):
        by_norm_key.setdefault(" ".join(toks), []).append(idx)
    for group in by_norm_key.values():
        for idx in group[1:]:
            union(group[0], idx)

    # Rule 2 - fuzzy fallback across all pairs (pool is small enough that no
    # blocking is needed, same as profiler's own comment justifies).
    for i, j in combinations(range(n), 2):
        ratio = difflib.SequenceMatcher(
            None, vendor_rows[i][1].lower(), vendor_rows[j][1].lower()
        ).ratio()
        if ratio >= NAME_SIMILARITY_THRESHOLD:
            union(i, j)

    clusters = {}
    for idx in range(n):
        clusters.setdefault(find(idx), []).append(idx)
    return clusters


def choose_canonical_name(vendor_rows, member_indices, po_counts):
    """Tiebreak, in order: most PO rows for that vendor_id (most frequent
    variant) -> longest name (most complete-looking) -> alphabetical (final
    deterministic tiebreak if both of the above still tie)."""
    candidates = [(vendor_rows[i][0], vendor_rows[i][1]) for i in member_indices]
    candidates.sort(key=lambda c: (-po_counts.get(c[0], 0), -len(c[1]), c[1]))
    return candidates[0][1]


def build_vendor_master(con):
    vendor_rows = con.execute(
        "SELECT DISTINCT vendor_id, vendor_name FROM legacy_procurement ORDER BY vendor_id"
    ).fetchall()
    po_counts = dict(con.execute("SELECT vendor_id, COUNT(*) FROM legacy_procurement GROUP BY vendor_id").fetchall())

    clusters = cluster_vendors(vendor_rows)

    con.execute("DROP TABLE IF EXISTS target_vendor_master")
    con.execute("CREATE TABLE target_vendor_master (vendor_id VARCHAR, canonical_vendor_name VARCHAR)")

    rows_out = []
    for member_indices in clusters.values():
        canonical_name = choose_canonical_name(vendor_rows, member_indices, po_counts)
        for i in member_indices:
            rows_out.append((vendor_rows[i][0], canonical_name))
    con.executemany("INSERT INTO target_vendor_master VALUES (?, ?)", rows_out)

    n_clusters, n_raw = len(clusters), len(vendor_rows)
    print(f"target_vendor_master: {n_clusters} vendors consolidated from {n_raw} raw name variants")

    multi_member_clusters = [
        [vendor_rows[i][0] for i in members] for members in clusters.values() if len(members) > 1
    ]
    return vendor_rows, clusters, n_clusters, n_raw, multi_member_clusters


# ---------------------------------------------------------------------------
# Part B: apply mapping_spec.yaml + cost_centre normalisation
# ---------------------------------------------------------------------------


def build_column_select_sql(domain):
    parts = []
    for m in domain["mappings"]:
        legacy_col, target_col, target_type = m["legacy_column"], m["target_column"], m["target_type"]
        if legacy_col == "cost_centre":
            parts.append(f"CAST({COST_CENTRE_CASE_SQL} AS VARCHAR) AS {target_col}")
        else:
            parts.append(f"CAST({legacy_col} AS {target_type}) AS {target_col}")
    return ",\n    ".join(parts)


def build_target_tables(con, spec):
    stats = {}
    for domain in spec["domains"].values():
        legacy_table, target_table = domain["legacy_table"], domain["target_table"]
        select_sql = build_column_select_sql(domain)
        con.execute(f"DROP TABLE IF EXISTS {target_table}")
        con.execute(f"CREATE TABLE {target_table} AS SELECT\n    {select_sql}\nFROM {legacy_table}")

        legacy_count = con.execute(f"SELECT COUNT(*) FROM {legacy_table}").fetchone()[0]
        target_count = con.execute(f"SELECT COUNT(*) FROM {target_table}").fetchone()[0]
        if legacy_count != target_count:
            raise RuntimeError(
                f"{target_table} has {target_count} rows but {legacy_table} has {legacy_count} - "
                f"a straight column projection should never add or drop rows."
            )

        cc_stats = None
        if any(m["legacy_column"] == "cost_centre" for m in domain["mappings"]):
            already_canonical, left_null, fixed, unrecognised = 0, 0, 0, 0
            legacy_values = [r[0] for r in con.execute(f"SELECT cost_centre FROM {legacy_table}").fetchall()]
            for v in legacy_values:
                new_v = normalize_cost_centre_py(v)
                if v is None or str(v).strip() == "":
                    left_null += 1
                elif CANONICAL_CC_RE.match(str(v).strip()):
                    already_canonical += 1
                elif new_v and CANONICAL_CC_RE.match(new_v):
                    fixed += 1
                else:
                    unrecognised += 1
            cc_stats = {
                "already_canonical": already_canonical, "fixed": fixed,
                "left_null": left_null, "unrecognised": unrecognised,
            }

        stats[target_table] = {"row_count": target_count, "cost_centre": cc_stats}
    return stats


def hr_status_column_dropped(spec):
    hr_targets = {m["target_column"] for m in spec["domains"]["hr"]["mappings"]}
    return "employment_status" not in hr_targets


# ---------------------------------------------------------------------------
# Referential integrity
# ---------------------------------------------------------------------------


def check_vendor_fk(con):
    return con.execute(
        """
        SELECT gl_entry_id, vendor_id FROM target_finance f
        WHERE vendor_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM target_vendor_master v WHERE v.vendor_id = f.vendor_id)
        ORDER BY gl_entry_id
        """
    ).fetchall()


def check_employee_fk(con):
    return con.execute(
        """
        SELECT expense_id, employee_id FROM target_expenses e
        WHERE employee_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM target_hr h WHERE h.employee_id = e.employee_id)
        ORDER BY expense_id
        """
    ).fetchall()


def check_cost_centre_consistency(con):
    violations = []
    for target_table in ("target_hr", "target_finance", "target_procurement", "target_expenses"):
        rows = con.execute(
            f"SELECT DISTINCT cost_centre FROM {target_table} "
            f"WHERE cost_centre IS NOT NULL AND NOT regexp_matches(cost_centre, '^CC-[0-9]{{3}}$')"
        ).fetchall()
        for (v,) in rows:
            violations.append((target_table, v))
    return violations


# ---------------------------------------------------------------------------
# Resolution logic
# ---------------------------------------------------------------------------


def resolve_cost_centre_issues(con, manifest_by_row_id):
    resolved = []
    candidates = con.execute(
        """
        SELECT il.issue_id, il.table_name, df.row_identifier
        FROM issue_log il
        JOIN discovery_findings df
          ON il.issue_id = 'ISSUE-' || lpad(df.finding_id::VARCHAR, 3, '0')
        WHERE il.status = 'Open' AND il.issue_type = 'consistency' AND il.column_name = 'cost_centre'
        """
    ).fetchall()

    for issue_id, table_name, row_identifier in candidates:
        if ground_truth_blocks(row_identifier, manifest_by_row_id):
            print(f"  SKIP {issue_id}: ground truth says completeness, not resolving.")
            continue

        id_col = ID_COLUMN_BY_LEGACY_TABLE[table_name]
        old_value = con.execute(f"SELECT cost_centre FROM {table_name} WHERE {id_col} = ?", [row_identifier]).fetchone()[0]
        new_value = normalize_cost_centre_py(old_value)
        old_ok = bool(old_value and CANONICAL_CC_RE.match(str(old_value)))
        new_ok = bool(new_value and CANONICAL_CC_RE.match(str(new_value)))

        if not old_ok and new_ok:
            actual_target_value = con.execute(
                f"SELECT cost_centre FROM {table_name.replace('legacy_', 'target_')} WHERE {id_col} = ?",
                [row_identifier],
            ).fetchone()[0]
            if actual_target_value != new_value:
                print(
                    f"  WARNING: SQL/Python cost_centre normalisation disagree for {row_identifier}: "
                    f"SQL gave '{actual_target_value}', Python gave '{new_value}'."
                )
            note = f"cost_centre normalised via canonical mapping in Stage 5: '{old_value}' -> '{new_value}'."
            mark_resolved(issue_id, note)
            resolved.append((issue_id, note))

    return resolved


def resolve_employment_status_issue(con, spec, manifest_by_row_id):
    resolved = []
    if not hr_status_column_dropped(spec):
        return resolved

    candidates = con.execute(
        """
        SELECT il.issue_id, df.row_identifier
        FROM issue_log il
        JOIN discovery_findings df
          ON il.issue_id = 'ISSUE-' || lpad(df.finding_id::VARCHAR, 3, '0')
        WHERE il.status = 'Open' AND il.issue_type = 'consistency'
          AND il.column_name = 'employment_status' AND df.detection_rule = 'status_date_contradiction'
        """
    ).fetchall()

    for issue_id, row_identifier in candidates:
        if ground_truth_blocks(row_identifier, manifest_by_row_id):
            print(f"  SKIP {issue_id}: ground truth says completeness, not resolving.")
            continue
        note = (
            "employment_status dropped from target_hr mapping per Stage 3 sign-off; "
            "employment status is now derived solely from termination_date (single source "
            "of truth), eliminating the Active/past-termination_date contradiction."
        )
        mark_resolved(issue_id, note)
        resolved.append((issue_id, note))

    return resolved


def resolve_vendor_duplication_issues(con, vendor_rows, clusters, manifest_by_row_id):
    resolved = []
    vendor_id_to_root = {vendor_rows[i][0]: root for root, idxs in clusters.items() for i in idxs}
    canonical_by_vendor_id = dict(con.execute("SELECT vendor_id, canonical_vendor_name FROM target_vendor_master").fetchall())

    candidates = con.execute(
        """
        SELECT il.issue_id, df.row_identifier
        FROM issue_log il
        JOIN discovery_findings df
          ON il.issue_id = 'ISSUE-' || lpad(df.finding_id::VARCHAR, 3, '0')
        WHERE il.status = 'Open' AND il.issue_type = 'duplication' AND il.column_name = 'vendor_name'
          AND df.detection_rule IN ('company_suffix_match', 'fuzzy_name_match')
        """
    ).fetchall()

    for issue_id, row_identifier in candidates:
        if ground_truth_blocks(row_identifier, manifest_by_row_id):
            print(f"  SKIP {issue_id}: ground truth says completeness, not resolving.")
            continue

        # row_identifier here is a po_id (see profiler.py's first_po_for), not a
        # vendor_id - resolve it to the vendor_id it was actually raised against.
        po_row = con.execute("SELECT vendor_id FROM legacy_procurement WHERE po_id = ?", [row_identifier]).fetchone()
        if not po_row:
            continue
        vendor_id = po_row[0]
        root = vendor_id_to_root.get(vendor_id)
        members = [vendor_rows[i][0] for i in clusters.get(root, [])] if root is not None else []

        if len(members) > 1:
            canonical = canonical_by_vendor_id[vendor_id]
            note = (
                f"Vendor name duplication resolved via target_vendor_master: "
                f"{', '.join(sorted(members))} consolidated to canonical name '{canonical}' (Stage 5)."
            )
            mark_resolved(issue_id, note)
            resolved.append((issue_id, note))

    return resolved


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

STILL_OPEN_REASONS = {
    "completeness": "blank value at source - no imputation/rejection logic in scope for this stage.",
    "accuracy": "invalid/out-of-range value - no correction logic in scope for this stage.",
    "duplication": "employee-level duplicate - no merge/dedup mechanism built for HR at this stage.",
    "obsolete_orphaned": "references a vendor/employee id that never existed or was never deactivated - no valid target to map to.",
}


def build_report(build_stats, n_clusters, n_raw, multi_member_clusters, fk_vendor_orphans,
                  fk_employee_orphans, cc_violations, row_id_lookup, resolved, still_open):
    lines = []
    lines.append("=" * 70)
    lines.append("MAPPING VALIDATION REPORT (Stage 5)")
    lines.append("=" * 70)

    lines.append("\nTarget table build")
    lines.append("-" * 40)
    for table, info in build_stats.items():
        lines.append(f"  {table}: {info['row_count']} rows")
    lines.append(f"  target_vendor_master: {n_raw} rows")

    lines.append("\nVendor consolidation (target_vendor_master)")
    lines.append("-" * 40)
    lines.append(f"  {n_clusters} vendors consolidated from {n_raw} raw name variants")
    for members in multi_member_clusters:
        lines.append(f"    merged: {', '.join(members)}")

    lines.append("\ncost_centre normalisation (per target table)")
    lines.append("-" * 40)
    for table, info in build_stats.items():
        cc = info["cost_centre"]
        if cc:
            lines.append(
                f"  {table}: {cc['already_canonical']} already canonical, {cc['fixed']} fixed, "
                f"{cc['left_null']} left null, {cc['unrecognised']} unrecognised"
            )

    lines.append("\nReferential integrity: target_finance.vendor_id -> target_vendor_master")
    lines.append("-" * 40)
    if fk_vendor_orphans:
        lines.append(f"  FAIL: {len(fk_vendor_orphans)} orphaned reference(s)")
        for gl_id, vendor_id in fk_vendor_orphans:
            issue_id = next((k for k, v in row_id_lookup.items() if v[0] == gl_id), None)
            status = row_id_lookup[issue_id][3] if issue_id else "not tracked"
            lines.append(f"    {gl_id} -> {vendor_id} not found (see {issue_id}, remains {status})")
    else:
        lines.append("  PASS: no orphaned vendor_id references")

    lines.append("\nReferential integrity: target_expenses.employee_id -> target_hr.employee_id")
    lines.append("-" * 40)
    if fk_employee_orphans:
        lines.append(f"  FAIL: {len(fk_employee_orphans)} orphaned reference(s)")
        for exp_id, emp_id in fk_employee_orphans:
            issue_id = next((k for k, v in row_id_lookup.items() if v[0] == exp_id), None)
            status = row_id_lookup[issue_id][3] if issue_id else "not tracked"
            lines.append(f"    {exp_id} -> {emp_id} not found (see {issue_id}, remains {status})")
    else:
        lines.append("  PASS: no orphaned employee_id references")

    lines.append("\ncost_centre cross-domain consistency (post-normalisation)")
    lines.append("-" * 40)
    if cc_violations:
        lines.append(f"  FAIL: {len(cc_violations)} non-conforming value(s)")
        for table, v in cc_violations:
            lines.append(f"    {table}: '{v}'")
    else:
        lines.append("  PASS: all non-null cost_centre values conform to CC-0NN")

    lines.append(f"\nResolved this stage ({len(resolved)})")
    lines.append("-" * 40)
    for issue_id, note in resolved:
        lines.append(f"  {issue_id}: {note}")

    lines.append(f"\nStill open ({len(still_open)})")
    lines.append("-" * 40)
    by_type = {}
    for issue_id, domain, issue_type, column_name in still_open:
        by_type.setdefault(issue_type, []).append((issue_id, domain, column_name))
    for issue_type, rows in sorted(by_type.items()):
        reason = STILL_OPEN_REASONS.get(issue_type, "not resolved by this stage.")
        lines.append(f"  {issue_type} - {reason}")
        for issue_id, domain, column_name in rows:
            lines.append(f"    {issue_id} ({domain}.{column_name})")

    lines.append(f"\n{len(resolved)} issue(s) resolved this stage, {len(still_open)} remain Open.")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    con = duckdb.connect(str(DB_PATH))

    require_tables(con, REQUIRED_TABLES)
    load_staging_tables(con)

    spec = load_mapping_spec()
    manifest_by_row_id = load_manifest_by_row_id()

    vendor_rows, clusters, n_clusters, n_raw, multi_member_clusters = build_vendor_master(con)
    build_stats = build_target_tables(con, spec)

    fk_vendor_orphans = check_vendor_fk(con)
    fk_employee_orphans = check_employee_fk(con)
    cc_violations = check_cost_centre_consistency(con)
    row_id_lookup = row_identifier_lookup(con)

    resolved = []
    resolved += resolve_cost_centre_issues(con, manifest_by_row_id)
    resolved += resolve_employment_status_issue(con, spec, manifest_by_row_id)
    resolved += resolve_vendor_duplication_issues(con, vendor_rows, clusters, manifest_by_row_id)

    still_open = con.execute(
        "SELECT issue_id, domain, issue_type, column_name FROM issue_log WHERE status = 'Open' ORDER BY issue_id"
    ).fetchall()

    report_text = build_report(
        build_stats, n_clusters, n_raw, multi_member_clusters, fk_vendor_orphans,
        fk_employee_orphans, cc_violations, row_id_lookup, resolved, still_open,
    )
    print(report_text)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report_text, encoding="utf-8")

    con.close()


if __name__ == "__main__":
    main()
