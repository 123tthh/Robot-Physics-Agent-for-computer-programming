"""CPU inference and exact observation codec for the pinned Unitree RL Lab donor.

This is a candidate-policy adapter, not a certified locomotion controller. It
does not connect to DDS, launch a simulator, write joints, or verify walking.
The 29-body-joint policy also controls arms; a typing skill cannot overlay it.
"""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path

from ..control.commands import RootVelocityCommand
from ..robot.g1 import G1Joint

TERMS = (("base_ang_vel", 3), ("projected_gravity", 3), ("velocity_commands", 3),
         ("joint_pos_rel", 29), ("joint_vel_rel", 29), ("last_action", 29))
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST = ROOT / "configs/robots/unitree_g1_velocity_candidate.json"


def vector(values, size, label):
    result = tuple(values)
    if len(result) != size or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in result):
        raise ValueError(f"{label} must contain {size} finite numbers")
    return result


def projected_gravity(quaternion_wxyz) -> tuple[float, float, float]:
    """R(q)^T @ [0,0,-1], matching Unitree's body-frame gravity observation."""
    w, x, y, z = vector(quaternion_wxyz, 4, "quaternion_wxyz")
    if abs(w*w+x*x+y*y+z*z-1.0) > 1e-3:
        raise ValueError("base quaternion must be normalized")
    return (2*(w*y-x*z), -2*(y*z+w*x), 2*(x*x+y*y)-1)


@dataclass(frozen=True)
class BodyPolicyState:
    time_s: float
    angular_velocity_body: tuple[float, ...]
    projected_gravity_body: tuple[float, ...]
    joint_positions_sdk: tuple[float, ...]
    joint_velocities_sdk: tuple[float, ...]

    def validate(self):
        vector((self.time_s,), 1, "time_s")
        if self.time_s < 0:
            raise ValueError("negative policy timestamp")
        vector(self.angular_velocity_body, 3, "body angular velocity")
        gravity = vector(self.projected_gravity_body, 3, "body projected gravity")
        if abs(sum(x*x for x in gravity)-1.0) > 1e-3:
            raise ValueError("projected gravity must be a unit vector")
        vector(self.joint_positions_sdk, 29, "SDK joint positions")
        vector(self.joint_velocities_sdk, 29, "SDK joint velocities")


class VelocityObservationCodec:
    """Term-major histories, each oldest→newest; never 5 whole frames stacked.

Per-frame terms total 96; each term has five samples, for 480 inputs. Official
C++ reset fills all five slots with the first observation; last_action stores
unscaled network outputs, while PD gains use SDK motor order.
"""
    def __init__(self, contract: dict):
        self.cfg = contract
        self.mapping = tuple(contract["joint_ids_map"])
        if len(self.mapping) != 29 or any(type(i) is not int for i in self.mapping) or set(self.mapping) != set(range(29)):
            raise ValueError("joint_ids_map must be a permutation of SDK joints 0..28")
        if contract["step_dt"] != 0.02:
            raise ValueError("this donor uses a 50 Hz control clock")
        self.default = vector(contract["default_joint_pos"], 29, "policy default pose")
        for name in ("stiffness", "damping"):
            if any(x < 0 for x in vector(contract[name], 29, name)):
                raise ValueError("PD gains must be nonnegative")
        if tuple(contract["observations"]) != tuple(name for name, _ in TERMS):
            raise ValueError("observation term order differs from the pinned donor")
        for name, size in TERMS:
            term = contract["observations"][name]
            if term["history_length"] != 5 or term.get("clip") is not None:
                raise ValueError("unsupported history/clip variant")
            vector(term["scale"], size, name+" scale")
        if set(contract["actions"]) != {"JointPositionAction"}:
            raise ValueError("unsupported action type")
        action = contract["actions"]["JointPositionAction"]
        if action.get("joint_ids") is not None or action.get("clip") is not None:
            raise ValueError("unsupported action selection or clipping")
        self.action_scale = vector(action["scale"], 29, "action scale")
        self.action_offset = vector(action["offset"], 29, "action offset")
        if self.action_offset != self.default:
            raise ValueError("action offsets differ from the default observation pose")
        self.ranges = contract["commands"]["base_velocity"]["ranges"]
        for name in ("lin_vel_x", "lin_vel_y", "ang_vel_z"):
            low, high = vector(self.ranges[name], 2, name)
            if low >= high:
                raise ValueError("invalid command range")
        self.reset()

    def reset(self):
        self.history = {name: deque(maxlen=5) for name, _ in TERMS}
        self.previous_action = (0.0,)*29
        self.previous_time = None

    def default_sdk_pose(self):
        return self.to_sdk_order(self.default)

    def to_sdk_order(self, values):
        values = vector(values, 29, "policy-order joints")
        result = [0.0]*29
        for policy_index, sdk_index in enumerate(self.mapping):
            result[sdk_index] = values[policy_index]
        return tuple(result)

    def observe(self, state: BodyPolicyState, command: RootVelocityCommand) -> tuple[float, ...]:
        state.validate()
        if self.previous_time is not None and abs(state.time_s-self.previous_time-0.02) > 1e-6:
            raise ValueError("policy input must advance exactly one 20 ms control period; reset after discontinuity")
        command_vector = (command.vx_mps, command.vy_mps, command.yaw_rate_rps)
        for name, value in zip(("lin_vel_x", "lin_vel_y", "ang_vel_z"), command_vector):
            if not self.ranges[name][0] <= value <= self.ranges[name][1]:
                raise ValueError(f"{name} command is outside donor training range")
        values = {
            "base_ang_vel": state.angular_velocity_body,
            "projected_gravity": state.projected_gravity_body,
            "velocity_commands": command_vector,
            "joint_pos_rel": tuple(state.joint_positions_sdk[sdk]-self.default[i] for i,sdk in enumerate(self.mapping)),
            "joint_vel_rel": tuple(state.joint_velocities_sdk[sdk] for sdk in self.mapping),
            "last_action": self.previous_action,
        }
        for name, _ in TERMS:
            scales = self.cfg["observations"][name]["scale"]
            scaled = tuple(a*b for a,b in zip(values[name],scales))
            count = 5 if self.previous_time is None else 1
            for _ in range(count):
                self.history[name].append(scaled)
        self.previous_time = state.time_s
        return tuple(value for name,_ in TERMS for sample in self.history[name] for value in sample)

    def accept_action(self, raw_actions) -> tuple[float, ...]:
        raw = vector(raw_actions, 29, "network action")
        targets = tuple(a*s+o for a,s,o in zip(raw,self.action_scale,self.action_offset))
        vector(targets,29,"processed targets")
        self.previous_action = raw
        return self.to_sdk_order(targets)


