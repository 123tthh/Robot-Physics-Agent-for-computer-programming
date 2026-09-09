"""Pinned simulation-asset manifest kept independent from Isaac imports."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
from typing import Any, Mapping

from ..config import load_json
from ..robot.g1 import G1DeploymentConfig, G1Joint
from .dex3_assets import EXPECTED_DEX3_JOINTS
from .navigation_assets import NavigationRoom
from .workstation_assets import WorkstationAssetChain


@dataclass(frozen=True, slots=True)
class SourceAsset:
    repository: str
    commit: str
    path: str
    sha256: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceAsset":
        result = cls(
            repository=str(data["repository"]),
            commit=str(data["commit"]),
            path=str(data["path"]),
            sha256=str(data["sha256"]),
        )
        if len(result.commit) != 40 or len(result.sha256) != 64:
            raise ValueError("asset commit and sha256 must be fully pinned")
        return result

    def verify_file(self, path: str | Path) -> None:
        source = Path(path)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest != self.sha256:
            raise ValueError(f"asset hash mismatch for {source}: {digest}")


@dataclass(frozen=True, slots=True)
class G1SimulationManifest:
    name: str
    backend: str
    isaac_sim_version: str
    isaac_lab_commit: str
    integration_repository: str
    integration_commit: str
    robot_config: str
    robot_asset: SourceAsset
    fixed_base: bool
    joint_order: tuple[str, ...]
    hand_joint_order: tuple[str, ...]
    runtime_asset: Mapping[str, Any]
    hand_assets_enabled: bool
    interaction_driver: str
    keyboard: Mapping[str, Any]
    workstation_chain: Mapping[str, Any]
    navigation_room: Mapping[str, Any]
    validation: Mapping[str, Any]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "G1SimulationManifest":
        simulator = dict(data["simulator"])
        robot = dict(data["robot"])
        interaction = dict(data["interaction"])
        manifest = cls(
            name=str(data["name"]),
            backend=str(simulator["backend"]),
            isaac_sim_version=str(simulator["isaac_sim_version"]),
            isaac_lab_commit=str(simulator["isaac_lab_commit"]),
            integration_repository=str(simulator["integration_repository"]),
            integration_commit=str(simulator["integration_commit"]),
            robot_config=str(robot["deployment_config"]),
            robot_asset=SourceAsset.from_dict(robot["asset"]),
            fixed_base=bool(robot["fixed_base"]),
            joint_order=tuple(str(item) for item in robot["joint_order"]),
            hand_joint_order=tuple(str(item) for item in robot.get("hand_joint_order", ())),
            runtime_asset=dict(robot.get("runtime_asset", {})),
            hand_assets_enabled=bool(interaction["hand_assets_enabled"]),
            interaction_driver=str(interaction["driver"]),
            keyboard=dict(data["keyboard"]),
            workstation_chain=dict(data["workstation_chain"]),
            navigation_room=dict(data["navigation_room"]),
            validation=dict(data.get("validation", {})),
        )
        manifest.validate()
        return manifest

    @classmethod
    def from_json(cls, path: str | Path) -> "G1SimulationManifest":
        return cls.from_dict(load_json(path))

    def validate(self, *, root: str | Path | None = None) -> None:
        if self.backend != "isaac_sim":
            raise ValueError("P1 manifest backend must be isaac_sim")
        if len(self.isaac_lab_commit) != 40 or len(self.integration_commit) != 40:
            raise ValueError("Isaac Lab and integration revisions must be fully pinned")
        if self.hand_assets_enabled:
            if self.interaction_driver != "isaac_physx_keycap_contact":
                raise ValueError("P1 with Dex3 requires the Isaac PhysX keycap contact driver")
            if self.hand_joint_order != EXPECTED_DEX3_JOINTS:
                raise ValueError("P1 Dex3 joint order must match the 14-joint semantic order")
            for field in ("urdf", "urdf_sha256", "usd", "usd_sha256", "usd_default_prim"):
                if field not in self.runtime_asset:
                    raise ValueError(f"P1 Dex3 runtime asset is missing {field}")
            if len(str(self.runtime_asset["urdf_sha256"])) != 64 or len(
                str(self.runtime_asset["usd_sha256"])
            ) != 64:
                raise ValueError("P1 runtime URDF and USD hashes must be fully pinned")
        elif self.interaction_driver != "replayable_keycap_probe":
            raise ValueError("P1 without hand assets must use the replayable keycap probe")
        expected_order = tuple(joint.name.lower() for joint in G1Joint)
        if self.joint_order != expected_order:
            raise ValueError("simulation manifest joint order does not match G1 SDK order")
        if not self.fixed_base:
            raise ValueError("P1 simulation requires a fixed-base G1")
        press = float(self.keyboard["press_threshold_m"])
        release = float(self.keyboard["release_threshold_m"])
        if press <= release:
            raise ValueError("keyboard press threshold must exceed release threshold")
        key_count = int(self.keyboard.get("key_count", 0))
        if key_count <= 0 or int(self.keyboard.get("legend_count", 0)) != key_count:
            raise ValueError("P1 keyboard must provide one visible legend per key")
        if self.keyboard.get("operator_side") != "negative_x":
            raise ValueError("P1 keyboard operator side must face the robot at negative X")
        for field in (
            "head_camera_resolution",
            "keyboard_camera_resolution",
            "overview_camera_resolution",
        ):
            resolution = self.keyboard.get(field)
            if (
                not isinstance(resolution, list)
                or len(resolution) != 2
                or any(int(value) <= 0 for value in resolution)
            ):
                raise ValueError(f"P1 keyboard manifest has invalid {field}")
        chain = self.workstation_chain
        if int(chain.get("stage_count", 0)) != 6:
            raise ValueError("P1 workstation asset chain must expose six stages")
        if chain.get("chair_asset_type") != "passive_mobile_office_chair_5caster":
            raise ValueError("P1 workstation requires a passive five-caster office chair")
        if int(chain.get("caster_count", 0)) != 5 or int(
            chain.get("passive_dof_count", 0)
        ) != 10:
            raise ValueError("P1 chair must expose five casters and ten passive joints")
        if not 0.43 <= float(chain.get("seat_top_height_m", 0.0)) <= 0.50:
            raise ValueError("P1 chair seat height must match the G1 sitting range")
        if chain.get("world_fixed") is not False:
            raise ValueError("P1 mobile chair must have a free world root")
        if chain.get("mobility_probe_required") is not True:
            raise ValueError("P1 mobile chair must require a measured mobility probe")
        if chain.get("motion_policy_status") != "not_claimed_asset_readiness_only":
            raise ValueError("P1 must not claim an unimplemented navigation/sit policy")
        navigation = self.navigation_room
        if navigation.get("room_shell_required") is not True:
            raise ValueError("P1 navigation assets require a room shell")
        if int(navigation.get("workstation_count", 0)) < 3:
            raise ValueError("P1 navigation room requires at least three workstations")
        if int(navigation.get("spawn_count", 0)) < 2:
            raise ValueError("P1 navigation room requires multiple spawn points")
        if navigation.get("policy_status") != "not_claimed_asset_readiness_only":
            raise ValueError("P1 navigation assets must not claim a navigation policy")
        if root is not None:
            config_path = Path(root) / self.robot_config
            profile = G1DeploymentConfig.from_json(config_path).simulation_profile()
            if profile.mode_machine != 15 or profile.body_dof != 29 or profile.wrist_motor != "5010":
                raise ValueError("P1 manifest must resolve to G1 29DoF mode 15 / 5010")
            asset_chain = WorkstationAssetChain.from_json(Path(root) / str(chain["config"]))
            if not math.isclose(
                asset_chain.summary()["seat_top_height_m"],
                float(chain["seat_top_height_m"]),
                abs_tol=1.0e-9,
            ):
                raise ValueError("P1 manifest and workstation config seat heights differ")
            navigation_config = NavigationRoom.from_json(
                Path(root) / str(navigation["config"])
            )
            if navigation_config.summary()["workstation_count"] != int(
                navigation["workstation_count"]
            ):
                raise ValueError("P1 manifest and navigation-room workstation counts differ")
            if navigation_config.target_workstation.id != navigation.get(
                "target_workstation_id"
            ):
                raise ValueError("P1 manifest and navigation-room targets differ")

    def capability(self) -> dict[str, Any]:
        return {
            "robot": "unitree_g1",
            "body_dof": 29,
            "mode_machine": 15,
            "wrist_motor": "5010",
            "fixed_base": self.fixed_base,
            "hand_assets": "dual_dex3_1" if self.hand_assets_enabled else "deferred",
            "hand_dof": len(self.hand_joint_order),
            "interaction_driver": self.interaction_driver,
            "workstation_asset_chain": self.workstation_chain.get("config"),
            "chair_asset_type": self.workstation_chain.get("chair_asset_type"),
            "chair_passive_dof": self.workstation_chain.get("passive_dof_count"),
            "navigation_room": self.navigation_room.get("config"),
            "navigation_workstation_count": self.navigation_room.get(
                "workstation_count"
            ),
            "physics_validation": self.validation.get(
                "physics_status", "pending_external_isaac_runtime"
            ),
            "acceptance_report": self.validation.get("acceptance_report"),
        }

    def verify_acceptance_report(self, path: str | Path) -> Mapping[str, Any]:
        """Validate durable P1 evidence without importing Isaac dependencies."""

        report = load_json(path)
        failures: list[str] = []
        runtime = dict(report.get("runtime", {}))
        robot = dict(report.get("robot", {}))
        scene = dict(report.get("scene", {}))
        bridge = dict(report.get("keyboard_bridge", {}))
        workstation = dict(report.get("workstation_asset_chain", {}))
        navigation = dict(report.get("navigation_environment", {}))
        if report.get("schema_version") != 1 or report.get("phase") != "P1":
            failures.append("unexpected acceptance-report schema or phase")
        if report.get("status") != "passed":
            failures.append("Isaac P1 acceptance status is not passed")
        if runtime.get("isaac_sim") != self.isaac_sim_version:
            failures.append("Isaac Sim version differs from the pinned manifest")
        if runtime.get("isaac_lab_commit") != self.isaac_lab_commit:
            failures.append("Isaac Lab commit differs from the pinned manifest")
        if robot.get("asset_sha256") != self.runtime_asset.get("usd_sha256"):
            failures.append("accepted USD hash differs from the pinned runtime asset")
        if robot.get("joint_count") != 43:
            failures.append("accepted robot does not expose 43 movable joints")
        if robot.get("body_joint_count") != 29 or robot.get("dex3_joint_count") != 14:
            failures.append("accepted body/Dex3 joint partition is not 29+14")
        if robot.get("rigid_body_count") != 52:
            failures.append("accepted G1+Dex3 articulation does not contain 52 rigid bodies")
        if robot.get("positive_mass_count") != robot.get("rigid_body_count"):
            failures.append("not every accepted rigid body has positive mass")
        if float(robot.get("minimum_mass_kg", 0.0)) <= 0.0:
            failures.append("accepted articulation contains non-positive mass")
        if not robot.get("inertia_finite") or not robot.get("inertia_positive_diagonal"):
            failures.append("accepted articulation has invalid inertia")
        if int(robot.get("collision_shape_count", 0)) <= 0:
            failures.append("accepted articulation has no collision shapes")
        if float(robot.get("dex3_min_motion_rad", 0.0)) <= 1.0e-4:
            failures.append("not every Dex3 joint produced measurable motion")
        if not robot.get("faces_keyboard"):
            failures.append("accepted robot heading does not face the keyboard")
        key_count = int(self.keyboard.get("key_count", 0))
        if scene.get("keyboard_key_count") != key_count:
            failures.append("accepted keyboard key count differs from the manifest")
        if scene.get("keyboard_prismatic_joint_count") != key_count:
            failures.append("not every accepted key has an independent travel joint")
        if scene.get("contact_sensor_body_count") != key_count:
            failures.append("not every accepted key has contact reporting")
        if scene.get("keyboard_legend_count") != key_count:
            failures.append("not every accepted key has a visible legend")
        if int(scene.get("keyboard_legend_lit_pixel_count", 0)) <= key_count:
            failures.append("accepted key legends contain no useful geometry")
        if not scene.get("keyboard_layout_operator_side_valid"):
            failures.append("keyboard rows are reversed relative to the robot/operator")
        if not scene.get("keyboard_left_right_valid"):
            failures.append("keyboard columns are mirrored relative to the robot/operator")
        if scene.get("keyboard_operator_side") != self.keyboard.get("operator_side"):
            failures.append("keyboard operator side differs from the pinned manifest")
        camera_specs = {
            "head_camera": self.keyboard.get("head_camera_resolution"),
            "keyboard_camera": self.keyboard.get("keyboard_camera_resolution"),
            "overview_camera": self.keyboard.get("overview_camera_resolution"),
        }
        for camera_name, resolution in camera_specs.items():
            camera = dict(scene.get(camera_name, {}))
            if not camera.get("initialized") or not camera.get("finite"):
                failures.append(f"{camera_name} did not produce finite initialized output")
            if float(camera.get("std", 0.0)) <= 0.0:
                failures.append(f"{camera_name} output is empty or constant")
            expected_shape = [1, int(resolution[1]), int(resolution[0]), 3]
            if camera.get("shape") != expected_shape:
                failures.append(f"{camera_name} resolution differs from the pinned manifest")
            artifact = Path(str(camera.get("artifact", "")))
            if not artifact.is_file():
                failures.append(f"{camera_name} render artifact is missing")
            elif hashlib.sha256(artifact.read_bytes()).hexdigest() != camera.get(
                "artifact_sha256"
            ):
                failures.append(f"{camera_name} render artifact hash differs from the report")
        event_counts = dict(bridge.get("event_counts", {}))
        if event_counts.get("keydown", 0) <= 0 or event_counts.get("keydown") != event_counts.get("keyup"):
            failures.append("physical keyboard events are absent or unbalanced")
        if not bridge.get("pty_output_observed"):
            failures.append("physical keyboard path did not produce PTY output")
        if bridge.get("keys_down_after_shutdown"):
            failures.append("accepted run left keys pressed after shutdown")
        sources = {
            bridge.get("physics_sample_source"),
            bridge.get("event_source"),
            bridge.get("desktop_source"),
        }
        if len(sources) != 3 or None in sources:
            failures.append("physics, event, and desktop evidence sources are not separated")
        if workstation.get("stage_count") != 6:
            failures.append("accepted workstation does not expose all six asset stages")
        if workstation.get("chair_asset_type") != self.workstation_chain.get(
            "chair_asset_type"
        ):
            failures.append("accepted workstation chair type differs from the manifest")
        if workstation.get("caster_count") != 5:
            failures.append("accepted mobile chair does not contain five casters")
        if workstation.get("passive_dof_count") != 10:
            failures.append("accepted mobile chair does not expose ten passive joints")
        if workstation.get("chair_swivel_joint_count") != 5 or workstation.get(
            "chair_wheel_joint_count"
        ) != 5:
            failures.append("accepted chair swivel/rolling joint topology is incomplete")
        if workstation.get("chair_rigid_body_count") != 11:
            failures.append("accepted chair rigid-body topology is incomplete")
        if workstation.get("chair_world_fixed") is not False:
            failures.append("accepted mobile chair is fixed to the world")
        if not 0.43 <= float(workstation.get("chair_seat_top_height_m", 0.0)) <= 0.50:
            failures.append("accepted chair seat height is outside the G1 sitting range")
        mobility = dict(workstation.get("chair_mobility_probe", {}))
        if not mobility.get("passed"):
            failures.append("accepted mobile chair did not pass the PhysX mobility probe")
        if float(mobility.get("displacement_m", 0.0)) < float(
            mobility.get("minimum_displacement_m", float("inf"))
        ):
            failures.append("accepted mobile chair displacement is below its threshold")
        if not mobility.get("sliding_dominant"):
            failures.append("accepted mobile chair motion is not sliding-dominant")
        if float(mobility.get("rolling_contribution_ratio", float("inf"))) > float(
            mobility.get("maximum_rolling_contribution_ratio", 0.0)
        ):
            failures.append("accepted mobile chair rolling contribution exceeds its limit")
        if not workstation.get("required_prims_present"):
            failures.append("accepted workstation is missing required USD prims")
        if workstation.get("motion_policy_status") != "not_claimed_asset_readiness_only":
            failures.append("P1 acceptance improperly claims a navigation/sit policy")
        if navigation.get("logical_wall_count") != 4:
            failures.append("accepted navigation room does not expose four logical walls")
        if navigation.get("wall_collider_count") != 6:
            failures.append("accepted navigation room doorway/wall topology is incomplete")
        if navigation.get("workstation_count") != self.navigation_room.get(
            "workstation_count"
        ):
            failures.append("accepted navigation-room workstation count differs")
        if navigation.get("target_workstation_id") != self.navigation_room.get(
            "target_workstation_id"
        ):
            failures.append("accepted navigation-room target workstation differs")
        if navigation.get("spawn_count") != self.navigation_room.get("spawn_count"):
            failures.append("accepted navigation-room spawn count differs")
        if not navigation.get("required_prims_present"):
            failures.append("accepted navigation room is missing required USD prims")
        if navigation.get("policy_status") != "not_claimed_asset_readiness_only":
            failures.append("P1 acceptance improperly claims a navigation policy")
        if failures:
            raise ValueError("; ".join(failures))
        return report
