"""Dependency-free cross-entropy residual-policy training and held-out evaluation."""

from __future__ import annotations

import json
import math
import os
import random
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean
from typing import Any

from .contracts import (
    CONTRACT_VERSION, CartesianResidual, DomainRandomization, Observation,
    TrainingConfig, TypingEnvironment, TypingGoal,
)
from .curriculum import STAGES, episode_seed, goal_from_text, sample_goal
from .environment import make_environment
from .verifier import TrainingVerifier

PARAMETER_COUNT = 12
CHECKPOINT_SCHEMA = "dex3_index_residual_cem"


@dataclass(frozen=True)
class ResidualPolicy:
    """Per-axis error feedback plus offset; norm-clipped Cartesian output.

    The nominal waypoint controller and independent safety supervisor remain
    outside the learned policy. Twelve parameters are practical for CPU CEM.
    """

    parameters: tuple[float, ...] = (0.0,) * PARAMETER_COUNT

    def __post_init__(self) -> None:
        if len(self.parameters) != PARAMETER_COUNT or not all(math.isfinite(x) for x in self.parameters):
            raise ValueError("policy requires 12 finite parameters")

    def action(self, observation: Observation, config: TrainingConfig) -> CartesianResidual:
        residuals = []
        for hand in range(2):
            vector = []
            for axis in range(3):
                index = hand * 3 + axis
                error = observation.waypoint_xyz_m[hand][axis] - observation.fingertip_xyz_m[hand][axis]
                feature = max(-1.0, min(1.0, error / 0.03))
                vector.append(config.residual_limit_m * math.tanh(
                    self.parameters[index] * feature + self.parameters[index + 6]))
            norm = math.dist(vector, (0, 0, 0))
            scale = min(1.0, config.residual_limit_m / max(norm, 1e-12))
            residuals.append(tuple(x * scale for x in vector))
        return CartesianResidual(*residuals).validated(config.residual_limit_m)


def _edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


class EmergencyReleaseError(RuntimeError):
    def __init__(self, episode: dict[str, Any]):
        self.episode = episode
        super().__init__("environment failed bounded emergency release: " + json.dumps(episode["cleanup"]))


def _cleanup(environment: TypingEnvironment) -> dict[str, Any]:
    observation = environment.emergency_release()
    positions_valid = len(observation.fingertip_xyz_m) == 2 and all(
        len(point) == 3 and all(math.isfinite(axis) for axis in point)
        for point in observation.fingertip_xyz_m)
    return {"attempted": True,
            "released": not observation.contacts and not observation.pressed_keys and positions_valid,
            "tick": observation.tick, "pressed_keys": list(observation.pressed_keys),
            "contacts": [asdict(contact) for contact in observation.contacts],
            "fingertip_xyz_m": observation.fingertip_xyz_m,
            "evidence_kind": environment.evidence_kind}


def run_episode(environment: TypingEnvironment, policy: ResidualPolicy, *,
                goal: TypingGoal, seed: int, config: TrainingConfig,
                randomization: DomainRandomization, split: str) -> dict[str, Any]:
    try:
        row = _run_episode(environment, policy, goal=goal, seed=seed, config=config,
                           randomization=randomization, split=split)
    except BaseException as original:
        try:
            cleanup = _cleanup(environment)
            if not cleanup["released"]:
                original.add_note("Emergency release did not clear sensor evidence: " + json.dumps(cleanup))
        except Exception as cleanup_error:
            original.add_note(f"Emergency release also failed: {cleanup_error}")
        raise
    row["cleanup"] = {"attempted": False, "released": None}
    if not row["success"]:
        try:
            row["cleanup"] = _cleanup(environment)
        except Exception as cleanup_error:
            row["cleanup"] = {"attempted": True, "released": False, "error": str(cleanup_error)}
        if not row["cleanup"]["released"]:
            # Do not start another episode while the adapter still holds a key.
            raise EmergencyReleaseError(row)
    return row


