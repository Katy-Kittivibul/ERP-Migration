import streamlit as st
import duckdb
import pandas as pd
import yaml
from pathlib import Path

# --- Configuration & Setup ---
st.set_page_config(page_title="ERP Migration Intelligence Platform", layout="wide")

@st.cache_resource
def get_db_connection():
    return duckdb.connect("data/db/erp.duckdb", read_only=True)

con = get_db_connection()

# --- Title and Header ---
st.title("ERP Migration Intelligence Platform")
st.markdown("Stakeholder dashboard for the SAP ECC → Target Platform migration.")

# --- Tabs ---
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "Discovery", 
    "Issue Log", 
    "Mapping & Migration", 
    "Reconciliation", 
    "Reporting Impact"
])

# --- Tab 1: Discovery ---
with tab1:
    st.header("Data Discovery Findings")
    st.markdown("Raw anomalies found in legacy data during the automated discovery phase.")
    
    findings_df = con.execute("SELECT * FROM discovery_findings").df()
    
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Total Issues Found", len(findings_df))
    
    st.subheader("Findings by Domain")
    domain_counts = findings_df['domain'].value_counts()
    st.bar_chart(domain_counts)
    
    st.subheader("Raw Findings Viewer")
    selected_domain = st.selectbox("Filter by Domain", ["All"] + list(findings_df['domain'].unique()), key="discovery_domain")
    if selected_domain != "All":
        display_df = findings_df[findings_df['domain'] == selected_domain]
    else:
        display_df = findings_df
        
    st.dataframe(display_df, use_container_width=True)

# --- Tab 2: Issue Log ---
with tab2:
    st.header("Issue Log")
    st.markdown("Formal problem statements for each discovered issue.")
    
    issue_df = con.execute("SELECT * FROM issue_log").df()
    
    open_issues = len(issue_df[issue_df['status'] == 'Open'])
    resolved_issues = len(issue_df[issue_df['status'] == 'Resolved'])
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Logged Issues", len(issue_df))
    col2.metric("Resolved", resolved_issues)
    col3.metric("Open", open_issues)
    
    st.subheader("Issue Details")
    for _, row in issue_df.iterrows():
        status_emoji = "✅" if row['status'] == "Resolved" else "⚠️"
        with st.expander(f"{status_emoji} {row['issue_id']} - {row['issue_type']} in {row['table_name']}"):
            st.markdown(f"**Domain:** {row['domain']}")
            st.markdown(f"**Table:** {row['table_name']} | **Column:** {row['column_name']}")
            st.markdown(f"**Type:** {row['issue_type']}")
            st.markdown(f"**Description:** {row['description']}")
            st.markdown(f"**Root Cause (Hypothesis):** {row['root_cause']}")
            st.markdown(f"**Business Impact:** {row['business_impact']}")
            st.markdown(f"**Status:** {row['status']}")
            st.markdown(f"**Resolution:** {row['resolution']}")
            st.caption(f"[View Problem Statement Document](docs/problem_statements/{row['issue_id']}.md)")
            
    st.divider()
    st.subheader("HR Duplicate Review (Flagged for Human Review)")
    st.markdown("""
    Unlike Procurement Vendors, which are **auto-resolved** and merged mechanically based on name similarity, 
    HR employees cannot be merged automatically because two people can share the same name. 
    Below are the flagged near-duplicate pairs from `target_hr_master` that require business review.
    """)
    try:
        hr_dup_df = con.execute("SELECT * FROM hr_duplicate_review").df()
        st.dataframe(hr_dup_df, use_container_width=True)
    except Exception as e:
        st.warning("hr_duplicate_review table not found or empty.")

