# P1 简化房间与多工位导航资产

状态：静态资产底座 `DONE`；导航策略与 rollout `PLANNED`。

## 已完成的场景

P1 主 USD 已扩展为第一阶段导航可用的简化办公房间：

- 房间尺寸 8×6×2.8 m，米制、Z-up；
- 西侧 1.2×2.2 m 门洞，四个逻辑墙面，门洞拆分后共 6 个碰撞墙段；
- A/B/C 三个唯一工位；A 使用全量 G1 + 双 Dex3 + 74 键键盘 + 移动椅，B/C 使用静态低成本代理；
- `entry_center`、`entry_south`、`entry_north` 三个机器人出生点；
- 每个工位提供 `workstation_approach`、`chair_alignment`、`work_pose` 三类锚点，共 12 个锚点；
- 一个带 PhysX 碰撞的文件柜障碍；
- 机器人导航净空半径配置为 0.40 m。

## 模块边界

场景参数只在 [`../configs/simulation/p1_navigation_room.json`](../../configs/simulation/p1_navigation_room.json) 定义；[`../src/vskill_os/simulation/navigation_assets.py`](../../src/vskill_os/simulation/navigation_assets.py) 负责与 Isaac 无关的配置校验；[`../src/vskill_os/adapters/isaac/navigation_scene.py`](../../src/vskill_os/adapters/isaac/navigation_scene.py) 只负责 USD/PhysX authoring；[`../src/vskill_os/adapters/isaac/p1_scene.py`](../../src/vskill_os/adapters/isaac/p1_scene.py) 负责编排和动态证据收集。该拆分允许后续地图、规划器和 ROS 2/Isaac 导航 Adapter 替换，不把低层速度或关节命令暴露给 Agent。

## 验收与证据

主场景为 [`../artifacts/isaac-p1/scenes/p1_g1_dex3_keyboard.usda`](../../artifacts/isaac-p1/scenes/p1_g1_dex3_keyboard.usda)。

- `make isaac-p1`：在本机 Isaac Sim 5.1 / Isaac Lab 2.3.2 中构建场景，检查所有导航 prim、相机、机器人、键盘与椅子动力学，并写入 [`../reports/isaac_p1_acceptance.json`](../../reports/isaac_p1_acceptance.json)；
- `make isaac-p1-validate`：在新进程中重开 USD，检查墙体与障碍碰撞、唯一工位 ID、唯一目标工位和完整锚点，写入 [`../reports/isaac_p1_workstation_validation.json`](../../reports/isaac_p1_workstation_validation.json)；
- 总览渲染位于 [`../reports/isaac_p1_renders/overview_camera.png`](../../reports/isaac_p1_renders/overview_camera.png)。

## 尚未声明的能力

资产存在不等于导航成功。当前阶段没有声明固定地图已生成、全局/局部规划器已接入、动态障碍可重规划、G1 已从入口走到工位，也没有由控制器直接写入 `workstation_reached`。这些能力属于 P5-T02 的后续导航 rollout，最终状态必须由独立定位、目标姿态与稳定性证据验证。

本项目的 OpenUSD 检查是项目级拓扑/物理验收；由于本机仍缺正式 Asset Validator 与 SimReady Foundation，它不构成 NVIDIA SimReady 正式认证。
