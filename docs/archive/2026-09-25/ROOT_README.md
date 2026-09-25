> 历史快照：2026-09-25 整理前的 `README.md`。仅保留历史背景与原始成果说明，不决定当前优先级或能力状态。当前入口见 [项目总览](../../overview.md)。

# V-Skill OS

[学习驱动整体架构（无 IK，目标设计）](../../research/learning/LEARNING_SYSTEM_ARCHITECTURE.md) · [基础运行时架构](../../ARCHITECTURE.md) · [学习训练调研](../../research/learning/DEX5_LEARNING_WITHOUT_IK.md)

面向人形机器人实体计算机操作的 Verifier-Grounded Agent–Skill 系统。目前已完成无需真机的纯 Python 基线，以及本机 Isaac Sim 5.1 / Isaac Lab 2.3.2 上的 P1 资产/混合仿真底座：Unitree G1 29DOF mode 15 / 5010、双 Dex3、五脚万向轮办公椅、8×6 m 四墙房间、三工位与导航锚点、74 键带高对比标识的物理键盘、PhysX 接触、三路 RTX 相机和键帽—PTY 链路。P2 自动化事件门禁已有诊断结果，但 GUI 尚未确认可见实体触键，因此保持 `PARTIAL`。Dex5 自由坐下、连续十指打字和终端屏幕闭环的新增验收见下。真实导航 rollout、主动载人椅移动、完全基于视觉的控制、真机策略和真实代码 Agent 任务仍待后续验收。

2026-09-23：[G1 + 双 Dex5-1 受支撑坐下与十指打字 Demo](../../experiments/dex5/DEX5_SEATED_DEMO.md) 已在 Isaac Sim 5.1 完整运行通过，双手十指物理输入 `asdf␠jkl;␠`，逐键验证接触、键程、事件、文字与释放，并保存录像。此结果限定于显式支撑与近似指尖接触模型，不代表自由平衡坐下或真机验收。复现入口为 `make dex5-assets`、`make demo-dex5`、`make isaac-dex5`。

2026-09-23：[G1 + 双 Dex5-1 自由平衡坐下实验](../../experiments/dex5/FREE_BALANCE_SITTING.md) 已在同一 Isaac Sim 5.1 环境完成两次独立运行：机器人站立后坐到 P1 被动五脚轮办公椅上，并小幅调整躯干；无骨盆支架、无根轨迹写入，900 个物理观测样本在同种子重跑中一致。[录像](../../../artifacts/free-sit/accepted-seed7/demo.mp4)与[独立验收](../../../reports/isaac_free_sitting_acceptance.json)已保存，原普通椅子另存为 `scene.usd`。复现入口为 `make free-sit-assets`、`make isaac-free-sit`、`make verify-free-sit`。此前结果限定于自由坐下；新的连续十指衔接验收见下。主动载人椅移动仍待实现。

2026-09-23：[44 cm 低椅连续坐下与十指打字](../../experiments/dex5/CONTINUOUS_COMPUTER_DEMO.md) 已通过：60 s 单一物理过程完成站立、自由坐下及双 Dex5-1 十指 `asdf␠jkl;␠`，3000 条全身物理观测、全部按键事件和最终释放经独立验证。[视频](../../../artifacts/computer/continuous-v2-seed7/demo.mp4) · [验收报告](../../../reports/isaac_continuous_computer/acceptance.json)。复现：`make isaac-computer`、`make verify-computer`。虚拟屏幕闭环亦已通过：实体输入 `ls` 与 Enter → PTY 真实 Shell → 动态屏幕纹理 → 机器人 RGB-D / OCR。[屏幕验收](../../../reports/isaac_continuous_computer/virtual-screen-acceptance.json)。

## 已实现

机器人—椅子交互研究分支新增 [论文调研与实验规划](../../research/chair/CHAIR_INTERACTION_RESEARCH.md) 和 [工程/训练接口](../../research/chair/CHAIR_INTERACTION_ENGINEERING.md)：包括独立轨迹验证、训练环境与采样接口、可选 PPO、物理 worker 传输和 `make demo-chair` 替身演示。状态为 `PARTIAL / 未测试`；本轮按要求没有运行测试、demo 或训练，真实载人椅物理 worker 尚待接入。

2026-09-23：新增 [不使用 IK 的坐下与双 Dex5-1 打字学习方案](../../research/learning/DEX5_LEARNING_WITHOUT_IK.md)，核查直接关节 RL、专家数据与视觉蒸馏路线，列出单指到连续坐下/十指打字的实验门槛。当前为调研与规划，尚未训练；上述已验收录像仍使用手部 IK，不是新学习方案的结果。

