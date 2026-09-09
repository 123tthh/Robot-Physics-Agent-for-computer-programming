.PHONY: demo demo-mixed demo-seated-recovery demo-p1 demo-p2 benchmark inspect inspect-action-graph inspect-dex3-assets isaac-p1-assets isaac-p1 isaac-p1-validate isaac-p2 test check

ISAACLAB_ROOT ?= /home/descfly/IsaacLab-2.3.2
ISAACLAB_CONDA ?= /home/descfly/miniconda3/bin/conda
ISAACLAB_ENV ?= isaaclab-2.3.2
ISAACLAB_ENV_PREFIX ?= /home/descfly/miniconda3/envs/$(ISAACLAB_ENV)
ISAACLAB_ACTIVATE ?= $(ISAACLAB_ENV_PREFIX)/etc/conda/activate.d/setenv.sh
ISAACLAB_PYTHON ?= $(ISAACLAB_ENV_PREFIX)/bin/python
P1_URDF := $(CURDIR)/artifacts/simulation-assets/unitree_ros_7d6075f7/robots/g1_description/g1_29dof_mode_15_with_dex3_1.urdf
P1_USD := $(CURDIR)/artifacts/isaac-p1/assets/g1_mode15_dex3_p1/g1_mode15_dex3_p1.usd

demo:
	PYTHONPATH=src python3 -m vskill_os.demo

demo-mixed:
	PYTHONPATH=src python3 -m vskill_os.cli run --scenario configs/scenarios/mixed_faults.json

demo-seated-recovery:
	PYTHONPATH=src python3 -m vskill_os.cli run --scenario configs/scenarios/seated_locomotion_recovery.json

demo-p1:
	PYTHONPATH=src python3 -m vskill_os.cli hybrid-smoke

demo-p2:
	PYTHONPATH=src python3 -m vskill_os.cli p2-smoke

inspect-action-graph:
	PYTHONPATH=src python3 -m vskill_os.cli inspect-action-graph

benchmark:
	PYTHONPATH=src python3 -m vskill_os.cli benchmark --episodes 20 --profile recovery

inspect:
	PYTHONPATH=src python3 -m vskill_os.cli inspect-robot
	PYTHONPATH=src python3 -m vskill_os.cli inspect-sim-assets
	PYTHONPATH=src python3 -m vskill_os.cli inspect-robot --policy-manifest configs/robots/g1_29dof_mode15_seated_policy.example.json
	PYTHONPATH=src python3 -m vskill_os.cli list-projects --subsystem sitting
	PYTHONPATH=src python3 -m vskill_os.cli list-projects --subsystem seated_locomotion
	PYTHONPATH=src python3 -m vskill_os.cli voice-plan "创建一个 Python 程序并独立测试"

inspect-dex3-assets:
	PYTHONPATH=src python3 -m vskill_os.cli inspect-dex3-assets

isaac-p1-assets: inspect-dex3-assets
	/bin/bash -c 'source "$(ISAACLAB_ACTIVATE)"; export PYTHONPATH="$(CURDIR)/src:$$PYTHONPATH"; exec "$(ISAACLAB_PYTHON)" "$(ISAACLAB_ROOT)/scripts/tools/convert_urdf.py" "$(P1_URDF)" "$(P1_USD)" --fix-base --joint-stiffness 100 --joint-damping 10 --joint-target-type position --headless'

isaac-p1:
	/bin/bash -c 'source "$(ISAACLAB_ACTIVATE)"; export PYTHONPATH="$(CURDIR)/src:$$PYTHONPATH"; exec "$(ISAACLAB_PYTHON)" -m vskill_os.adapters.isaac.p1_scene --headless --device cuda:0'

isaac-p1-validate:
	/bin/bash -c 'source "$(ISAACLAB_ACTIVATE)"; export PYTHONPATH="$(CURDIR)/src:$$PYTHONPATH"; exec "$(ISAACLAB_PYTHON)" -m vskill_os.adapters.isaac.validate_workstation_scene'

isaac-p2:
	/bin/bash -c 'source "$(ISAACLAB_ACTIVATE)"; export PYTHONPATH="$(CURDIR)/src:$$PYTHONPATH"; exec "$(ISAACLAB_PYTHON)" -m vskill_os.adapters.isaac.p2_scene --headless --device cuda:0 --mode chord'

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v

check: test
	python3 -m compileall -q src tests
