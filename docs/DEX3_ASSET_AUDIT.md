# G1 29DoF mode 15 / 5010 + Dex3 资产审计

## 结论

已经收集并校验官方 Dex3-1 手部 URDF、G1 29DoF body URDF 和 Unitree Sim
IsaacLab 的固定基座 G1+Dex3 USD。资产分成两类使用：

- **目标 URDF/USD 已可用于 P1 仿真。** 由 `g1_29dof_mode_15.urdf` 与左右手
  `dex3_1_l/r.urdf` 组合，保留 5010 腕部和 mode 15 身体动力学，并已由本机
  Isaac Lab Importer 转为分层 USD；
- **官方现成 USD 只作为结构与 Isaac 配置参考。** 它的 default prim 是
  `g1_29dof_with_hand_rev_1_0`，对应 mode 5、4010 腕部，不能冒充本项目的
  mode 15 / 5010 动力学资产。

截至 2026-09-04，在检查到的 Unitree 官方 ROS、IsaacLab 和 USD 数据集中，没有
发现同时满足 mode 15、5010 腕部和双 Dex3 的现成成品 USD。

本资产包现在是 P1 默认资产。P1 已验证其加载、关节集合和双手关节运动；这只
允许它作为后续策略训练的仿真起点，不等于 Dex3 指尖触键或 sim-to-real 已通过。

## 来源与固定版本

| 项目 | 固定版本 | 用途 |
|---|---|---|
| [Unitree ROS](https://github.com/unitreerobotics/unitree_ros) | `7d6075f7f58588b189b940130e3edab3c839b2df` | mode 15 / 5010 身体、左右 Dex3-1、网格 |
| [Unitree Sim IsaacLab](https://github.com/unitreerobotics/unitree_sim_isaaclab) | `e30c25b1dffdf92ada1d6c8c1fe9a47bdde0fecc` | 固定基座/全身 Dex3 场景和 actuator 配置 |
| [官方 USD 数据集](https://huggingface.co/datasets/unitreerobotics/unitree_sim_isaaclab_usds) | `394cf2448f8a9ed815c77c701a761f3d1ff1c8fb` | 现成固定基座 USD 参考 |

Unitree 的 [G1 Description 表](https://github.com/unitreerobotics/unitree_ros/blob/master/robots/g1_description/README.md)
明确标注：`g1_29dof_mode_15` 是 5010 腕部，而现成
`g1_29dof_with_hand_rev_1_0` 是 mode 5 / 4010 腕部。Unitree Sim IsaacLab 的
[`robots/unitree.py`](https://github.com/unitreerobotics/unitree_sim_isaaclab/blob/main/robots/unitree.py)
则直接加载现成的固定基座与全身 Dex3 USD。

## 本地资产

资产位于 Git 忽略的 `artifacts/simulation-assets/`，避免把约 167 MB 二进制文件
提交到源码仓库：

```text
artifacts/simulation-assets/
├── unitree_ros_7d6075f7/robots/
│   ├── g1_description/
│   │   ├── g1_29dof_mode_15.urdf
│   │   ├── g1_29dof_mode_15_with_dex3_1.urdf
│   │   ├── g1_29dof_with_hand_rev_1_0.urdf
│   │   └── meshes/
│   └── dex3_1/
│       ├── dex3_1_l.urdf
│       ├── dex3_1_r.urdf
│       └── meshes/
└── unitree_sim_isaaclab_usds_394cf244/robots/
    └── g1-29dof-dex3-base-fix-usd/
        └── g1_29dof_with_dex3_base_fix.usd

artifacts/isaac-p1/assets/g1_mode15_dex3_p1/
├── g1_mode15_dex3_p1.usd
└── configuration/
    ├── g1_mode15_dex3_p1_base.usd
    ├── g1_mode15_dex3_p1_physics.usd
    ├── g1_mode15_dex3_p1_robot.usd
    └── g1_mode15_dex3_p1_sensor.usd
```

机器可读的来源、哈希和兼容性结论在
`configs/simulation/g1_29dof_5010_dex3_assets.json`。

## 校验结果

| 资产 | 结构结果 | SHA-256 | 判定 |
|---|---|---|---|
| 生成的 mode 15 + Dex3 URDF | 52 links、51 joints、29 body + 14 hand movable joints、48 mesh files | `e7b3231a1ca30c8c08f927dd860d41291f0b7640c33e5a978913092ffb619246` | **P1 目标 URDF 可用** |
| 转换的 mode 15 + Dex3 USD | 215 prims、52 physics joints、43 revolute joints、52 rigid bodies、51 collision shapes（Dex3 16）、52 个正质量、6 used layers | `be081c94bc93d8dc3fb8b7047847f753db5fe1ef2b60536508fed1d82581f230` | **OpenUSD + Isaac/PhysX 可用** |
| 官方 legacy G1+Dex3 URDF | 53 links、52 joints、29+14 movable joints | `97da67732d067c3147fc5fb7b7bafc8982718f4e7f8c92ff82266a4d9c07200d` | 仅参考；body 不兼容 |
| 官方 fixed-base USD | USDC、364 prims、55 physics joints、43 revolute joints、29+14 关节名；部分 frame 无显式正质量 | `d23b9bbc8b59cf20fa1983f55874f855a76e038b9e22eae39542e62be5b4ba3d` | OpenUSD 可用；仅作结构参考，动力学不兼容 |

URDF 校验覆盖唯一 link/joint 名、单根关节树、父子引用、旋转关节限位、全部相对
mesh 文件和精确的 29+14 可动关节集合。USD 已用 OpenUSD 实际打开 stage，并检查
default prim、物理关节、全部旋转关节名、刚体质量和碰撞几何；不是只检查扩展名或文件头。

```bash
# 已安装 OpenUSD/pxr 时会同时解析 USD stage
make inspect-dex3-assets

# 在 Isaac 或 OpenUSD Python 环境中要求完整 USD 解析，否则直接失败
PYTHONPATH=src python3 -m vskill_os.cli inspect-dex3-assets --require-pxr
```

G1 原始 URDF 的四个 IMU/相机/LiDAR frame 没有质量和视觉几何。组合器为这些
坐标系加入无碰撞、透明的微米级占位视觉与可忽略惯量，消除了 Isaac Importer
产生的悬空 `visuals` reference，同时保留 frame 名称和外参。

## 已通过与仍待验证

- 已通过：URDF 依赖闭合、分层 USD 解析、43 个主动关节精确集合、固定基座
  PhysX 启动、双 Dex3 14 轴运动、74 键接触/标识/标准朝向、三路 RTX 相机和键帽到 PTY；
- P1 明确关闭 G1 自碰撞，因此自碰撞过滤和手—机身碰撞调参尚不是已验证能力；
- 尚未将 Dex3 指尖目标 Adapter 接入 `press_key/press_chord` Skill；Agent 仍不得
  直接输出 14 维手部关节命令；
- 尚未完成真实触觉、关节方向台架对照和 sim-to-real 兼容性证明。

耐久化运行证据见 `reports/isaac_p1_acceptance.json`。
