# Codex 工作规划与任务协议

本文规定如何使用仓库中的 Markdown 规划 Codex 工作。目标是让每次开发任务边界明确、可独立验证，并能在新的 Codex 会话中继续。

## 1. 每次任务的阅读顺序

Codex 开始工作前应依次阅读：

1. 根目录 [`AGENTS.md`](../AGENTS.md)；
2. [`overview.md`](overview.md) 与 [`BRANCHES.md`](BRANCHES.md)，确认实际工作树；
3. [`WORKSTREAMS.md`](WORKSTREAMS.md) 中的子项目与证据边界；
4. [`ARCHITECTURE.md`](ARCHITECTURE.md) 与本文；
5. [`ROADMAP.md`](ROADMAP.md) 中当前任务，再读取对应阶段/专项文件；
6. 专项文件列出的代码、配置和报告。

不要要求 Codex 一次性阅读所有外部项目或实现所有阶段。

## 2. 状态约定

阶段和任务使用以下状态：

| 状态 | 含义 |
|---|---|
| `DONE` | 产物存在，验收命令和门槛均通过 |
| `PARTIAL` | 已有接口或模拟实现，但阶段门槛尚未通过 |
| `READY` | 前置阶段满足，可以领取 |
| `BLOCKED` | 缺少明确的外部资产、硬件信息或权限 |
| `PLANNED` | 尚未开始，且依赖未完全满足 |

文档中的状态是规划快照。Codex 完成任务后应更新任务状态、完成证据和剩余风险，而不是只勾选复选框。

## 3. 任务粒度

一个 Codex 任务应满足：

- 归属一个子项目，最多修改一个主要能力边界；允许映射多个历史阶段编号；
- 有稳定任务编号，例如 `P1-T03`；
- 可以在一次任务中实现和验证；
- 明确列出不做什么；
- 包含至少一个自动化验收或可复现的人工验收；
- 不以“接入整个 Isaac Sim”“完成真机控制”等开放目标作为单个任务。

如果任务同时跨越 Runtime、Isaac、语音、导航和真机，应先拆分 Adapter 契约、替身实现和单独集成任务。

## 4. Codex 任务模板

启动新任务时可直接复制以下提示词，并替换方括号内容：

```text
请先阅读 AGENTS.md、docs/overview.md、docs/BRANCHES.md、
docs/WORKSTREAMS.md、docs/ARCHITECTURE.md、docs/ROADMAP.md、
docs/CODEX_WORKPLAN.md 和 [专项文件]。

执行任务：[任务编号和名称]

目标：
[一个可验证的结果]

范围：
- [允许修改的模块]
- [需要新增的接口/测试/文档]

不在范围：
- 不提前实现后续阶段；
- 不让 Agent 输出关节/力矩命令；
- 不用控制器声明替代 Verifier 证据；
- 不把重型仿真或厂商依赖导入核心领域模型。

验收：
- [任务特定命令或场景]
- make check
- make demo

完成后：
1. 汇总改动、验证结果和剩余风险；
2. 更新对应任务证据与 docs/WORKSTREAMS.md；优先级变化只更新 docs/ROADMAP.md；
3. 不自动开始下一个阶段。
```

## 5. Codex 的实现规则

### 5.1 先契约，后实现

新增能力时优先确定：

```text
Skill 名称
+ 输入参数
+ 前置状态
+ 局部后置状态
+ 下一技能就绪状态
+ 独占资源
+ 失败码
+ 恢复 Skill
+ Evidence 来源
```

再实现模拟 Adapter，最后接真实系统。

### 5.2 Oracle 与最终观测分离

Isaac 真值、Pygame 内部事件、文件 API 可以作为开发 Oracle，但论文评测必须能切换到与真机一致的视觉、接触、OCR 和测试证据。两者必须使用不同的 `source`。

### 5.3 依赖隔离

- `src/vskill_os` 核心尽量保持标准库可运行；
- Isaac Sim、ROS 2、DDS、ASR、LLM 和厂商 SDK 放在可选 Adapter 或独立进程；
- 不同 Isaac/Isaac Lab 版本使用独立环境，通过协议连接；
- 模型权重必须有版本、许可证、哈希和兼容性 Manifest。

### 5.4 完成声明

Codex 不能因为单元测试通过就宣称真机能力完成。报告必须区分：

- 领域契约已实现；
- 纯 Python 模拟已验证；
- Isaac 动力学已验证；
- 硬件在环已验证；
- 真机已验证。

## 6. 状态与任务归属

跨项目状态统一维护在 [WORKSTREAMS.md](WORKSTREAMS.md)，实施顺序见 [ROADMAP.md](ROADMAP.md)，P0–P6 专项任务见 [phases/README.md](phases/README.md)。本文不再复制一份阶段状态表。

开始任务前检查实时 Git 状态。分支名不代表未提交文件已经隔离；现有多个分支指向相同提交的情况见 [BRANCHES.md](BRANCHES.md)。每个任务保存明确的起点提交、改动范围、后端和证据来源。

## 7. 通用 Definition of Done

一个任务只有同时满足以下条件才可标记 `DONE`：

1. 代码、配置或文档产物已经存在；
2. 新接口有最小可运行实现或明确替身；
3. 行为改动覆盖正常、失败和恢复路径；仅文档/导航整理检查链接与命令并运行现有回归，不新增镜像实现的测试；
4. Verifier 使用独立证据，而非复述控制器状态；
5. 失败码能定位原因且不会静默降级；
6. 运行 `make check` 与 `make demo`；
7. 文档说明能力等级和未验证部分；
8. 没有覆盖用户已有的无关改动。

## 8. 任务结束记录模板

每个阶段文件末尾可追加：

```text
完成记录：
- 任务：P?-T??
- 状态：DONE / PARTIAL / BLOCKED
- 修改：文件或模块
- 验证：命令、场景和结果
- 能力等级：契约 / 纯 Python / Isaac / HIL / 真机
- 剩余风险：
- 下一可领取任务：
```
