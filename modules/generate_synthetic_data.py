"""Generates synthetic SAP-ECC-flavoured legacy data for the HR, Finance,
Procurement, and Expenses domains, and injects a fixed set of data-quality
issues per the plan in CLAUDE.md. Every injected issue is recorded as ground
truth in data/raw/injected_issues.json for scoring the discovery module.

Run: python modules/generate_synthetic_data.py
"""

import csv
import json
import random
from pathlib import Path

from faker import Faker

SEED = 42
random.seed(SEED)
fake = Faker("en_GB")
Faker.seed(SEED)

ROOT_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT_DIR / "data" / "raw"

N_ROWS = 200
N_COST_CENTRES = 15

ISSUE_TYPES = {
    "completeness",
    "accuracy",
    "consistency",
    "duplication",
    "obsolete_orphaned",
}

DEPTS = ["HR", "FIN", "PROC", "SALES", "IT", "OPS", "MKT", "LEGAL"]
ACCOUNT_CODES = ["400100", "400200", "500100", "600100", "700100"]
DOCUMENT_TYPES = ["Invoice", "Credit Memo", "Payment", "Journal Entry"]
DELIVERY_STATUSES = ["Delivered", "Pending", "Cancelled", "Partially Delivered"]
EXPENSE_CATEGORIES = ["Travel", "Meals", "Accommodation", "Office Supplies", "Training", "Other"]
APPROVAL_STATUSES = ["Approved", "Pending", "Rejected"]

# ---------------------------------------------------------------------------
# Ground truth manifest
# ---------------------------------------------------------------------------

_injected_issues = []
_issue_counter = 0


def log_issue(domain, table, row_identifier, column, issue_type, description):
    global _issue_counter
    if issue_type not in ISSUE_TYPES:
        raise ValueError(f"Unknown issue_type: {issue_type}")
    _issue_counter += 1
    _injected_issues.append(
        {
            "issue_id": f"ISS-{_issue_counter:03d}",
            "domain": domain,
            "table": table,
            "row_identifier": row_identifier,
            "column": column,
            "issue_type": issue_type,
            "description": description,
        }
    )


# ---------------------------------------------------------------------------
# HR
# ---------------------------------------------------------------------------


def generate_hr(cost_centres):
    employee_ids = [f"EMP-{i:04d}" for i in range(1, N_ROWS + 1)]
    manager_pool = random.sample(employee_ids, 20)

    rows = []
    for emp_id in employee_ids:
        name = fake.name()
        dept_code = random.choice(DEPTS)
        hire_date = fake.date_between(start_date="-10y", end_date="-30d")
        is_terminated = random.random() < 0.15
        if is_terminated:
            employment_status = "Terminated"
            termination_date = fake.date_between(start_date=hire_date, end_date="today")
        else:
            employment_status = "Active"
            termination_date = None

        manager_id = random.choice(manager_pool)
        if manager_id == emp_id:
            manager_id = ""

        rows.append(
            {
                "employee_id": emp_id,
                "name": name,
                "dept_code": dept_code,
                "job_title": fake.job(),
                "hire_date": hire_date.isoformat(),
                "termination_date": termination_date.isoformat() if termination_date else "",
                "employment_status": employment_status,
                "cost_centre": random.choice(cost_centres),
                "manager_id": manager_id,
            }
        )
    return rows, employee_ids


