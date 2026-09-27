"""Training adapter sharing the accepted batch scene, without per-tick JSON.

The policy gets a numeric observation and cannot access this supervisor. Raw
evaluation trajectories are captured separately by the original single-hand
backend; online training completion counters are never verifier results.
"""

import torch

from .observations import teacher_observation
from .rewards import PressProgress, potential, reward_terms
from .training_safety import TensorActionGuard, TensorSafetyMonitor, SAFETY_CODES


class SingleKeyTrainingEnv:
    def __init__(self, scene, schema, config, reward_spec):
        self.scene, self.schema, self.config, self.reward_spec = scene, schema, config, reward_spec
        schema.check_claims(schema.claims())
        if schema.control != scene.control or schema.mask.active != ('left_hand_index_1_joint',):
            raise ValueError('v1 diagnostic requires the audited single left index drive')
        self.device = scene.targets.device
        self.count = scene.batch.num_envs
        self.key = scene.key_names.index('a')
        self.active_indices = [schema.control.joint_names.index(n) for n in schema.mask.active]
        self.guard = TensorActionGuard(schema, self.device)
        self.safety = TensorSafetyMonitor(scene.limits, scene.origins, scene.config)
        self.progress = PressProgress(self.count, len(scene.key_names), self.key, self.device)
        self.previous_action = torch.zeros((self.count, 23), device=self.device)
        self.returns = torch.zeros(self.count, device=self.device)
        self.generations = torch.zeros(self.count, dtype=torch.long, device=self.device)
        self.physics_steps = 0
        self.reset_count = 0
        self.fault_counts = torch.zeros(len(SAFETY_CODES), dtype=torch.long, device=self.device)
        self.tip_offset = scene.targets.new_tensor((.026, .006, 0.)).expand(self.count, -1)
        # Same authored direct-joint state and safe settling as VF-01; no demos.
        for _ in range(scene.config.settling_steps):
            scene.advance()
            if self.safety.check(self.raw()).any():
                raise RuntimeError('unsafe training initialization')
        scene.cache_reset_state()
        self.progress.reset(slice(None))

    def raw(self):
        s = self.scene
        offset = s.quat_apply(s.robot.data.body_quat_w[:, s.tip_id], self.tip_offset)
        return dict(q=s.robot.data.joint_pos, v=s.robot.data.joint_vel, targets=s.targets,
            key_q=s.keyboard.data.joint_pos[:, s.key_ids], key_targets=s.keyboard.data.joint_pos_target[:, s.key_ids],
            forces=s.contacts.data.net_forces_w[:, s.contact_ids], key_xyz=s.keyboard.data.body_pos_w[:, s.key_body_ids],
            tip=s.robot.data.body_pos_w[:, s.tip_id]+offset, pelvis=s.robot.data.body_pos_w[:, s.pelvis_id],
            torso=s.robot.data.body_quat_w[:, s.torso_id])

    def observation(self, raw=None):
        raw = self.raw() if raw is None else raw
        ids = self.scene.ids
        return teacher_observation(raw['q'][:, ids], raw['v'][:, ids], raw['targets'][:, ids], self.previous_action,
            raw['tip'], raw['key_xyz'][:, self.key], (-raw['key_q'][:, self.key]).clamp(min=0.),
            raw['forces'][:, self.key], self.progress.pressed_seen, self.progress.clear_age[:, self.key],
            self.progress.elapsed, self.schema.control, self.config.horizon_s)

    def _potential(self, raw):
        return potential(raw['tip'], raw['key_xyz'][:, self.key], (-raw['key_q'][:, self.key]).clamp(min=0.), self.progress)

    def reset_rows(self, rows):
        self.scene.reset_rows(rows.tolist())
        self.progress.reset(rows)
        self.previous_action[rows] = 0.
        self.generations[rows] += 1
        self.reset_count += len(rows)

    @torch.no_grad()
    def step(self, policy_action):
        if policy_action.shape != (self.count, len(self.active_indices)):
            raise ValueError('active policy head differs from the frozen named mask')
        full = torch.zeros_like(self.previous_action)
        full[:, self.active_indices] = policy_action
        before = self.raw()
        phi_before = self._potential(before).clone()
        previous_action = self.previous_action.clone()
        _, approved = self.guard.apply(self.scene.targets[:, self.scene.ids], full)
        self.scene.targets[:, self.scene.ids] = approved
        self.previous_action = full.clone()
        alive = torch.ones(self.count, dtype=torch.bool, device=self.device)
        completed = torch.zeros_like(alive)
        mistakes = torch.zeros_like(alive)
        unsafe = torch.zeros_like(alive)
        truncated = torch.zeros_like(alive)
        duration = torch.zeros(self.count, device=self.device)
        endpoint_time = torch.zeros_like(duration)
        endpoint_obs = torch.zeros_like(self.observation())
        endpoint_phi = torch.zeros_like(duration)
        endpoint_forces = torch.zeros_like(before['forces'])
        for _ in range(self.scene.config.decimation):
            self.scene.advance()
            self.physics_steps += 1
            raw = self.raw()
            codes = self.safety.check(raw)
            bad = codes.any(-1)
            if (bad & (~alive)).any():
                raise RuntimeError('independent safety failed during post-terminal reset settling')
            pulse, mistake = self.progress.advance((-raw['key_q']).clamp(min=0.), raw['forces'], self.scene.config.physics_dt_s)
            duration += alive.float()*self.scene.config.physics_dt_s
            completed |= alive & pulse & (~bad) & (~mistake)
            mistakes |= alive & mistake
            unsafe |= alive & bad
            timeout = self.progress.elapsed >= self.config.horizon_s-1e-5
            truncated |= alive & timeout & (~(pulse | mistake | bad))
            ended = alive & (pulse | mistake | bad | timeout)
            # Save true endpoints BEFORE any reset. The value function must
            # never bootstrap a truncated episode from its next reset state.
            obs = self.observation(raw)
            phi = self._potential(raw)
            endpoint_obs[alive] = obs[alive]
            endpoint_phi[alive] = phi[alive]
            endpoint_forces[alive] = raw['forces'][alive]
            endpoint_time[alive] = self.progress.elapsed[alive]
            if ended.any():
                rows = ended.nonzero(as_tuple=False).flatten()
                self.fault_counts += codes[ended].sum(0)
                alive[rows] = False
                # Safety rows reset immediately at this substep; no policy can
                # choose to keep pushing through an unsafe contact.
                self.reset_rows(rows)
        terms = reward_terms(self.reward_spec, phi_before, endpoint_phi, completed, mistakes, unsafe,
            full, previous_action, endpoint_forces, duration, self.config.gamma)
        rewards = terms.sum(-1)
        self.returns += rewards
        done = ~alive
        episodes = torch.stack((self.returns[done], endpoint_time[done], completed[done], mistakes[done],
                               unsafe[done], truncated[done]), dim=-1)
        self.returns[done] = 0.
        return dict(observation=self.observation(), terminal_observation=endpoint_obs,
            rewards=rewards, terms=terms, terminated=completed | mistakes | unsafe, truncated=truncated,
            episodes=episodes, completed=completed, safety_codes=self.fault_counts.clone())