def _run_episode(environment: TypingEnvironment, policy: ResidualPolicy, *,
                 goal: TypingGoal, seed: int, config: TrainingConfig,
                 randomization: DomainRandomization, split: str) -> dict[str, Any]:
    observation = environment.reset(goal=goal, seed=seed, randomization=randomization)
    verifier = TrainingVerifier(goal, config)
    result = verifier.update(observation)
    total_reward = 0.0
    max_force = 0.0
    previous_action = CartesianResidual()
    step = 0
    for step in range(1, config.max_steps + 1):
        if result.complete or result.failure_code:
            step -= 1
            break
        action = policy.action(observation, config)
        # Check the action in the runner as well as the adapter. An adapter must
        # additionally enforce IK, joint limits and its own physical stop gates.
        action.validated(config.residual_limit_m)
        before = result.accepted_events
        observation = environment.step(action)
        result = verifier.update(observation)
        distance = (sum(math.dist(p, t) for p, t in zip(observation.fingertip_xyz_m,
                                                      observation.waypoint_xyz_m, strict=True))
                    if not result.failure_code else 0.0)
        smoothness = (math.dist(action.left_xyz_m, previous_action.left_xyz_m)
                      + math.dist(action.right_xyz_m, previous_action.right_xyz_m))
        # Potential and event shaping are separate from terminal verification.
        total_reward += 3.0 * (result.accepted_events - before) - 0.02 - 0.5 * distance - 2 * smoothness
        if result.complete:
            total_reward += 30.0
        if result.failure_code:
            total_reward -= 30.0
        previous_action = action
        max_force = max(max_force, *(contact.normal_force_n for contact in observation.contacts
                                    if math.isfinite(contact.normal_force_n)), 0.0)
        if result.complete or result.failure_code:
            break
    code = result.failure_code or (None if result.complete else "TIME_LIMIT")
    safety_codes = {"STANDING_STABILITY_LOST", "FOOT_SLIP", "FORBIDDEN_COLLISION",
                    "HAND_SEPARATION_VIOLATION", "CONTACT_FORCE_LIMIT", "FINGERTIP_VELOCITY_LIMIT"}
    return {
        "split": split, "seed": seed, "stage": goal.stage, "goal": goal.text,
        "observed_text": observation.screen_text, "success": result.complete,
        "failure_code": code, "terminated": bool(result.complete or result.failure_code),
        "truncated": code == "TIME_LIMIT", "steps": step,
        "return": round(total_reward, 9), "accepted_events": result.accepted_events,
        "expected_events": result.expected_events,
        "character_error_rate": _edit_distance(goal.text, observation.screen_text) / len(goal.text),
        "max_contact_force_n": round(max_force, 9), "safety_violation": code in safety_codes,
        "characters_per_minute": len(goal.text) * 60 / max(step * config.timestep_s, 1e-12)
        if result.complete else 0.0,
        "backend": environment.backend_name, "evidence_kind": environment.evidence_kind,
        "verification_scope": ("adapter_sensor_contract" if environment.evidence_kind == "physics_sensor"
                               else "synthetic_contract"),
        # A plugin self-declaring physics evidence is not an audited physical
        # acceptance campaign; this runner deliberately makes no such claim.
        "physical_acceptance": False,
    }