def inject_hr_issues(rows, employee_ids):
    # Duplication: 2 employees re-entered under a new employee_id with a
    # minor name variant (e.g. accidental double onboarding).
    name_variants = [
        lambda n: n.replace("Jon", "Jonathan") if "Jon" in n else n + " ",
        lambda n: n.strip().split(" ")[0] + " " + n.strip().split(" ")[-1][0] + ".",
    ]
    dup_sources = random.sample([r for r in rows], 2)
    next_id = N_ROWS + 1
    for i, source in enumerate(dup_sources):
        new_id = f"EMP-{next_id:04d}"
        next_id += 1
        variant_name = name_variants[i % len(name_variants)](source["name"])
        dup_row = dict(source)
        dup_row["employee_id"] = new_id
        dup_row["name"] = variant_name
        rows.append(dup_row)
        employee_ids.append(new_id)
        log_issue(
            domain="HR",
            table="legacy_hr",
            row_identifier=new_id,
            column="name",
            issue_type="duplication",
            description=(
                f"Likely duplicate of {source['employee_id']} ('{source['name']}'): "
                f"re-entered as {new_id} with name variant '{variant_name}'."
            ),
        )

    # Consistency: employment_status says Active but termination_date is
    # populated with a past date (contradictory record).
    active_rows = [r for r in rows if r["employment_status"] == "Active"]
    target = random.choice(active_rows)
    bad_term_date = fake.date_between(start_date="-2y", end_date="-30d")
    target["termination_date"] = bad_term_date.isoformat()
    log_issue(
        domain="HR",
        table="legacy_hr",
        row_identifier=target["employee_id"],
        column="employment_status",
        issue_type="consistency",
        description=(
            f"employment_status is 'Active' but termination_date is populated with a "
            f"past date ({bad_term_date.isoformat()}) - contradictory record."
        ),
    )


# ---------------------------------------------------------------------------
# Procurement
# ---------------------------------------------------------------------------


def generate_procurement(cost_centres):
    vendor_ids = [f"VEND-{i:03d}" for i in range(1, 41)]
    vendor_names = {vid: fake.company() for vid in vendor_ids}
    material_codes = [f"MAT-{i:04d}" for i in range(1, 60)]

    # Every vendor in the pool gets at least one PO row (first len(vendor_ids)
    # rows cover the pool exactly once) so that a vendor_id used elsewhere
    # (e.g. Finance) is never an orphan purely by chance of random sampling.
    # Remaining rows draw vendors at random as normal.
    vendor_draws = list(vendor_ids)
    vendor_draws += [random.choice(vendor_ids) for _ in range(N_ROWS - len(vendor_ids))]
    random.shuffle(vendor_draws)

    rows = []
    for i, vendor_id in enumerate(vendor_draws, start=1):
        rows.append(
            {
                "po_id": f"PO-{i:05d}",
                "vendor_id": vendor_id,
                "vendor_name": vendor_names[vendor_id],
                "material_code": random.choice(material_codes),
                "quantity": random.randint(1, 500),
                "unit_price": round(random.uniform(5, 5000), 2),
                "po_date": fake.date_between(start_date="-2y", end_date="today").isoformat(),
                "delivery_status": random.choice(DELIVERY_STATUSES),
                "cost_centre": random.choice(cost_centres),
            }
        )
    return rows, vendor_ids, vendor_names


def inject_procurement_issues(rows, vendor_ids, vendor_names):
    # Duplication: a vendor re-entered under a new vendor_id with a minor
    # company-name variant, attached to a new PO row.
    source_vendor_id = random.choice(vendor_ids)
    source_name = vendor_names[source_vendor_id]
    new_vendor_id = f"VEND-{len(vendor_ids) + 1:03d}"
    variant_name = source_name + " Ltd" if "Ltd" not in source_name else source_name.replace(" Ltd", " Limited")
    vendor_ids.append(new_vendor_id)
    vendor_names[new_vendor_id] = variant_name

    new_po = dict(rows[0])
    new_po_id = f"PO-{len(rows) + 1:05d}"
    new_po.update({"po_id": new_po_id, "vendor_id": new_vendor_id, "vendor_name": variant_name})
    rows.append(new_po)
    log_issue(
        domain="Procurement",
        table="legacy_procurement",
        row_identifier=new_po_id,
        column="vendor_name",
        issue_type="duplication",
        description=(
            f"Vendor '{variant_name}' ({new_vendor_id}) appears to be a duplicate of "
            f"'{source_name}' ({source_vendor_id}) - minor name variant, likely same vendor "
            f"entered as two master records."
        ),
    )

    # Accuracy: 2 negative quantities (physically impossible for a PO line).
    # Consistency: cost_centre format drift (e.g. "C005" instead of "CC-005").
    # Sampled together so no row is picked for two different injections.
    picked = random.sample(rows[:N_ROWS], 3)
    for row in picked[:2]:
        row["quantity"] = -abs(row["quantity"])
        log_issue(
            domain="Procurement",
            table="legacy_procurement",
            row_identifier=row["po_id"],
            column="quantity",
            issue_type="accuracy",
            description=f"quantity is negative ({row['quantity']}) - not a valid PO quantity.",
        )

    drift_row = picked[2]
    original_cc = drift_row["cost_centre"]
    drifted = "C" + original_cc.split("-")[1]
    drift_row["cost_centre"] = drifted
    log_issue(
        domain="Procurement",
        table="legacy_procurement",
        row_identifier=drift_row["po_id"],
        column="cost_centre",
        issue_type="consistency",
        description=f"cost_centre stored as '{drifted}' instead of canonical format '{original_cc}'.",
    )


