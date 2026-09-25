> 历史快照：2026-09-25 整理前的 `docs/PROJECT_HANDOFF.md`。仅保留历史背景与原始成果说明，不决定当前优先级或能力状态。当前入口见 [项目总览](../../overview.md)。

# Humanoid Computer Agent 项目衔接说明

> 用途：为下一阶段开发或新的 Codex 会话提供单一、可信的工程入口。本文描述截至 2026-09-04 工作树的实际代码状态；项目目前已完成**纯 Python 基线和 Isaac P1 资产/混合仿真底座**：G1 mode 15 / 5010、双 Dex3、五脚轮办公椅、8×6 m 四墙房间、三工位与导航锚点、74 键带标识 PhysX 键盘、标准操作侧、三路 RTX 相机与 PTY。P2 ActionGraph 和自动事件报告仅作诊断，GUI 尚未确认可见实体触键。尚未接入真实导航/坐下策略、载人椅子动力学、真实机器人、camera-only 视觉伺服、学习式指尖策略或本地模型权重。

## 1. 项目一句话定义

本项目研究 Unitree G1 人形机器人如何通过实体键盘、屏幕和语音完成计算机任务，并用独立证据验证每次物理—GUI—软件状态交接：

```text
用户语音/目标
→ 高层 Agent 选择 L2/L3 Skill
→ 分阶段机器人控制执行有限时域动作
→ Verifier 检查物理、GUI、软件证据
→ 成功则推进 SkillGraph 并保存检查点
→ 失败则按结构化失败码恢复、重试或停止
```

核心研究主张不是“LLM 调用机器人函数”，而是：

1. Skill 具有前置条件、局部后置条件、下一技能就绪性、资源和失败码；
2. 控制器只能声明结果，只有 Verifier 可以写入已验证任务状态；
3. 同一套恢复机制贯通机器人动作、GUI 焦点和代码测试；
4. 高层 Agent 不直接输出关节位置、速度或力矩。

## 2. 当前版本快照

| 项目 | 当前值 |
|---|---|
| 仓库 | `humanoid-computer-agent` |
| Python 包 | `vskill-os` 0.4.0 |
| 开发分支 | `feat/g1-5010-control-stack` |
| 功能基线 | `f47ce81 refactor: remove TISA coupling from locomotion` |
| Python 要求 | `>=3.11`，核心无第三方运行依赖 |
| 单元/集成测试 | 83 项，全部通过 |
| Recovery Benchmark | 固定 seed 7、20 episodes：20/20 成功，错误完成声明 0 |
| 真机状态 | 未接入；`selected_mode_machine` 故意保持为 `null` |

上述 Benchmark 只证明软件状态机、故障注入和恢复路径正确，不代表真实机器人成功率或动力学性能。

## 3. 不可破坏的设计边界

- Agent 只允许调用 L2/L3 Skill，不能调用 `joint`、`torque`、`motor`、`low_cmd` 或 DDS 低层命令。
- Skill 接收到的是状态副本，不能直接修改正式 `StateSnapshot`。
- `terminal_ready`、`work_pose_ready` 等派生状态必须在原子状态通过验证后再推导，禁止“未验证声明互相证明”。
- Codex/代码 Agent 的完成文字不是成功证据；必须由独立测试执行器产生退出码和输出证据。
- 安全停止、碰撞/跌倒保护、资源仲裁必须独立于 LLM 和学习策略。
- 仿真和真机实现必须暴露相同 Skill 契约，硬件依赖放在 Adapter 层。
- 本项目不使用 TISA；导航仅保留普通 `{vx, vy, yaw_rate}` 策略接口和数值限幅。

仓库级开发约束见 [`AGENTS.md`](../../../AGENTS.md)。

## 4. 已实现的端到端任务

默认工作流 [`configs/workflows/create_hello.json`](../../../configs/workflows/create_hello.json) 包含 9 个图节点：

```text
navigate_to_workstation
→ align_with_chair
→ sit_and_prepare_for_typing
→ calibrate_keyboard
→ press_chord([ctrl, alt, t])
→ launch_code_agent
→ submit_task
→ run_test
→ report_task
```

