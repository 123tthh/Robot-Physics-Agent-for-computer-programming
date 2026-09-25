# Dex5-1 资产来源与仿真改动

本 Demo 的双手使用宇树官方 **Dex5-1** URDF 与 STL，不使用 Dex3 或其他五指手替代。基础机器人为本仓库原定的 G1 29 DoF、mode 15、5010 手腕。

## 可复现来源

- 官方仓库：[unitreerobotics/unitree_ros](https://github.com/unitreerobotics/unitree_ros/tree/7d6075f7f58588b189b940130e3edab3c839b2df)。固定提交：`7d6075f7f58588b189b940130e3edab3c839b2df`。
- 原始手模型：`robots/dexterous_hand_description/dex5_1/Dex5-URDF-L/Dex5-URDF-L.urdf` 和对应 `Dex5-URDF-R`。
- 锁文件：[`g1_29dof_5010_dex5_assets.json`](../configs/simulation/g1_29dof_5010_dex5_assets.json)。包含 80 个文件、34,962,841 字节的路径、长度、SHA-256、Git blob SHA-1，覆盖机器人原始 URDF、引用的网格、双手原始 URDF、42 个手部网格和许可证。
- 许可证以固定提交的 `LICENSE` 为准：**BSD-3-Clause**。分发原始或修改后的模型时保留该许可证和版权声明。

仓库根目录运行：

```bash
PYTHONPATH=src python3 -m vskill_os.simulation.dex5_assets \
  --fetch --compose --seating-fixture \
  --report artifacts/isaac-dex5/asset_audit.json
```

此命令只下载缺失文件。已有文件与锁定哈希不符会失败并保留文件，不会静默覆盖。离线复查时去掉 `--fetch`；无需 Isaac Sim 或其他第三方 Python 包。原始文件放在 `artifacts/simulation-assets/unitree_ros_7d6075f7/`，合成文件默认输出到 `artifacts/isaac-dex5/assets/g1_mode15_dex5.urdf`，同目录保留 `UNITREE_LICENSE.txt`。

## 坐标数与主动关节数

[制造商说明](https://www.unitree.com/Dex5-1/)标明每手 20 个自由度，其中 16 个主动、4 个耦合；非拇指末节与前一节耦合。原始 URDF 每手有 20 个旋转坐标，但未给出 `mimic` 关系。不能据此宣称每手有 20 个独立电机。

合成资产每手保留全部 20 个物理关节，添加 4 个末节 `mimic`。总计为 **29 身体坐标 + 40 手部坐标，其中手部为 32 主动 + 8 耦合**。命名为 `left_hand_index_0_joint` 等，包含 `thumb/index/middle/ring/little` 五指。非拇指 0 为侧摆，1、2 为主动屈曲，3 为耦合末节。

当前耦合采用线性近似：`q_distal = (distal_upper / proximal_upper) * q_proximal`，比例由各关节原始上限计算；这仅是可复现的仿真假设，制造商页面未提供完整传动函数，不能作为硬件标定结果。

本机 Isaac Sim 5.1 / Isaac Lab 2.3.2 实际导入已检查到 8 个 PhysX mimic schema。该版本 `convert_mimic_joints_to_normal_joints` 配置名具有误导性：Isaac Lab 将其值直接传给 `set_parse_mimic`，当前设为 `True` 才保留耦合。场景在加载后独立检查 schema 数量，而不按配置名推断导入成功。此导入结果只证明耦合被保留，不证明打字或坐姿动力学通过。

## 已独立复现的上游问题

| 原始模型问题 | 合成资产的显式处理 |
| --- | --- |
| 左手无名指侧摆关节名为 `Link_41L` | 统一为 `left_hand_ring_0_joint`；保留原始文件 |
| 右手非拇指 16 个关节 `effort=0` | 仿真中设为左手同类关节的 `0.93 Nm` |
| 右手 `Roll_41R`、`Roll_51R` 的 `velocity=0` | 仿真中设为对应左手约 `0.76794 rad/s` |
| 原始模型没有末节耦合 | 添加上述显式近似 `mimic`，共 8 个 |

组合器不修改上游文件，审计报告同时保留原始缺陷和源文件哈希。源模型包含左右手坐标方向差异，例如左拇指第二坐标为负向屈曲；控制器不能对双手盲目使用相同符号。

## 手腕安装与坐下支撑

手腕安装是仿真适配，不是宇树认证硬件组合。原生 Dex5 手指沿局部 `+Y` 伸出；适配器将其映射到 G1 手腕 `+X`，双手掌面朝下：

| 手 | 相对 G1 `wrist_yaw_link` 的 xyz (m) | rpy (rad) |
| --- | --- | --- |
| 左 | `(0.0415, 0.003, 0)` | `(0, 0, -π/2)` |
| 右 | `(0.0415, -0.003, 0)` | `(π, 0, π/2)` |

尚未验证真实转接板尺寸、整套装配惯量和硬件兼容性。所有源碰撞网格仍用于仿真。

可选 `--seating-fixture` 增加明确命名的 **两轴仿真支撑机构**：固定根 `seating_fixture_base` 通过前后平移关节连接 `seating_fixture_carriage`，再通过升降关节连接 `pelvis`。

| 额外支撑轴 | 名称 | 方向 | 范围 (m) | 最大力 (N) | 最大速度 (m/s) |
| --- | --- | --- | --- | --- | --- |
| 前后平移 | `seating_fixture_slide_joint` | `+X` | `[-0.30, 0]` | 2000 | 0.15 |
| 升降 | `seating_fixture_lift_joint` | `+Z` | `[-0.27, 0]` | 2000 | 0.10 |

这两个额外棱柱坐标不属于 G1 或 Dex5。启用时，组合资产含 80 个 link、79 个 joint、74 个不同网格文件。场景可先将机器人生成在椅前 `x=0.25 m`，弯腿后再控制平移轴移动 `-0.25 m`，避免初始站姿腿部与座面重叠。该支撑用于复现受支撑的坐下演示，不能作为自由基座平衡或真实坐下能力的证据。

## 验证范围

标准库资产检查验证源文件哈希、网格存在性、单根连通树、重复名称、关节集、正速度/力矩上限、8 个耦合关系以及额外支撑坐标的独立计数。单元测试另外覆盖三指替代、哈希篡改、损坏下载、路径逃逸、缺失网格、非法图和错误耦合。

这些检查证明模型准备过程可复现。实际 Isaac Sim 5.1 导入、接触、打字和姿态证据由对应场景运行和独立验收报告提供，资产检查本身不证明任务成功。
