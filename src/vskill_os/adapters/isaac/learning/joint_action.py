"""Normalized action adapter over the independently guarded VF-01 backend.

This module is importable on CPU. Only the supervisor receives the backend,
reset, pause/resume and manifests. The policy port cannot change limits or
clear a safety latch. This is an API boundary, not a hostile-code sandbox.
"""

from dataclasses import dataclass
from uuid import uuid4

from ....learning.environment import (
    ActionRejected, JointTargetAction, JointTargetGuard, PolicyObservation,
)
from ....learning.policy_manifest import (
    ActionSchema, ControlClaim, MappingEvent, MappingTrace, NormalizedJointAction, PolicyLease,
)


@dataclass(frozen=True, slots=True)
class MappedAction:
    proposed_targets_rad: tuple[float, ...]
    filtered_targets_rad: tuple[float, ...]
    backend_action: JointTargetAction


class JointActionMapper:
    """No free-running integrator: every proposal starts at executed feedback."""

    def __init__(self, schema: ActionSchema):
        self.schema = schema
        self.guard = JointTargetGuard(schema.control)

    def check_observation(self, observation: PolicyObservation, *, initializing=False):
        spec = self.schema.control
        if tuple(j.name for j in observation.joints) != spec.joint_names:
            raise ActionRejected("JOINT_MAPPING")
        if observation.time_s < 0 or any(
            not low - 1e-6 <= j.commanded_target_rad <= high + 1e-6
            or not low - 0.03 <= j.position_rad <= high + 0.03
            for j, low, high in zip(observation.joints, spec.lower_rad, spec.upper_rad, strict=True)
        ):
            raise ActionRejected("JOINT_STATE")
        if initializing and any(abs(j.commanded_target_rad - j.position_rad)
                                > self.schema.scale.initial_tracking_tolerance_rad for j in observation.joints):
            raise ActionRejected("UNSAFE_INITIAL_TRACKING")

    def map(self, observation, action, *, lease_token, sequence, backend_token, backend_sequence):
        spec = self.schema.control
        if action.owner_token != lease_token:
            raise ActionRejected("CONTROL_OWNERSHIP")
        if action.joint_names != spec.joint_names:
            raise ActionRejected("JOINT_MAPPING")
        if action.sequence != sequence:
            raise ActionRejected("ACTION_SEQUENCE")
        if any(abs(v) > 1 for v in action.values):
            raise ActionRejected("ACTION_RANGE")
        self.check_observation(observation)
        targets = tuple(j.commanded_target_rad for j in observation.joints)
        deltas = tuple(a * scale for a, scale in zip(action.values, self.schema.step_scale_rad, strict=True))
        proposed = tuple(q + d for q, d in zip(targets, deltas, strict=True))
        masked = tuple(d if n in self.schema.mask.active else 0.0
                       for n, d in zip(spec.joint_names, deltas, strict=True))
        low_level = JointTargetAction(spec.joint_names, masked, action.duration_s, action.valid_until_s,
                                      backend_token, backend_sequence)
        filtered = self.guard.apply(targets, low_level, time_s=observation.time_s,
                                    owner_token=backend_token, expected_sequence=backend_sequence)
        return MappedAction(proposed, filtered, low_level)


class NormalizedPolicyPort:
    __slots__ = ("__session", "__token")

    def __init__(self, session, token):
        self.__session, self.__token = session, token

    def observe(self):
        return self.__session.observe(self.__token)

    def step(self, action: NormalizedJointAction):
        return self.__session.step(self.__token, action)