`software_recovery` 中，模拟代码 Agent 首次生成错误的 `hello.py`；独立测试拒绝结果，系统调用 `request_fix`，再次测试通过后才允许 `report_task`。`mixed_faults` 还会依次覆盖路径阻塞、椅子移动、Alt 漏按、代码 Agent 网络中断和代码错误。

## 5. 工程模块与职责

| 路径 | 已实现内容 | 下一阶段替换点 |
|---|---|---|
| `src/vskill_os/contracts.py` | 版本化 Skill 契约和 Skill Protocol | 保持稳定，扩展 schema 时兼容旧版本 |
| `domain.py` | Evidence、StateSnapshot、Request/Result、事实 TTL/依赖元数据 | 后续增加置信度传播 |
| `registry.py` | Skill 实现注册和重复检查 | 配置到实现类的插件式加载 |
| `runtime.py` | 前置检查、TTL/级联失效、异步执行、资源锁、超时、两阶段验证 | 跨进程资源锁和 ROS 2 Action 适配 |
| `ipc.py` / `worker_stub.py` | worker 请求/进度/取消/结果/健康协议，本地进程替身 | ROS 2/DDS/Unix socket 传输 |
| `verifier.py` | 可信证据源、原子/派生规则 | 相机、OCR、接触估计与学习式 Verifier |
| `graph.py` / `graph_executor.py` | DAG 校验、就绪节点、恢复重试、检查点续跑 | 动态重规划和并发安全执行 |
| `ledger.py` | SQLite 事件、检查点、恢复记忆 | MCAP/rosbag 证据索引和数据版本 |
| `recovery.py` | 失败码到恢复 Skill 的确定性路由 | 上下文排序、恢复代价和人工升级 |
| `simulation/` + `adapters/isaac/` | 世界真值、故障注入、代码沙箱、键帽桥、PTY worker、0.4775 m 座高且自由移动的五脚轮椅/工位资产链、P1 运行器与 P2 诊断运行器 | 真实导航/坐下策略、camera-only Verifier、HIL/真机 Adapter |
| `action_graph/` + `configs/action_graphs/modules/` | 版本化、可组合、无关节命令的有限时域按键动作模块与独立证据验证 | 跨进程资源锁、更多键盘动作模块 |
| `skills/simulation.py` | 17 个执行/恢复 Skill | 用真实感知和策略实现逐项替换 |
| `control/` | 阶段状态机、速度限幅、坐下守卫、G1 控制栈 | 接入 locomotion/WBC/坐下策略 executor |
| `robot/g1.py` | G1 29DOF/5010 身份与策略 Manifest 强校验 | 读取真机状态并绑定实际 policy artifact |
| `typing.py` | 键盘几何、手/手指规划语义、组合键、文本纠错 | 在已启用 Dex3 资产上接 IK、视觉伺服和接触控制 |
| `voice/pipeline.py` | ASR/Planner 接口、白名单、localhost LLM Adapter | sherpa/SenseVoice/Qwen 的真实进程 Adapter |
| `benchmark.py` | nominal/recovery/stress 随机故障评测 | 动力学扰动、感知噪声和消融实验 |

## 6. G1 29DOF/5010 当前处理

目标机器人是 Unitree G1 29DOF、5010 腕部版本，但“29DOF 5010”不能唯一确定动力学配置。代码显式支持 `mode_machine` 13、14、15、16、18，并区分髋部减速比和腰部锁定状态。

- 硬件配置：[`configs/robots/g1_29dof_5010.json`](../../../configs/robots/g1_29dof_5010.json)
- 策略 Manifest 示例：[`configs/robots/g1_29dof_mode15_policy.example.json`](../../../configs/robots/g1_29dof_mode15_policy.example.json)
- 校验实现：[`src/vskill_os/robot/g1.py`](../../../src/vskill_os/robot/g1.py)

当前仿真使用 mode 15：

```text
g1-29dof-mode15-wrist5010-hip22.5x22.5-free
```

