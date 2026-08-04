-- sql/investigation/accuracy.sql
-- Manual queries to uncover logic and value-based inaccuracies.
-- Why run this: Even if data exists and is formatted correctly, its actual value might be impossible.
-- We must find logic breaks (like negative prices) before they corrupt target databases.

-- 1. Impossible Quantities in Procurement
-- Quantities or unit prices cannot be negative. This query isolates rows violating this physical logic.
SELECT po_id, material_code, quantity, unit_price
FROM legacy_procurement
WHERE quantity < 0 OR unit_price < 0;

-- 2. Invalid Currency Codes in Finance
-- Checking currency codes against a hardcoded list of expected, plausible values.
-- This catches typos like "GPB" instead of "GBP".
SELECT gl_entry_id, amount, currency 
FROM legacy_finance
WHERE currency NOT IN ('GBP', 'EUR', 'USD') AND currency IS NOT NULL;

-- 3. Implausible Dates in HR
-- A termination date occurring before the hire date is a logical impossibility.
SELECT employee_id, hire_date, termination_date 
FROM legacy_hr
WHERE CAST(termination_date AS DATE) < CAST(hire_date AS DATE);
