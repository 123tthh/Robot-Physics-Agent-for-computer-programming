"""Small bounded-action PPO with terminal-observation-aware GAE."""

import math
import torch
from torch import nn
from torch.distributions import Normal
from torch.nn import functional as F


def generalized_advantage(rewards, values, next_values, terminated, truncated, gamma, lam):
    """next_values are evaluated BEFORE reset, including time-limit endpoints.

    A timeout bootstraps its terminal observation, but cannot connect GAE to
    the next episode. A physical failure/success is terminal and has no bootstrap.
    """
    advantages = torch.zeros_like(rewards)
    carry = torch.zeros_like(rewards[0])
    for t in reversed(range(len(rewards))):
        delta = rewards[t] + gamma * (~terminated[t]).float() * next_values[t] - values[t]
        carry = delta + gamma * lam * (~(terminated[t] | truncated[t])).float() * carry
        advantages[t] = carry
    return advantages, advantages + values


class ActorCritic(nn.Module):
    def __init__(self, observation_dim, action_dim, hidden=64, initial_log_std=-.5):
        super().__init__()
        def network(output):
            return nn.Sequential(nn.Linear(observation_dim, hidden), nn.Tanh(),
                                 nn.Linear(hidden, hidden), nn.Tanh(), nn.Linear(hidden, output))
        self.actor, self.critic = network(action_dim), network(1)
        self.log_std = nn.Parameter(torch.full((action_dim,), initial_log_std))
        for layer in self.modules():
            if isinstance(layer, nn.Linear):
                nn.init.orthogonal_(layer.weight, math.sqrt(2))
                nn.init.zeros_(layer.bias)
        nn.init.orthogonal_(self.actor[-1].weight, .01)
        nn.init.orthogonal_(self.critic[-1].weight, 1.)

    def distribution(self, obs):
        return Normal(self.actor(obs), self.log_std.clamp(-3., 1.).exp())

    def value(self, obs):
        return self.critic(obs).squeeze(-1)

    def evaluate(self, obs, latent):
        distribution = self.distribution(obs)
        # Stable log(1 - tanh(z)^2), preserving the exact sampled latent.
        correction = 2 * (math.log(2) - latent - F.softplus(-2*latent))
        log_prob = (distribution.log_prob(latent) - correction).sum(-1)
        # Explicit latent-Normal entropy regularizer, not a claim of exact
        # squashed-action entropy. PPO likelihoods include the Jacobian.
        return log_prob, distribution.entropy().sum(-1), self.value(obs)

    @torch.no_grad()
    def act(self, obs, deterministic=False):
        distribution = self.distribution(obs)
        latent = distribution.mean if deterministic else distribution.sample()
        log_prob, _, value = self.evaluate(obs, latent)
        return latent.tanh(), latent, log_prob, value


class PPOTrainer:
    def __init__(self, model, config):
        self.model, self.config = model, config
        self.optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate, eps=1e-5)

    def update(self, rollout):
        c = self.config
        advantage, returns = generalized_advantage(rollout['rewards'], rollout['values'], rollout['next_values'],
            rollout['terminated'], rollout['truncated'], c.gamma, c.gae_lambda)
        advantages = advantage.flatten()
        advantages = (advantages-advantages.mean()) / (advantages.std(unbiased=False)+1e-8)
        obs = rollout['observations'].flatten(0, 1)
        latent = rollout['latent'].flatten(0, 1)
        old_logp = rollout['log_prob'].flatten()
        old_values = rollout['values'].flatten()
        returns = returns.flatten()
        losses, kls, norms = [], [], []
        stop = False
        for _ in range(c.epochs):
            order = torch.randperm(len(obs), device=obs.device)
            for ids in order.split(c.minibatch_size):
                logp, entropy, values = self.model.evaluate(obs[ids], latent[ids])
                logratio = logp-old_logp[ids]
                ratio = logratio.exp()
                kl = ((ratio-1)-logratio).mean()
                if not torch.isfinite(kl):
                    raise RuntimeError('nonfinite PPO likelihood')
                if kl.item() > c.target_kl:
                    stop = True
                    break
                policy_loss = -torch.minimum(ratio*advantages[ids],
                    ratio.clamp(1-c.clip_ratio, 1+c.clip_ratio)*advantages[ids]).mean()
                clipped = old_values[ids] + (values-old_values[ids]).clamp(-c.clip_ratio, c.clip_ratio)
                value_loss = .5*torch.maximum((values-returns[ids]).square(), (clipped-returns[ids]).square()).mean()
                loss = policy_loss + c.value_coefficient*value_loss - c.entropy_coefficient*entropy.mean()
                if not torch.isfinite(loss):
                    raise RuntimeError('nonfinite PPO loss')
                self.optimizer.zero_grad(set_to_none=True)
                loss.backward()
                norm = nn.utils.clip_grad_norm_(self.model.parameters(), c.max_grad_norm, error_if_nonfinite=True)
                self.optimizer.step()
                losses.append(loss.detach()); kls.append(kl.detach()); norms.append(norm.detach())
            if stop:
                break
        def mean(items):
            return torch.stack(items).mean().item() if items else 0.
        return dict(loss=mean(losses), approximate_kl=mean(kls), gradient_norm=mean(norms),
                    optimizer_steps=len(losses), kl_early_stop=stop,
                    mean_advantage=advantage.mean().item(), mean_return=returns.mean().item())
