"""HR duplicate review (per CLAUDE.md's "Target-only addition - target_hr_master"
guardrails, added after Stage 5): clusters near-duplicate HR names using
profiler.py's existing similarity logic, but - unlike target_vendor_master -
NEVER auto-designates a canonical employee_id or merges employee records.

Produces:
  - target_hr_master(employee_id, canonical_name, cluster_id): one row per
    employee. canonical_name is always the employee's OWN name (never merged);
    cluster_id tags employees whose name looks like a possible duplicate of
    another employee's, and is NULL otherwise.
  - hr_duplicate_review(employee_id_a, employee_id_b, name_a, name_b,
    similarity_score, same_dept_code, same_cost_centre, hire_date_delta_days,
    recommendation): one row per flagged pair, for human (HR) review.

Only issue_log entries backing a "likely_duplicate" pair are marked Resolved
(via issue_log.mark_resolved, with a note that HR sign-off is still required
before any actual merge). Every "needs_human_review" pair is left Open, with
a note added pointing at hr_duplicate_review - this deliberately does NOT call
mark_resolved, since that would incorrectly flip status.

This module is purely additive: it does not touch target_vendor_master or any
of mapping_validator.py's existing logic. It DOES append a section to
docs/mapping_validation_report.md explaining why HR duplicates are handled
differently from vendor duplicates - run this AFTER mapping_validator.py each
time, since mapping_validator.py overwrites that report file wholesale.

Run: python modules/mapping/hr_dedup_review.py
"""

import difflib
import sys
from collections import namedtuple
from itertools import combinations
from pathlib import Path

import duckdb

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from modules.discovery.profiler import (  # noqa: E402
    NAME_SIMILARITY_THRESHOLD, TITLES, load_staging_tables, _normalize_tokens,
)
from modules.issue_tracker.issue_log import mark_resolved, write_markdown_file  # noqa: E402
from modules.mapping.mapping_validator import (  # noqa: E402
    require_tables, load_manifest_by_row_id, ground_truth_blocks,
)

DB_PATH = ROOT_DIR / "data" / "db" / "erp.duckdb"
REPORT_PATH = ROOT_DIR / "docs" / "mapping_validation_report.md"
REPORT_SECTION_MARKER = "## HR DUPLICATE REVIEW"

# A data-entry duplicate in this dataset is a literal re-entry of the same
# record (identical hire_date). 30 days gives real-world headroom for two
# near-simultaneous onboarding entries while still requiring dept_code AND
# cost_centre to match exactly - a coincidental same-name match (different
# person, different dept/cost_centre/hire era) will not pass all three.
HIRE_DATE_DELTA_THRESHOLD_DAYS = 30

HrDuplicatePair = namedtuple(
    "HrDuplicatePair",
    "employee_id_a employee_id_b name_a name_b similarity_score "
    "same_dept_code same_cost_centre hire_date_delta_days recommendation",
)


# ---------------------------------------------------------------------------
# Clustering - mirrors profiler.py's _find_person_duplicates' three rules
# (exact match, surname-abbreviation, fuzzy fallback), reusing its constants
# and normalisation helper directly. Reimplemented as union-find rather than
# calling _find_person_duplicates itself, since that function writes straight
# into profiler's private findings list and isn't reusable as a clustering
# utility - same reuse tradeoff already made for vendor clustering in
# mapping_validator.py's cluster_vendors().
# ---------------------------------------------------------------------------


def cluster_hr_names(con):
    rows = con.execute("SELECT employee_id, name FROM legacy_hr ORDER BY employee_id").fetchall()
    n = len(rows)
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

    normalized = [_normalize_tokens(name, TITLES) for _, name in rows]

    # Rule 1 - exact match once whitespace/case/titles are normalised away.
    by_norm = {}
    for idx, toks in enumerate(normalized):
        by_norm.setdefault(" ".join(toks), []).append(idx)
    for group in by_norm.values():
        for idx in group[1:]:
            union(group[0], idx)

    # Rule 2 - surname abbreviated to a single initial (same first token, one
    # record's last token is a single letter that starts the other's).
    by_first_token = {}
    for idx, toks in enumerate(normalized):
        if toks:
            by_first_token.setdefault(toks[0], []).append(idx)
    for group in by_first_token.values():
        for i, j in combinations(group, 2):
            if find(i) == find(j):
                continue
            t1, t2 = normalized[i], normalized[j]
            if len(t1) < 2 or len(t2) < 2:
                continue
            last1, last2 = t1[-1], t2[-1]
            if last1 == last2:
                continue
            is_abbreviation = (len(last1) == 1 and last2.startswith(last1)) or (
                len(last2) == 1 and last1.startswith(last2)
            )
            if is_abbreviation:
                union(i, j)

    # Rule 3 - fuzzy fallback, blocked by first-3-letters of first name.
    by_block = {}
    for idx, toks in enumerate(normalized):
        if toks:
            by_block.setdefault(toks[0][:3], []).append(idx)
    for group in by_block.values():
        for i, j in combinations(group, 2):
            if find(i) == find(j):
                continue
            ratio = difflib.SequenceMatcher(
                None, rows[i][1].strip().lower(), rows[j][1].strip().lower()
            ).ratio()
            if ratio >= NAME_SIMILARITY_THRESHOLD:
                union(i, j)

    clusters = {}
    for idx in range(n):
        clusters.setdefault(find(idx), []).append(idx)
    clusters = {root: members for root, members in clusters.items() if len(members) > 1}
    return rows, clusters


