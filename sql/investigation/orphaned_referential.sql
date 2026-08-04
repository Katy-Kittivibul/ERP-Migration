-- sql/investigation/orphaned_referential.sql
-- Manual queries to discover broken relationships between tables.
-- Why run this: Referential integrity is essential for a relational database. When a transaction
-- references a master record that doesn't exist (an orphan), it cannot be migrated safely.

-- 1. Orphaned Vendors in Finance
-- Uses a LEFT JOIN to find Finance entries pointing to a vendor_id that is completely absent
-- from the Procurement master records.
SELECT f.gl_entry_id, f.vendor_id, f.amount
FROM legacy_finance f
LEFT JOIN legacy_procurement p ON f.vendor_id = p.vendor_id
WHERE f.vendor_id IS NOT NULL 
  AND p.vendor_id IS NULL;

-- 2. Orphaned Employees in Expenses
-- Finds expense submissions from employee IDs that don't exist in the HR system.
SELECT e.expense_id, e.employee_id, e.amount
FROM legacy_expenses e
LEFT JOIN legacy_hr h ON e.employee_id = h.employee_id
WHERE h.employee_id IS NULL;

-- 3. Logic-Broken Employment States in HR
-- Finds employees marked 'Active' but who have a termination_date in the past. 
-- These are 'state orphans' - the data contradicts its own referential state logic.
SELECT employee_id, name, employment_status, termination_date
FROM legacy_hr
WHERE employment_status = 'Active' 
  AND termination_date IS NOT NULL 
  AND CAST(termination_date AS DATE) <= CURRENT_DATE;
