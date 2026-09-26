# V-Skill OS · 人形机器人实体计算机操作

让机器人通过实体键盘和屏幕完成计算机任务，由独立 Verifier 判断物理动作、界面变化和软件结果是否成立。Agent 只调用技能；关节控制、安全保护和完成判定分别保留独立边界。

当前研究主线：[面向实体计算机操作的验证反馈驱动技能学习](verification-feedback-skill-learning/README.md)。从单臂单手、少量按键起步，研究何时继续输入、何时验证和如何恢复。已交付单键物理闭环、文本反馈隔离、1/16/64 批环境验收及[真实终端与机器人相机复验](verification-feedback-skill-learning/01_environment_and_evidence/DESKTOP_CAMERA.md)，并完成[直接关节动作映射健康检查](verification-feedback-skill-learning/02_joint_ppo_baseline/ACTION_MAPPING.md)；下一项为单键 PPO 可学性诊断，策略训练尚未开展。

## 项目入口

| 要了解什么 | 入口 |
|---|---|
| 项目目标、当前范围 | [项目总览](docs/overview.md) |
| 各条路线做什么、完成到哪 | [子项目与证据登记](docs/WORKSTREAMS.md) |
| 系统如何分层、代码放在哪里 | [统一架构](docs/ARCHITECTURE.md) |
| 下一步做什么、通过什么门槛 | [统一路线图](docs/ROADMAP.md) |
| 分支、独立工作树和整合顺序 | [分支登记](docs/BRANCHES.md) |
| 专项设计、实验与历史资料 | [文档目录](docs/README.md) |

当前同时保留 Dex5 坐姿基线和独立工作树中的 Dex3 站姿基线。已有物理报告只证明其记录的场景；无 IK 学习、真实代码任务、主动滑椅、自由导航和真机尚有各自的验收缺口。详细范围只在子项目登记中维护，避免多份总览相互矛盾。

## 快速运行

Python 3.11+，核心不要求 Isaac、Torch 或真实机器人：

```bash
make help
make check
make demo
make demo-mixed
```

`make demo` 验证受控代码生成、独立测试与失败恢复；不代表真实机器人或真实代码 Agent 已完成任务。可编辑安装后使用 `vskill`，也可直接运行：

```bash
PYTHONPATH=src python3 -m vskill_os.cli list-skills
PYTHONPATH=src python3 -m vskill_os.cli run --scenario configs/scenarios/mixed_faults.json
```

## 实验入口

| 路线 | 轻量入口 | 物理运行 / 验证 | 条件 |
|---|---|---|---|
| 工位、Dex3 历史基线 | `make demo-p1`、`make demo-p2` | `make isaac-p1`、`make isaac-p2` | 本工作树，Isaac 5.1 / Lab 2.3.2 |
| Dex5 受支撑十指基线 | `make demo-dex5` | `make isaac-dex5` | 先准备 Dex5 资产；显式支撑 |
| Dex5 自由坐下 | 见[实验说明](docs/experiments/dex5/FREE_BALANCE_SITTING.md) | `make isaac-free-sit`、`make verify-free-sit` | 自由根；独立物理记录 |
| 连续坐下与打字 | 见[连续演示](docs/experiments/dex5/CONTINUOUS_COMPUTER_DEMO.md) | `make isaac-computer`、`make verify-computer` | 仍使用手部 IK |
| 虚拟计算机屏幕 | 同上 | `make isaac-virtual-computer`、`make verify-virtual-computer` | 额外桌面依赖与机器人相机 |
| 椅子研究 | `make chair-plan`、`make demo-chair` | 真实物理 worker 待接入 | demo 为替身；训练入口不等于可用策略 |
| 验证反馈学习 | `make demo-learning-evidence`、`make demo-learning-feedback` | `make isaac-learning-single-hand`、`make isaac-learning-feedback`、`make isaac-learning-batch`、`make isaac-learning-desktop`、`make isaac-learning-action-health` | 单 `a`、批环境及真实 PTY → 仿真机器人 RGB-D/OCR 及动作映射已验收；尚未训练策略 |
| Dex3 站姿 | 见[分支登记](docs/BRANCHES.md) | 在独立工作树运行 | Isaac 6.0.1，固定基座双食指 |

物理入口的资产准备、环境变量和能力限制以对应实验说明为准。`make help` 只展示入口，不启动仿真或训练。

## 仓库布局

```text
src/vskill_os/    共享核心、技能、控制与可选适配器
configs/          契约、任务图、硬件和实验配置
tests/            核心与适配接口回归
tools/            资产准备、记录复核、校准与导出
docs/             总览、统一规划和专项设计
references/       文献与外部项目资料
reports/          耐久化验收报告，必须注明适用场景
artifacts/        本地大文件、录像、权重与运行目录（Git 忽略）
```

开始修改前阅读 [AGENTS.md](AGENTS.md) 和[开发协议](docs/CODEX_WORKPLAN.md)。原有详细首页及规划摘要保存在[历史快照](docs/archive/2026-09-25/ROOT_README.md)。本地工作目录和 `LocalCodeSandbox` 不构成面向不可信代码的操作系统隔离；真实软件任务仍需独立隔离适配器与测试门禁。
