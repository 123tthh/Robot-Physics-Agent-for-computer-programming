# 子项目与证据登记

盘点日期：2026-09-27。本表是跨项目能力状态的统一入口；本轮新增 VF-02-T02 单种子 PPO 百万转移诊断与五检查点独立物理复验，其他子项目沿用既有报告，未重新验收。Git 所属关系另见[分支登记](BRANCHES.md)。

## 状态口径

开发状态使用 `DONE / PARTIAL / READY / BLOCKED / PLANNED`，含义见[开发协议](CODEX_WORKPLAN.md)。每项还必须说明证据范围：契约、纯 Python/替身、Isaac 物理、机器人相机、真实软件测试、HIL 或真机。`DONE` 只对声明的范围成立。

每份验收需绑定代码/资产/配置版本、手型、姿态、控制方式、后端、输入、种子及原始记录。不同场景和证据范围不互相覆盖；不将“新报告”无条件当成“旧阶段完成”。

## 子项目总表

| ID / 子项目 | 当前范围与状态 | 证据或入口 | 未完成边界 |
|---|---|---|---|
| CORE 共享运行时 | `DONE`：契约、任务图、资源锁、TTL、账本、恢复的轻量基线 | `make check`、`make demo`；[P0](phases/phase_0_runtime_baseline.md) | 真实多 worker 控制权、锁内状态重检与持续撤销仍需集成验收 |
| SIM 共享仿真底座 | `DONE`：限定 P1 的资产、PhysX/RTX、键盘桥和 PTY | [P1](phases/phase_1_hybrid_simulation.md)、[资产链](simulation/P0_P1_ASSET_CHAIN.md) | 场景锚点不是导航 rollout；资产接触不是通用技能 |
| D3-5 Dex3 历史触键 | `PARTIAL`：Isaac 5.1 固定基座 ActionGraph 与事件诊断 | [P2](phases/phase_2_bimanual_typing.md)、[资产审计](experiments/dex3/DEX3_ASSET_AUDIT.md) | 原 P2 GUI 可见实体触键仍待确认；不由 D3-6 或 D5 的结果代替 |
| D5 Dex5 坐姿与连续输入 | `DONE`：指定场景演示；通用技能仍 `PARTIAL` | [受支撑十指](experiments/dex5/DEX5_SEATED_DEMO.md)、[自由坐下](experiments/dex5/FREE_BALANCE_SITTING.md)、[连续演示](experiments/dex5/CONTINUOUS_COMPUTER_DEMO.md) | 现有手部 IK；不代表任意文本、视觉控制、无 IK 学习或真机 |
| DESKTOP 屏幕与软件任务 | `PARTIAL`：实体 `ls`/Enter → PTY → 机器人 RGB-D/OCR 已有报告 | [屏幕验收](../reports/isaac_continuous_computer/virtual-screen-acceptance.json)、[P3](phases/phase_3_code_agent_loop.md) | 已有本地代码 Agent/测试适配器；真实隔离编程任务失败—修复—复测尚未端到端验收 |
| D3-6 Dex3 站姿实验 | `PARTIAL`：Isaac 6.0.1 固定站姿双食指 `ak` 两次通过 | [独立工作树与报告位置](BRANCHES.md#3-dex3-站姿工作树) | `afjk` 失败；无自由站立、行走或机器人相机验收；未合入当前树 |
| CHAIR 机器人—椅子交互 | `PARTIAL`：领域模型、独立轨迹验证、替身、worker 客户端和可选 PPO | [工程接口](research/chair/CHAIR_INTERACTION_ENGINEERING.md)、[研究规划](research/chair/CHAIR_INTERACTION_RESEARCH.md) | 真实载人椅物理 worker、策略训练和主动重定位未验收；自由坐下不证明主动滑椅 |
| LEARN 验证反馈驱动技能学习 | `PARTIAL`：T01–T04 单键物理、反馈与批环境；T05 相机、VF-02-T01 动作映射及 T02 单种子 PPO 诊断通过 | [PPO 诊断](../verification-feedback-skill-learning/02_joint_ppo_baseline/SINGLE_KEY_PPO.md)、[动作映射](../verification-feedback-skill-learning/02_joint_ppo_baseline/ACTION_MAPPING.md)、[相机验收](../verification-feedback-skill-learning/01_environment_and_evidence/DESKTOP_CAMERA.md)、[批环境验收](../verification-feedback-skill-learning/01_environment_and_evidence/BATCH_ENVIRONMENT.md)、[六阶段架构](../verification-feedback-skill-learning/README.md) | 固定骨盆、单键、特权状态 PPO；相机证据仍来自人工诊断。三训练种子、多键、真实退格、视觉控制与机制对照待验收 |
| IMPROVE 受约束自改进 | `PLANNED`：episode、候选档案、晋升与回滚设计 | [RSI 集成设计](research/rsi/RSI_INTEGRATION_PLAN.md) | 不另建 Runtime；无已验证持续改进或 RSI 能力 |
| NAV / VOICE 导航与语音 | `PARTIAL`：场景、控制端口、工具白名单与规则/loopback 基线 | [P5](phases/phase_5_navigation_and_voice.md)、[语音接口](integrations/VOICE_LLM.md) | 真实导航 rollout、感知和真实模型性能待验收；D3-6 中 velocity 候选不等于行走通过 |
| REAL 真机与 HIL | `PLANNED`：身份校验与策略兼容性框架 | [P6](phases/phase_6_sim2real_self_improvement.md)、[G1 配置](core/G1_29DOF_5010.md) | 尚无本登记可引用的 HIL/真机通过证据 |

## Dex5 已有物理证据的边界

| 实验 | 报告 | 可支持的结论 |
|---|---|---|
| 受支撑十指短串 | [验收](../reports/isaac_dex5_acceptance.json) | 显式骨盆支撑、近似接触模型下，十指 `asdf jkl; ` 与释放 |
| 自由坐下 | [验收](../reports/isaac_free_sitting_acceptance.json) | 两次同种子自由根坐下记录；学习平衡网络配合高度/躯干指令 |
| 连续坐下与十指 | [验收](../reports/isaac_continuous_computer/acceptance.json) | 44 cm 低椅、60 s 同一物理过程、指定短串与释放；手部 IK |
| 虚拟屏幕 | [验收](../reports/isaac_continuous_computer/virtual-screen-acceptance.json) | 指定 Shell 输入与机器人相机/OCR 链路；不是代码任务完成证据 |

报告中一些原始录像、图像和记录位于 Git 忽略的 `artifacts/`；换机器后必须按实验说明重新准备或恢复，只有摘要文件不足以重新验证原始记录。通过记录一致性校验也不等于对传感器真实性的认证。

## 本次轻量回归

整理结果与执行记录见[整理验收](REORGANIZATION.md)。轻量测试通过仅更新软件证据，不提升上表的物理、训练或真机状态。
