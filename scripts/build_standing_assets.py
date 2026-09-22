"""Optional Isaac 6.0.1 asset rebuild, separate from the copied baseline assets.

Run through ``scripts/run_standing_demo.sh --build-assets``. This operation starts
Kit and must never run alongside the user's existing Isaac process.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from vskill_os.simulation.dex3_assets import (
    compose_mode15_g1_with_dex3,
    validate_g1_dex3_usd,
)
from vskill_os.adapters.isaac.runtime_guard import RuntimeLease


PINNED_UNITREE_COMMIT = "7d6075f7f58588b189b940130e3edab3c839b2df"
REPO_ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--unitree-ros-root", type=Path,
        default=REPO_ROOT / "artifacts/simulation-assets/unitree_ros_7d6075f7",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=REPO_ROOT / "artifacts/standing/rebuilt-assets",
    )
    args = parser.parse_args()
    runtime_root = Path(os.environ.get("ISAAC_PATH", "")).resolve()
    version_file = runtime_root / "VERSION"
    version = version_file.read_text().strip() if version_file.is_file() else "missing"
    if version.split("-", 1)[0].split("+", 1)[0] != "6.0.1":
        parser.error("use the standing launcher with a standalone Isaac Sim 6.0.1 installation")
    with RuntimeLease(runtime_root):
        return _build(args, runtime_root, version, parser)


def _build(args: argparse.Namespace, runtime_root: Path, version: str, parser: argparse.ArgumentParser) -> int:

    source = args.unitree_ros_root.resolve()
    robots = source / "robots" if (source / "robots").is_dir() else source
    hand = robots / "dexterous_hand_description/dex3_1"
    if not hand.is_dir():
        hand = robots / "dex3_1"  # Layout of the copied, pinned baseline bundle.
    source_commit = None
    if (source / ".git").exists():
        source_commit = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], text=True,
        ).strip()
        if source_commit != PINNED_UNITREE_COMMIT:
            parser.error(f"Unitree source must be pinned to {PINNED_UNITREE_COMMIT}; got {source_commit}")
    output_dir = args.output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        parser.error("output directory is not empty; choose a new --output-dir to preserve previous assets")
    output_dir.mkdir(parents=True, exist_ok=True)
    urdf = robots / "g1_description/g1_29dof_mode_15_with_dex3_1.urdf"
    composition = compose_mode15_g1_with_dex3(
        g1_urdf=robots / "g1_description/g1_29dof_mode_15.urdf",
        left_hand_urdf=hand / "dex3_1_l.urdf",
        right_hand_urdf=hand / "dex3_1_r.urdf",
        output=urdf,
    )

    # Imports must follow the runtime/process guard and SimulationApp creation.
    from isaacsim import SimulationApp

    app = SimulationApp({"headless": True})
    try:
        import omni.kit.app
        from pxr import Usd

        manager = omni.kit.app.get_app().get_extension_manager()
        for extension in ("omni.scene.optimizer.core", "isaacsim.robot.schema", "isaacsim.asset.importer.urdf"):
            manager.set_extension_enabled_immediate(extension, True)
        from isaacsim.asset.importer.urdf.impl import URDFImporter, URDFImporterConfig

        config = URDFImporterConfig()
        config.urdf_path = str(urdf)
        config.usd_path = str(output_dir)
        config.fix_base = True
        config.merge_fixed_joints = False
        config.allow_self_collision = False
        config.joint_drive_type = "force"
        config.joint_target_type = "position"
        config.override_joint_stiffness = 100.0
        config.override_joint_damping = 10.0
        config.run_multi_physics_conversion = False
        result = URDFImporter(config).import_urdf()
        if not result:
            raise RuntimeError("Isaac URDF importer returned no output asset")
        usd = Path(result).resolve()
        validation = validate_g1_dex3_usd(usd, require_pxr=True)
        stage = Usd.Stage.Open(str(usd))
        layer_files = sorted({Path(layer.realPath) for layer in stage.GetUsedLayers() if layer.realPath})
        report = {
            "isaac_sim_version": version,
            "isaac_sim_root": str(runtime_root),
            "unitree_pinned_commit": PINNED_UNITREE_COMMIT,
            "source_git_commit_verified": source_commit,
            "copied_sources_require_manifest_comparison": source_commit is None,
            "composite_urdf": composition.to_dict(),
            "usd_validation": validation.to_dict(),
            "output_usd": str(usd),
            "layers": [{"path": str(path), "sha256": _sha256(path)} for path in layer_files],
            "standing_typing_physics_verified": False,
            "note": "Asset conversion/structure validation is not a physical typing acceptance run.",
        }
        report_path = output_dir / "asset_build_report.json"
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({"output_usd": str(usd), "report": str(report_path)}, indent=2))
    finally:
        app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
