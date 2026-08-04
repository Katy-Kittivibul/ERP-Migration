-- sql/investigation/completeness.sql
-- Manual queries to investigate missing (NULL) values in critical fields.
-- Why run this: Before writing migration logic, we need to know how many rows are missing mandatory fields
-- like cost_centre or vendor_id, so we can decide whether to impute, flag, or hold them back.

-- 1. Check for missing cost_centre in Expenses
-- Expenses must map back to a cost_centre for financial rollups. This query isolates the affected rows.
SELECT expense_id, employee_id, amount, submitted_date 
FROM legacy_expenses 
WHERE cost_centre IS NULL OR trim(cost_centre) = '';

-- 2. Null rate overview for critical Procurement fields
-- Summarises the total count of missing vendor_ids and material_codes to assess overall data health.
SELECT 
    COUNT(*) AS total_rows,
    SUM(CASE WHEN vendor_id IS NULL THEN 1 ELSE 0 END) AS missing_vendor_id,
    SUM(CASE WHEN material_code IS NULL THEN 1 ELSE 0 END) AS missing_material_code
FROM legacy_procurement;

-- 3. Check for missing HR foundational data (name or hire_date)
-- An employee record without a name or hire date is generally invalid and requires HR intervention.
SELECT employee_id, job_title, employment_status 
FROM legacy_hr 
WHERE name IS NULL OR hire_date IS NULL;