接真机前必须完成：

1. 在 Unitree App 的 `Device > Data > Robot > Machine Type` 读取实际 `mode_machine`；
2. 将其写入 `selected_mode_machine`；
3. 从真机再次读取 `mode_machine/body_dof/wrist_motor` 并调用 `hardware_profile()` 校验；
4. 为每个 ONNX/策略权重保存 Manifest，锁定关节顺序、减速比、腰部状态、频率、仿真版本和训练提交；
5. 任何字段不一致时拒绝加载策略，而不是警告后继续。

## 7. 分阶段机器人运控

[`src/vskill_os/control/phases.py`](../../../src/vskill_os/control/phases.py) 实现不可任意跳级的控制状态机：

```text
stand_ready → locomotion → station_alignment → sit_transition
→ seated_stabilization → upper_body_operation
→ keyboard_calibration → typing
```

任意运行阶段都可进入 `recovery` 或终止态 `safe_stop`。`safe_stop` 只能由外部复位离开。

现有 locomotion 仅实现：

- 低维 `RootVelocityCommand(vx, vy, yaw_rate)`；
- 进入/退出阶段的守卫；
- 线速度和角速度限幅；
- 最近命令和阶段切换历史记录。

它**没有**实现定位、规划、避障、步态神经网络推理或真实关节控制。后续优先通过 Adapter 接 Unitree RL Lab 的 G1 velocity ONNX，而不改变 Agent/Skill 接口。

## 8. 坐下与工作姿态

[`src/vskill_os/control/sitting.py`](../../../src/vskill_os/control/sitting.py) 已实现七状态守卫：

```text
reverse_alignment
→ controlled_descent
→ contact_confirmation
→ load_transfer
→ stabilization
→ work_pose
→ complete
```

守卫使用椅子位姿误差、骨盆高度、座椅接触置信度、双脚接触、骨盆速度、躯干角速度、屏幕可见和双手可达。当前 `run_nominal_sit()` 使用手工构造的观测序列推进状态，控制器插槽只是语义名称：

- `geometric_alignment`
- `motion_imitation_policy`
- `contact_aware_policy`
- `task_residual_policy`
- `seated_stabilizer`
- `hold_work_pose`

因此“坐下模块已接入”仅指软件阶段逻辑已经进入 SkillGraph，并不表示 WBC-AGILE/BeyondMimic 策略已经运行。下一步应实现 `WholeBodyPolicyAdapter`，将真实仿真观测转换为 `SitObservation`，由阶段控制器选择对应策略或控制器。

## 9. 键盘规划与 P1/P2 灵巧手仿真

[`src/vskill_os/typing.py`](../../../src/vskill_os/typing.py) 当前具备：

- 每手 7 轴 Dex3 的语义关节顺序；
- 标准键盘平面几何与旋转/平移标定；
- 左右手和拇指/食指/中指分配；
- 大写、Shift、空格、回车和组合键规划；
- 依据最长正确前缀生成退格与重打方案；
- 键盘事件流与屏幕文本必须一致的双证据验证。

P1 已接入双 Dex3-1 资产：mode 15 / 5010 组合 USD 在 Isaac 中暴露 29+14 个主动关节，14 个手关节全部完成运动健康检查。74 键键盘具有随键帽运动的高对比网格字模，空格行在机器人侧且左右键序非镜像；真实 PhysX 键程/接触通过确定性 key-switch drive 产生，以独立验收事件链。

P2 已复用该资产生成有限时域 ActionGraph 与自动化 articulation/contact/event/screen 报告；但 GUI 人工观察未确认可见实体触键，因此状态回退为 `PARTIAL`，不能用该报告宣称触键完成。ActionGraph 只包含键位、执行者、笛卡尔目标、资源与证据要求；关节目标只存在于 Isaac Adapter 的 DLS 边界。尚未实现：

- 键盘视觉检测与手眼标定；
- 通用 WBC 与学习式指尖残差策略；
- 真实触觉闭环与真机 DDS Adapter；
- 连续文本示范数据和 ACT/Diffusion/残差策略训练。

