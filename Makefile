.PHONY: demo demo-mixed benchmark test check

demo:
	PYTHONPATH=src python3 -m vskill_os.demo

demo-mixed:
	PYTHONPATH=src python3 -m vskill_os.cli run --scenario configs/scenarios/mixed_faults.json

benchmark:
	PYTHONPATH=src python3 -m vskill_os.cli benchmark --episodes 20 --profile recovery

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v

check: test
	python3 -m compileall -q src tests
