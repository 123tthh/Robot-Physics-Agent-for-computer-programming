.PHONY: demo demo-mixed benchmark inspect test check

demo:
	PYTHONPATH=src python3 -m vskill_os.demo

demo-mixed:
	PYTHONPATH=src python3 -m vskill_os.cli run --scenario configs/scenarios/mixed_faults.json

benchmark:
	PYTHONPATH=src python3 -m vskill_os.cli benchmark --episodes 20 --profile recovery

inspect:
	PYTHONPATH=src python3 -m vskill_os.cli inspect-robot
	PYTHONPATH=src python3 -m vskill_os.cli list-projects --subsystem sitting
	PYTHONPATH=src python3 -m vskill_os.cli voice-plan "创建一个 Python 程序并独立测试"

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v

check: test
	python3 -m compileall -q src tests
