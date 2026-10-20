FROM python:3.11-slim

WORKDIR /app

# Install system deps for yfinance / lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m compileall -q /usr/local/lib/python3.11/site-packages/

COPY main.py trade_evidence.py financial_integrity.py portfolio_integrity.py research_integrity.py portfolio_reconciliation.py paper_ledger.py research_store.py ownership_history.py legacy_ownership.py performance_audit.py market_history.py copy_study.py market_provenance.py equity_workspace.py forward_performance.py financial_sources.py benchmark_study.py broker_reconciliation.py ibkr_flex.py source_campaigns.py ./
COPY financial_context.py signal_trials.py number_evidence.py quote_checks.py price_readiness.py cashflow_evidence.py security_identity.py statement_checks.py market_lineage.py market_lineage_v2.py completed_actions.py reviewed_statements.json ./
COPY ibkr_connection.py ./
COPY system_status.py ./
RUN python -m py_compile system_status.py
RUN python -m py_compile ibkr_connection.py
RUN python -m py_compile financial_context.py signal_trials.py number_evidence.py quote_checks.py price_readiness.py cashflow_evidence.py security_identity.py statement_checks.py market_lineage.py market_lineage_v2.py completed_actions.py
RUN python -m py_compile main.py trade_evidence.py financial_integrity.py portfolio_integrity.py research_integrity.py portfolio_reconciliation.py paper_ledger.py research_store.py ownership_history.py legacy_ownership.py performance_audit.py market_history.py copy_study.py market_provenance.py equity_workspace.py forward_performance.py financial_sources.py benchmark_study.py broker_reconciliation.py ibkr_flex.py source_campaigns.py

# Data volume will be mounted at /data on Fly.io
ENV DATA_DIR=/data
ENV OLLAMA_URL=http://localhost:11434

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
