-- sql/investigation/duplication.sql
-- Manual queries to identify exact and near-duplicate records.
-- Why run this: Duplicate master data (like employees or vendors) skews reporting and causes
-- downstream system errors. We need to find them to decide whether to merge them or flag them for review.

-- 1. Exact Name Duplicates in HR
-- A simple exact grouping. Finds employees with identically spelled names.
SELECT name, COUNT(*) as duplicate_count, string_agg(employee_id, ', ') as employee_ids
FROM legacy_hr
GROUP BY name
HAVING COUNT(*) > 1;

-- 2. Near-Duplicates in HR (Ignoring spacing and casing)
-- Finds employees whose names are identical once normalized (e.g., 'J. Smith' and 'j.smith').
-- In a real scenario, this helps catch data entry variants that the exact match misses.
SELECT 
    LOWER(REPLACE(name, ' ', '')) as normalized_name, 
    COUNT(*) as duplicate_count, 
    string_agg(employee_id || ' (' || name || ')', ', ') as employees
FROM legacy_hr
GROUP BY LOWER(REPLACE(name, ' ', ''))
HAVING COUNT(*) > 1;

-- 3. Exact and Near Vendor Duplicates in Procurement
-- Vendors are notorious for duplication (e.g. "Acme Corp" vs "Acme Corp.").
-- We use a CTE to get DISTINCT vendors first to avoid a fan-out from multiple POs per vendor.
WITH distinct_vendors AS (
    SELECT DISTINCT vendor_id, vendor_name
    FROM legacy_procurement
)
SELECT 
    LOWER(REPLACE(REPLACE(vendor_name, ' Ltd', ''), ' PLC', '')) as normalized_name, 
    COUNT(*) as duplicate_count, 
    string_agg(vendor_id || ' (' || vendor_name || ')', ', ') as vendors
FROM distinct_vendors
GROUP BY LOWER(REPLACE(REPLACE(vendor_name, ' Ltd', ''), ' PLC', ''))
HAVING COUNT(*) > 1;
