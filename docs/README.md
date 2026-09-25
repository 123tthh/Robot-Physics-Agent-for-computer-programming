# 文档目录与维护规则

只需要先读[项目总览](overview.md)。专项文件已实际归入下列目录，所有仓库内文档链接同步更新。

## 本地目录

```text
docs/
  README.md、overview.md、ARCHITECTURE.md 等  项目入口与治理
  core/                                  核心规范与轻量验证
  simulation/                            仿真底座与资产
  experiments/                           按手型和姿态划分的实验
  research/                              学习、椅子和自改进研究
  integrations/                          语音与上游项目
  proposals/                             原始提案和 PDF
  phases/                                P0–P6 任务细则
  archive/                               历史快照（主项目）
```

源码、配置、测试、工具、`reports/` 和 `artifacts/` 保持原路径。报告与资产中的哈希、录像路径和复现脚本继续有效。各专项目录有自己的 `README.md`，可直接按文件夹浏览。旧专项文档路径的迁移可在 Git 重命名记录中追溯。

## 项目治理：每类事实只维护一次

| 内容 | 权威入口 |
|---|---|
| 目标与范围 | [overview.md](overview.md) |
| 分层、权限、代码归属 | [ARCHITECTURE.md](ARCHITECTURE.md) |
| 子项目状态与证据 | [WORKSTREAMS.md](WORKSTREAMS.md) |
| 优先级、依赖、旧编号映射 | [ROADMAP.md](ROADMAP.md) |
| Git 分支与工作树 | [BRANCHES.md](BRANCHES.md) |
| 任务模板、完成定义 | [CODEX_WORKPLAN.md](CODEX_WORKPLAN.md) |
| 本次整理记录 | [REORGANIZATION.md](REORGANIZATION.md) |

## 核心与公共接口

- [Skill 规范](core/SKILL_SPEC.md)、[控制阶段](core/CONTROL_PHASES.md)、[G1 身份与策略兼容性](core/G1_29DOF_5010.md)。
- [纯 Python 仿真](core/SIMULATION.md)、[混合仿真使用指南](simulation/sim_guidance.md)、[Benchmark](core/BENCHMARK.md)。
- [P0–P6 专项任务索引](phases/README.md)、[SIM 任务与验收细则](simulation/SIMULATION_PHASE_PLAN.md)。

## 仿真与实体输入基线

- 公共资产：[工位资产链](simulation/P0_P1_ASSET_CHAIN.md)、[导航房间资产](simulation/P1_NAVIGATION_ROOM.md)、[手型选择](simulation/HAND_SELECTION.md)。
- Dex3 历史基线：[资产审计](experiments/dex3/DEX3_ASSET_AUDIT.md)、[双手任务细则](phases/phase_2_bimanual_typing.md)。
- Dex5 基线：[资产审计](experiments/dex5/DEX5_ASSET_AUDIT.md)、[受支撑十指](experiments/dex5/DEX5_SEATED_DEMO.md)、[自由坐下](experiments/dex5/FREE_BALANCE_SITTING.md)、[连续输入与虚拟屏幕](experiments/dex5/CONTINUOUS_COMPUTER_DEMO.md)。
- Dex3 站姿：文档和报告位于独立工作树，入口见[分支登记](BRANCHES.md#3-dex3-站姿工作树)。

## 学习、改进与扩展研究

- 严格无 IK：[学习系统专项设计](research/learning/LEARNING_SYSTEM_ARCHITECTURE.md)、[训练方法调研](research/learning/DEX5_LEARNING_WITHOUT_IK.md)。
- 受约束自改进：[RSI 集成设计](research/rsi/RSI_INTEGRATION_PLAN.md)、[研究背景](research/rsi/RSI_RESEARCH_CN.md)、[相关代码库](research/rsi/RSI_CODEBASES.md)。
- 机器人—椅子：[研究与实验](research/chair/CHAIR_INTERACTION_RESEARCH.md)、[工程/训练协议](research/chair/CHAIR_INTERACTION_ENGINEERING.md)。
- 导航和语音：[P5 任务](phases/phase_5_navigation_and_voice.md)、[本地语音接口](integrations/VOICE_LLM.md)。
- 上游资料：[相关工程](integrations/RELATED_PROJECTS.md)、[设计依据](integrations/REFERENCES.md)、[参考资料目录](../references/README.md)、[外部项目机器目录](../configs/research/projects.json)。`projects.json` 列的是外部项目，不是本仓库子项目状态表。

## 提案与历史背景

原始提案保留作者内容，不作为当前实现承诺：[项目建议书](proposals/PROJECT_PROPOSAL_CN.md)、[idea](proposals/idea.md)、[ideaself](proposals/ideaself.md)。已有 PDF 位于 [proposals/idea.pdf](proposals/idea.pdf)。

旧 [PROJECT_OVERVIEW](PROJECT_OVERVIEW.md)、[PROJECT_HANDOFF](PROJECT_HANDOFF.md)、[BUILD_ORDER](BUILD_ORDER.md) 现为兼容导航；整理前全文连同旧首页、架构与总览保存在 [2026-09-25 历史快照](archive/2026-09-25/README.md)。历史段落中的“当前”“下一步”只适用于原快照。

## 更新约定

1. 新实验先声明子项目 ID、后端、手型/姿态、控制方式和证据等级。
2. 专项文档记录任务方法与细节，报告保存原始结果；跨项目状态更新 `WORKSTREAMS.md`。
3. 优先级变化只更新 `ROADMAP.md`；其他文档链接它，不复制完整“下一步”。
4. 分支/工作树变化更新 `BRANCHES.md`，记录日期和提交；未提交成果不能称为分支已交付。
5. 目标设计标注“拟建/待实现”；替身、物理、相机、软件和真机验收分开。
6. 历史报告不覆盖失败，不修改旧成绩；新能力引用新的验收记录。
7. `reports/` 放可追溯的小型证据，`artifacts/` 放大文件与运行目录；文档注明复现所需的本地文件。
