# 面向实体计算机操作的验证反馈驱动技能学习

状态：`PARTIAL`，2026-09-25。VF-01-T01 已实现[任务与证据记录、独立离线评估及正负回放样例](01_environment_and_evidence/IMPLEMENTATION.md)；物理环境、学习模块和其余指标仍待交付，不表示已经训练或通过物理实验。本目录是研究实施说明，运行代码按仓库统一架构放入 `src/vskill_os/`，不建立第二套运行时。

## 研究主线

利用物理接触与屏幕反馈，让机器人学会何时继续输入、何时获取新证据，以及如何从错误中恢复。

目标是单臂单手在固定工位输入少量可达字符组成的 4–8 字符短串，并能纠正漏字、重复字和误字。第一个工程任务进一步缩小为一个键的“接近—按下—真实释放”。第一轮结果覆盖阶段 1–4；视觉策略、双手和自由坐姿在机制证据成立后推进。

核心问题：**物理动作成功和数字结果成功并不等价，在反馈不完整、存在延迟且验证有成本时，如何联合选择输入长度、验证时机与恢复动作？**

候选假设：保留物理—数字证据不一致及其时间历史，能比固定频率或简单置信度阈值更好地选择继续输入、验证和恢复，在同等时间或验证预算下提高整串成功率。若只胜过“末尾验证”，不能证明学习机制必要。

## 六个阶段

| 阶段 | 工程架构 | 回答的问题 | 进入下一阶段的主要依据 |
|---|---|---|---|
| VF-01 环境与证据 | [01_environment_and_evidence](01_environment_and_evidence/README.md) | 一次物理按下、事件和文本变化是否能独立追溯？ | 可重复 reset/step、独立评估、物理释放负例、1/16/64 环境隔离检查 |
| VF-02 直接关节 PPO | [02_joint_ppo_baseline](02_joint_ppo_baseline/README.md) | 单键及少量键的无 IK 控制是否可学？ | 单键三训练种子，各独立评估 100 回合，完整成功率目标约 95%；短串所需技能另验收 |
| VF-03 输入—验证—恢复 | [03_verification_and_recovery](03_verification_and_recovery/README.md) | 证据历史能否改善操作长度和恢复决策？ | 冻结低层技能，完成风险预测器与有持续时间的决策学习；通过观察权限审计 |
| VF-04 严格对照评测 | [04_benchmark_and_ablations](04_benchmark_and_ablations/README.md) | 学习机制是否优于强规则基线？ | 冻结任务/扰动/种子；与逐字、固定频率、阈值等基线公平比较；保留负结果 |
| VF-05 视觉与多指扩展 | [05_visual_policy_and_bimanual](05_visual_policy_and_bimanual/README.md) | 去掉特权观测后，机制是否仍有效？ | 无 IK 教师 → RGB-D 学生；单手多指、双手与未见短串逐级复验 |
| VF-06 自由坐姿交接 | [06_seated_skill_handoff](06_seated_skill_handoff/README.md) | 坐下终态能否使后续触键更可靠？ | 从站姿连续执行、无中途 reset，分别验证支撑、交接、输入和数字结果 |

上表门槛是待检验的实验目标，不是已有结果。VF-04 的测试集与预算协议在 VF-01 时就先建立；正式方法比较在 VF-03 后执行，避免用最终测试集调方法。

## 整体工程架构

```mermaid
flowchart TB
    Goal[目标字符串与预算] --> Decision[证据历史与决策策略]
    Decision --> Choice{技能边界决策}
    Choice -->|继续 1/2/4 字符| Press[冻结的低层按键策略]
    Choice -->|请求验证| Screen[新屏幕帧与识别]
    Choice -->|恢复| Recover[物理释放 / 退格 / 重输]
    Choice -->|请求结束| Verify[独立 Verifier]
    Press --> Safety[独立限位限速与支撑保护]
    Recover --> Safety
    Safety --> Physics[机器人 / 键帽物理环境]
    Physics --> Bridge[接触与键程事件桥]
    Bridge --> Text[文本状态机或真实桌面]
    Text --> Screen
    Screen --> History[带时间与置信度的证据历史]
    Physics --> History
    History --> Decision
    Physics --> Recorder[原始记录器]
    Bridge --> Recorder
    Text --> Recorder
    Recorder --> Verify
    Recorder -.离线监督.-> Train[训练器与独立评估]
```

“文本状态机 → 策略”没有直接真值通道；新屏幕证据需经请求验证接口。安全检查和真实释放守卫始终运行，不由策略选择是否执行。训练记录器可以读取仿真真值，但策略输入由单独的可见性过滤器构造。

## 共同设计与代码位置

