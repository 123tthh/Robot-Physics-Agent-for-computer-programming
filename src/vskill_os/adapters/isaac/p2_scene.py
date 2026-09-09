"""Isaac P2 diagnostic and fingertip-control acceptance entry point.

The task-level planner supplies only modular Cartesian/contact/event actions.
This optional Isaac adapter is the low-level boundary that may turn those
objectives into joint targets while keeping such commands out of ActionGraph.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import time
import traceback
from typing import Any

from vskill_os.action_graph import (
    ActionExecution,
    ActionGraphRunner,
    ActionKind,
    ActionModuleCatalog,
    ActionNode,
    ActionStatus,
    IndependentActionVerifier,
    KeyboardActionCompiler,
)
from vskill_os.domain import Evidence, EvidenceDomain
from vskill_os.simulation.desktop import PtyDesktopWorker, render_terminal_text
from vskill_os.simulation.keyboard_bridge import (
    KeyEvent,
    KeyEventType,
    KeycapConfig,
    KeycapSample,
    KeyboardEventBridge,
)
from vskill_os.typing import (
    ExpectedKeyEvent,
    Finger,
    HandSide,
    KeyRole,
    Keystroke,
    PlannedKeyEventType,
    TypingPlanner,
)

from .p1_scene import KEY_PITCH_M, keyboard_key_center, standard_keyboard_layout


# The controlled point is at the Dex3 distal-link centerline; its collision
# geometry extends roughly 10 mm below that point when posed over the keybed.
FINGERTIP_CONTACT_HEIGHT_M = 0.013


class IsaacDex3ActionAdapter:
    """Low-level DLS/PhysX adapter for executor-neutral keyboard actions."""

    TIP_BODY_NAMES = {
        "left.thumb": "left_hand_thumb_2_link",
        "left.index": "left_hand_index_1_link",
        "left.middle": "left_hand_middle_1_link",
        "right.thumb": "right_hand_thumb_2_link",
        "right.index": "right_hand_index_1_link",
        "right.middle": "right_hand_middle_1_link",
    }
    TIP_LOCAL_OFFSETS_M = {
        "left.thumb": (0.0, -0.052, 0.0),
        "left.index": (0.052, 0.0, 0.0),
        "left.middle": (0.052, 0.0, 0.0),
        "right.thumb": (0.0, 0.052, 0.0),
        "right.index": (0.052, 0.0, 0.0),
        "right.middle": (0.052, 0.0, 0.0),
    }
    ARM_JOINTS = {
        "left": (
            "left_shoulder_pitch_joint",
            "left_shoulder_roll_joint",
            "left_shoulder_yaw_joint",
            "left_elbow_joint",
            "left_wrist_roll_joint",
            "left_wrist_pitch_joint",
            "left_wrist_yaw_joint",
        ),
        "right": (
            "right_shoulder_pitch_joint",
            "right_shoulder_roll_joint",
            "right_shoulder_yaw_joint",
            "right_elbow_joint",
            "right_wrist_roll_joint",
            "right_wrist_pitch_joint",
            "right_wrist_yaw_joint",
        ),
    }
    FINGER_JOINTS = {
        "left.thumb": (
            "left_hand_thumb_0_joint",
            "left_hand_thumb_1_joint",
            "left_hand_thumb_2_joint",
        ),
        "left.index": (
            "left_hand_index_0_joint",
            "left_hand_index_1_joint",
        ),
        "left.middle": (
            "left_hand_middle_0_joint",
            "left_hand_middle_1_joint",
        ),
        "right.thumb": (
            "right_hand_thumb_0_joint",
            "right_hand_thumb_1_joint",
            "right_hand_thumb_2_joint",
        ),
        "right.index": (
            "right_hand_index_0_joint",
            "right_hand_index_1_joint",
        ),
        "right.middle": (
            "right_hand_middle_0_joint",
            "right_hand_middle_1_joint",
        ),
    }
    WAIST_JOINTS = (
        "waist_yaw_joint",
        "waist_roll_joint",
        "waist_pitch_joint",
    )

    def __init__(
        self,
        *,
        torch_module: Any,
        quat_apply: Any,
        sim: Any,
        robot: Any,
        keyboard: Any,
        contact_sensor: Any,
        desktop: PtyDesktopWorker,
        physics_dt: float,
    ) -> None:
        self.torch = torch_module
        self.quat_apply = quat_apply
        self.sim = sim
        self.robot = robot
        self.keyboard = keyboard
        self.contact_sensor = contact_sensor
        self.desktop = desktop
        self.physics_dt = physics_dt
        self.robot_targets = robot.data.joint_pos.clone()
        self.keyboard_targets = keyboard.data.joint_pos.clone()
        self.tip_body_ids = {
            executor: robot.find_bodies([name], preserve_order=True)[0][0]
            for executor, name in self.TIP_BODY_NAMES.items()
        }
        self.arm_joint_ids = {
            hand: robot.find_joints(list(names), preserve_order=True)[0]
            for hand, names in self.ARM_JOINTS.items()
        }
        self.finger_joint_ids = {
            executor: robot.find_joints(list(names), preserve_order=True)[0]
            for executor, names in self.FINGER_JOINTS.items()
        }
        self.waist_joint_ids = robot.find_joints(
            list(self.WAIST_JOINTS), preserve_order=True
        )[0]
        self.key_joint_ids = {
            name: keyboard.find_joints([f"{_safe_key(name)}_joint"], preserve_order=True)[0][0]
            for name in _key_centers()
        }
        self.contact_indices = {
            name: index for index, name in enumerate(contact_sensor.body_names)
        }
        self.key_centers = _key_centers()
        self.objectives: dict[str, Any] = {}
        self.active_key_by_executor: dict[str, str] = {}
        self.event_log: list[KeyEvent] = []
        self.event_context_log: list[dict[str, object]] = []
        self.current_node_id = "initialization"
        self.bridge = KeyboardEventBridge(KeycapConfig())
        self.physical_time = time.monotonic()
        self.sample_count = 0
        self.minimum_errors: dict[str, float] = {}
        self.ik_debug: list[dict[str, object]] = []
        self.maximum_forces: dict[str, float] = {key: 0.0 for key in self.key_centers}
        self.maximum_displacements: dict[str, float] = {
            key: 0.0 for key in self.key_centers
        }

    async def execute(self, node: ActionNode) -> ActionExecution:
        self.current_node_id = node.node_id
        target = str(node.parameters.get("target_key", ""))
        if node.kind is ActionKind.CARTESIAN_MOVE:
            center = self._key_center(target)
            offset = float(node.parameters.get("height_offset_m", 0.0))
            desired_tip = self.torch.tensor(
                [
                    center[0],
                    center[1],
                    center[2] + FINGERTIP_CONTACT_HEIGHT_M + offset,
                ],
                device=self.robot.device,
            )
            self.objectives[node.executor] = desired_tip
            self.active_key_by_executor[node.executor] = target
            tolerance = float(node.parameters.get("position_tolerance_m", 0.004))
            hand = node.executor.split(".", 1)[0]
            if node.parameters.get("phase") == "approach":
                # Remove the large reach-phase target lead before descending
                # toward the switch.  The approach controller then uses a
                # much tighter lead bound to avoid a transient key strike.
                self.robot_targets.copy_(self.robot.data.joint_pos)
                reached, error = await self._move_hand(
                    hand,
                    tolerance=tolerance,
                    focus_executor=node.executor,
                    maximum_steps=500,
                    maximum_joint_delta=0.003,
                    target_lead=0.05,
                )
            else:
                reached, error = await self._move_hand(
                    hand,
                    tolerance=tolerance,
                    focus_executor=node.executor,
                    maintenance_tolerance=(
                        0.020
                        if node.parameters.get("phase") == "prepose"
                        else 0.012
                    ),
                )
            self.minimum_errors[node.node_id] = error
            return ActionExecution(
                reached,
                (
                    Evidence(
                        EvidenceDomain.PHYSICAL,
                        "fingertip_pose",
                        {
                            "target_key": target,
                            "phase": node.parameters.get("phase"),
                            "within_tolerance": reached,
                            "error_m": error,
                            "tip_xyz_m": self._tip_point(node.executor).tolist(),
                        },
                        "isaac_articulation_state",
                    ),
                ),
                None if reached else "UNREACHABLE_KEY",
                "" if reached else f"DLS residual {error:.5f} m",
            )
        if node.kind is ActionKind.CONTACT_GATE:
            contacted, force, adjacent = await self._seek_contact(
                node.executor, target
            )
            return ActionExecution(
                True,
                (
                    Evidence(
                        EvidenceDomain.PHYSICAL,
                        "fingertip_key_contact",
                        {
                            "target_key": target,
                            "in_contact": contacted,
                            "force_n": force,
                            "adjacent_contacts": list(adjacent),
                            "tip_xyz_m": self._tip_point(node.executor).tolist(),
                        },
                        "isaac_physx_contact_sensor",
                    ),
                ),
            )
        if node.kind is ActionKind.KEY_ACTUATE:
            event_start = len(self.event_log)
            actuated = await self._seek_actuation(node.executor, target)
            events = self.event_log[event_start:]
            displacement = self._key_displacement(target)
            return ActionExecution(
                actuated,
                (
                    Evidence(
                        EvidenceDomain.PHYSICAL,
                        "keycap_travel",
                        {
                            "target_key": target,
                            "travel_m": displacement,
                            "force_n": self._contact_force(target),
                        },
                        "isaac_articulation_state",
                    ),
                    *(_event_evidence(event) for event in events),
                ),
                None if actuated else "MISSED_EVENT",
            )
        if node.kind is ActionKind.KEY_HOLD:
            return ActionExecution(
                True,
                (
                    Evidence(
                        EvidenceDomain.PHYSICAL,
                        "key_hold_state",
                        {
                            "target_key": target,
                            "keys_down": list(self.bridge.keys_down),
                            "stable": target in self.bridge.keys_down,
                            "force_n": self._contact_force(target),
                        },
                        "isaac_physx_keycap_state",
                    ),
                ),
            )
        if node.kind is ActionKind.KEY_RELEASE:
            event_start = len(self.event_log)
            released = await self._release_key(node.executor, target)
            events = self.event_log[event_start:]
            return ActionExecution(
                released,
                tuple(_event_evidence(event) for event in events),
                None if released else (
                    "STUCK_MODIFIER"
                    if node.parameters.get("role") == "modifier"
                    else "STUCK_KEY"
                ),
            )
        if node.kind is ActionKind.KEY_RELEASE_GROUP:
            target_keys = tuple(
                str(key) for key in node.parameters.get("target_keys", ())
            )
            executors = tuple(
                str(executor) for executor in node.parameters.get("executors", ())
            )
            event_start = len(self.event_log)
            released = await self._release_group(executors, target_keys)
            events = self.event_log[event_start:]
            states = tuple(
                Evidence(
                    EvidenceDomain.PHYSICAL,
                    "key_release_state",
                    {
                        "target_key": key,
                        "released": key not in self.bridge.keys_down,
                        "clear": (
                            key not in self.bridge.keys_down
                            and self._contact_force(key) < 0.01
                        ),
                        "clearance_m": max(
                            0.0,
                            float(self._tip_point(executor)[2].item())
                            - self._key_center(key)[2],
                        ),
                    },
                    "isaac_physx_contact_sensor",
                )
                for executor, key in zip(executors, target_keys)
            )
            return ActionExecution(
                released,
                (*(_event_evidence(event) for event in events), *states),
                None if released else "STUCK_MODIFIER",
            )
        if node.kind is ActionKind.CLEARANCE_GATE:
            released = target not in self.bridge.keys_down
            return ActionExecution(
                True,
                (
                    Evidence(
                        EvidenceDomain.PHYSICAL,
                        "key_release_state",
                        {
                            "target_key": target,
                            "released": released,
                            "clear": released and self._contact_force(target) < 0.01,
                            "clearance_m": max(
                                0.0,
                                float(self._tip_point(node.executor)[2].item())
                                - self._key_center(target)[2],
                            ),
                        },
                        "isaac_physx_contact_sensor",
                    ),
                ),
            )
        if node.kind is ActionKind.BARRIER:
            return ActionExecution(
                True,
                (
                    Evidence(
                        EvidenceDomain.PHYSICAL,
                        "barrier_state",
                        {"ready": True, "barrier": node.parameters.get("barrier")},
                        "isaac_action_barrier",
                    ),
                ),
            )
        if node.kind is ActionKind.SCREEN_GATE:
            frame = self.desktop.snapshot()
            return ActionExecution(
                True,
                (
                    Evidence(
                        EvidenceDomain.GUI,
                        "screen_state",
                        {
                            "visible": frame.visible,
                            "focused": frame.focused,
                            "text": render_terminal_text(frame.text),
                            "keys_down": list(self.bridge.keys_down),
                        },
                        frame.source,
                    ),
                ),
            )
        return ActionExecution(False, failure_code="UNSUPPORTED_ACTION")

    async def emergency_release(self, reason: str) -> tuple[Evidence, ...]:
        for executor, key in tuple(self.active_key_by_executor.items()):
            if key in self.bridge.keys_down:
                await self._release_key(executor, key)
        releases = self.bridge.all_keys_up(
            monotonic_time=self.physical_time + self.physics_dt,
            reason=f"isaac_action_graph:{reason}",
        )
        self._emit_events(releases)
        return (
            Evidence(
                EvidenceDomain.PHYSICAL,
                "all_keys_up",
                {"keys_down": list(self.bridge.keys_down), "reason": reason},
                "keyboard_event_bridge",
            ),
        )

    async def _move_hand(
        self,
        hand: str,
        *,
        tolerance: float,
        focus_executor: str | None = None,
        maintenance_tolerance: float = 0.012,
        maximum_steps: int = 300,
        maximum_joint_delta: float = 0.018,
        target_lead: float = 0.16,
    ) -> tuple[bool, float]:
        torch = self.torch
        # Control calibrated fingertip points instead of distal body origins.
        # Dex3 collision tips sit ahead of the link frame, so the translational
        # Jacobian includes the angular contribution at that offset.
        hand_objectives = {
            executor: target
            for executor, target in self.objectives.items()
            if executor.startswith(f"{hand}.")
        }
        joint_ids = list(self.arm_joint_ids[hand])
        for executor in hand_objectives:
            for joint_id in self.finger_joint_ids[executor]:
                if joint_id not in joint_ids:
                    joint_ids.append(joint_id)
        error_norm = float("inf")
        for _ in range(maximum_steps):
            errors = []
            jacobians = []
            for executor, target in hand_objectives.items():
                body_id = self.tip_body_ids[executor]
                point, offset_w = self._tip_point_and_offset(executor)
                errors.append(target - point)
                body_jacobian = self.robot.root_physx_view.get_jacobians()[
                    0, body_id - 1, :, joint_ids
                ]
                rx, ry, rz = offset_w.unbind()
                zero = torch.zeros((), device=self.robot.device)
                skew_offset = torch.stack(
                    (
                        torch.stack((zero, -rz, ry)),
                        torch.stack((rz, zero, -rx)),
                        torch.stack((-ry, rx, zero)),
                    )
                )
                jacobians.append(
                    body_jacobian[:3] - skew_offset @ body_jacobian[3:]
                )
            error = torch.cat(errors)
            error_by_executor = {
                executor: float(torch.linalg.vector_norm(item).item())
                for executor, item in zip(hand_objectives, errors)
            }
            error_norm = (
                error_by_executor[focus_executor]
                if focus_executor is not None
                else max(error_by_executor.values())
            )
            maintained = all(
                executor == focus_executor or value <= maintenance_tolerance
                for executor, value in error_by_executor.items()
            )
            if error_norm <= tolerance and maintained:
                return True, error_norm
            task_jacobian = torch.cat(jacobians, dim=0)
            damping = 0.04
            regularizer = torch.eye(
                task_jacobian.shape[0], device=self.robot.device
            ) * damping**2
            delta = task_jacobian.T @ torch.linalg.solve(
                task_jacobian @ task_jacobian.T + regularizer,
                error * 0.55,
            )
            maximum_delta = maximum_joint_delta
            peak_delta = float(torch.max(torch.abs(delta)).item())
            if peak_delta > maximum_delta:
                delta = delta * (maximum_delta / peak_delta)
            current = self.robot.data.joint_pos[0, joint_ids]
            limits = self.robot.data.joint_pos_limits[0, joint_ids]
            # Integrate the DLS command into the actuator target while bounding
            # its lead over measured state.  Rebuilding the target as
            # ``current + delta`` every 120 Hz step leaves only a tiny PD error;
            # that error cannot counter arm gravity and the distal links sag
            # even though the geometric Jacobian points in the right direction.
            target_before = self.robot_targets[0, joint_ids]
            integrated = torch.minimum(
                torch.maximum(target_before + delta, current - target_lead),
                current + target_lead,
            )
            desired = torch.minimum(
                torch.maximum(integrated, limits[:, 0] + 0.002),
                limits[:, 1] - 0.002,
            )
            capture_debug = not self.ik_debug and len(hand_objectives) == 1
            if capture_debug:
                executor = next(iter(hand_objectives))
                before_position = self._tip_point(executor).clone()
                debug_entry: dict[str, object] = {
                    "executor": executor,
                    "jacobian_shape": list(task_jacobian.shape),
                    "tip_position_before_m": before_position.tolist(),
                    "tip_target_m": hand_objectives[executor].tolist(),
                    "position_error_m": errors[0].tolist(),
                    "predicted_task_delta_m": (task_jacobian @ delta).tolist(),
                    "delta_joint_norm_rad": float(torch.linalg.vector_norm(delta).item()),
                    "jacobian_column_norms_by_joint": {
                        self.robot.joint_names[int(joint_id)]: float(value)
                        for joint_id, value in zip(
                            joint_ids,
                            torch.linalg.vector_norm(task_jacobian, dim=0).tolist(),
                        )
                    },
                    "maximum_target_lead_rad": target_lead,
                }
            self.robot_targets[0, joint_ids] = desired
            self._step()
            await asyncio.sleep(0)
            if capture_debug:
                after_position = self._tip_point(executor)
                debug_entry["tip_position_after_m"] = after_position.tolist()
                debug_entry["observed_tip_delta_m"] = (
                    after_position - before_position
                ).tolist()
                self.ik_debug.append(debug_entry)
        self.ik_debug.append(
            {
                "executors": list(hand_objectives),
                "final_tip_positions_m": {
                    executor: self._tip_point(executor).tolist()
                    for executor in hand_objectives
                },
                "final_tip_targets_m": {
                    executor: target.tolist()
                    for executor, target in hand_objectives.items()
                },
                "final_tip_errors_m": {
                    executor: float(
                        torch.linalg.vector_norm(
                            target - self._tip_point(executor)
                        ).item()
                    )
                    for executor, target in hand_objectives.items()
                },
                "final_joint_position_rad": self.robot.data.joint_pos[0, joint_ids].tolist(),
                "final_joint_target_rad": self.robot_targets[0, joint_ids].tolist(),
            }
        )
        return False, error_norm

    async def _seek_contact(
        self, executor: str, key: str
    ) -> tuple[bool, float, tuple[str, ...]]:
        hand = executor.split(".", 1)[0]
        for _ in range(80):
            force = self._contact_force(key)
            if force > 0.01:
                return True, force, self._adjacent_contacts(key)
            self.objectives[executor][2] -= 0.00025
            await self._move_hand(
                hand,
                tolerance=0.0007,
                focus_executor=executor,
                maximum_steps=30,
                maximum_joint_delta=0.002,
                target_lead=0.04,
            )
        return False, self._contact_force(key), self._adjacent_contacts(key)

    async def _seek_actuation(self, executor: str, key: str) -> bool:
        hand = executor.split(".", 1)[0]
        for _ in range(30):
            if self._key_displacement(key) >= self.bridge.config.press_threshold_m:
                return any(
                    event.key == key and event.event_type is KeyEventType.KEYDOWN
                    for event in self.event_log[-4:]
                )
            self.objectives[executor][2] -= 0.00015
            await self._move_hand(
                hand,
                tolerance=0.0006,
                focus_executor=executor,
                maximum_steps=30,
                maximum_joint_delta=0.002,
                target_lead=0.04,
            )
        return False

    async def _release_key(self, executor: str, key: str) -> bool:
        hand = executor.split(".", 1)[0]
        self.objectives[executor][2] = (
            self._key_center(key)[2] + FINGERTIP_CONTACT_HEIGHT_M + 0.035
        )
        await self._move_hand(
            hand,
            tolerance=0.004,
            focus_executor=executor,
            maximum_steps=240,
            maximum_joint_delta=0.006,
            target_lead=0.08,
        )
        for _ in range(80):
            self._step()
            await asyncio.sleep(0)
            if key not in self.bridge.keys_down and self._contact_force(key) < 0.01:
                return True
        return False

    async def _release_group(
        self, executors: tuple[str, ...], keys: tuple[str, ...]
    ) -> bool:
        if len(executors) != len(keys) or len(keys) < 2:
            return False
        released = True
        for executor, key in zip(executors, keys):
            released = await self._release_key(executor, key) and released
        return released and all(
            key not in self.bridge.keys_down and self._contact_force(key) < 0.01
            for key in keys
        )

    def _step(self) -> None:
        self.robot.set_joint_position_target(self.robot_targets)
        self.robot.write_data_to_sim()
        self.keyboard.set_joint_position_target(self.keyboard_targets)
        self.keyboard.write_data_to_sim()
        self.sim.step(render=False)
        self.robot.update(self.physics_dt)
        self.keyboard.update(self.physics_dt)
        self.contact_sensor.update(self.physics_dt, force_recompute=True)
        self.physical_time += self.physics_dt
        for key in set(self.active_key_by_executor.values()):
            displacement = self._key_displacement(key)
            force = self._contact_force(key)
            self.maximum_displacements[key] = max(
                self.maximum_displacements[key], displacement
            )
            self.maximum_forces[key] = max(self.maximum_forces[key], force)
            sample = KeycapSample(
                key=key,
                displacement_m=displacement,
                in_contact=force > 0.01,
                monotonic_time=self.physical_time,
                source="isaac_physx_keycap_contact",
                sample_id=f"isaac-p2-{self.sample_count}",
                adjacent_contacts=self._adjacent_contacts(key),
            )
            self.sample_count += 1
            self._emit_events(self.bridge.process(sample, now=self.physical_time))

    def _emit_events(self, events: Any) -> None:
        for event in events:
            self.event_log.append(event)
            self.event_context_log.append(
                {
                    "node_id": self.current_node_id,
                    "event_type": event.event_type.value,
                    "key": event.key,
                    "event_time": event.monotonic_time,
                    "reason": event.reason,
                    "travel_m": self._key_displacement(event.key),
                    "force_n": self._contact_force(event.key),
                }
            )
            self.desktop.handle_event(event)

    def _tip_point(self, executor: str):
        return self._tip_point_and_offset(executor)[0]

    def _tip_point_and_offset(self, executor: str):
        torch = self.torch
        body_id = self.tip_body_ids[executor]
        body_position = self.robot.data.body_pos_w[0, body_id]
        body_quaternion = self.robot.data.body_quat_w[0, body_id]
        local_offset = torch.tensor(
            self.TIP_LOCAL_OFFSETS_M[executor], device=self.robot.device
        )
        offset_w = self.quat_apply(body_quaternion.unsqueeze(0), local_offset.unsqueeze(0))[0]
        return body_position + offset_w, offset_w

    def _key_center(self, key: str) -> tuple[float, float, float]:
        try:
            return self.key_centers[key]
        except KeyError as exc:
            raise KeyError(f"Isaac keyboard has no configured center for {key!r}") from exc

    def _key_displacement(self, key: str) -> float:
        return abs(float(self.keyboard.data.joint_pos[0, self.key_joint_ids[key]].item()))

    def _contact_force(self, key: str) -> float:
        index = self.contact_indices[_safe_key(key)]
        value = self.contact_sensor.data.net_forces_w[0, index]
        return float(self.torch.linalg.vector_norm(value).item())

    def _adjacent_contacts(self, target: str) -> tuple[str, ...]:
        allowed = {target, *self.bridge.keys_down}
        return tuple(
            sorted(
                key
                for key in self.key_centers
                if key not in allowed and self._contact_force(key) > 0.01
            )
        )


def _key_centers() -> dict[str, tuple[float, float, float]]:
    return {
        spec.name: keyboard_key_center(
            spec,
            operator_row_x=0.72,
            left_origin_y=0.145,
            top_z=0.7602,
            key_pitch=KEY_PITCH_M,
        )
        for spec in standard_keyboard_layout()
    }


def _safe_key(key: str) -> str:
    aliases = {
        "`": "grave",
        "-": "minus",
        "=": "equal",
        "[": "left_bracket",
        "]": "right_bracket",
        "\\": "backslash",
        ";": "semicolon",
        "'": "apostrophe",
        ",": "comma",
        ".": "period",
        "/": "slash",
    }
    result = aliases.get(key, key)
    return f"key_{result}" if result[:1].isdigit() else result


def _event_evidence(event: KeyEvent) -> Evidence:
    return Evidence(
        EvidenceDomain.PHYSICAL,
        "key_event",
        {
            "event_type": event.event_type.value,
            "key": event.key,
            "modifiers": list(event.modifiers),
            "event_id": event.event_id,
        },
        event.source,
    )


def _parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[4]
    parser = argparse.ArgumentParser(description="Run the P2 Isaac Dex3 fingertip acceptance")
    parser.add_argument(
        "--scene-usd",
        type=Path,
        default=root / "artifacts/isaac-p1/scenes/p1_g1_dex3_keyboard.usda",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "reports/isaac_p2_acceptance.json",
    )
    parser.add_argument("--physics-dt", type=float, default=1.0 / 120.0)
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument(
        "--robot-forward-offset-m",
        type=float,
        default=0.28,
        help="P2 fixed-base reach calibration relative to the P1 overview pose",
    )
    parser.add_argument("--inspect-only", action="store_true")
    parser.add_argument(
        "--mode",
        choices=("single", "chord"),
        default="single",
        help="run a single-key gate or the full Ctrl+Alt+T chord gate",
    )
    parser.add_argument(
        "--target-key",
        default="capslock",
        help="single-key P2 baseline target (ignored in chord mode)",
    )
    parser.add_argument(
        "--executor",
        choices=tuple(IsaacDex3ActionAdapter.TIP_BODY_NAMES),
        help="override the single key or chord primary-key fingertip",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    scene_usd = args.scene_usd.resolve()
    if not scene_usd.is_file():
        raise FileNotFoundError(f"P1 scene USD is missing: {scene_usd}")
    if not math.isfinite(args.physics_dt) or args.physics_dt <= 0.0:
        raise ValueError("physics dt must be finite and positive")
    if not math.isfinite(args.robot_forward_offset_m) or not 0.0 <= args.robot_forward_offset_m <= 0.35:
        raise ValueError("robot forward offset must be within [0, 0.35] m")

    from isaaclab.app import AppLauncher

    simulation_app = AppLauncher(headless=args.headless, enable_cameras=True).app
    try:
        import torch

        from pxr import Gf

        import isaaclab
        import isaaclab.sim as sim_utils
        from isaaclab.actuators import ImplicitActuatorCfg
        from isaaclab.assets import Articulation, ArticulationCfg
        from isaaclab.sensors import ContactSensor, ContactSensorCfg

        if not sim_utils.open_stage(str(scene_usd)):
            raise RuntimeError(f"Isaac could not open scene: {scene_usd}")
        stage = sim_utils.get_current_stage()
        robot_prim = stage.GetPrimAtPath("/World/G1")
        robot_prim.GetAttribute("xformOp:translate").Set(
            Gf.Vec3d(args.robot_forward_offset_m, 0.0, 0.7900000214576721)
        )
        sim = sim_utils.SimulationContext(
            sim_utils.SimulationCfg(dt=args.physics_dt, render_interval=12, device=args.device)
        )
        robot = Articulation(
            ArticulationCfg(
                prim_path="/World/G1",
                spawn=None,
                actuators={
                    "g1_and_dex3": ImplicitActuatorCfg(
                        joint_names_expr=[".*"],
                        effort_limit_sim=300.0,
                        velocity_limit_sim=30.0,
                        stiffness=1000.0,
                        damping=100.0,
                    )
                },
            )
        )
        keyboard = Articulation(
            ArticulationCfg(
                prim_path="/World/Keyboard",
                spawn=None,
                actuators={
                    "key_switches": ImplicitActuatorCfg(
                        joint_names_expr=[".*_joint"],
                        effort_limit_sim=20.0,
                        velocity_limit_sim=1.0,
                        stiffness=1000.0,
                        damping=20.0,
                    )
                },
            )
        )
        contact_sensor = ContactSensor(
            ContactSensorCfg(
                prim_path="/World/Keyboard/Keys/.*",
                update_period=0.0,
                history_length=2,
                force_threshold=0.01,
                track_air_time=True,
            )
        )
        sim.reset()
        for _ in range(12):
            sim.step(render=False)
            robot.update(args.physics_dt)
            keyboard.update(args.physics_dt)
            contact_sensor.update(args.physics_dt, force_recompute=True)

        tip_names = (
            "left_hand_thumb_2_link",
            "left_hand_index_1_link",
            "left_hand_middle_1_link",
            "right_hand_thumb_2_link",
            "right_hand_index_1_link",
            "right_hand_middle_1_link",
        )
        tip_ids = {
            name: robot.find_bodies([name], preserve_order=True)[0][0]
            for name in tip_names
        }
        key_names = (
            ("t", "right_alt", "right_ctrl")
            if args.mode == "chord"
            else (args.target_key,)
        )
        key_ids = {
            name: keyboard.find_joints(
                [f"{_safe_key(name)}_joint"], preserve_order=True
            )[0][0]
            for name in key_names
        }
        report = {
            "schema_version": 1,
            "phase": "P2",
            "status": "inspected" if args.inspect_only else "diagnostic",
            "generated_at_unix": time.time(),
            "runtime": {
                "isaac_sim_path": os.environ.get("ISAAC_PATH", ""),
                "isaac_lab": getattr(isaaclab, "__version__", "unknown"),
                "isaac_lab_commit": subprocess.run(
                    ["git", "-C", os.environ.get("ISAACLAB_PATH", ""), "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
                "device": args.device,
                "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none",
                "robot_forward_offset_m": args.robot_forward_offset_m,
            },
            "robot": {
                "fixed_base": bool(robot.is_fixed_base),
                "joint_count": robot.num_joints,
                "body_count": robot.num_bodies,
                "body_names": list(robot.body_names),
                "joint_names": list(robot.joint_names),
                "jacobian_shape": list(robot.root_physx_view.get_jacobians().shape),
                "tip_body_ids": tip_ids,
                "tip_positions_world_m": {
                    name: robot.data.body_pos_w[0, body_id].tolist()
                    for name, body_id in tip_ids.items()
                },
            },
            "keyboard": {
                "joint_count": keyboard.num_joints,
                "target_key_joint_ids": key_ids,
                "target_key_positions_rad": {
                    name: float(keyboard.data.joint_pos[0, joint_id].item())
                    for name, joint_id in key_ids.items()
                },
                "contact_sensor_bodies": len(contact_sensor.body_names),
            },
            "action_graph": {
                "contract_version": "1.0.0",
                "joint_commands_in_graph": False,
                "low_level_boundary": "isaac_damped_least_squares_ik",
            },
        }
        if not args.inspect_only:
            temporary = tempfile.TemporaryDirectory(prefix="vskill-isaac-p2-")
            desktop = PtyDesktopWorker(temporary.name)
            desktop.start()
            try:
                from isaaclab.utils.math import quat_apply

                catalog = ActionModuleCatalog.from_directory(
                    Path(__file__).resolve().parents[4]
                    / "configs/action_graphs/modules"
                )
                compiler = KeyboardActionCompiler(catalog)
                planner = TypingPlanner()
                if args.mode == "chord":
                    # Press the shorter thumb/Alt first, then descend the
                    # independently flexing middle finger onto Ctrl.  The
                    # resulting held modifier set is still Ctrl+Alt when T is
                    # actuated, while the reverse order avoids disturbing a
                    # key already held by the longer finger.
                    plan = planner.plan_chord_detailed(
                        ["alt", "ctrl", "t"], maximum_duration_seconds=5.0
                    )
                    primary_executor = args.executor or "left.middle"
                    hand_name, finger_name = primary_executor.split(".", 1)
                    primary_hand = HandSide(hand_name)
                    primary_finger = Finger(finger_name)
                    plan = replace(
                        plan,
                        assignments=tuple(
                            replace(
                                assignment,
                                hand=primary_hand,
                                finger=primary_finger,
                            )
                            if assignment.role is KeyRole.PRIMARY
                            else assignment
                            for assignment in plan.assignments
                        ),
                    )
                    modifiers = plan.press_order[:-1]
                    primary_key = plan.press_order[-1]
                    active_modifiers = set(modifiers)
                    release_events = [
                        ExpectedKeyEvent(
                            PlannedKeyEventType.KEYUP,
                            primary_key,
                            tuple(sorted(active_modifiers)),
                        )
                    ]
                    for modifier in modifiers:
                        active_modifiers.discard(modifier)
                        release_events.append(
                            ExpectedKeyEvent(
                                PlannedKeyEventType.KEYUP,
                                modifier,
                                tuple(sorted(active_modifiers)),
                            )
                        )
                    plan = replace(
                        plan,
                        release_order=(primary_key, *modifiers),
                        expected_events=(*plan.expected_events[:3], *release_events),
                    )
                    graph = compiler.compile_chord(plan)
                    target_keys = plan.press_order
                    expected_timeline = tuple(
                        (
                            event.event_type.value,
                            event.key,
                            event.modifiers,
                        )
                        for event in plan.expected_events
                    )
                    executor_contract: object = [
                        {
                            "key": assignment.physical_key,
                            "executor": assignment.executor,
                            "role": assignment.role.value,
                        }
                        for assignment in plan.assignments
                    ]
                else:
                    if args.executor:
                        hand_name, finger_name = args.executor.split(".", 1)
                        hand, finger = HandSide(hand_name), Finger(finger_name)
                    else:
                        hand, finger = planner._hand_and_finger(args.target_key)
                    graph = compiler.compile_single_key(
                        Keystroke(
                            key=args.target_key,
                            modifiers=(),
                            hand=hand,
                            finger=finger,
                            expected_text="",
                        )
                    )
                    target_keys = (args.target_key,)
                    expected_timeline = (
                        (KeyEventType.KEYDOWN.value, args.target_key, ()),
                        (KeyEventType.KEYUP.value, args.target_key, ()),
                    )
                    executor_contract = f"{hand.value}.{finger.value}"
                adapter = IsaacDex3ActionAdapter(
                    torch_module=torch,
                    quat_apply=quat_apply,
                    sim=sim,
                    robot=robot,
                    keyboard=keyboard,
                    contact_sensor=contact_sensor,
                    desktop=desktop,
                    physics_dt=args.physics_dt,
                )
                graph_result = asyncio.run(
                    ActionGraphRunner(
                        adapter=adapter,
                        verifier=IndependentActionVerifier(),
                    ).run(graph)
                )
                releases = adapter.bridge.all_keys_up(
                    monotonic_time=adapter.physical_time + args.physics_dt,
                    reason="isaac_p2_shutdown",
                )
                adapter._emit_events(releases)
                counts = Counter(event.event_type.value for event in adapter.event_log)
                balanced = counts[KeyEventType.KEYDOWN.value] == counts[KeyEventType.KEYUP.value]
                observed_timeline = tuple(
                    (event.event_type.value, event.key, event.modifiers)
                    for event in adapter.event_log
                )
                event_timeline_verified = observed_timeline == expected_timeline
                event_duration_seconds = (
                    adapter.event_log[-1].monotonic_time
                    - adapter.event_log[0].monotonic_time
                    if adapter.event_log
                    else 0.0
                )
                configured_duration_limit = graph.metadata.get(
                    "maximum_duration_seconds"
                )
                duration_limit_seconds = (
                    None
                    if configured_duration_limit is None
                    else float(configured_duration_limit)
                )
                event_duration_verified = (
                    duration_limit_seconds is None
                    or event_duration_seconds <= duration_limit_seconds
                )
                key_metrics = {
                    key: {
                        "maximum_key_displacement_m": adapter.maximum_displacements[key],
                        "maximum_contact_force_n": adapter.maximum_forces[key],
                    }
                    for key in target_keys
                }
                succeeded = all(
                    (
                        graph_result.status is ActionStatus.SUCCEEDED,
                        all(
                            adapter.maximum_displacements[key]
                            >= adapter.bridge.config.press_threshold_m
                            for key in target_keys
                        ),
                        all(
                            0.01 < adapter.maximum_forces[key] <= 8.0
                            for key in target_keys
                        ),
                        balanced,
                        event_timeline_verified,
                        event_duration_verified,
                        not adapter.bridge.keys_down,
                    )
                )
                report["status"] = "passed" if succeeded else "failed"
                report["action_graph"].update(
                    {
                        "name": graph.name,
                        "version": graph.version,
                        "module_instances": list(graph.modules),
                        "node_count": len(graph.node_ids),
                        "run": graph_result.to_dict(),
                    }
                )
                report["fingertip_acceptance"] = {
                    "mode": args.mode,
                    "executor_contract": executor_contract,
                    "target_keys": list(target_keys),
                    "physics_sample_count": adapter.sample_count,
                    "key_metrics": key_metrics,
                    "minimum_action_errors_m": adapter.minimum_errors,
                    "ik_debug": adapter.ik_debug,
                    "event_counts": dict(counts),
                    "events_balanced": balanced,
                    "event_timeline_verified": event_timeline_verified,
                    "event_duration_seconds": event_duration_seconds,
                    "duration_limit_seconds": duration_limit_seconds,
                    "event_duration_verified": event_duration_verified,
                    "expected_event_timeline": [
                        {
                            "event_type": event_type,
                            "key": key,
                            "modifiers": list(modifiers),
                        }
                        for event_type, key, modifiers in expected_timeline
                    ],
                    "keys_down_after_shutdown": list(adapter.bridge.keys_down),
                    "event_timeline": [
                        {
                            "event_type": event.event_type.value,
                            "key": event.key,
                            "modifiers": list(event.modifiers),
                            "source": event.source,
                        }
                        for event in adapter.event_log
                    ],
                    "event_context_timeline": adapter.event_context_log,
                }
            finally:
                desktop.stop()
                temporary.cleanup()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except BaseException:
        traceback.print_exc()
        raise
    finally:
        simulation_app.close(wait_for_replicator=False, skip_cleanup=True)


if __name__ == "__main__":
    raise SystemExit(main())
