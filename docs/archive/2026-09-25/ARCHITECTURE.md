> 历史快照：2026-09-25 整理前的 `docs/ARCHITECTURE.md`。仅保留历史背景与原始成果说明，不决定当前优先级或能力状态。当前入口见 [项目总览](../../overview.md)。

# 架构说明

本文描述已有核心运行时及历史适配实现。2026-09-25 新增的[学习驱动整体架构](../../LEARNING_SYSTEM_ARCHITECTURE.md)是无 IK 路线的目标设计入口，覆盖训练、策略部署、全身技能与虚拟计算机闭环；目标模块尚未全部实现。

## 组件关系

```mermaid
flowchart TD
    U["用户目标"] --> A["Supervisor Agent"]
    A --> G["V-SkillGraph / Registry"]
    G --> R["Skill Runtime"]
    R --> E["机器人与数字环境"]
    E --> V["Verifier"]
    V --> L["Event Ledger / Recovery"]
    L --> A
```

## 责任边界

### Supervisor Agent

- 接收目标和结构化任务状态；
- 生成 L2/L3 Skill 调用；
- 根据结果选择继续、恢复、回滚或求助；
- 不直接访问 ROS 控制器或厂商 SDK。

### Skill Runtime

- 校验前置条件；
- 锁定手臂、相机、键盘和沙箱等资源；
- 执行有限时域 Skill；
- 调用独立 Verifier；
- 记录请求、证据、结果和失败码。

### Verifier

- 接收 Skill 的状态声明和原始证据；
- 使用规则、状态估计器、视觉模型或数字 Oracle 验证；
- 只向状态快照写入已验证事实；
- 学习式 Verifier 必须保持相同接口。

### Recovery Manager

- 将结构化失败码映射到局部恢复 Skill；
- 失败码无法处理时交回高层 Agent 重规划；
- 恢复次数由场景配置限制，避免无限循环。

### Graph Supervisor

- 从 JSON 加载并验证无环 SkillGraph；
- 只执行依赖已完成的就绪节点；
- 每个节点成功后保存 SQLite 检查点；
- 优先复用历史成功恢复记录，否则使用默认失败码路由；
- 支持中断后在新进程中恢复任务。

### ActionGraph Runtime

- 位于 L2/L3 Skill 与机器人 Adapter 之间，描述有限时域的笛卡尔、接触、键事件和屏幕动作；
- 从版本化模块组装 DAG，按资源锁执行，并在任一节点失败时统一释放按键；
- schema 明确拒绝关节位置、速度和力矩命令；
- Adapter 只能返回动作声明与原始证据，`IndependentActionVerifier` 独立检查位姿、邻键、力、键程、事件唯一性、释放净空和屏幕结果；
- 历史纯 Python 与 Isaac Adapter 使用同一模块和 Verifier，低层 DLS 位于 Isaac 边界。无 IK 学习路线将以直接关节策略适配器替代该执行路径，保留目标与验证语义；当前 DLS 实现不属于学习路线。

## 适配层

`interfaces.py` 定义机器人、感知、数字 Agent 和控制策略端口：

- `RobotAdapter`：未来接 ROS 2 Action 或 Unitree SDK；
- `PerceptionAdapter`：未来接屏幕 OCR、键盘/椅子检测；
- `CodeAgentAdapter`：未来接 Codex 沙箱；
- `TestRunnerAdapter`：未来接独立测试执行器。
- `WorkerAdapter`：跨进程 Isaac、桌面和模型 worker 的请求/取消/结果边界；
- `LocomotionPolicyAdapter`：站立行走速度策略；
- `SeatedLocomotionPolicyAdapter`：无动力轮椅坐姿速度策略与停止后再稳定；
- `WholeBodyPolicyAdapter`、`DexterousHandAdapter`：坐下/工作姿态与实体按键。

核心运行时不依赖具体机器人、模型或通信中间件，因此仿真、真机和离线回放可以共用同一套 Skill 契约。

## 状态写入原则

1. Runtime 把 `StateSnapshot` 副本交给控制器；
2. 控制器返回“状态声明 + 证据”，不能直接修改正式状态；
3. 原子观测必须来自配置的可信证据源；
4. 派生状态如 `terminal_ready` 必须由显式规则或学习式 Verifier 计算；
5. 未验证状态不能成为后续 Skill 的前置条件；
6. 所有状态变更必须能够追溯到调用和证据。
7. 易失事实由 Runtime 记录 TTL 和依赖；过期或依赖失效会在下一控制器启动前级联删除。
