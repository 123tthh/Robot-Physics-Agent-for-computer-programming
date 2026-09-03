# V-Skill OS

面向人形机器人实体计算机操作的 Verifier-Grounded Agent–Skill 系统。目前提供完整、无需真机的仿真实现，并已建立 Unitree G1 29DOF/5010、分阶段全身控制、Dex3 实体打字与本地语音—LLM 的可测试适配边界；ROS 2、仿真策略权重、视觉模型和真实代码 Agent 仍通过可选 adapter 接入。

## 已实现

- 版本化 Skill 契约和 Registry；
- JSON 配置的 V-SkillGraph；
- 异步 Runtime、资源互斥、超时、异常隔离和前置条件检查；
- “控制器声明状态，Verifier 独立确认”的状态写入机制；
- 物理、GUI、软件三域证据模型；
- SQLite 事件账本、结构化恢复记忆和任务检查点；
- SkillGraph 执行、分层恢复、失败后重试与跨进程恢复；
- 模拟机器人、椅子、键盘、屏幕和代码 Agent；
- 真实本地 Python 产物生成和独立测试；
- nominal、软件故障、混合故障场景；
- 可复现的随机故障 Benchmark；
- CLI、示例和标准库自动化测试。
- G1 29DOF/5010 `mode_machine` 13/14/15/16/18 硬件身份与策略兼容性强校验；
- `stand → locomotion → align → sit → stabilize → keyboard → typing` 控制状态机；
- `{vx, vy, yaw_rate}` 地形风险门控与独立控制权分配；
- 六段坐下接触守卫，以及 WBC-AGILE/BeyondMimic 策略插槽；
- Dex3 键盘几何、左右手/手指分配、组合键和最长正确前缀纠错；
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
cd /root/humanoid-computer-agent
make check
make demo
make demo-mixed
make benchmark
make inspect
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

故障由场景配置注入并在首次触发后消费，因此恢复后重试可得到确定性结果。

## 目录

```text
configs/skills/       Skill 契约配置
configs/workflows/    V-SkillGraph 任务图
configs/scenarios/    故障场景
configs/robots/       G1 29DOF/5010 身份与部署防错配置
configs/models/       本地语音和轻量 LLM 候选配置
configs/research/     已评估上游项目的机器可读目录
docs/                 架构、规范、仿真和评测说明
src/vskill_os/        核心包
tests/                单元、集成、恢复和 Benchmark 测试
artifacts/            本地运行产物与账本（Git 忽略）
```

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

- [项目总览](docs/PROJECT_OVERVIEW.md)
- [系统架构](docs/ARCHITECTURE.md)
- [Skill 规范](docs/SKILL_SPEC.md)
- [纯仿真实现](docs/SIMULATION.md)
- [Benchmark 设计](docs/BENCHMARK.md)
- [构建顺序](docs/BUILD_ORDER.md)
- [参考工作与设计映射](docs/REFERENCES.md)
- [G1 29DOF/5010 配置基线](docs/G1_29DOF_5010.md)
- [分阶段运控、坐下与实体打字](docs/CONTROL_PHASES.md)
- [本地轻量语音—LLM](docs/VOICE_LLM.md)
- [相关项目与集成决策](docs/RELATED_PROJECTS.md)