- 版本化 Skill 契约和 Registry；
- JSON 配置的 V-SkillGraph；
- 异步 Runtime、资源互斥、超时、异常隔离和前置条件检查；
- 易失状态 TTL、依赖失效传播和可注入时钟；
- 版本化 worker 请求/进度/取消/结果/健康协议与本地替身进程；
- “控制器声明状态，Verifier 独立确认”的状态写入机制；
- 物理、GUI、软件三域证据模型；
- SQLite 事件账本、结构化恢复记忆和任务检查点；
- SkillGraph 执行、分层恢复、失败后重试与跨进程恢复；
- 模拟机器人、椅子、键盘、屏幕和代码 Agent；
- 真实本地 Python 产物生成和独立测试；
- nominal、软件故障、坐姿移动恢复和混合故障场景；
- 可复现的随机故障 Benchmark；
- CLI、示例和标准库自动化测试。
- G1 29DOF/5010 `mode_machine` 13/14/15/16/18 硬件身份与策略兼容性强校验；
- `stand → locomotion → align → sit → stabilize → seated locomotion → keyboard → typing` 控制状态机；
- `{vx, vy, yaw_rate}` 步态命令接口与独立速度限幅；
- 六段坐下接触守卫，以及 WBC-AGILE/BeyondMimic 策略插槽；
- 无动力万向轮椅上的低维坐姿移动 Skill、接触/倾角守卫、进度与跟踪独立验证；
- 坐姿策略 96 维 actor / 217 维 critic 观测清单与 G1 机型强校验；
- 键盘几何、左右手/手指规划语义、组合键和最长正确前缀纠错；
- 键帽迟滞/防抖/长按保护/`all_keys_up` 与 PTY 桌面 worker；
- 针对本机 RTX 5090 锁定 G1 mode 15/5010、Isaac Sim 5.1、Isaac Lab 2.3.2 和上游提交的 P1 清单；
- 从官方 mode 15 body 与双 Dex3-1 生成目标 URDF/USD，并在 PhysX 中验证 29+14 个主动关节；
- 74 个独立 4 mm 行程键帽与随键运动的网格字模，修正机器人操作侧和标准左右键序；
- 接触阵列、头部/键盘/全景三路 RTX 相机、保存渲染图与物理按键到 PTY 的端到端验收；
- 不含关节命令的版本化 ActionGraph，以及预定位、触键、保持、成组释放和屏幕门禁模块；
- Dex3 指尖点雅可比/DLS、全键盘邻键接触审计、8 N 力门限和事件唯一性诊断；
- Isaac 自动报告记录右拇指/中指与左中指的组合键事件，但 GUI 尚未确认清晰的实体触键；
- 纯 Python P2 回归覆盖单键、组合键、短命令、错字退格修复与统一释放；
- P0/P1 工位资产链覆盖进入、接近、对椅、坐下前、坐姿稳定和工作姿态，并提供 0.4775 m 座高、自由根、五脚轮椅的 10 个被动关节及实测 PhysX 移动门禁；
- P1 导航资产提供带 1.2 m 门洞的 8×6×2.8 m 四墙房间、A/B/C 三个唯一工位、3 个机器人出生点、每工位 3 类入口锚点和 1 个碰撞障碍；
- 本地 VAD/ASR/LLM/TTS 配置、loopback LLM adapter 和 L2/L3 工具白名单；
- 相关上游项目的机器可读调研目录与明确集成决策。

## 系统闭环

```text
用户目标
→ V-SkillGraph 选择就绪节点
→ Runtime 检查前置条件并锁定资源
→ Skill 操作模拟环境并返回声明与证据
→ Verifier 决定哪些状态可以写入
→ 成功后保存检查点
→ 失败则查询恢复记忆/失败码并调用恢复 Skill
→ 软件测试通过后才允许报告完成
```

## 快速运行

```bash
cd /home/descfly/humanoid-computer-agent
make check
make demo
make demo-mixed
make demo-seated-recovery
make demo-p1
make demo-p2
make inspect-action-graph
make benchmark
make inspect
make inspect-dex3-assets
# 已安装本文锁定的 Isaac 环境时：
make isaac-p1-assets
make isaac-p1
make isaac-p1-validate
make isaac-p2
```

也可以直接使用 CLI：

```bash
PYTHONPATH=src python3 -m vskill_os.cli run \
  --scenario configs/scenarios/mixed_faults.json

PYTHONPATH=src python3 -m vskill_os.cli benchmark \
  --episodes 50 \
  --profile recovery \
  --seed 7 \
  --output artifacts/benchmark.json

PYTHONPATH=src python3 -m vskill_os.cli list-skills

PYTHONPATH=src python3 -m vskill_os.cli inspect-robot

PYTHONPATH=src python3 -m vskill_os.cli inspect-sim-assets

PYTHONPATH=src python3 -m vskill_os.cli inspect-dex3-assets

PYTHONPATH=src python3 -m vskill_os.cli hybrid-smoke

PYTHONPATH=src python3 -m vskill_os.cli list-projects --subsystem sitting

PYTHONPATH=src python3 -m vskill_os.cli voice-plan \
  "创建一个 Python 程序并独立测试"
```

