# P0–P6 专项任务索引

P 编号保留为能力分类及任务追踪，不再代表严格串行开发顺序。跨项目状态见[子项目登记](../WORKSTREAMS.md)，优先级与 SIM/学习/RSI 映射见[统一路线图](../ROADMAP.md)。以下只索引任务细则，不重复维护完成状态。

| 阶段 | 专项任务 | 对应子项目 |
|---|---|---|
| P0 | [Runtime、契约与证据](phase_0_runtime_baseline.md) | CORE |
| P1 | [Isaac 与桌面底座](phase_1_hybrid_simulation.md) | SIM |
| P2 | [双手、多指与组合键](phase_2_bimanual_typing.md) | D3-5 / D5；D3-6 在独立工作树 |
| P3 | [代码 Agent 与独立测试](phase_3_code_agent_loop.md) | DESKTOP |
| P4 | [坐下、椅子与姿态](phase_4_posture_and_chair.md) | D5 / CHAIR |
| P5 | [导航与本地语音](phase_5_navigation_and_voice.md) | NAV / VOICE |
| P6 | [Sim-to-real 与自改进](phase_6_sim2real_self_improvement.md) | REAL / IMPROVE |

各任务文件的状态是对应范围与日期的记录：Dex5 十指通过不完成 Dex3 历史可见触键；自由坐下不完成主动滑椅；`ls` 屏幕通过不完成真实编程任务。

同一能力只维护一个任务实现；学习和 RSI 通过共同契约与数据层接入，不能各自建立绕过 Verifier 的状态链。物理仿真操作细则见[SIM 规划](../simulation/SIMULATION_PHASE_PLAN.md)。
