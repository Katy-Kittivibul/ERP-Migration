# Mapping Spec Judgement Calls & Notes

## 1. Cost Centre Normalisation
**Canonical Format chosen:** `CC-0NN` (e.g., `CC-001`, `CC-012`).
**Justification:** This format is already the most prevalent across the legacy tables (15 out of 17 distinct valid entries follow it). It uniquely identifies the domain with the "CC" prefix and pads the identifier for consistent string sorting and formatting, ensuring downstream joins on `cost_centre` succeed reliably.

### Normalisation Mapping Table
| Legacy Variant | Canonical Target | Explanation |
|---|---|---|
| `CC-001` to `CC-015` | `CC-001` to `CC-015` | Retained as-is (already standard). |
| `009` | `CC-009` | Missing prefix; mapped to `CC-009`. |
| `C007` | `CC-007` | Malformed prefix; mapped to `CC-007`. |
| `NaN` / `NULL` | `NULL` | Remains `NULL` (this is a completeness issue handled separately, not a normalisation issue). |

## 2. Column Mapping Judgement Calls
- **`HR.name` -> `target_hr.full_name`:** Renamed for clarity to prevent reserved keyword clashes in SQL dialects, mapped as VARCHAR.
- **`Expenses.category` & `Expenses.approval_status`:** Mapped to VARCHAR in the target schema for now, but these should ideally be refactored into strict `ENUM` types (e.g., `['Travel', 'Accommodation', 'Other', 'Office Supplies', 'Training', 'Meals']` and `['Approved', 'Pending', 'Rejected']`) during the implementation to prevent future data drift. I have mapped them as VARCHAR in the spec to allow MVP progression, but this is a deliberate flag.
- **`Procurement.delivery_status` & `Finance.document_type`:** Similar to Expenses fields above, these free-text fields should ideally be enforced via ENUMs or reference tables to prevent drift, though left as VARCHAR in the mapping spec.

## 3. Columns Not Migrated (Dropped)
- **`HR.employment_status` (Dropped):** 
  - **Reason:** This column exhibits "Consistency" logic errors (e.g., showing 'Active' when `termination_date` is in the past). 
  - **Justification:** It is fundamentally redundant and prone to contradiction. In the target system, employment status should be dynamically derived from `termination_date` (`active` if `termination_date IS NULL` or `> CURRENT_DATE`). Dropping it forces the target schema to rely on a single source of truth for employment timelines.
- **`Procurement.vendor_name` (Dropped):**
  - **Reason:** Flagged for "Duplication" issues (duplicate vendor records with minor name variants). 
  - **Justification:** Storing vendor names in a transactional table like Procurement is a denormalisation that guarantees inconsistencies. The target schema should solely rely on the shared `vendor_id`. *(See "Awkward Schema Design" note below).*

## 4. Cross-Check Against Discovery Findings
The proposed `cost_centre` normalisation explicitly resolves the specific drift instances flagged by the profiler:
- **Finding ID 7 (Finance / `GL-000029`):** 
  - *Before:* `009` (Flagged: "cost_centre is stored as '009', which doesn't match the standard 'CC-0NN' format")
  - *After:* `CC-009` — This fixes the consistency issue and ensures it will join correctly with HR/Expenses.
- **Finding ID 8 (Procurement / `PO-00187`):** 
  - *Before:* `C007` (Flagged: "cost_centre is stored as 'C007'")
  - *After:* `CC-007` — Correctly resolved to standard format.
- *Note:* Missing cost centres in Expenses (e.g., Finding IDs 1, 2, 3) are completeness issues, so normalisation leaves them as `NULL`. They must be resolved via imputation or business-rule rejections, not format normalisation.

## 5. Awkward CLAUDE.md Schema Considerations
- **Missing Vendor Master Table:** By correctly dropping `vendor_name` from Procurement to resolve duplication/consistency issues, we entirely lose vendor names because there is no `target_vendor` master table defined in the 4-domain MVP scope. While `vendor_id` successfully acts as the shared identity key between Finance and Procurement, a clean migration architecture would genuinely require a 5th domain (Vendor Master) to store the names.
- **Missing Foreign Keys:** Because the scope is limited to 4 transactional/operational tables, we cannot enforce true referential integrity for `cost_centre` or `vendor_id` without creating dummy reference dimension tables in the target database. The `mapping_validator.py` will have to mock these constraints manually.
