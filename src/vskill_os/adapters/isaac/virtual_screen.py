"""Runtime screen texture and a camera rigidly attached to the robot head."""
import hashlib
import json
from pathlib import Path


class IsaacVirtualScreen:
    def __init__(self, stage, output: Path, *, display_x: float):
        import omni.ui as ui
        from pxr import Sdf, UsdGeom, UsdShade
        from isaaclab.sensors import Camera, CameraCfg
        import isaaclab.sim as sim_utils
        from ..virtual_computer import VirtualComputer

        self.output = Path(output)
        self.computer = VirtualComputer(self.output)
        self.provider = ui.DynamicTextureProvider('vskill_terminal')
        path = '/World/VirtualScreen'
        mesh = UsdGeom.Mesh.Define(stage, path)
        x, w, bottom, top = display_x - .0105, .505 / 2, .815, 1.105
        mesh.CreatePointsAttr([(x, w, bottom), (x, -w, bottom), (x, -w, top), (x, w, top)])
        mesh.CreateFaceVertexCountsAttr([4])
        mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
        mesh.CreateSubdivisionSchemeAttr('none')
        UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('st', Sdf.ValueTypeNames.TexCoord2fArray, 'vertex').Set([(0, 0), (1, 0), (1, 1), (0, 1)])
        material = UsdShade.Material.Define(stage, path + '/Material')
        surface = UsdShade.Shader.Define(stage, path + '/Material/Surface')
        surface.CreateIdAttr('UsdPreviewSurface')
        surface.CreateInput('roughness', Sdf.ValueTypeNames.Float).Set(1.)
        surface.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set((0., 0., 0.))
        surface.CreateInput('specularColor', Sdf.ValueTypeNames.Color3f).Set((0., 0., 0.))
        texture = UsdShade.Shader.Define(stage, path + '/Material/Texture')
        texture.CreateIdAttr('UsdUVTexture')
        texture.CreateInput('file', Sdf.ValueTypeNames.Asset).Set('dynamic://vskill_terminal')
        texture.CreateInput('sourceColorSpace', Sdf.ValueTypeNames.Token).Set('sRGB')
        texture.CreateOutput('rgb', Sdf.ValueTypeNames.Float3)
        reader = UsdShade.Shader.Define(stage, path + '/Material/UV')
        reader.CreateIdAttr('UsdPrimvarReader_float2')
        reader.CreateInput('varname', Sdf.ValueTypeNames.Token).Set('st')
        reader.CreateOutput('result', Sdf.ValueTypeNames.Float2)
        texture.CreateInput('st', Sdf.ValueTypeNames.Float2).ConnectToSource(reader.ConnectableAPI(), 'result')
        surface.CreateInput('emissiveColor', Sdf.ValueTypeNames.Color3f).ConnectToSource(texture.ConnectableAPI(), 'rgb')
        material.CreateSurfaceOutput().ConnectToSource(surface.ConnectableAPI(), 'surface')
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)
        self.camera = Camera(CameraCfg(prim_path='/World/ComputerCamera',
            width=1280, height=720, data_types=['rgb', 'distance_to_image_plane'],
            update_latest_camera_pose=True,
            offset=CameraCfg.OffsetCfg(pos=(.08, 0., .49), rot=(.99875026, 0., .04997917, 0.), convention='world'),
            spawn=sim_utils.PinholeCameraCfg(focal_length=10., clipping_range=(.02, 20.))))
        self.camera_frames = []
        self.publish(0.)

    def sync_mount(self, robot):
        # Fabric updates rigid-body poses without updating the USD hierarchy.
        # Compose a constant head-to-camera transform from measured body pose;
        # never aim the camera at the screen or alter the robot's root.
        import torch
        from isaaclab.utils.math import quat_apply, quat_mul
        body = robot.find_bodies(['head_link'])[0][0]
        parent_q = robot.data.body_link_quat_w[:, body]
        offset = torch.tensor([[.08, 0., .49]], device=robot.device)
        mount_q = torch.tensor([[.99875026, 0., .04997917, 0.]], device=robot.device)
        position = robot.data.body_link_pos_w[:, body] + quat_apply(parent_q, offset)
        self.camera.set_world_poses(position, quat_mul(parent_q, mount_q), convention='world')

    def publish(self, time_s):
        pixels, changed = self.computer.snapshot(time_s)
        if changed:
            self.provider.set_data_array(pixels, [pixels.shape[1], pixels.shape[0]])

    def capture(self, time_s, label):
        import numpy as np
        from PIL import Image
        self.camera.update(.05, force_recompute=True)
        rgb = self.camera.data.output['rgb'][0, :, :, :3].cpu().numpy().astype('uint8')
        depth = self.camera.data.output['distance_to_image_plane'][0].cpu().numpy()
        rgb_path = self.output / f'camera-{label}.png'
        depth_path = self.output / f'depth-{label}.npy'
        Image.fromarray(rgb).save(rgb_path)
        np.save(depth_path, depth)
        self.camera_frames.append({'label': label, 'simulation_time_s': time_s,
            'rgb': str(rgb_path), 'rgb_sha256': hashlib.sha256(rgb_path.read_bytes()).hexdigest(),
            'depth': str(depth_path), 'depth_sha256': hashlib.sha256(depth_path.read_bytes()).hexdigest(),
            'intrinsic_matrix': self.camera.data.intrinsic_matrices[0].tolist(),
            'position_w': self.camera.data.pos_w[0].tolist(),
            'quaternion_w_ros': self.camera.data.quat_w_ros[0].tolist()})

    def close(self):
        result = self.computer.close()
        result['camera_frames'] = self.camera_frames
        result['camera_mount'] = {'body': '/World/G1/head_link', 'position_local_m': [.08, 0., .49],
                                  'quaternion_local_wxyz': [.99875026, 0., .04997917, 0.],
                                  'pose_source': 'measured_physx_link_pose_times_constant_mount'}
        (self.output / 'evidence.json').write_text(json.dumps(result, indent=2) + '\n')
        return result
