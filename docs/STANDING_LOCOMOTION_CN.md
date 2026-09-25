# 站立行走、导航和到位验证

此模块把「走到工作台前并稳定站立」实现为有限时域的速度 Skill。Agent 只指定平面位置和朝向，速度 Skill 发出 `vx / vy / yaw_rate`；腿部关节动作由独立的步行策略 Adapter 产生。姿态、接触和停止由传感器轨迹验证。`controller completed`、直接设置机器人根位姿、或 CPU 平面轨迹都不能作为真实行走证明。

实现位于 `src/vskill_os/control/standing_locomotion.py`，只依赖 Python 标准库及现有领域模型。没有导入 Isaac、Torch、ROS 或 CUDA。

## 当前完成与证据范围

- 已实现：SE(2) 位置/朝向追踪、身体坐标系速度、向量速度限幅、线性/角加速度限幅、接近目标时减速、超时停止。
- 已实现：独立站立守卫，检查支撑接触、躯干倾斜、基座高度、支撑脚滑动、碰撞、速度失控、时钟陈旧及位姿跳变。
- 已实现：到位后连续 0.5 秒双脚支撑、低速、低倾角验证；发出停止后再次独立验证稳定停止。
- 已实现：本地策略权重、机器人资产、模式、手部负载、关节顺序、观测尺度、动作尺度、训练提交和版本契约。
- 已实现：种子固定的 CPU 运动学模型、可重放报告和故障测试。其 `evidence_mode=synthetic`，`physical_walking_verified=false` 始终成立。
- 待完成：mode 15 / 5010 + 双 Dex3-1 的兼容权重，以及浮动基座 Isaac 步行策略 Adapter 和其物理验收。当前固定基座敲键盘场景不能直接执行行走。

## 本地策略资产检查

2026-09-22 的首次搜索仅覆盖 IsaacLab 和本项目资产，没有覆盖 `/home/descfly/projects`。2026-09-25 已在 `/home/descfly/projects/unitree_rl_lab` 找到官方 G1 29DOF 行走 ONNX，并实现精确观察编码及 CPU 推理。原策略使用 rev1.0 资产，当前双 Dex3 目标资产仍需物理兼容性验收。详见 [Unitree 策略定位与接入](UNITREE_LOCOMOTION_CANDIDATES_CN.md)。

找到的材料如下：

| 材料 | 判定 |
| --- | --- |
| `configs/robots/g1_29dof_mode15_policy.example.json` | 模式描述示例；仿真版本和训练提交仍为 `PIN_BEFORE_USE`，没有权重 |
| `configs/robots/g1_29dof_mode15_seated_policy.example.json` | 坐姿移动示例，不能作为站立步行策略 |
| `artifacts/standing-training-verified/checkpoint.json` 与 `artifacts/standing-training-cpu/checkpoint.json` | 双食指敲键盘的 synthetic 训练结果，不是腿部步行策略 |
| IsaacLab `source/isaaclab_tasks/isaaclab_tasks/manager_based/locomotion/velocity/config/g1/rough_env_cfg.py` | G1 速度任务配置；使用 `G1_MINIMAL_CFG`，不是已验证的本项目 mode15 + Dex3 模型 |
| IsaacLab `source/isaaclab_assets/isaaclab_assets/robots/unitree.py` | `G1_MINIMAL_CFG` 指向远端 `Robots/Unitree/G1/g1_minimal.usd`；配置中的 `torso_joint` 等命名与本项目 29 轴身体契约不同 |

本模块的导航测试仍使用 CPU 运动学。Unitree 候选单独做 CPU 推理，不把推理结果当作行走证据；独立 Isaac 6 固定基座触键测试见工位反馈报告。

## 可复现 CPU 演示

在仓库根目录执行：

