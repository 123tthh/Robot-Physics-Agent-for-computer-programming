# Skill 接口规范 v0.1

## 契约字段

| 字段 | 含义 |
|---|---|
| `name` | 稳定、唯一的 Skill 名称 |
| `version` | 行为或接口版本 |
| `preconditions` | 开始执行前必须已验证的状态 |
| `local_postconditions` | 当前 Skill 的直接目标 |
| `next_skill_readiness` | 允许后续 Skill 开始的派生状态 |
| `resources` | 执行期间需要独占的资源 |
| `failure_codes` | 可预期的结构化失败码 |
| `recoveries` | Skill 声明的候选恢复能力 |

## 执行语义

```text
REQUESTED
→ PRECONDITION_CHECK
→ RESOURCE_LOCKED
→ EXECUTING
→ VERIFYING
→ SUCCEEDED / FAILED / REJECTED / CANCELED
```

- `REJECTED`：前置条件不成立，控制器未启动；
- `FAILED`：控制器失败或后置验证失败；
- `SUCCEEDED`：所有契约后置条件和请求附加条件均已验证；
- `CANCELED`：上层取消、安全层抢占或系统关闭。

## 命名约定

- 状态名使用小写 snake_case：`terminal_ready`；
- 失败码使用大写 snake_case：`TERMINAL_NOT_FOCUSED`；
- Skill 名使用动词开头：`press_chord`、`run_test`；
- 证据类型描述观测：`process_exit_code`、`keyboard_pose`。

## 证据

每项证据包含：

- `domain`：`physical`、`gui`、`software` 或 `system`；
- `kind`：证据类型；
- `value`：可序列化值；
- `source`：产生证据的传感器、模型或执行器；
- `confidence`：`[0, 1]` 置信度。

开发 Oracle（键盘事件、退出码、文件 API）和最终实体观测必须使用不同 `source`，方便评测时过滤。

## 当前限制

- Runtime 已实现调用级超时和取消结果，尚未实现 ROS 2 跨进程抢占；
- Registry 尚未从配置自动加载实现类；
- Verifier 目前由确定性规则与可信模拟证据组成，尚未接学习模型；
- 状态快照为内存对象，尚未加入 TTL 和失效条件；
- Resource Lock 只适用于单进程；
- G1 29DOF/5010 身份、分阶段控制和模拟适配器已实现；ROS 2/DDS、视觉模型、策略权重和真实代码 Agent 仍为待接可选适配器。
