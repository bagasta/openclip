.PHONY: dev setup open stop test
dev:
	python3 scripts/run.py
setup:
	python3 scripts/run.py --setup
open:
	python3 -m webbrowser http://127.0.0.1:7860
stop:
	python3 scripts/run.py --stop
test:
	.venv/bin/python -m unittest discover -s tests -v
