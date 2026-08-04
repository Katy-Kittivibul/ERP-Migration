@echo off
echo Starting ERP Migration Dashboard...
call venv\Scripts\activate
streamlit run dashboard\app.py
pause