# ---------------------------------------------------------------------------
# Finance
# ---------------------------------------------------------------------------


def generate_finance(cost_centres, vendor_ids):
    currencies_pool = ["GBP", "USD", "EUR"]
    rows = []
    for i in range(1, N_ROWS + 1):
        rows.append(
            {
                "gl_entry_id": f"GL-{i:06d}",
                "cost_centre": random.choice(cost_centres),
                "account_code": random.choice(ACCOUNT_CODES),
                "amount": round(random.uniform(-5000, 20000), 2),
                "currency": random.choices(currencies_pool, weights=[0.8, 0.1, 0.1])[0],
                "posting_date": fake.date_between(start_date="-2y", end_date="today").isoformat(),
                "document_type": random.choice(DOCUMENT_TYPES),
                "vendor_id": random.choice(vendor_ids),
            }
        )
    return rows


def inject_finance_issues(rows, vendor_ids):
    picked = random.sample(rows, 4)

    # Obsolete/orphaned: 2 entries reference a vendor_id not present in Procurement.
    for row in picked[:2]:
        bad_vendor_id = "VEND-999"
        row["vendor_id"] = bad_vendor_id
        log_issue(
            domain="Finance",
            table="legacy_finance",
            row_identifier=row["gl_entry_id"],
            column="vendor_id",
            issue_type="obsolete_orphaned",
            description=(
                f"References vendor_id '{bad_vendor_id}', which does not exist in the "
                f"Procurement vendor pool - orphaned reference."
            ),
        )

    # Accuracy: currency code typo (GBP -> GPB).
    typo_row = picked[2]
    typo_row["currency"] = "GPB"
    log_issue(
        domain="Finance",
        table="legacy_finance",
        row_identifier=typo_row["gl_entry_id"],
        column="currency",
        issue_type="accuracy",
        description="currency recorded as 'GPB', a typo of the valid ISO code 'GBP'.",
    )

    # Consistency: cost_centre format drift (e.g. "005" instead of "CC-005").
    drift_row = picked[3]
    original_cc = drift_row["cost_centre"]
    drifted = original_cc.split("-")[1]
    drift_row["cost_centre"] = drifted
    log_issue(
        domain="Finance",
        table="legacy_finance",
        row_identifier=drift_row["gl_entry_id"],
        column="cost_centre",
        issue_type="consistency",
        description=f"cost_centre stored as '{drifted}' instead of canonical format '{original_cc}'.",
    )


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------


def generate_expenses(cost_centres, employee_ids):
    rows = []
    for i in range(1, N_ROWS + 1):
        rows.append(
            {
                "expense_id": f"EXP-{i:05d}",
                "employee_id": random.choice(employee_ids),
                "cost_centre": random.choice(cost_centres),
                "category": random.choice(EXPENSE_CATEGORIES),
                "amount": round(random.uniform(5, 3000), 2),
                "submitted_date": fake.date_between(start_date="-1y", end_date="today").isoformat(),
                "approval_status": random.choice(APPROVAL_STATUSES),
                "currency": "GBP",
            }
        )
    return rows


