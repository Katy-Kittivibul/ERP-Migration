# ERP Migration Intelligence Platform

An end-to-end data engineering portfolio project simulating a complex enterprise ERP migration (e.g., SAP ECC to a modernized target platform). 

Rather than a simple "copy data from A to B" exercise, this platform simulates the messy reality of enterprise data migrations by intentionally generating synthetic legacy data injected with real-world data quality issues (orphaned records, format drift, duplicates, and logic errors). It then builds a robust, automated Python pipeline to detect, track, map, cleanse, and reconcile that data across four core business domains: HR, Finance, Procurement, and Expenses.

## 🏗️ Architecture

```mermaid
graph TD
    A[Synthetic Legacy Data & Issues] --> B[Discovery Profiler]
    B --> C[Issue Tracker / Log]
    C --> D[Mapping Validator & Cleansing]
    D --> E[Migration Simulation & Reconciliation]
    E --> F[Downstream Reporting Impact]
    F --> G[Stakeholder Streamlit Dashboard]
    
    subgraph Core Engine
    B
    C
    D
    E
    F
    end
    
    subgraph Data Layer
    DB[(erp.duckdb)]
    Core Engine <--> DB
    end
    
    G -.-> DB
```

## 🎯 How this maps to the Job Description (JD)

| JD Requirement | How this project demonstrates it |
|---|---|
| **Data Discovery & Profiling** | The `modules/discovery/profiler.py` script automatically scans for Completeness, Accuracy, Consistency, Duplication, and Orphaned referential issues. |
| **SQL Investigation Skills** | The `sql/investigation/` directory contains authentic, hand-written DuckDB SQL queries that an analyst would use live for ad-hoc root-cause analysis. |
| **Data Mapping & Cleansing** | Declarative YAML rules (`modules/mapping/mapping_spec.yaml`) define target schemas, and `mapping_validator.py` resolves canonical master records (e.g., deduplicating vendors). |
| **Migration Simulation & QA** | `modules/migration_sim/migrate.py` enforces a strict migration policy (holding back invalid rows), and `reconcile.py` programmatically proves zero unintended data loss via column checksums and business rule aggregates. |
| **Stakeholder Communication** | Auto-generates plain-English Markdown problem statements and features a non-technical Streamlit Dashboard (`dashboard/app.py`) for project visibility. |
| **Commercial Impact Awareness** | The `reporting_impact` module proves how resolving a seemingly minor format drift directly fixes silent misgroupings in downstream financial KPIs. |

## 🚀 Getting Started

### 1. Prerequisites
- Python 3.10+
- `pip`

### 2. Setup
Clone the repository and install the dependencies in a virtual environment:
```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Mac/Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Generate the Data & Run the Pipeline
Run the synthetic data generator to create the legacy `.csv` files and inject the data quality issues. Then, execute the pipeline modules sequentially to simulate the migration:

```bash
python modules/generate_synthetic_data.py
python modules/discovery/profiler.py
python modules/issue_tracker/issue_log.py
python modules/mapping/mapping_validator.py
python modules/migration_sim/migrate.py
python modules/migration_sim/reconcile.py
python modules/reporting_impact/impact_report.py
```

### 4. Launch the Dashboard
View the final results, issue logs, and reconciliation reports via the interactive Streamlit dashboard:
```bash
# On Windows, you can simply run:
launch_dashboard.bat

# Or run manually via Streamlit:
streamlit run dashboard/app.py
```

## 📚 Further Reading
For a deep dive into the engineering decisions, database choices, and the strict migration policies applied to unresolved issues, please read the [Architecture & Design Document](ARCHITECTURE.md).