```bash
PYTHONPATH=src python3 -m vskill_os.control.standing_locomotion \
  --goal-x 0.4 --goal-y 0.1 --goal-yaw 0.2 --seed 7 \
  --output reports/standing/navigation_synthetic.json

PYTHONPATH=src python3 -m unittest discover -s tests -p test_standing_locomotion.py -v
```

同一 seed、目标和版本得到相同结果。报告保存全部观测和速度命令、独立验证、停止结果及策略证据。CPU 模型积分平面运动，并不模拟腿部、步态、足底摩擦或全身动力学。

## 与完整任务组合

```python
from vskill_os.control.standing_locomotion import (
    KinematicWalkingAdapter, SE2Goal, StandingNavigationSkill,
)

navigation = StandingNavigationSkill(KinematicWalkingAdapter(seed=7))
result = navigation.run(SE2Goal(x_m=0.4, y_m=0.1, yaw_rad=0.2))
assert result.succeeded and result.arrived and result.stabilized
assert result.stop_verified
assert not result.physical_walking_verified  # CPU 演示的明确边界
```

工作台完整流程应为：清晰无障碍路线 → 步行 Skill → 到位与稳定验证 → 键盘标定/视觉定位 → 双手敲击 → 屏幕反馈与纠错。真实场景只有 `physical_walking_verified=true` 才能把「已走到工作台」记入物理任务状态。不能用 synthetic 的 `succeeded=true` 授予物理前置条件。

当前模块只追踪给定的 SE(2) 目标，要求外部路线规划已保证可通行；它不是障碍地图构建器。碰撞守卫负责异常停止，不能替代全局避障规划。

## 物理 Adapter 契约

`LocomotionPolicyAdapter` 必须提供：

```text
evidence_mode = "physical"
checkpoint: PolicyCheckpoint
robot_asset_path: 本地机器人资产路径
runtime_version: 与策略训练/验证清单匹配的版本

observe() -> LocomotionObservation
step(RootVelocityCommand, dt_s) -> LocomotionObservation
emergency_stop() -> None
```

- `step` 内部运行兼容的腿部策略，产生并应用关节目标，推进一次策略周期；不能通过 `set_world_pose`、根位姿插值、根速度强制写入来冒充步态控制。
- 独立观测包含世界坐标 SE(2)、身体坐标速度、足底接触和滑动、躯干倾斜、基座高度、碰撞与仿真时间。物理来源必须标为 `isaac_physx_locomotion`，执行模式必须为 `joint_policy`。
- 场景重置必须标记 `teleported=true`，并开始新的 episode；导航过程中出现该标记立即失败。即便没有标记，超出物理速度范围的位姿跳变也会被拒绝。
- `emergency_stop` 必须将期望运动置零并保持底层平衡/安全控制运行。返回只表示请求已发出；Skill 仍检查后续传感器能否证实停止。
- 策略兼容性校验读取与校验文件，不加载或执行模型。权重哈希、动作/观测模式正确也不代表已经通过真实步行测试。
- 机器人 USD 根文件哈希只属于契约的一部分；场景加载器还必须固定并验证所有引用层和网格依赖，沿用现有资产依赖清单流程。

默认速度不超过 0.25 m/s、转速不超过 0.4 rad/s、线性加速度不超过 0.35 m/s²；单次导航最长允许 120 秒。突然失去支撑、超力学包络或用户取消会请求立即安全停止。紧急零命令用于停止故障，不受常规加速斜率约束；物理 Adapter 应使用其安全制动/平衡机制。

## 独立验证与测试

测试覆盖身体坐标与最短转角、向量速度/加速度限制、确定性、种子隔离、正常到位/稳定、失去双足支撑、支撑脚滑动、倾倒、碰撞、陈旧观测、用户取消、停止确认、策略不兼容与哈希篡改、teleport、未声明位姿跳变，以及畸形传感器数据。

只有最终一帧在目标位置不足以通过；需要完整的有效时间序列和稳定保持窗口。发送零速度不等于已经停止；控制器宣告完成不能代替传感器证据。