class JointActionSession:
    """Supervisor lifecycle. A safety stop requires reset, never policy resume.

    pause() revokes input immediately; hold_step() advances one guarded PD tick.
    A pause holds the last target, so it is not a physical release or a hardware
    emergency stop. The simulator's supervisor continues ticking while paused.
    """

    def __init__(self, backend, schema: ActionSchema):
        self.backend, self.schema = backend, schema
        self.mapper = JointActionMapper(schema)
        self.state, self.token = "finished", ""
        self.observation = None
        self.events = []

    def _record(self, kind, *, before=None, action=None, port_token="", proposed=(), filtered=(), index=None, failures=()):
        self.events.append(MappingEvent(kind, self.observation.time_s if before is None else before,
            self.observation.time_s, self.state, self.token, self.sequence, port_token, action, proposed,
            filtered, self.observation.joints, index, tuple(failures)))

    def _lease(self):
        return (NormalizedPolicyPort(self, self.token), self.observation,
                PolicyLease(self.observation.env_id, self.observation.episode_id, self.token, self.schema.sha256()))

    def reset(self, spec, claims: tuple[ControlClaim, ...]):
        if self.state in ("active", "paused"):
            raise RuntimeError("finish the active mapping episode before reset")
        self.schema.check_claims(claims)  # No physical reset on ownership conflict.
        observation, metadata = self.backend.reset(spec)
        self.observation, self.metadata = observation, metadata
        self.events, self.sequence, self.backend_sequence = [], 0, 0
        self.claims, self.token, self.state = claims, "", "latched"
        if (metadata.control_spec != self.schema.control or metadata.support_mode != self.schema.support_mode
                or observation.env_id != metadata.env_id or observation.episode_id != metadata.episode_id
                or observation.time_s != 0):
            self._record("fault", failures=("BACKEND_SCHEMA",))
            raise ActionRejected("BACKEND_SCHEMA")
        try:
            self.mapper.check_observation(observation, initializing=True)
        except ActionRejected as exc:
            self._record("fault", failures=(exc.code,))
            raise
        self.state, self.token = "active", uuid4().hex
        self._record("reset")
        return self._lease()

    def _active(self, token):
        if self.state in ("latched", "finished"):
            raise ActionRejected("RESET_REQUIRED")
        if self.state == "paused":
            raise ActionRejected("CONTROL_STOPPED")
        if token != self.token:
            raise ActionRejected("CONTROL_OWNERSHIP")

    def observe(self, token):
        self._active(token)
        return self.observation

    def step(self, token, action):
        try:
            self._active(token)
            mapped = self.mapper.map(self.observation, action, lease_token=self.token, sequence=self.sequence,
                                     backend_token=self.metadata.owner_token, backend_sequence=self.backend_sequence)
        except ActionRejected as exc:
            if self.observation is not None:
                self._record("rejected", action=action, port_token=token, failures=(exc.code,))
            raise
        return self._drive("policy", mapped, action=action, port_token=token)

    def _drive(self, kind, mapped, *, action=None, port_token=""):
        before, index = self.observation.time_s, self.backend_sequence
        try:
            step = self.backend.step(mapped.backend_action)
            observation = step.observation
            if ((observation.env_id, observation.episode_id) != (self.metadata.env_id, self.metadata.episode_id)
                    or not before < observation.time_s <= before + self.schema.control.control_dt_s + 1e-9
                    or (not step.terminated and not step.truncated
                        and abs(observation.time_s - before - self.schema.control.control_dt_s) > 1e-9)):
                raise ActionRejected("BACKEND_FEEDBACK")
            self.mapper.check_observation(observation)
            if any(abs(j.commanded_target_rad - target) > 1e-6 for j, target in
                   zip(observation.joints, mapped.filtered_targets_rad, strict=True)):
                raise ActionRejected("BACKEND_FEEDBACK")
            self.observation = observation  # includes actual float32 targets, never the unsent proposal
        except Exception as exc:
            self.state, self.token = "latched", ""
            self._record("fault", before=before, action=action, port_token=port_token,
                         proposed=mapped.proposed_targets_rad, filtered=mapped.filtered_targets_rad,
                         index=index, failures=(getattr(exc, "code", "PHYSICS_BACKEND_ERROR"),))
            raise
        self.backend_sequence += 1
        if kind == "policy":
            self.sequence += 1
        if step.terminated or step.failure_codes:
            self.state, self.token = "latched", ""
        elif step.truncated:
            self.state, self.token = "finished", ""
        self._record(kind, before=before, action=action, port_token=port_token,
                     proposed=mapped.proposed_targets_rad, filtered=mapped.filtered_targets_rad,
                     index=index, failures=step.failure_codes)
        return step

    def pause(self):
        if self.state != "active":
            raise ActionRejected("RESET_REQUIRED" if self.state != "paused" else "CONTROL_STOPPED")
        self.state, self.token = "paused", ""
        self.paused_at = self.observation.time_s
        self._record("stop")

    def hold_step(self):
        if self.state != "paused":
            raise ActionRejected("RESET_REQUIRED")
        targets = tuple(j.commanded_target_rad for j in self.observation.joints)
        spec = self.schema.control
        action = JointTargetAction(spec.joint_names, (0.0,) * len(targets), spec.control_dt_s,
                                   self.observation.time_s + spec.control_dt_s, self.metadata.owner_token, self.backend_sequence)
        filtered = self.mapper.guard.apply(targets, action, time_s=self.observation.time_s,
            owner_token=self.metadata.owner_token, expected_sequence=self.backend_sequence)
        return self._drive("hold", MappedAction(targets, filtered, action))

    def resume(self, claims: tuple[ControlClaim, ...]):
        if self.state != "paused":
            raise ActionRejected("RESET_REQUIRED")
        self.schema.check_claims(claims)
        if self.observation.time_s <= self.paused_at:
            raise ActionRejected("STALE_OBSERVATION")
        # This is the latest owned backend observation, never a policy-supplied
        # pose. No assignment to physics state or target occurs on handoff.
        # Contact may load a PD joint away from its target. Preserve that
        # target, check measured limits, and keep the backend force monitor on;
        # rebasing to measured q here would silently unload/move the finger.
        self.mapper.check_observation(self.observation)
        self.state, self.token, self.sequence = "active", uuid4().hex, 0
        self._record("resume")
        return self._lease()

    def finish(self):
        if self.observation is None:
            raise RuntimeError("finish requires reset")
        if self.state == "latched":
            raise ActionRejected("RESET_REQUIRED")
        if self.state == "finished":
            return
        self.backend.finish()
        self.state, self.token = "finished", ""
        self._record("finish")

    def trace(self, physical_episode_sha256: str) -> MappingTrace:
        if self.state in ("active", "paused"):
            raise RuntimeError("finish before exporting mapping evidence")
        return MappingTrace("vf-action-mapping/1", self.observation.env_id, self.observation.episode_id,
                            self.schema, self.claims, physical_episode_sha256, tuple(self.events))
