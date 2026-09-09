# 纯仿真实现

> 本文记录轻量纯 Python 模拟器；已完成的 Isaac P1 资产/底座、P2 诊断运行器与后续方案见 [`sim_guidance.md`](sim_guidance.md)、[`P0_P1_ASSET_CHAIN.md`](P0_P1_ASSET_CHAIN.md)、[`phases/phase_1_hybrid_simulation.md`](phases/phase_1_hybrid_simulation.md) 和 [`phases/phase_2_bimanual_typing.md`](phases/phase_2_bimanual_typing.md)。

P1 保留两条路径：`make demo-p1` 是无需 Isaac 的可回放回归；`make isaac-p1` 在 Isaac Sim 5.1 中加载 G1 mode 15 / 5010 + 双 Dex3、74 键带标识的 PhysX 键盘与头部/键盘/全景三路 RTX 相机，再经同一迟滞/防抖桥生成 `KEYDOWN/KEYUP` 并驱动真实 PTY。后者同时保存渲染图和耐久化证据 `reports/isaac_p1_acceptance.json`。

P2 同样保留双路径：`make demo-p2` 运行模块化 ActionGraph 的单键、200 次组合键压力回归、短文本和纠错；`make isaac-p2` 用真实 Dex3 刚体/关节雅可比和 PhysX 接触执行 `Ctrl+Alt+T`，输出 `reports/isaac_p2_acceptance.json`。两条路径共享同一 ActionGraph 契约和独立 Verifier。

## 目的

当前模拟器用于在不接真机、ROS 2、相机和在线 Foundation Model 的情况下，验证项目最关键的软件假设：

- Skill 契约是否能阻止非法状态推进；
- 下一技能就绪性是否能被独立检查；
- 物理、GUI 和软件故障能否统一表达；
- Agent 是否能通过失败码和恢复记忆完成重试；
- 数字 Agent 的“完成”声明是否会被独立测试否决；
- 中断后能否从最近的已验证状态恢复。

## 两套状态

模拟器故意区分两套状态：

| 状态 | 所有者 | Agent 是否直接可见 |
|---|---|---|
| `SimulatedWorld.truth` | 环境真值 | 否 |
| `StateSnapshot.facts` | Verifier 确认的任务状态 | 是 |

Skill 操作 `SimulatedWorld`，返回 `claimed_states` 和 `Evidence`。Runtime 把状态副本交给 Skill，控制器即使直接修改副本也不能改变任务状态。Verifier 接受声明后，Runtime 才更新 `StateSnapshot`。

## 证据源

- `sim_robot`：导航、椅子对齐、坐姿和可达性；
- `sim_keyboard`：键盘位姿与按键事件；
- `sim_screen`：终端、焦点和代码 Agent 界面；
- `sim_code_agent`：代码产物和修复动作；
- `sim_test_runner`：退出码、标准输出和错误信息。

`terminal_ready` 与 `work_pose_ready` 是派生状态，不由单一 Skill 的文字声明决定：

```text
work_pose_ready
= seated_stable + screen_visible + hands_reachable

terminal_ready
= terminal_visible + terminal_focused + fingers_released + work_pose_ready
```

`test_passed` 必须包含 `sim_test_runner` 产生的退出码 0。

`seated_reposition_verified` 也不是控制器可直接写入的原子状态。Verifier
会从 `seated_locomotion_metrics` 重新检查命令非零、实际位移/转角、速度
RMSE、骨盆—座椅偏移、接触丢失时长、躯干/椅子倾角和停止后稳定。
仅仅保持坐姿但没有移动的策略会被拒绝。

## 模拟 Skill

| Skill | 成功状态 | 可注入故障 | 恢复 |
|---|---|---|---|
| `navigate_to_workstation` | `workstation_reached` | `NAVIGATION_BLOCKED` | `clear_navigation_path` |
| `align_with_chair` | `chair_aligned` | `CHAIR_NOT_FOUND` | `realign_with_chair` |
| `sit_and_prepare_for_typing` | `work_pose_ready` | `CHAIR_SHIFTED`、`POSTURE_UNSTABLE` | 重新对齐、稳定姿态 |
| `reposition_while_seated` | `seated_reposition_verified` | `SEAT_CONTACT_LOST`、`SEATED_POSTURE_UNSTABLE`、`SEATED_TRACKING_FAILED` | 恢复座面接触或重新稳定 |
| `calibrate_keyboard` | `keyboard_calibrated` | `KEYBOARD_NOT_FOUND` | 重新标定 |
| `press_chord` | `terminal_ready` | `ALT_MISSED`、`WRONG_KEY`、`TERMINAL_NOT_FOCUSED` | 重标定或恢复焦点 |
| `launch_code_agent` | `code_agent_ready` | `NETWORK_INTERRUPTED` | 恢复会话 |
| `submit_task` | `artifact_generated` | `EMPTY_TASK`、`NETWORK_INTERRUPTED` | 恢复会话 |
| `run_test` | `test_passed` | `TEST_FAILED`、`TEST_TIMEOUT` | `request_fix` |
| `report_task` | `task_reported` | — | — |

## 数字 Agent 模拟

`LocalCodeSandbox` 在独立工作目录生成 `hello.py`。软件恢复场景先写入错误输出，`run_test` 使用隔离模式的 Python 子进程执行文件并对比期望输出；失败后 `request_fix` 写入正确版本并重新测试。

该实现只接受模拟器内置模板，不把自然语言直接转换成任意可执行代码。

## 扩展方式

加入新仿真任务时：

1. 新建实现 `Skill` 协议的有限时域 Skill；
2. 定义完整 `SkillContract`；
3. 将实现注册到 `build_simulation_registry`；
4. 为新状态配置 Verifier 规则或可信证据；
5. 在 `configs/workflows` 中增加图节点；
6. 在场景中定义故障序列；
7. 添加 nominal、故障、恢复和错误验证测试。
