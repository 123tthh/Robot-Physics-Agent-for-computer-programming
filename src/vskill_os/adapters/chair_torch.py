"""Optional single-environment PPO baseline. Import only in a training environment.

No symmetry/curriculum reproduction is claimed. Supply a separate Trainer adapter
for those experiments. Actor uses a tanh-transformed Gaussian with corrected log p.
"""

from __future__ import annotations

from pathlib import Path

from ..chair.runner import Decision, OnPolicyTrainer, Transition
from ..chair.training import TrainingConfig


class TorchPPOLearner:
    supports_symmetry = False

    def __init__(self, seed: int) -> None:
        import torch
        self.torch = torch
        torch.manual_seed(seed)

        def mlp(inputs: int, outputs: int):
            return torch.nn.Sequential(torch.nn.Linear(inputs, 512), torch.nn.ELU(),
                                       torch.nn.Linear(512, 256), torch.nn.ELU(),
                                       torch.nn.Linear(256, 128), torch.nn.ELU(),
                                       torch.nn.Linear(128, outputs))

        self.actor, self.critic = mlp(96, 29), mlp(217, 1)
        self.log_std = torch.nn.Parameter(torch.full((29,), -1.0))
        self.parameters = list(self.actor.parameters()) + list(self.critic.parameters()) + [self.log_std]
        self.optimizer = torch.optim.Adam(self.parameters, lr=1e-3)

    def _distribution(self, actor):
        torch = self.torch
        return torch.distributions.Normal(self.actor(actor), self.log_std.clamp(-5, 1).exp())

    def _log_prob(self, distribution, action):
        torch = self.torch
        bounded = action.clamp(-1 + 1e-6, 1 - 1e-6)
        latent = torch.atanh(bounded)
        return (distribution.log_prob(latent) - torch.log1p(-bounded.square())).sum(-1)

    def act(self, actor: tuple[float, ...], critic: tuple[float, ...]) -> Decision:
        torch = self.torch
        with torch.no_grad():
            dist = self._distribution(torch.tensor(actor, dtype=torch.float32))
            action = dist.sample().tanh().clamp(-1 + 1e-6, 1 - 1e-6)
            logp = self._log_prob(dist, action).item()
        return Decision(tuple(action.tolist()), logp, self.value(critic))

    def value(self, critic: tuple[float, ...]) -> float:
        torch = self.torch
        with torch.no_grad():
            return self.critic(torch.tensor(critic, dtype=torch.float32)).item()

    def update(self, rollout: tuple[Transition, ...], advantages: tuple[float, ...],
               returns: tuple[float, ...]) -> dict[str, float]:
        torch = self.torch
        actor = torch.tensor([t.observation.actor for t in rollout], dtype=torch.float32)
        critic = torch.tensor([t.observation.critic for t in rollout], dtype=torch.float32)
        actions = torch.tensor([t.decision.action for t in rollout], dtype=torch.float32)
        old_logp = torch.tensor([t.decision.log_probability for t in rollout], dtype=torch.float32)
        advantages_t = torch.tensor(advantages, dtype=torch.float32)
        advantages_t = (advantages_t - advantages_t.mean()) / (advantages_t.std(unbiased=False) + 1e-8)
        returns_t = torch.tensor(returns, dtype=torch.float32)
        # Full-batch reference PPO; production vectorization/minibatches are external.
        for _ in range(5):
            dist = self._distribution(actor)
            logp = self._log_prob(dist, actions)
            ratio = (logp - old_logp).exp()
            policy_loss = -torch.minimum(ratio * advantages_t,
                                         ratio.clamp(0.8, 1.2) * advantages_t).mean()
            value_loss = (self.critic(critic).squeeze(-1) - returns_t).square().mean()
            # Reparameterized estimate of transformed distribution entropy.
            fresh_action = dist.rsample().tanh().clamp(-1 + 1e-6, 1 - 1e-6)
            entropy = -self._log_prob(dist, fresh_action).mean()
            loss = policy_loss + 0.5 * value_loss - 0.01 * entropy
            if not torch.isfinite(loss):
                raise ValueError("nonfinite PPO loss")
            self.optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.parameters, 1.0, error_if_nonfinite=True)
            self.optimizer.step()
        return {"policy_loss": policy_loss.item(), "value_loss": value_loss.item(), "entropy": entropy.item()}

    def save(self, path: Path) -> None:
        self.torch.save({"schema": "chair-ppo-research-v1", "actor": self.actor.state_dict(),
                         "critic": self.critic.state_dict(), "log_std": self.log_std.detach(),
                         "optimizer": self.optimizer.state_dict(),
                         "rng": self.torch.get_rng_state(),
                         "observation_schema": "chair-v1", "joint_mapping": "worker-identity.json",
                         "normalization": "identity", "deployment_approved": False}, path)


def create_trainer(config: TrainingConfig) -> OnPolicyTrainer:
    if config.symmetry or config.curriculum:
        raise ValueError("reference PPO supports only SY=false, CC=false; use a research Trainer for SY/CC")
    return OnPolicyTrainer(TorchPPOLearner(config.seed))