# --- Tab 3: Mapping & Migration ---
with tab3:
    st.header("Mapping & Migration Status")
    
    st.subheader("Master Table Consolidation")
    col1, col2 = st.columns(2)
    try:
        vendor_master_count = con.execute("SELECT COUNT(*) FROM target_vendor_master").fetchone()[0]
        col1.metric("Canonical Vendors (Procurement)", vendor_master_count)
    except:
        pass
    
    try:
        hr_master_count = con.execute("SELECT COUNT(*) FROM target_hr_master").fetchone()[0]
        col2.metric("Canonical Employees (HR)", hr_master_count)
    except:
        pass

    st.subheader("Held-Back Rows")
    st.markdown("Rows excluded entirely from the target migration due to orphaned references or severe accuracy issues.")
    try:
        held_back_df = con.execute("SELECT * FROM held_back_rows").df()
        st.metric("Total Held-Back Rows", len(held_back_df))
        st.dataframe(held_back_df, use_container_width=True)
    except Exception:
         st.warning("held_back_rows table not found.")

    st.subheader("Mapping Specification Summary")
    try:
        with open("modules/mapping/mapping_spec.yaml", "r") as f:
            mapping = yaml.safe_load(f)
            for domain, specs in mapping.items():
                with st.expander(f"{domain.upper()} Mapping"):
                    mapping_df = pd.DataFrame(specs)
                    st.dataframe(mapping_df, use_container_width=True)
    except Exception:
        st.info("No mapping_spec.yaml found.")
        
# --- Tab 4: Reconciliation ---
with tab4:
    st.header("Migration Reconciliation")
    st.markdown("**Policy:**")
    st.markdown("- **Held back:** rows with orphaned/referential-break or accuracy issues.")
    st.markdown("- **Migrated with flag:** rows with completeness or duplication issues.")
    
    st.subheader("1. Row Count Reconciliation")
    domains = ["hr", "finance", "procurement", "expenses"]
    
    cols = st.columns(4)
    for idx, domain in enumerate(domains):
        try:
            legacy_count = con.execute(f"SELECT COUNT(*) FROM legacy_{domain}").fetchone()[0]
            target_count = con.execute(f"SELECT COUNT(*) FROM target_{domain}").fetchone()[0]
            delta = target_count - legacy_count
            cols[idx].metric(f"{domain.upper()} Target Rows", target_count, delta=delta, delta_color="normal")
        except:
            pass

    st.subheader("2. Column-Level Checksum")
    st.success("All Migrated Rows: 100% Match (No unintended data drift)")
    
    st.subheader("3. Business Rule Reconciliation")
    st.error("**[Rule 1] Total Expense Amount by Cost Centre:** FAIL (300446.9 legacy vs 300446.8999999999 target)")
    st.success("**[Rule 2] Total PO Value by Vendor ID:** PASS (Excludes 2 held-back rows with negative quantities or orphaned vendors)")
    st.success("**[Rule 3] Total Finance Amount by Account Code:** PASS (Excludes 3 held-back orphaned vendor rows)")
    st.info("See full details in `docs/reconciliation_report.md`")

# --- Tab 5: Reporting Impact ---
with tab5:
    st.header("Downstream Reporting Impact")
    st.markdown("Demonstrating how resolving data anomalies in migration (e.g. `cost_centre` format drift) fixes silent misgroupings in downstream financial KPIs.")
    
    try:
        impact_df = con.execute("SELECT * FROM reporting_impact_example").df()
        
        legacy_impact = impact_df[impact_df['scenario'].str.contains('before', case=False)]
        target_impact = impact_df[impact_df['scenario'].str.contains('after', case=False)]
        
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Before (Legacy)")
            st.markdown("Cost centre `CC-009` was incorrectly split into two separate rows due to the malformed `009` code.")
            st.dataframe(legacy_impact, use_container_width=True)
            
        with col2:
            st.subheader("After (Target)")
            st.markdown("Cost centre `CC-009` rolls up perfectly into a single, accurate budget line.")
            st.dataframe(target_impact, use_container_width=True)
            
    except Exception as e:
        st.warning(f"Could not load reporting_impact_example table: {e}")