可编辑安装后可直接使用 `vskill`：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
vskill run --scenario configs/scenarios/software_recovery.json
```

## 内置任务

默认 SkillGraph 完成以下状态转换：

```text
到达工位
→ 对齐椅子
→ 坐下并建立可操作姿态
→ 保持座面接触完成短距离坐姿重定位
→ 标定键盘
→ 打开并聚焦终端
→ 启动代码 Agent
→ 生成 hello.py
→ 独立执行测试
→ 失败时请求修复
→ 测试通过后报告
```

`software_recovery` 场景中，模拟代码 Agent 会先生成错误程序。系统必须拒绝其“已完成”声明，运行独立测试，将失败映射到 `request_fix`，再次测试通过后才进入 `report_task`。

## 场景

| 配置 | 用途 |
|---|---|
| `nominal.json` | 无故障、首稿正确 |
| `software_recovery.json` | 首稿错误，验证软件恢复闭环 |
| `mixed_faults.json` | 路径、椅子、键盘、网络和代码多域故障 |
| `posture_recovery.json` | 坐姿不稳后由稳定 Skill 恢复，避免重复接触转换 |
| `seated_locomotion_recovery.json` | 坐姿移动中座面接触丢失并局部恢复、重试 |

故障由场景配置注入并在首次触发后消费，因此恢复后重试可得到确定性结果。

## 目录

```text
configs/skills/       Skill 契约配置
configs/workflows/    V-SkillGraph 任务图
configs/scenarios/    故障场景
configs/robots/       G1 29DOF/5010 身份与部署防错配置
configs/simulation/   固定版本的 P1 G1/Isaac/键盘资产清单
configs/models/       本地语音和轻量 LLM 候选配置
configs/research/     已评估上游项目的机器可读目录
docs/                 架构、规范、仿真和评测说明
src/vskill_os/        核心包
tests/                单元、集成、恢复和 Benchmark 测试
artifacts/            本地运行产物与账本（Git 忽略）
reports/              可由轻量核心复核的耐久化验收证据
```

`make isaac-p1-assets` 会重组官方 mode 15 / 5010 + 双 Dex3 URDF，并用本机 Isaac Lab Importer 生成 USD；`make isaac-p1` 启动 P1 无界面 PhysX/RTX 验收；`make isaac-p2` 复用该场景执行双手 `Ctrl+Alt+T` ActionGraph，并写入 `reports/isaac_p2_acceptance.json`。默认路径与用户提供的 `/home/descfly/IsaacLab-2.3.2` 和 `isaaclab-2.3.2` conda 环境一致，可用 Make 变量覆盖。

## 检查点恢复示例

```bash
PYTHONPATH=src python3 -m vskill_os.cli run \
  --task-id resume-demo \
  --ledger artifacts/resume-demo.db \
  --workspace artifacts/workspaces/resume-demo \
  --stop-after-nodes 7

PYTHONPATH=src python3 -m vskill_os.cli run \
  --task-id resume-demo \
  --ledger artifacts/resume-demo.db \
  --workspace artifacts/workspaces/resume-demo \
  --resume
```

## 安全说明

当前 `LocalCodeSandbox` 只执行仿真器生成的固定 Python 模板，用于验证“数字 Agent 声明不等于测试通过”。它不是面向不可信代码的安全沙箱；接入真实 Codex 前必须替换为容器或虚拟机隔离实现。

## 文档

- [Codex 规划入口](../../overview.md)
- [Codex 工作协议](../../CODEX_WORKPLAN.md)
- [Isaac Sim + Pygame/PTY 混合仿真指南](../../simulation/sim_guidance.md)
- [仿真专项分阶段实施规划](../../simulation/SIMULATION_PHASE_PLAN.md)
- [分阶段任务索引](../../phases/README.md)
- [灵巧手选型与键盘验收](../../simulation/HAND_SELECTION.md)
- [G1 mode 15 / 5010 + Dex3 资产审计](../../experiments/dex3/DEX3_ASSET_AUDIT.md)
- [双 Dex5-1 受支撑坐下与十指打字 Demo](../../experiments/dex5/DEX5_SEATED_DEMO.md)
- [双 Dex5 与被动轮椅的自由平衡坐下实验](../../experiments/dex5/FREE_BALANCE_SITTING.md)
- [Dex5-1 资产来源、耦合与安装近似审计](../../experiments/dex5/DEX5_ASSET_AUDIT.md)
- [项目总览](../../PROJECT_OVERVIEW.md)
- [系统架构](../../ARCHITECTURE.md)
- [Skill 规范](../../core/SKILL_SPEC.md)
- [纯仿真实现](../../core/SIMULATION.md)
- [Benchmark 设计](../../core/BENCHMARK.md)
- [构建顺序](../../BUILD_ORDER.md)
- [参考工作与设计映射](../../integrations/REFERENCES.md)
- [G1 29DOF/5010 配置基线](../../core/G1_29DOF_5010.md)
- [分阶段运控、坐下与实体打字](../../core/CONTROL_PHASES.md)
- [本地轻量语音—LLM](../../integrations/VOICE_LLM.md)
- [相关项目与集成决策](../../integrations/RELATED_PROJECTS.md)
