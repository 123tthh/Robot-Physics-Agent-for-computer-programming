"""Frozen VF-02-T03 protocol and descriptive per-training-seed statistics."""

from dataclasses import dataclass, replace
import math
from typing import Literal

from .ppo_config import PPOConfig
from .serialization import JsonRecord


@dataclass(frozen=True, slots=True)
class MultiseedProtocol(JsonRecord):
    version: Literal['vf-single-key-multiseed/1'] = 'vf-single-key-multiseed/1'
    training_seeds: tuple[int, ...] = (7, 17, 29)
    evaluation_seeds: tuple[int, ...] = tuple(range(2001, 2101))
    transitions_per_seed: int = 1_000_000
    minimum_success_rate: float = .95

    def __post_init__(self):
        JsonRecord.__post_init__(self)
        if (len(self.training_seeds) != 3 or len(set(self.training_seeds)) != 3
                or len(self.evaluation_seeds) != 100 or len(set(self.evaluation_seeds)) != 100
                or min((*self.training_seeds, *self.evaluation_seeds)) < 0
                or set(self.training_seeds) & set(self.evaluation_seeds)
                or self.transitions_per_seed != 1_000_000 or self.minimum_success_rate != .95):
            raise ValueError('T03 requires three distinct training seeds, 100 disjoint evaluation seeds, 1M each and 95% per seed')

    def training_config(self, baseline: PPOConfig, seed: int) -> PPOConfig:
        if baseline != PPOConfig() or seed not in self.training_seeds:
            raise ValueError('use the frozen T02 hyperparameters and a declared T03 training seed')
        return replace(baseline, seed=seed, transitions=self.transitions_per_seed,
                       evaluation_seeds=self.evaluation_seeds)


def wilson_reference_interval(successes: int, episodes: int) -> tuple[float, float]:
    """95% binomial IID reference only, not a fixed-scene generalization claim."""
    if (type(successes) is not int or type(episodes) is not int
            or episodes <= 0 or not 0 <= successes <= episodes):
        raise ValueError('invalid binomial counts')
    z = 1.959963984540054
    p, z2 = successes/episodes, z*z
    denominator = 1+z2/episodes
    center = (p+z2/(2*episodes))/denominator
    half = z*math.sqrt(p*(1-p)/episodes+z2/(4*episodes*episodes))/denominator
    return max(0., center-half), min(1., center+half)


def summarize_seed(protocol: MultiseedProtocol, training_seed: int, evaluation: dict) -> dict:
    """Summarize independently verified cases; invalid evidence fails the gate."""
    if training_seed not in protocol.training_seeds:
        raise ValueError('undeclared training seed')
    cases = evaluation.get('cases', [])
    valid = (evaluation.get('evidence_valid') is True and len(cases) == 100
        and [c.get('seed') for c in cases] == list(protocol.evaluation_seeds)
        and all(c.get('evidence_valid') is True for c in cases)
        and evaluation.get('training_transitions') == protocol.transitions_per_seed)
    successes = sum(c.get('physical_primitive_passed') is True for c in cases)
    valid = valid and evaluation.get('success_count') == successes and evaluation.get('episode_count') == len(cases)
    failures = {}
    for case in cases:
        if case.get('physical_primitive_passed') is not True:
            for code in set(case.get('task_failures', [])) or {'INVALID_EVIDENCE' if not case.get('evidence_valid') else 'UNCLASSIFIED_FAILURE'}:
                failures[code] = failures.get(code, 0)+1
    times = [c['elapsed_sim_s'] for c in cases if c.get('physical_primitive_passed') is True]
    return dict(training_seed=training_seed, evidence_valid=bool(valid), episode_count=len(cases),
        success_count=successes, success_rate=successes/len(cases) if cases else None,
        passed=bool(valid and successes/100 >= protocol.minimum_success_rate),
        failure_codes=failures, successful_mean_sim_s=sum(times)/len(times) if times else None,
        binomial_iid_95_reference_interval=wilson_reference_interval(successes, len(cases)) if cases else None,
        interval_scope='IID binomial reference only; fixed-scene repeats do not establish generalization or independent random resets')
