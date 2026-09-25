"""Explicit plan/train entry points; importing this module does not start a job."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib
import json
from pathlib import Path
import subprocess

from .training import ChairTrainingEnv, TrainingConfig, experiment_matrix


def factory(spec: str):
    module, separator, name = spec.partition(":")
    if not separator or not module or not name:
        raise ValueError("factory must be module:function")
    value = getattr(importlib.import_module(module), name)
    if not callable(value):
        raise ValueError("factory must be callable")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "train"))
    parser.add_argument("--config", type=Path, default=Path("configs/chair/research.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backend", default="vskill_os.adapters.chair_worker:create_backend")
    parser.add_argument("--backend-config", type=Path)
    parser.add_argument("--trainer", default="vskill_os.adapters.chair_torch:create_trainer")
    args = parser.parse_args()
    config = TrainingConfig(**json.loads(args.config.read_text()))
    if args.output.exists():
        parser.error("output already exists; select a fresh experiment directory")
    if args.mode == "plan":
        args.output.mkdir(parents=True)
        plan = {"schema": "chair-plan-v1", "status": "not_run",
                "runs": experiment_matrix(config), "physical_success_verified": False}
        (args.output / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
        print(f"Wrote {len(plan['runs'])} planned runs; no training was started.")
        return
    if args.backend_config is None:
        parser.error("train requires --backend-config with an implemented physics worker")
    options = json.loads(args.backend_config.read_text())
    # Resolve/validate learner before launching a heavyweight physics process.
    trainer = factory(args.trainer)(config)
    backend = factory(args.backend)(config, options)
    env = ChairTrainingEnv(backend, config)
    try:
        if backend.evidence_level != "simulation_oracle":
            raise ValueError("training CLI accepts only explicit simulation backends")
        result = trainer.train(env, str(args.output))
        print(json.dumps(result, indent=2))
    finally:
        try:
            if args.output.is_dir():
                identity = getattr(backend, "identity", {})
                (args.output / "worker-identity.json").write_text(json.dumps(identity, indent=2) + "\n")
                package_root = Path(__file__).resolve().parents[1]
                digest = hashlib.sha256()
                for path in sorted(package_root.rglob("*.py")):
                    digest.update(str(path.relative_to(package_root)).encode())
                    digest.update(path.read_bytes())
                revision = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
                provenance = {"schema": "chair-provenance-v1", "config": asdict(config),
                              "source_sha256": digest.hexdigest(), "base_commit": revision.stdout.strip(),
                              "backend_factory": args.backend, "trainer_factory": args.trainer,
                              "deployment_approved": False}
                (args.output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
        finally:
            env.close()


if __name__ == "__main__":
    main()
