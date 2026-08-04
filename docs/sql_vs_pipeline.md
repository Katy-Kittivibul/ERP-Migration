# Manual SQL Investigation vs Python Pipeline

When performing a data migration, why do we use both manual SQL queries (as seen in `sql/investigation/`) and a robust Python pipeline (`modules/discovery/`, `modules/migration_sim/`)?

## Manual SQL: The Analyst's Toolkit
Writing and executing standalone SQL queries directly against a database client is the industry standard for **ad-hoc investigation**. 

Data analysts reach for manual SQL when they need to:
- **Discover Unknowns:** You don't know what you're looking for yet. Running a quick `GROUP BY` on string length is the fastest way to discover format drift.
- **Root Cause Analysis:** When a dashboard breaks, analysts write custom `LEFT JOIN` and `WHERE` clauses live to isolate the specific records causing the issue.
- **Prototype Logic:** Before a rule is hardcoded into a pipeline, an analyst will test the logic manually via SQL to ensure it catches the right edge cases without false positives.

## Python Pipeline: Repeatable Engineering
While SQL is perfect for exploration, it is terrible for **repeatability at scale**. If you migrate 10 million rows across 50 tables every week during a 6-month transformation programme, you cannot rely on an analyst manually running and interpreting 200 different SQL files.

We build the Python pipeline for:
- **Automation:** Running identical checks over the entire payload instantly.
- **State Management & Traceability:** Our pipeline programmatically tags bad rows, writes them to `held_back_rows`, and links them to an `issue_log` ID. 
- **Business Reporting:** Python generates the user-friendly Markdown reports and Streamlit dashboards that non-technical stakeholders actually read.

**In summary:** Manual SQL is how we *discover* and *diagnose* the problem. The Python pipeline is how we *enforce* the solution systematically.
