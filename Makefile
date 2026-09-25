.PHONY: demo demo-mixed demo-seated-recovery demo-p1 demo-p2 benchmark inspect inspect-action-graph inspect-dex3-assets isaac-p1-assets isaac-p1 isaac-p1-validate isaac-p2 standing-plan standing-replay standing-runtime standing-training-smoke isaac-standing isaac-standing-gui isaac-standing-assets test check

ISAACLAB_ROOT ?= /home/descfly/IsaacLab-2.3.2
ISAACLAB_CONDA ?= /home/descfly/miniconda3/bin/conda
ISAACLAB_ENV ?= isaaclab-2.3.2
ISAACLAB_ENV_PREFIX ?= /home/descfly/miniconda3/envs/$(ISAACLAB_ENV)
ISAACLAB_ACTIVATE ?= $(ISAACLAB_ENV_PREFIX)/etc/conda/activate.d/setenv.sh
ISAACLAB_PYTHON ?= $(ISAACLAB_ENV_PREFIX)/bin/python
P1_URDF := $(CURDIR)/artifacts/simulation-assets/unitree_ros_7d6075f7/robots/g1_description/g1_29dof_mode_15_with_dex3_1.urdf
P1_USD := $(CURDIR)/artifacts/isaac-p1/assets/g1_mode15_dex3_p1/g1_mode15_dex3_p1.usd
STANDING_TEXT ?= ak
ISAAC_SIM_ROOT ?= /home/descfly/isaac-sim-standalone-6.0.1-linux-x86_64

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

standing-plan:
	./scripts/run_standing_demo.sh --plan-only --text "$(STANDING_TEXT)"

standing-replay:
	./scripts/run_standing_demo.sh --synthetic --text "$(STANDING_TEXT)"

standing-runtime:
	ISAAC_SIM_ROOT="$(ISAAC_SIM_ROOT)" ./scripts/run_standing_demo.sh --check-runtime

standing-training-smoke:
	PYTHONPATH=src python3 -m vskill_os.training train --config configs/training/dex3_standing.json --output artifacts/standing-training --iterations 3
	PYTHONPATH=src python3 -m vskill_os.training evaluate --checkpoint artifacts/standing-training/checkpoint.json --episodes 8 --output artifacts/standing-training/evaluation.json

.PHONY: standing-navigation desktop-feedback workstation-feedback monitor-static
standing-navigation:
	PYTHONPATH=src python3 -m vskill_os.control.standing_locomotion --goal-x .4 --goal-y .1 --goal-yaw .2 --seed 7 --output reports/standing/navigation.json

desktop-feedback:
	PYTHONPATH=src:artifacts/desktop-deps SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3 -m vskill_os.adapters.pygame_desktop --backend pygame --output reports/feedback

workstation-feedback:
	PYTHONPATH=src:artifacts/desktop-deps SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3 -m vskill_os.workstation_mission --backend pygame --output reports/workstation_mission

monitor-static:
	PYTHONPATH=src python3 scripts/monitor_bridge_example.py --transport reports/workstation_mission/after --offline

.PHONY: unitree-policy-import unitree-policy-inspect
UNITREE_POLICY_PYTHON ?= python3
unitree-policy-import:
	python3 scripts/import_unitree_velocity.py

unitree-policy-inspect:
	PYTHONPATH=src $(UNITREE_POLICY_PYTHON) -m vskill_os.adapters.unitree_velocity

isaac-standing:
	ISAAC_SIM_ROOT="$(ISAAC_SIM_ROOT)" ./scripts/run_standing_demo.sh --headless --text "$(STANDING_TEXT)"

isaac-standing-gui:
	ISAAC_SIM_ROOT="$(ISAAC_SIM_ROOT)" ./scripts/run_standing_demo.sh --no-headless --text "$(STANDING_TEXT)"

isaac-standing-assets:
	ISAAC_SIM_ROOT="$(ISAAC_SIM_ROOT)" ./scripts/run_standing_demo.sh --build-assets

test:
	PYTHONPATH=src:artifacts/desktop-deps SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3 -m unittest discover -s tests -v

check: test
	python3 -m compileall -q src tests