纯 Python 后端已完成 `单键 → Ctrl+Alt+T → 短字符串 → 错键恢复`，Isaac 已完成单键与组合键物理门禁。下一步应补 camera-only 定位/OCR 和扰动评测，再进入 HIL/真机；不要直接从自由文本高速打字起步。

## 10. 本地语音—LLM

当前链路为：

```text
PCM → VAD → ASR → 本地工具规划器 → AgentToolPolicy → V-SkillGraph
```

已实现的是协议和安全边界：`ScriptedASR`、规则规划器、只接受 `localhost/127.0.0.1` 的 OpenAI-compatible Planner、工具白名单与低层调用拦截。

候选配置：

- 机器人边缘：sherpa-onnx VAD、SenseVoiceSmall、Qwen3 1.7B GGUF、llama.cpp；
- GPU 工作站：FunASR/SenseVoiceSmall、Qwen3 4B、llama.cpp 或 vLLM。

配置文件只是候选清单，**没有下载模型，也没有验证模型 ID、量化版本、显存、实时率或中文命令准确率**。下一阶段需要锁定模型版本和许可证，并用项目内中英命令集测试 VAD 漏检、CER/WER、工具选择、参数、延迟和越权率。

## 11. Verifier、账本与恢复

系统区分：

- `SimulatedWorld.truth`：环境真值，对 Agent 不直接可见；
- `StateSnapshot.facts`：Verifier 接受的任务事实。

主要派生关系：

```text
work_pose_ready
= seated_stable + screen_visible + hands_reachable

terminal_ready
= terminal_visible + terminal_focused + fingers_released + work_pose_ready
```

SQLite 账本保存调用事件、任务检查点和成功恢复记忆。失败码包括导航阻塞、椅子/姿态、键盘、终端焦点、网络、测试失败和超时；默认恢复路由位于 [`src/vskill_os/recovery.py`](../../../src/vskill_os/recovery.py)。

当前 Verifier 是可信模拟证据加确定性规则；Runtime 已为 `keyboard_calibrated`、`work_pose_ready`、`terminal_focused` 和 `terminal_ready` 配置 TTL/依赖传播。真实阶段仍必须为每种 evidence source 标定置信度、TTL 和时钟误差。

## 12. 运行与复现

若已将压缩包解压到用户主目录：

```bash
cd ~/humanoid-computer-agent
git status --short --branch
make check
make demo
make demo-mixed
make benchmark
make inspect
make demo-p1
```

无需安装即可使用 CLI：

```bash
PYTHONPATH=src python3 -m vskill_os.cli run \
  --scenario configs/scenarios/mixed_faults.json

PYTHONPATH=src python3 -m vskill_os.cli benchmark \
  --episodes 50 --profile recovery --seed 7 \
  --output artifacts/benchmark.json

PYTHONPATH=src python3 -m vskill_os.cli inspect-robot
PYTHONPATH=src python3 -m vskill_os.cli list-projects --subsystem sitting
PYTHONPATH=src python3 -m vskill_os.cli voice-plan \
  "创建一个 Python 程序并独立测试"
```

运行产物位于 `artifacts/`，包括 SQLite 账本、任务工作区和 Benchmark JSON；该目录被 Git 忽略。

## 13. 下一阶段建议开发顺序

### A. 先在已完成的 P1 资产链上闭环导航与坐下

1. 保持 Isaac 重依赖仅存在于 `adapters/isaac/`，不导入核心包；
2. 复用已锁定的 Isaac Sim 5.1 / Isaac Lab 2.3.2 和 P1 资产哈希；
3. 复用有限时域 Dex3 ActionGraph，不在 Agent 或 Skill 层加入关节命令；
4. 先实现 `LocomotionPolicyAdapter` 的入口到椅前 rollout，再实现 `WholeBodyPolicyAdapter` 的坐下转换；
5. 每个阶段保留独立定位、碰撞、接触和稳定性 Verifier，代码 Agent 与 camera-only 感知后续接入。

### B. 依次闭环三个机器人能力