def load_candidate(path: Path = DEFAULT_MANIFEST, *, project_root: Path = ROOT) -> dict:
    manifest = json.loads(path.read_text())
    if manifest.get("schema") != "unitree_velocity_candidate" or manifest.get("version") != "1.0.0":
        raise ValueError("unknown candidate manifest")
    root = project_root.resolve()
    for info in manifest["files"].values():
        artifact = (root/info["project_path"]).resolve()
        if not artifact.is_relative_to(root):
            raise ValueError("artifact path escapes project")
        if hashlib.sha256(artifact.read_bytes()).hexdigest() != info["sha256"]:
            raise ValueError(f"candidate artifact hash mismatch: {artifact.name}")
    import yaml  # optional adapter dependency, never imported by the core codec
    deployment = root/manifest['files']['deploy.yaml']['project_path']
    if yaml.safe_load(deployment.read_text()) != manifest['contract']:
        raise ValueError("normalized contract differs from pinned upstream deploy.yaml")
    return manifest


class UnitreeVelocityCandidate:
    """Optional ONNX CPU adapter; caller retains all actuation/safety authority."""
    version = "1.0.0"
    def __init__(self, manifest: Path = DEFAULT_MANIFEST, *, project_root: Path = ROOT):
        self.manifest = load_candidate(manifest, project_root=project_root)
        self.codec = VelocityObservationCodec(self.manifest["contract"])
        import numpy as np
        import onnxruntime as ort
        self.np = np
        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(project_root/self.manifest["files"]["policy.onnx"]["project_path"]),
                                           sess_options=options, providers=["CPUExecutionProvider"])
        inputs, outputs = self.session.get_inputs(), self.session.get_outputs()
        if len(inputs)!=1 or (inputs[0].name,inputs[0].shape,inputs[0].type)!=("obs",[1,480],"tensor(float)"):
            raise ValueError("unexpected ONNX input signature")
        if len(outputs)!=1 or (outputs[0].name,outputs[0].shape,outputs[0].type)!=("actions",[1,29],"tensor(float)"):
            raise ValueError("unexpected ONNX output signature")
        if self.session.get_providers() != ["CPUExecutionProvider"]:
            raise ValueError("candidate inspection must use CPU only")

    def reset(self):
        self.codec.reset()

    def infer(self, state: BodyPolicyState, command: RootVelocityCommand) -> dict:
        observation = self.codec.observe(state, command)
        values = self.np.asarray([observation],dtype=self.np.float32)
        if not self.np.isfinite(values).all():
            raise ValueError("observation overflows float32")
        action = self.session.run(["actions"],{"obs":values})[0]
        if action.shape != (1,29):
            raise ValueError("unexpected inference output shape")
        raw = tuple(float(v) for v in action[0])
        targets = self.codec.accept_action(raw)
        return {"raw_actions_policy_order":raw, "joint_targets_sdk_order":targets,
                "joint_names_sdk_order":tuple(j.name.lower()+"_joint" for j in G1Joint),
                "observation_sha256":hashlib.sha256(values.tobytes()).hexdigest(),
                "physical_walking_verified":False,"joint_targets_applied":False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,default=DEFAULT_MANIFEST)
    parser.add_argument('--output',type=Path,default=Path('reports/unitree_policy/cpu_inference.json'))
    args=parser.parse_args(argv)
    model=UnitreeVelocityCandidate(args.manifest)
    traces=[]
    for label,command in (("stand",RootVelocityCommand()),("forward",RootVelocityCommand(.15,0,0))):
        model.reset()
        for step in range(6):
            state=BodyPolicyState(step*.02,(0.,0.,0.),(0.,0.,-1.),model.codec.default_sdk_pose(),(0.,)*29)
            result=model.infer(state,command)
            traces.append({"scenario":label,"step":step,**result})
    report={"schema":"unitree_candidate_cpu_inspection","version":1,
            "source_checkout_commit":model.manifest['source_checkout_commit'],
            "policy_sha256":model.manifest['files']['policy.onnx']['sha256'],
            "input_shape":[1,480],"output_shape":[1,29],"provider":"CPUExecutionProvider",
            "inference_calls":len(traces),"finite_outputs":True,"simulation_started":False,
            "physical_walking_verified":False,"target_mode15_dex3_compatible":False,
            "observation_source":"constant_body_state_fixture_not_physics","traces":traces}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({"cpu_inference_passed":True,"calls":len(traces),"report":str(args.output),"physical_walking_verified":False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