def inject_expenses_issues(rows):
    picked = random.sample(rows, 4)

    # Completeness: missing cost_centre on 3 expense rows.
    for row in picked[:3]:
        row["cost_centre"] = ""
        log_issue(
            domain="Expenses",
            table="legacy_expenses",
            row_identifier=row["expense_id"],
            column="cost_centre",
            issue_type="completeness",
            description="cost_centre is missing (blank) on this expense claim.",
        )

    # Obsolete/orphaned: employee_id not present in HR (leaver not deactivated).
    orphan_row = picked[3]
    bad_employee_id = "EMP-9999"
    orphan_row["employee_id"] = bad_employee_id
    log_issue(
        domain="Expenses",
        table="legacy_expenses",
        row_identifier=orphan_row["expense_id"],
        column="employee_id",
        issue_type="obsolete_orphaned",
        description=(
            f"References employee_id '{bad_employee_id}', not present in HR - simulates a "
            f"leaver whose record was never deactivated/reassigned."
        ),
    )


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def write_csv(path, rows, fieldnames):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def print_summary():
    by_domain_type = {}
    for issue in _injected_issues:
        key = (issue["domain"], issue["issue_type"])
        by_domain_type[key] = by_domain_type.get(key, 0) + 1

    domains = sorted({d for d, _ in by_domain_type})
    types = sorted(ISSUE_TYPES)

    header = f"{'Domain':<12}" + "".join(f"{t:<18}" for t in types) + "Total"
    print("\nInjected issues summary")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    grand_total = 0
    for d in domains:
        row_counts = [by_domain_type.get((d, t), 0) for t in types]
        total = sum(row_counts)
        grand_total += total
        print(f"{d:<12}" + "".join(f"{c:<18}" for c in row_counts) + f"{total}")
    print("-" * len(header))
    col_totals = [sum(by_domain_type.get((d, t), 0) for d in domains) for t in types]
    print(f"{'Total':<12}" + "".join(f"{c:<18}" for c in col_totals) + f"{grand_total}")
    print()


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    cost_centres = [f"CC-{i:03d}" for i in range(1, N_COST_CENTRES + 1)]

    hr_rows, employee_ids = generate_hr(cost_centres)
    proc_rows, vendor_ids, vendor_names = generate_procurement(cost_centres)
    fin_rows = generate_finance(cost_centres, vendor_ids)
    exp_rows = generate_expenses(cost_centres, employee_ids)

    inject_hr_issues(hr_rows, employee_ids)
    inject_procurement_issues(proc_rows, vendor_ids, vendor_names)
    inject_finance_issues(fin_rows, vendor_ids)
    inject_expenses_issues(exp_rows)

    write_csv(
        RAW_DIR / "legacy_hr.csv",
        hr_rows,
        ["employee_id", "name", "dept_code", "job_title", "hire_date", "termination_date",
         "employment_status", "cost_centre", "manager_id"],
    )
    write_csv(
        RAW_DIR / "legacy_finance.csv",
        fin_rows,
        ["gl_entry_id", "cost_centre", "account_code", "amount", "currency", "posting_date",
         "document_type", "vendor_id"],
    )
    write_csv(
        RAW_DIR / "legacy_procurement.csv",
        proc_rows,
        ["po_id", "vendor_id", "vendor_name", "material_code", "quantity", "unit_price",
         "po_date", "delivery_status", "cost_centre"],
    )
    write_csv(
        RAW_DIR / "legacy_expenses.csv",
        exp_rows,
        ["expense_id", "employee_id", "cost_centre", "category", "amount", "submitted_date",
         "approval_status", "currency"],
    )

    with open(RAW_DIR / "injected_issues.json", "w", encoding="utf-8") as f:
        json.dump(_injected_issues, f, indent=2)

    print(f"Wrote {len(hr_rows)} HR rows, {len(fin_rows)} Finance rows, "
          f"{len(proc_rows)} Procurement rows, {len(exp_rows)} Expenses rows to {RAW_DIR}")
    print(f"Wrote {len(_injected_issues)} ground-truth issues to "
          f"{RAW_DIR / 'injected_issues.json'}")
    print_summary()


if __name__ == "__main__":
    main()