1. G1 mode 15 仿真步态：到工位并稳定停下；
2. WBC-AGILE/BeyondMimic 坐下：真实接触驱动七阶段守卫；
3. Dex3 单键与组合键：自动事件诊断已存在，但需重新完成 GUI 可见实体触键和独立视觉验收。

每一项都必须有 nominal、扰动、失败、恢复和安全停止测试，再组合成长程流程。

### C. 最后接入真实本地模型

1. 先用文本输入测试 Qwen 工具调用 schema；
2. 再接 ASR，保持规则规划器作为可复现基线；
3. 将 LLM 输出视为不可信请求，继续经过白名单、Runtime 和 Verifier；
4. TTS 只负责反馈，不参与安全或成功判定。

## 14. 当前缺口与风险清单

- `RobotAdapter`、`PerceptionAdapter` 和策略接口仍是 Protocol；P1 Isaac 运行器是资产验收 Adapter，P2 是诊断运行器，均不是通用生产控制器；
- 未接 ROS 2 Action、Unitree SDK2/DDS 或真实 ONNX Runtime；
- Resource Lock 仍限单进程；worker 已有取消协议，但 ROS 2/DDS 抢占尚未接入；
- TTL 已进入 Runtime；跨主机/重启后的时钟偏差和真实传感器有效期尚未标定；
- Registry 仍手动注册实现类；
- `LocalCodeSandbox` 只适合固定可信模板，不是安全沙箱；
- 模型配置尚未通过真实下载和性能测试；
- P1 键盘是 74 键 ANSI-like 程序化资产，尚未覆盖小键盘区、不同键盘尺寸和真实开关曲线；
- 仿真 Benchmark 故障为离散、一次性注入，尚不包含连续动力学扰动；
- 外部项目和版本信息记录于 2026-09-03，接入前需重新核对上游兼容性与许可证。

## 15. 上游项目优先级

| 优先级 | 项目 | 用途 |
|---|---|---|
| P0 | Unitree G1 description | 机器人资产和 `mode_machine` 身份事实源 |
| P0 | Unitree RL Lab | G1 velocity/mimic 策略与 ONNX 基线 |
| P0 | Unitree Sim IsaacLab | DDS 等价仿真和 G1/Dex3 资产 |
| P1 | WBC-AGILE | 坐下/站起和全身 motion tracking 首选 Adapter |
| P1 | Unitree XR Teleoperate | Dex3 键盘示范采集 |
| P1 | Unitree LeRobot | ACT/Diffusion Policy 与数据格式基线 |
| P2 | BeyondMimic / GR00T WBC | 全身运动先验和部署对比 |
| P2 | sherpa-onnx / SenseVoice | 离线语音基线 |
| P2 | Qwen3 / llama.cpp | 本地结构化工具规划 |

完整链接、成熟度和采用边界见 [`docs/integrations/RELATED_PROJECTS.md`](../../integrations/RELATED_PROJECTS.md) 与 [`configs/research/projects.json`](../../../configs/research/projects.json)。

## 16. 交接给下一位开发者的首要任务

开始任何新功能前：

1. 阅读本文件、[`AGENTS.md`](../../../AGENTS.md) 和 [`docs/ARCHITECTURE.md`](../../ARCHITECTURE.md)；
2. 执行 `make check && make demo`，保留输出作为新分支基线；
3. 从 `feat/g1-5010-control-stack` 建立新功能分支；
4. P1 资产底座已完成；下一项先闭环入口到椅前的导航和真实坐下，再恢复 P2 可见触键验收；
5. 新实现必须继续返回 `claimed_states + Evidence`，不得直接写正式状态；
6. 同时补 nominal、故障、恢复和验证绕过测试；
7. 提交前执行 `make check`、对应 Demo 和最小 Benchmark。

下一阶段的最佳第一个验收目标是：

> 用隔离工作区接入真实代码 Agent，完成“生成失败 → 独立测试拒绝 → 结构化反馈 → 修复 → 复测通过”，并确保 Agent 的完成文本不能绕过软件 Verifier。
