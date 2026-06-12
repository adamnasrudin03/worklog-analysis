.PHONY: report test open watch lint demo

report:
	python3 analyze_worklog.py

demo:
	python3 analyze_worklog.py data/templates/worklog-export.example.csv

open:
	python3 analyze_worklog.py --open

watch:
	python3 analyze_worklog.py --watch

test:
	python3 -m unittest test_worklog.py -v

lint:
	python3 -m ruff check .
	python3 -m ruff format --check .
