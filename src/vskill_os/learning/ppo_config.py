"""Frozen single-key diagnostic configuration; no model/backend dependencies."""

from dataclasses import dataclass
from typing import Literal

from .serialization import JsonRecord


@dataclass(frozen=True, slots=True)
class PPOConfig(JsonRecord):
    version: Literal['vf-single-key-ppo/1'] = 'vf-single-key-ppo/1'
    seed: int = 7
    num_envs: int = 64
    transitions: int = 1_000_000
    rollout_steps: int = 128
    minibatch_size: int = 2048
    epochs: int = 4
    hidden_size: int = 64
    learning_rate: float = 0.0003
    gamma: float = 0.99
    gae_lambda: float = 0.95
    clip_ratio: float = 0.2
    entropy_coefficient: float = 0.001
    value_coefficient: float = 0.5
    max_grad_norm: float = 0.5
    target_kl: float = 0.03
    initial_log_std: float = -0.5
    horizon_s: float = 3.0
    checkpoint_interval: int = 250_000
    evaluation_seeds: tuple[int, ...] = (1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008)

    def __post_init__(self):
        JsonRecord.__post_init__(self)
        if (self.seed < 0 or self.num_envs not in (1, 16, 64) or self.transitions < self.num_envs
                or self.transitions % self.num_envs or min(self.rollout_steps, self.minibatch_size, self.epochs,
                self.hidden_size, self.checkpoint_interval) <= 0 or self.horizon_s != 3.0
                or not 0 < self.gamma <= 1 or not 0 <= self.gae_lambda <= 1
                or not 0 < self.clip_ratio < 1 or min(self.learning_rate, self.max_grad_norm, self.target_kl) <= 0
                or min(self.entropy_coefficient, self.value_coefficient) < 0
                or not self.evaluation_seeds or len(set(self.evaluation_seeds)) != len(self.evaluation_seeds)
                or any(s < 0 or s == self.seed for s in self.evaluation_seeds)):
            raise ValueError('invalid PPO diagnostic budget, optimizer or frozen evaluation seeds')


@dataclass(frozen=True, slots=True)
class PressRewardSpec(JsonRecord):
    version: Literal['vf-single-press-reward/1'] = 'vf-single-press-reward/1'
    completion: float = 10.0
    potential_scale: float = 1.0
    time_per_s: float = 0.02
    action_change: float = 0.001
    contact_per_s: float = 0.01
    mistake: float = 2.0
    safety: float = 5.0

    def __post_init__(self):
        JsonRecord.__post_init__(self)
        if min(self.completion, self.potential_scale, self.time_per_s, self.action_change,
               self.contact_per_s, self.mistake, self.safety) < 0 or self.completion <= 2*self.potential_scale:
            raise ValueError('completion bonus must dominate bounded shaping')


NORMALIZATION = 'fixed_physical_units/1; no fitted statistics or evaluation updates'


def teacher_feature_names(joint_names):
    return tuple(f'{field}:{name}' for field in ('position', 'velocity', 'executed_target', 'previous_action')
                 for name in joint_names) + ('tip_minus_key:x', 'tip_minus_key:y', 'tip_minus_key:z',
                 'target_travel', 'target_force', 'target_contact', 'physical_press_seen',
                 'physical_clear_age', 'time_remaining', 'target_is_a')
