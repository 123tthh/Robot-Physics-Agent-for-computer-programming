# 分阶段规划索引

阶段按技术依赖排序，不代表固定工期。Codex 每次只领取一个带编号的任务；完成后更新该阶段的状态和证据。

跨越多个项目阶段的仿真实施拆分、退出门槛和推荐顺序见 [`../SIMULATION_PHASE_PLAN.md`](../simulation/SIMULATION_PHASE_PLAN.md)。

| 阶段 | 文件 | 当前状态 | 目标 |
|---|---|---|---|
| P0 | [`phase_0_runtime_baseline.md`](phase_0_runtime_baseline.md) | `DONE`/维护 | 固化 Agent–Skill–Verifier 基线 |
| P1 | [`phase_1_hybrid_simulation.md`](phase_1_hybrid_simulation.md) | `DONE`（资产/底座） | G1+双 Dex3、五脚轮椅、四墙房间、三工位/导航锚点、键帽接触、RTX 相机和 PTY |
| P2 | [`phase_2_bimanual_typing.md`](phase_2_bimanual_typing.md) | `PARTIAL` | 自动化事件门禁已通过，但 GUI 未确认可见实体触键 |
| P3 | [`phase_3_code_agent_loop.md`](phase_3_code_agent_loop.md) | `PARTIAL` | 接入真实隔离代码 Agent 和独立测试 |
| P4 | [`phase_4_posture_and_chair.md`](phase_4_posture_and_chair.md) | `PARTIAL` | 坐下、椅子调整、稳定工作姿态 |
| P5 | [`phase_5_navigation_and_voice.md`](phase_5_navigation_and_voice.md) | `PARTIAL` | 导航 L3 Skill 与本地语音入口 |
| P6 | [`phase_6_sim2real_self_improvement.md`](phase_6_sim2real_self_improvement.md) | `PLANNED` | 真机部署、评测与安全自改进 |

## 推荐当前工作顺序

```text
P1-T01 → P1-T02 → P1-T03
→ P2-T01 → P2-T02 → P2-T03
→ P3-T01 → P3-T02
```

在固定基座的键盘—终端—代码闭环通过前，不应把主要开发资源投入导航或完整坐下策略。

## 跨阶段规则

- P2 可以与 P1 的资产构建交错，但不能绕过 P1 的键盘事件桥；
- P3 可使用模拟键盘先开发，不得把模拟成功写成实体打字完成；
- P4、P5 的控制策略只能通过 Adapter 接入，不能改变 Agent 的低层权限；
- P6 必须复用前面阶段的 Skill 契约和 Verifier，禁止为真机另写一套不可比较流程。