先读 [COMMON_CONTRACTS.md](COMMON_CONTRACTS.md)：它统一消息、时间、观察权限、物理释放、结果和版本定义。每个阶段都说明已有实现与拟建模块；拟建路径仅是建议，当前并未创建可执行环境或训练入口。

```text
src/vskill_os/learning/                    已有：离线任务/轨迹/独立契约评估；在线历史待建
src/vskill_os/skills/                      现有：新增学习按键、观察与恢复技能包装
src/vskill_os/adapters/isaac/learning/      拟建：物理环境、批量采样与传感器适配
src/vskill_os/adapters/learning_torch/     拟建：PPO、风险预测器、循环决策与视觉训练
configs/learning/verification_feedback/   已有：v1 合成任务/评估判据；训练配置待建
artifacts/verification_feedback/          已有：合成契约回放；物理轨迹/权重待采集
reports/verification_feedback/            已有：契约验收摘要；物理/训练报告待采集
```

Isaac/Torch 只进入可选 Adapter；Agent/SkillGraph 不接收或输出关节向量。部署策略仍使用现有 Runtime、资源契约、独立 Verifier 和 Ledger。研究决策器提交的技能请求也必须经过相同门禁。

## 初始范围与假设

- 默认基于主工作树的 G1 + Dex5 资产，单臂/腕 7 维 + 单手独立驱动 16 维；站姿 Dex3 是单独对照路线，不复用这组动作维度。
- 实际字符集合和 Backspace 必须先经过同一手臂的无 IK 可达性与物理接触检查；不预先假定 `asdf` 与退格都可在相同小工作区完成。
- 保持既有坐姿稳定模块的策略和控制范围固定，首先声明“手部无 IK”。全身严格无 IK 还需审计冻结身体模块的数据来源；支持方式、关节控制权和失败均记录。
- 先锁定当前已验证的 Isaac 5.1 / Lab 2.3.2 资产基线，新建环境是否兼容以及可运行规模需实测；不自动迁移到 6.0.1。
- 初始文本编辑规则限定为末尾追加与末尾退格，焦点正确、光标在末尾；延迟和文本丢失可作独立扰动。随机光标/焦点操作需先有真实可执行的恢复技能，不能用隐藏 API 自动修复。
- 暂不把导航、主动滑椅、真实编程任务、十指覆盖作为第一轮论文的必要条件。

## 当前仓库可复用的部分

| 已有内容 | 代码 | 复用边界 |
|---|---|---|
| 键程—事件桥 | [keyboard_bridge.py](../src/vskill_os/simulation/keyboard_bridge.py) | 迟滞、防抖、事件与 reason；超时 KeyUp 不等于物理释放 |
| 规则纠错 | [TypingPlanner.repair](../src/vskill_os/typing.py) | 最长公共前缀的退格/重输计划；需消费同一屏幕估计，不读隐藏真值；Dex5 执行映射另建 |
| 物理观测验证 | [dex5_typing.py](../src/vskill_os/simulation/dex5_typing.py)、[动作验证](../src/vskill_os/action_graph/runtime.py) | 复用验证语义；适配少量键、恢复与学习执行器，不能直接宣称现有脚本就是学习环境 |
| 桌面、屏幕、相机 | [virtual_computer.py](../src/vskill_os/adapters/virtual_computer.py)、[virtual_screen.py](../src/vskill_os/adapters/isaac/virtual_screen.py) | 现有特定任务闭环；文本框、按需反馈和批环境隔离待实现 |
| 姿态与安全领域 | [control/](../src/vskill_os/control)、[Runtime](../src/vskill_os/runtime.py) | 原守卫可复用；锁内状态重检、持续撤销和统一控制权仍是待加固项 |

研究想法与相关工作名称来自用户提供的方案；本次未重新检索或逐篇核验 RoboPianist、OmniPianist、Reward Machines、主动感知或 T-STAR，不据此声称发表级新颖性。正式写作前应逐篇建立“问题、证据表示、动作、验证成本、恢复、传感器权限”的对照表。

## 当前实现与下一项任务

**VF-01-T01 已完成契约范围交付**：`make demo-learning-evidence` 导出 10 个案例并独立复核，覆盖正确按下、持续物理释放、管理释放、重复/漏事件、反馈延迟、错误文本及有记录的修复。

下一项为 **VF-01-T02：单环境物理 reset/step**。人工构造样例只证明契约；需要采集同样类型的真实物理记录并验证后端，才进入 PPO。复现方式与限制见[实现说明](01_environment_and_evidence/IMPLEMENTATION.md)，更完整拆分见各阶段文档。

项目共同约束：[AGENTS.md](../AGENTS.md)、[统一架构](../docs/ARCHITECTURE.md)、[子项目状态](../docs/WORKSTREAMS.md)、[统一路线图](../docs/ROADMAP.md)。
