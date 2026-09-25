"""On-policy collection, GAE, checkpoints and provenance; optimizer is an adapter."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Protocol

from .model import Observation, finite, vector
from .training import ChairTrainingEnv


@dataclass(frozen=True, slots=True)
class Decision:
    action: tuple[float, ...]
    log_probability: float
    value: float

    def __post_init__(self) -> None:
        vector(self.action, 29)
        finite((self.log_probability, self.value))
        if max(abs(v) for v in self.action) > 1:
            raise ValueError("learner must return a bounded action with its correct log probability")


@dataclass(frozen=True, slots=True)
class Transition:
    observation: Observation
    decision: Decision
    reward: float
    next_value: float
    terminated: bool
    truncated: bool


class Learner(Protocol):
    supports_symmetry: bool

    def act(self, actor: tuple[float, ...], critic: tuple[float, ...]) -> Decision:
        """Actor network must not consume critic-only fields."""
        ...

    def value(self, critic: tuple[float, ...]) -> float: ...

    def update(self, rollout: tuple[Transition, ...], advantages: tuple[float, ...],
               returns: tuple[float, ...]) -> dict[str, float]: ...

    def save(self, path: Path) -> None:
        """Persist weights, optimizer, normalizers, RNG states and joint mapping."""
        ...


class CurriculumEvaluator(Protocol):
    def evaluate(self, learner: Learner, level: int) -> tuple[int, float, float]:
        """Separate seeded evaluation environments; episodes/success/stationary rates.

        The learner is read-only; never reset/step the training environment here.
        Success uses held-out raw trajectory verification, not episode return.
        """
        ...


def generalized_advantages(rollout: tuple[Transition, ...], gamma: float = 0.99,
                           lam: float = 0.95) -> tuple[tuple[float, ...], tuple[float, ...]]:
    finite((gamma, lam))
    if not 0 <= gamma <= 1 or not 0 <= lam <= 1:
        raise ValueError("gamma and lambda must be in [0, 1]")
    advantages: list[float] = []
    carry = 0.0
    for t in reversed(rollout):
        bootstrap = 0.0 if t.terminated else t.next_value
        delta = t.reward + gamma * bootstrap - t.decision.value
        carry = delta + gamma * lam * carry * (not (t.terminated or t.truncated))
        advantages.append(carry)
    advantages.reverse()
    return tuple(advantages), tuple(a + t.decision.value for a, t in zip(advantages, rollout))


class OnPolicyTrainer:
    def __init__(self, learner: Learner, evaluator: CurriculumEvaluator | None = None) -> None:
        self.learner, self.evaluator = learner, evaluator

    def train(self, env: ChairTrainingEnv, output_dir: str) -> dict[str, object]:
        cfg = env.config
        if cfg.symmetry and not self.learner.supports_symmetry:
            raise ValueError("SY requested but learner lacks a validated reflection transform")
        if cfg.curriculum and self.evaluator is None:
            raise ValueError("CC requires an independent held-out curriculum evaluator")
        out = Path(output_dir)
        # Never accidentally overwrite an earlier experiment/checkpoint.
        out.mkdir(parents=True, exist_ok=False)
        (out / "config.json").write_text(json.dumps(asdict(cfg), indent=2) + "\n")
        summary: dict[str, object] = {
            "schema": "chair-run-v1", "status": "running", "iterations_completed": 0,
            "evidence_level": env.backend.evidence_level, "physical_success_verified": False,
            "deployment_approved": False,
        }
        try:
            observation = env.reset(seed=cfg.seed)
            with (out / "metrics.jsonl").open("w") as log:
                log.write(json.dumps({"reset": asdict(env.last_reset)}) + "\n")
                for iteration in range(cfg.iterations):
                    transitions = []
                    reward_terms: dict[str, float] = {}
                    for _ in range(cfg.rollout_steps):
                        decision = self.learner.act(observation.actor, observation.critic)
                        step = env.step(decision.action)
                        next_value = 0.0 if step.terminated else self.learner.value(step.observation.critic)
                        finite((step.reward, next_value))
                        transitions.append(Transition(observation, decision, step.reward,
                                                      next_value, step.terminated, step.truncated))
                        for name, amount in step.info["reward_terms"].items():
                            reward_terms[name] = reward_terms.get(name, 0.0) + amount
                        observation = env.reset() if step.terminated or step.truncated else step.observation
                        if step.terminated or step.truncated:
                            log.write(json.dumps({"iteration": iteration + 1,
                                                  "episode_end": {"terminated": step.terminated,
                                                                  "truncated": step.truncated,
                                                                  "sample": step.info["sample"]},
                                                  "reset": asdict(env.last_reset)}, allow_nan=False) + "\n")
                    rollout = tuple(transitions)
                    advantages, returns = generalized_advantages(rollout)
                    losses = self.learner.update(rollout, advantages, returns)
                    finite(tuple(losses.values()))
                    if self.evaluator is not None and (iteration + 1) % 100 == 0:
                        episodes, success, stationary = self.evaluator.evaluate(self.learner, env.level)
                        env.record_curriculum_evaluation(episodes=episodes, success_rate=success,
                                                        stationary_rate=stationary)
                        log.write(json.dumps({"iteration": iteration + 1, "evaluation": {
                            "episodes": episodes, "success_rate": success,
                            "stationary_rate": stationary}}, allow_nan=False) + "\n")
                    log.write(json.dumps({"iteration": iteration + 1, "losses": losses,
                                          "reward_sum": sum(t.reward for t in rollout),
                                          "unscaled_reward_term_sums": reward_terms,
                                          "curriculum_level": env.level}, allow_nan=False) + "\n")
                    log.flush()
                    summary["iterations_completed"] = iteration + 1
                    if (iteration + 1) % 100 == 0:
                        self.learner.save(out / f"checkpoint-{iteration + 1}.bin")
            checkpoint = out / "checkpoint-final.bin"
            self.learner.save(checkpoint)
            summary.update(status="training_completed", checkpoint_sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest())
        except BaseException as exc:
            summary.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            (out / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
        return summary
