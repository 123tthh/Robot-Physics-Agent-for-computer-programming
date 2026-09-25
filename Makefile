.PHONY: demo demo-mixed demo-seated-recovery demo-p1 demo-p2 benchmark inspect inspect-action-graph inspect-dex3-assets isaac-p1-assets isaac-p1 isaac-p1-validate isaac-p2 test check

ISAACLAB_ROOT ?= /home/descfly/IsaacLab-2.3.2
ISAACLAB_CONDA ?= /home/descfly/miniconda3/bin/conda
ISAACLAB_ENV ?= isaaclab-2.3.2
ISAACLAB_ENV_PREFIX ?= /home/descfly/miniconda3/envs/$(ISAACLAB_ENV)
ISAACLAB_ACTIVATE ?= $(ISAACLAB_ENV_PREFIX)/etc/conda/activate.d/setenv.sh
ISAACLAB_PYTHON ?= $(ISAACLAB_ENV_PREFIX)/bin/python
DEX5_ARGS ?=
DEX5_RUN_DIR ?= $(CURDIR)/artifacts/dex5/run
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

.PHONY: dex5-assets demo-dex5 isaac-dex5 isaac-dex5-gui isaac-dex5-inspect

dex5-assets:
	PYTHONPATH=src python3 -m vskill_os.simulation.dex5_assets --fetch --compose --seating-fixture --report artifacts/isaac-dex5/asset_audit.json

demo-dex5:
	PYTHONPATH=src python3 -m vskill_os.simulation.dex5_typing --output artifacts/dex5/replay.json

isaac-dex5:
	/bin/bash -c 'source "$(ISAACLAB_ACTIVATE)"; export PYTHONPATH="$(CURDIR)/src:$$PYTHONPATH"; exec "$(ISAACLAB_PYTHON)" -m vskill_os.adapters.isaac.dex5_demo --headless --output-dir "$(DEX5_RUN_DIR)" $(DEX5_ARGS)'

isaac-dex5-gui:
	$(MAKE) isaac-dex5 DEX5_ARGS="--no-headless $(DEX5_ARGS)"

isaac-dex5-inspect:
	$(MAKE) isaac-dex5 DEX5_ARGS="--inspect-only $(DEX5_ARGS)"

# Free-base experiments are separate from the accepted assisted typing demo.
.PHONY: free-sit-assets isaac-free-sit verify-free-sit
FREE_SIT_ARGS ?=
FREE_SIT_RUN_DIR ?= $(CURDIR)/artifacts/free-sit/run

free-sit-assets:
	PYTHONPATH=src python3 -m vskill_os.simulation.dex5_assets --fetch --report artifacts/free-sit/unitree_asset_audit.json
	python3 tools/fetch_free_sitting_policy.py --fetch

isaac-free-sit:
	/bin/bash -c 'source "$(ISAACLAB_ACTIVATE)"; export PYTHONPATH="$(CURDIR)/src:$$PYTHONPATH"; exec "$(ISAACLAB_PYTHON)" -m vskill_os.adapters.isaac.free_sitting_scene --headless --output-dir "$(FREE_SIT_RUN_DIR)" $(FREE_SIT_ARGS)'

verify-free-sit:
	PYTHONPATH=src python3 tools/verify_free_sitting_recording.py "$(FREE_SIT_RUN_DIR)/report.json" --output "$(FREE_SIT_RUN_DIR)/independent_verification.json"

.PHONY: isaac-computer verify-computer
COMPUTER_RUN_DIR ?= $(CURDIR)/artifacts/computer/run
COMPUTER_ARGS ?=

isaac-computer:
	$(MAKE) isaac-free-sit FREE_SIT_RUN_DIR="$(COMPUTER_RUN_DIR)" FREE_SIT_ARGS='--seat-height .44 --height .50 --hold-load-fraction .35 --typing-height-drop .02 --duration 60 --type-text "asdf jkl; " --room --desk-offset-x .12 --video $(COMPUTER_ARGS)'

