# P2：双手、多指和组合键

状态：`PARTIAL`（纯 Python 和自动化事件门禁已通过；GUI 观察未确认可见按键动作）

[`../../src/vskill_os/typing.py`](../../src/vskill_os/typing.py) 已实现键盘几何、完整修饰键/主键分配、事件时序和最长正确前缀纠错；[`../../src/vskill_os/action_graph/`](../../src/vskill_os/action_graph) 提供不含关节命令的模块化 ActionGraph；[`../../src/vskill_os/adapters/isaac/p2_scene.py`](../../src/vskill_os/adapters/isaac/p2_scene.py) 在 P1 的 G1 29DOF mode 15 / 5010 + 双 Dex3 场景上完成指尖物理控制。

## 目标

完成 `单键 → Ctrl+Alt+T → 短字符串 → 错键恢复`，使每次按键都有物理、数字事件和屏幕结果证据。

## 核心设计

组合键是时间约束任务：先按住修饰键，再点按主键，最后释放，而不是追求多指严格同时触底。

```text
左/右手预定位
→ Alt Down
→ Ctrl Down
→ Verifier 确认修饰键保持
→ T Down/Up
→ Alt Up
→ Ctrl Up
→ Verifier 确认 terminal_visible + terminal_focused + fingers_released
```

## 任务

### P2-T01：扩展 ChordPlan 领域模型

状态：`DONE`

产物：

- 每个键对应的 `hand/finger`；
- press/release 顺序和保持条件；
- 最大持续时间和预期事件流；
- Dex3 两手六指的候选分配器；
- 不可达、手指冲突和无主键测试。

验收：`Ctrl+Alt+T` 的计划中三个物理键都有独立执行者，且计划不包含关节命令。

### P2-T02：单指按键控制基线

状态：`PARTIAL`

产物：

- 键上悬停、接近、接触、触底、保持、释放轨迹；
- 笛卡尔/IK 目标与手指混合力控 Adapter；
- 指尖—键帽接触和邻键碰撞证据；
- 误触、漏触、重复事件和过力测试。

验收：固定键盘上单键事件、接触和释放一致，控制器文字不能单独证明成功。

### P2-T03：双手组合键协调器

状态：`PARTIAL`

产物：

- 双手 barrier/prepose；
- 修饰键保持和主键点按；
- 双手资源锁、取消和异常统一释放；
- `ALT_MISSED`、`WRONG_KEY`、`STUCK_MODIFIER`、`HAND_COLLISION_RISK`；
- 正常与恢复场景。

当前证据：纯 Python 后端连续 200 次组合键通过率 100%；Isaac 自动化报告记录了目标键位移、接触和事件，但 GUI 人工观察没有看到清晰的实体按键动作。因此该报告只保留为诊断证据，不能作为 P2 完成证明。成组释放模块仍要求两个独立 KeyUp 严格按计划出现。

### P2-T04：连续文本与纠错闭环

状态：`DONE`

产物：

- 左右手动态指派；
- Shift/大写/符号/空格/回车/退格；
- 键盘事件与屏幕 OCR/文本的双证据比较；
- 最长正确前缀退格修复；
- 每键成功矩阵和失败统计。

验收：短命令和代码片段可以在可重复扰动下完成，错键不会导致整段盲目重打。

## 学习策略边界

先建立几何与力控基线，再学习小范围残差：

- 输入：目标键相对位姿、接触、视觉偏差、历史误差；
- 输出：有限范围的腕/指尖残差，不是全身关节命令；
- 标签：键盘事件可作自动标签，屏幕/OCR作独立验证；
- 安全：残差受工作空间、速度、力和碰撞守卫限制。

## 阶段退出条件

- `Ctrl+Alt+T` 形成端到端物理—GUI闭环；
- 异常时所有修饰键可靠释放；
- 每个失败可以定位到分配、可达、接触、事件或焦点；
- 连续文本有可量化的成功率、误触率和恢复率。

## 已实现的模块边界

ActionGraph 模块位于 [`../../configs/action_graphs/modules/`](../../configs/action_graphs/modules)：

- `bimanual_prepose@1.1.0`：双手粗预定位；
- `key_prepose@1.0.0`：在按下任何修饰键前预定位额外指尖；
- `key_down@1.1.0`：悬停、受控接近、接触、键程和保持；
- `key_up@1.0.1`：单键释放和净空；
- `modifier_release_group@1.0.0`：同手双修饰键的成组释放；
- `screen_gate@1.0.0`：独立终端可见/焦点/文本门禁。

任务图只携带键位、语义执行者、笛卡尔目标、资源和证据要求；关节目标只存在于 Isaac Adapter 的 DLS 边界。物理节点由独立的 articulation/contact/event 证据验证，控制器完成声明不能写入已验证状态。

## 当前诊断证据

- `make demo-p2`：单键、200 次 `Ctrl+Alt+T`、短命令、`codwx → codex` 修复和 PTY 输出；
- `make isaac-p2`：加载 P1 USD，验证 43 个 G1+Dex3 主动关节、74 个键帽、Dex3 指尖点雅可比、PhysX 接触和终端门禁；
- [`../../reports/isaac_p2_acceptance.json`](../../reports/isaac_p2_acceptance.json)：本机 RTX 5090 的可复核运行报告；
- `make check`：领域模型、Verifier、故障统一释放和集成回归。

Isaac 自动化报告中，Alt、Ctrl、T 的峰值接触力分别约为 5.62 N、5.77 N、2.23 N，三个键各记录一次 KeyDown/KeyUp。由于 GUI 观察未确认可见触键，这些数值暂不提升为阶段完成证据。

## 能力边界与后续风险

- P2 的 200 次压力回归是确定性纯 Python 后端；Isaac 动力学门禁已通过一次完整可复现运行，尚未宣称 200 次 Isaac 成功率或真机成功率；
- 当前精确键位和接触来自 Isaac articulation/contact oracle，camera-only 键盘定位、OCR 和视觉伺服仍属于后续评测/Sim-to-real；
- 当前固定基座前向标定为 0.28 m，真机必须重新标定工作台、键盘和指尖碰撞点；
- 未训练残差策略；现阶段为受限 DLS 几何/接触基线。

完成记录：

- 任务：P2-T01 至 P2-T04
- 状态：PARTIAL（等待可见物理触键和独立视觉复核）
- 修改：ChordPlan、ActionGraph、纯 Python/Isaac Adapter、独立 Verifier、CLI/Make 入口、测试与报告
- 验证：`make check`、`make demo`、`make demo-p2`、`make isaac-p2`
- 能力等级：领域契约 + 纯 Python + Isaac；非 HIL/真机
- 下一可领取任务：P3 真实隔离代码 Agent，或 P2/P6 camera-only 与 sim-to-real 强化
