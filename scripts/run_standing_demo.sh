#!/usr/bin/env bash
# Standalone Isaac Sim 6.0.1 entry point; no Isaac Lab / conda dependency.
set -euo pipefail

standing_repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
standing_sim_root="${ISAAC_SIM_ROOT:-/home/descfly/isaac-sim-standalone-6.0.1-linux-x86_64}"
standing_python="${STANDING_PYTHON:-python3}"
standing_mode="isaac"
if [[ $# -gt 0 ]]; then
    case "$1" in
        --plan-only) standing_mode="plan"; shift ;;
        --synthetic) standing_mode="synthetic"; shift ;;
        --check-runtime) standing_mode="runtime"; shift ;;
        --build-assets) standing_mode="assets"; shift ;;
        --help|-h)
            cat <<'HELP'
Usage: scripts/run_standing_demo.sh [MODE] [arguments]

Default: launch the fixed standing-base Dex3 demo in Isaac Sim 6.0.1.
  --plan-only       Build a deterministic plan with ordinary Python; no simulator.
  --synthetic       Replay synthetic evidence; never a physical acceptance run.
  --check-runtime   Read runtime/version metadata; do not start Kit or use the GPU.
  --build-assets    Compose pinned Unitree URDF and run the Isaac 6 USD importer.

Isaac arguments: --text afjk --output reports/standing/isaac_report.json
  --headless / --no-headless --device cpu --record-dir reports/standing/frames
  --inspect-only --config configs/simulation/dex3_standing_demo.json --seed 0
Plan/synthetic arguments: --text afjk --output reports/standing/plan.json
Asset arguments: --unitree-ros-root PATH --output-dir PATH

ISAAC_SIM_ROOT overrides the installation path (must be 6.0.1).
STANDING_PYTHON overrides ordinary Python (3.11+), not Isaac's bundled Python.
The launch guard refuses to compete with an existing Isaac/Kit/GPU process.
HELP
            exit 0 ;;
    esac
fi

cd -- "$standing_repo_root"
if [[ "$standing_mode" == "plan" || "$standing_mode" == "synthetic" ]]; then
    standing_args=(-m vskill_os.standing_typing --text afjk)
    if [[ "$standing_mode" == "plan" ]]; then
        standing_args+=(--plan-only --output reports/standing/plan.json)
    else
        standing_args+=(--output reports/standing/synthetic_report.json)
    fi
    exec env PYTHONPATH="$standing_repo_root/src" "$standing_python" "${standing_args[@]}" "$@"
fi

if [[ ! -f "$standing_sim_root/VERSION" || ! -f "$standing_sim_root/python.sh" ]]; then
    echo "Isaac Sim installation is incomplete: $standing_sim_root" >&2
    exit 2
fi
standing_version="$(<"$standing_sim_root/VERSION")"
standing_version="${standing_version%%$'\n'*}"
case "$standing_version" in
    6.0.1|6.0.1-*|6.0.1+*) ;;
    *) echo "Expected Isaac Sim 6.0.1, found '$standing_version'. Refusing to mix runtimes." >&2; exit 2 ;;
esac
if [[ ! -x "$standing_sim_root/kit/python/bin/python3" ]]; then
    echo "Missing bundled Isaac Sim Python: $standing_sim_root/kit/python/bin/python3" >&2
    exit 2
fi

if [[ "$standing_mode" == "runtime" ]]; then
    # Ordinary Python only: even python.sh is deliberately not sourced here.
    exec env PYTHONPATH="$standing_repo_root/src" "$standing_python" -m vskill_os.adapters.isaac.runtime_guard \
        --isaac-root "$standing_sim_root" --check-only "$@"
fi

# Guard executes with ordinary Python, before python.sh can start any Kit code.
# The scene and asset builder repeat the guard so direct invocation is protected.
env PYTHONPATH="$standing_repo_root/src" "$standing_python" -m vskill_os.adapters.isaac.runtime_guard \
    --isaac-root "$standing_sim_root"

# Keep the user's shell/environment intact. Only this child process is cleaned.
standing_clean_env=(env
    -u PYTHONEXE -u PYTHONHOME -u PYTHONPATH -u LD_LIBRARY_PATH -u LD_PRELOAD
    -u CONDA_PREFIX -u CONDA_DEFAULT_ENV -u CONDA_PROMPT_MODIFIER -u VIRTUAL_ENV
    -u ISAAC_PATH -u ISAACLAB_PATH -u CARB_APP_PATH -u EXP_PATH
    "PYTHONPATH=$standing_repo_root/src" "PYTHONNOUSERSITE=1"
    "PATH=/usr/local/bin:/usr/bin:/bin")
if [[ "$standing_mode" == "assets" ]]; then
    exec "${standing_clean_env[@]}" bash "$standing_sim_root/python.sh" "$standing_repo_root/scripts/build_standing_assets.py" "$@"
fi
exec "${standing_clean_env[@]}" bash "$standing_sim_root/python.sh" -m vskill_os.adapters.isaac.standing_scene "$@"