verify-computer:
	PYTHONPATH=src python3 tools/verify_continuous_typing.py "$(COMPUTER_RUN_DIR)/report.json" --output "$(COMPUTER_RUN_DIR)/independent_verification.json"

.PHONY: virtual-computer-deps isaac-virtual-computer verify-virtual-computer
VIRTUAL_COMPUTER_RUN_DIR ?= $(CURDIR)/artifacts/computer/virtual-run

virtual-computer-deps:
	mkdir -p artifacts/virtual-computer
	"$(ISAACLAB_PYTHON)" -m pip install --target artifacts/virtual-computer/deps pygame==2.6.1 pyte==0.8.2 wcwidth==0.2.13 --report artifacts/virtual-computer/dependencies.json

isaac-virtual-computer:
	$(MAKE) isaac-free-sit FREE_SIT_RUN_DIR="$(VIRTUAL_COMPUTER_RUN_DIR)" FREE_SIT_ARGS='--seat-height .44 --height .50 --hold-load-fraction .35 --typing-height-drop .02 --duration 40 --room --desk-offset-x .12 --virtual-computer --terminal-command ls --video'

verify-virtual-computer:
	PYTHONPATH=src "$(ISAACLAB_PYTHON)" tools/verify_virtual_computer.py "$(VIRTUAL_COMPUTER_RUN_DIR)/report.json" --output "$(VIRTUAL_COMPUTER_RUN_DIR)/independent_verification.json"

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v

check: test
	python3 -m compileall -q src tests

# P4 research targets are opt-in; never included in check or the default demo.
.PHONY: chair-plan demo-chair chair-train
CHAIR_OUTPUT ?= artifacts/chair/demo
CHAIR_FAULT ?= none
CHAIR_BACKEND_CONFIG ?= configs/chair/worker.example.json

chair-plan:
	PYTHONPATH=src python3 -m vskill_os.chair.cli plan --output artifacts/chair/plan

demo-chair:
	PYTHONPATH=src python3 -m vskill_os.chair.demo --output "$(CHAIR_OUTPUT)" --fault "$(CHAIR_FAULT)"

chair-train:
	PYTHONPATH=src python3 -m vskill_os.chair.cli train --config configs/chair/ppo_baseline.json --backend-config "$(CHAIR_BACKEND_CONFIG)" --output "$(CHAIR_OUTPUT)"

# VF-01-T01 exports synthetic evidence only; it does not start Isaac or PPO.
.PHONY: demo-learning-evidence
LEARNING_EVIDENCE_DIR ?= artifacts/verification_feedback/vf01/contracts

demo-learning-evidence:
	PYTHONPATH=src python3 -m vskill_os.learning demo --output "$(LEARNING_EVIDENCE_DIR)"

# Read-only navigation; leave the historical default target (demo) unchanged.
.PHONY: help
help:
	@printf '%s\n' \
	  'V-Skill OS - project entry points' \
	  '  Docs:       docs/overview.md | docs/ARCHITECTURE.md | docs/README.md' \
	  '  Planning:   docs/WORKSTREAMS.md | docs/ROADMAP.md | docs/BRANCHES.md' \
	  '  Core:       make check | make demo | make demo-mixed' \
	  '  CPU models: make demo-p1 | make demo-p2 | make demo-dex5' \
	  '  Isaac 5.1:  make isaac-p1 | make isaac-p2 | make isaac-dex5' \
	  '  Free sit:   make isaac-free-sit | make verify-free-sit' \
	  '  Continuous: make isaac-computer | make verify-computer' \
	  '  Screen:     make isaac-virtual-computer | make verify-virtual-computer' \
	  '  Chair:      make chair-plan | make demo-chair (proxy only)' \
	  '  Dex3/6.0.1: separate worktree; see docs/BRANCHES.md' \
	  '  Learning:   make demo-learning-evidence (contracts only; no physics/PPO)' \
	  'See experiment docs for assets, optional dependencies, and evidence scope.'
