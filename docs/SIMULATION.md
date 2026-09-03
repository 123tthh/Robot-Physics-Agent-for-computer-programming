# 纯仿真实现

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

## 模拟 Skill

| Skill | 成功状态 | 可注入故障 | 恢复 |
|---|---|---|---|
| `navigate_to_workstation` | `workstation_reached` | `NAVIGATION_BLOCKED` | `clear_navigation_path` |
| `align_with_chair` | `chair_aligned` | `CHAIR_NOT_FOUND` | `realign_with_chair` |
| `sit_and_prepare_for_typing` | `work_pose_ready` | `CHAIR_SHIFTED`、`POSTURE_UNSTABLE` | 重新对齐、稳定姿态 |
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
