-- sql/investigation/consistency.sql
-- Manual queries to investigate formatting drift across the data.
-- Why run this: Inconsistent formatting (like 'CC-001' vs '001') causes silent data grouping 
-- errors in downstream reports. We query for patterns to discover the edge cases.

-- 1. Cost Centre Length Distribution in Finance
-- Grouping by string length is a fast heuristic for spotting format drift. If the standard
-- is 6 characters ('CC-0NN'), anything else is an anomaly.
SELECT LENGTH(cost_centre) as cc_length, COUNT(*) as row_count
FROM legacy_finance
GROUP BY LENGTH(cost_centre)
ORDER BY row_count DESC;

-- 2. Finding Non-Conforming Cost Centres in Expenses
-- Directly querying for values that break the expected 'CC-%' pattern.
SELECT expense_id, cost_centre 
FROM legacy_expenses
WHERE cost_centre NOT LIKE 'CC-%' AND cost_centre IS NOT NULL;

-- 3. Currency Code Consistency in Finance
-- Currency codes should strictly be 3-character ISO codes. This reveals any that aren't.
SELECT currency, COUNT(*) as occurrences
FROM legacy_finance
WHERE LENGTH(currency) != 3
GROUP BY currency;