def summarize(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    if not episodes:
        raise ValueError("at least one episode is required")
    return {
        "episodes": len(episodes),
        "success_rate": fmean(float(item["success"]) for item in episodes),
        "mean_return": fmean(item["return"] for item in episodes),
        "mean_character_error_rate": fmean(item["character_error_rate"] for item in episodes),
        "mean_steps": fmean(item["steps"] for item in episodes),
        "mean_successful_characters_per_minute": fmean(
            [item["characters_per_minute"] for item in episodes if item["success"]] or [0.0]),
        "safety_violation_rate": fmean(float(item["safety_violation"]) for item in episodes),
        "failures": dict(sorted(Counter(item["failure_code"] for item in episodes
                                        if item["failure_code"]).items())),
        "evidence_kinds": sorted(set(item["evidence_kind"] for item in episodes)),
        "physical_acceptance_episodes": sum(item["physical_acceptance"] for item in episodes),
    }


def write_json(path: Path, content: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(content, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_checkpoint(path: Path) -> dict[str, Any]:
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    if checkpoint.get("schema") != CHECKPOINT_SCHEMA or checkpoint.get("version") != CONTRACT_VERSION:
        raise ValueError("incompatible training checkpoint")
    TrainingConfig(**checkpoint["config"])
    DomainRandomization(**checkpoint["randomization"])
    ResidualPolicy(tuple(checkpoint["parameters"]))
    std = checkpoint["std"]
    if len(std) != PARAMETER_COUNT or any(not math.isfinite(x) or x <= 0 for x in std):
        raise ValueError("invalid CEM standard deviations")
    if checkpoint["stage"] not in STAGES or type(checkpoint["iteration"]) is not int or checkpoint["iteration"] < 0:
        raise ValueError("invalid checkpoint training progress")
    return checkpoint


def _tuple_tree(value: Any) -> Any:
    return tuple(_tuple_tree(item) for item in value) if isinstance(value, list) else value


def train(config: TrainingConfig, randomization: DomainRandomization, *, output: Path,
          iterations: int, resume: Path | None = None) -> dict[str, Any]:
    """Run additional generations; preserve RNG and split counters on resume."""
    if iterations < 1:
        raise ValueError("iterations must be positive")
    rng = random.Random(config.seed)
    mean = [0.0] * PARAMETER_COUNT
    std = [config.initial_std] * PARAMETER_COUNT
    start = 0
    stage_index = 0
    if resume:
        checkpoint = load_checkpoint(resume)
        if checkpoint["config"] != asdict(config) or checkpoint["randomization"] != asdict(randomization):
            raise ValueError("resume requires the identical configuration and randomization")
        mean, std = checkpoint["parameters"], checkpoint["std"]
        start = checkpoint["iteration"]
        stage_index = STAGES.index(checkpoint["stage"])
        rng.setstate(_tuple_tree(checkpoint["rng_state"]))
    output.mkdir(parents=True, exist_ok=True)
    if not resume and any((output / name).exists() for name in ("checkpoint.json", "episodes.jsonl")):
        raise ValueError("output already contains a training run; use --resume or a new output directory")
    environment = make_environment(config)
    try:
        for iteration in range(start, start + iterations):
            stage = STAGES[stage_index]
            # Common random numbers isolate policy differences. Mean is always
            # retained as a candidate, avoiding accidental loss of the baseline.
            candidates = [list(mean)] + [
                [rng.gauss(value, sigma) for value, sigma in zip(mean, std, strict=True)]
                for _ in range(config.population - 1)]
            ranked = []
            episode_rows = []
            for candidate_index, parameters in enumerate(candidates):
                episodes = []
                for episode_index in range(config.episodes_per_candidate):
                    index = iteration * config.episodes_per_candidate + episode_index
                    seed = episode_seed(config.seed, "train", index)
                    row = run_episode(environment, ResidualPolicy(tuple(parameters)),
                                      goal=sample_goal(stage, seed), seed=seed, config=config,
                                      randomization=randomization, split="train")
                    row.update(iteration=iteration, candidate=candidate_index)
                    episodes.append(row)
                ranked.append((fmean(row["return"] for row in episodes), candidate_index, parameters))
                episode_rows.extend(episodes)
            ranked.sort(key=lambda item: (-item[0], item[1]))
            elites = [item[2] for item in ranked[:config.elite_count]]
            mean = [fmean(parameters[i] for parameters in elites) for i in range(PARAMETER_COUNT)]
            std = [max(config.minimum_std, math.sqrt(fmean(
                (parameters[i] - mean[i]) ** 2 for parameters in elites))) for i in range(PARAMETER_COUNT)]
            validation = []
            for episode_index in range(config.curriculum_evaluation_episodes):
                index = iteration * config.curriculum_evaluation_episodes + episode_index
                seed = episode_seed(config.seed, "validation", index)
                row = run_episode(environment, ResidualPolicy(tuple(mean)), goal=sample_goal(stage, seed),
                                  seed=seed, config=config, randomization=randomization, split="validation")
                row.update(iteration=iteration, candidate="mean")
                validation.append(row)
            validation_summary = summarize(validation)
            if validation_summary["success_rate"] >= config.curriculum_pass_rate:
                stage_index = min(stage_index + 1, len(STAGES) - 1)
            with (output / "episodes.jsonl").open("a", encoding="utf-8") as stream:
                for row in episode_rows + validation:
                    stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            checkpoint = {
                "schema": CHECKPOINT_SCHEMA, "version": CONTRACT_VERSION,
                "algorithm": "cross_entropy_method_cartesian_residual",
                "iteration": iteration + 1, "stage": STAGES[stage_index],
                "parameters": mean, "std": std, "rng_state": rng.getstate(),
                "config": asdict(config), "randomization": asdict(randomization),
                "backend": environment.backend_name, "evidence_kind": environment.evidence_kind,
                "last_validation_stage": stage, "last_validation": validation_summary,
                "scope": "fixed-base standing, two index fingertips, residual waypoint correction",
            }
            write_json(output / "checkpoint.json", checkpoint)
        return checkpoint
    finally:
        environment.close()


def evaluate(checkpoint: dict[str, Any], *, episodes_per_stage: int = 8,
             text: str | None = None) -> dict[str, Any]:
    if episodes_per_stage < 1:
        raise ValueError("evaluation episodes must be positive")
    config = TrainingConfig(**checkpoint["config"])
    randomization = DomainRandomization(**checkpoint["randomization"])
    policy = ResidualPolicy(tuple(checkpoint["parameters"]))
    environment = make_environment(config)
    rows = []
    try:
        stages = ("custom",) if text is not None else STAGES
        for stage_index, stage in enumerate(stages):
            for index in range(episodes_per_stage):
                seed = episode_seed(config.seed, "evaluation", stage_index * episodes_per_stage + index)
                goal = goal_from_text(text) if text is not None else sample_goal(stage, seed)
                rows.append(run_episode(environment, policy, goal=goal, seed=seed, config=config,
                                        randomization=randomization, split="evaluation"))
        return {"schema": "dex3_index_residual_evaluation", "version": CONTRACT_VERSION,
                "checkpoint_iteration": checkpoint["iteration"], "summary": summarize(rows),
                "by_stage": {stage: summarize([row for row in rows if row["stage"] == stage])
                             for stage in stages}, "episodes": rows,
                "note": "Synthetic results do not establish Isaac Sim or real-robot performance."
                if environment.evidence_kind == "synthetic_non_physics"
                else "Adapter sensor-contract results are not audited physical acceptance."}
    finally:
        environment.close()
