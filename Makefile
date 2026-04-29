VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
DB_PATH ?= data/hypershift.db

.PHONY: venv init-db collect analyze report dashboard pipeline test lint clean

venv:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(PIP) install -e ".[dev]"

init-db:
	$(PYTHON) -m setu_rp.cli --db-path $(DB_PATH) init-db

collect:
	$(PYTHON) -m setu_rp.cli --db-path $(DB_PATH) collect

analyze:
	$(PYTHON) -m setu_rp.cli --db-path $(DB_PATH) analyze

report:
	$(PYTHON) -m setu_rp.cli --db-path $(DB_PATH) report

dashboard:
	$(PYTHON) -m streamlit run src/setu_rp/reporting/dashboard.py -- --db-path $(DB_PATH)

pipeline: collect analyze report

test:
	$(VENV)/bin/pytest tests/ -v

lint:
	$(VENV)/bin/ruff check src/ tests/

clean:
	rm -rf $(VENV) data/ build/ dist/ *.egg-info src/*.egg-info reports/