# ---------------------------------------------------------------------------
# target_hr_master - per-employee, never merges identity
# ---------------------------------------------------------------------------


def build_target_hr_master(con, rows, clusters):
    con.execute("DROP TABLE IF EXISTS target_hr_master")
    con.execute(
        """
        CREATE TABLE target_hr_master (
            employee_id VARCHAR,
            canonical_name VARCHAR,
            cluster_id VARCHAR
        )
        """
    )

    # cluster_id is a stable, deterministic tag (the lowest employee_id in the
    # cluster) - NOT a designated "canonical" employee. canonical_name is
    # always the employee's own name: this table records which employees look
    # like possible duplicates of each other, it does not pick a winner.
    idx_to_cluster_id = {}
    for members in clusters.values():
        cluster_employee_ids = sorted(rows[i][0] for i in members)
        cluster_id = cluster_employee_ids[0]
        for i in members:
            idx_to_cluster_id[i] = cluster_id

    rows_out = [(emp_id, name, idx_to_cluster_id.get(idx)) for idx, (emp_id, name) in enumerate(rows)]
    con.executemany("INSERT INTO target_hr_master VALUES (?, ?, ?)", rows_out)
    return idx_to_cluster_id


# ---------------------------------------------------------------------------
# hr_duplicate_review - per-pair, for human review
# ---------------------------------------------------------------------------


def build_hr_duplicate_review(con, rows, clusters):
    con.execute("DROP TABLE IF EXISTS hr_duplicate_review")
    con.execute(
        """
        CREATE TABLE hr_duplicate_review (
            employee_id_a VARCHAR,
            employee_id_b VARCHAR,
            name_a VARCHAR,
            name_b VARCHAR,
            similarity_score DOUBLE,
            same_dept_code BOOLEAN,
            same_cost_centre BOOLEAN,
            hire_date_delta_days INTEGER,
            recommendation VARCHAR
        )
        """
    )

    hr_by_id = {
        r[0]: r for r in con.execute(
            "SELECT employee_id, name, dept_code, cost_centre, hire_date FROM legacy_hr"
        ).fetchall()
    }

    pairs = []
    for members in clusters.values():
        member_ids = sorted(rows[i][0] for i in members)
        for id_a, id_b in combinations(member_ids, 2):
            _, name_a, dept_a, cc_a, hire_a = hr_by_id[id_a]
            _, name_b, dept_b, cc_b, hire_b = hr_by_id[id_b]
            similarity = difflib.SequenceMatcher(None, name_a.strip().lower(), name_b.strip().lower()).ratio()
            same_dept = dept_a == dept_b
            same_cc = cc_a == cc_b
            delta_days = abs((hire_a - hire_b).days)
            recommendation = (
                "likely_duplicate"
                if same_dept and same_cc and delta_days <= HIRE_DATE_DELTA_THRESHOLD_DAYS
                else "needs_human_review"
            )
            pairs.append(HrDuplicatePair(
                id_a, id_b, name_a, name_b, similarity, same_dept, same_cc, delta_days, recommendation,
            ))

    con.executemany(
        "INSERT INTO hr_duplicate_review VALUES (?,?,?,?,?,?,?,?,?)",
        [tuple(p) for p in pairs],
    )
    return pairs


# ---------------------------------------------------------------------------
# Resolution - only "likely_duplicate" pairs get mark_resolved(); everything
# else stays Open with an annotation, never a status flip.
# ---------------------------------------------------------------------------


def lookup_open_hr_duplication_issues(con):
    """row_identifier -> issue_id, for Open HR-duplication issue_log rows."""
    rows = con.execute(
        """
        SELECT df.row_identifier, il.issue_id
        FROM issue_log il
        JOIN discovery_findings df
          ON il.issue_id = 'ISSUE-' || lpad(df.finding_id::VARCHAR, 3, '0')
        WHERE il.status = 'Open' AND il.domain = 'HR' AND il.issue_type = 'duplication'
        """
    ).fetchall()
    return dict(rows)


def annotate_open_issue(con, issue_id, note):
    """Records a note on an Open issue_log row WITHOUT resolving it - deliberately
    bypasses issue_log.mark_resolved(), which always flips status to Resolved."""
    con.execute("UPDATE issue_log SET resolution = ? WHERE issue_id = ?", [note, issue_id])
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


def resolve_hr_duplicates(con, pairs, manifest_by_row_id):
    open_issues = lookup_open_hr_duplication_issues(con)
    resolved, annotated = [], []

    for pair in pairs:
        issue_id = open_issues.get(pair.employee_id_a) or open_issues.get(pair.employee_id_b)
        if not issue_id:
            continue
        row_identifier = pair.employee_id_a if pair.employee_id_a in open_issues else pair.employee_id_b
        other_id = pair.employee_id_b if row_identifier == pair.employee_id_a else pair.employee_id_a

        if ground_truth_blocks(row_identifier, manifest_by_row_id):
            print(f"  SKIP {issue_id}: ground truth says completeness, not resolving.")
            continue

        if pair.recommendation == "likely_duplicate":
            note = (
                f"Flagged as probable data-entry duplicate: same dept/cost_centre, "
                f"hire dates {pair.hire_date_delta_days} days apart. Consolidation requires "
                f"HR sign-off, not auto-merged."
            )
            mark_resolved(issue_id, note)
            resolved.append((issue_id, pair))
        else:
            note = (
                f"Flagged as a possible near-duplicate of {other_id} (similarity "
                f"{pair.similarity_score:.0%}) - see hr_duplicate_review table. dept_code/"
                f"cost_centre/hire_date do not all align, so this needs HR business review; "
                f"not auto-resolved."
            )
            annotate_open_issue(con, issue_id, note)
            annotated.append((issue_id, pair))

    return resolved, annotated


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def print_summary(clusters, pairs, resolved, annotated):
    n_likely = sum(1 for p in pairs if p.recommendation == "likely_duplicate")
    n_review = len(pairs) - n_likely

    print(f"\nHR duplicate review: {len(clusters)} cluster(s) found ({len(pairs)} pair(s))")
    print(f"  likely_duplicate:   {n_likely}")
    print(f"  needs_human_review: {n_review}")

    print(f"\nResolved via issue_log.mark_resolved ({len(resolved)}):")
    for issue_id, pair in resolved:
        print(f"  {issue_id}: {pair.employee_id_a} vs {pair.employee_id_b} ({pair.name_a!r} / {pair.name_b!r})")

    print(f"\nAnnotated, left Open ({len(annotated)}):")
    for issue_id, pair in annotated:
        print(f"  {issue_id}: {pair.employee_id_a} vs {pair.employee_id_b} ({pair.name_a!r} / {pair.name_b!r})")
    print()


def append_report_section(clusters, pairs, resolved, annotated):
    n_likely = sum(1 for p in pairs if p.recommendation == "likely_duplicate")
    n_review = len(pairs) - n_likely

    section = f"""{REPORT_SECTION_MARKER} (added after Stage 5 - see CLAUDE.md's target_hr_master note)
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
{HIRE_DATE_DELTA_THRESHOLD_DAYS} days) are marked Resolved in issue_log - with
a note that consolidation still requires HR sign-off, not that it happened
automatically. Every other flagged pair stays Open with a note pointing back
to this review table.

Clusters found: {len(clusters)} ({len(pairs)} pair(s))
  likely_duplicate:   {n_likely} (Resolved in issue_log; HR sign-off still required to actually merge)
  needs_human_review: {n_review} (left Open - see hr_duplicate_review for detail)

See the hr_duplicate_review and target_hr_master tables in erp.duckdb for full detail.
"""

    existing = REPORT_PATH.read_text(encoding="utf-8") if REPORT_PATH.exists() else ""
    marker_pos = existing.find(REPORT_SECTION_MARKER)
    if marker_pos != -1:
        existing = existing[:marker_pos].rstrip()
    REPORT_PATH.write_text(existing.rstrip() + "\n\n" + section, encoding="utf-8")


def main():
    con = duckdb.connect(str(DB_PATH))

    require_tables(con, ["legacy_hr", "discovery_findings", "issue_log"])
    load_staging_tables(con)

    manifest_by_row_id = load_manifest_by_row_id()

    rows, clusters = cluster_hr_names(con)
    build_target_hr_master(con, rows, clusters)
    pairs = build_hr_duplicate_review(con, rows, clusters)

    resolved, annotated = resolve_hr_duplicates(con, pairs, manifest_by_row_id)

    print_summary(clusters, pairs, resolved, annotated)
    append_report_section(clusters, pairs, resolved, annotated)

    con.close()


if __name__ == "__main__":
    main()
