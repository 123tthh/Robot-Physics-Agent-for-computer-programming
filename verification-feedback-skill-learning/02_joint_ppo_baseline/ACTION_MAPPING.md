# VF-02-T01：动作映射健康检查

状态：`DONE`，2026-09-27，限以下诊断范围。本项交付低层归一化动作到直接关节目标的映射、独立保护、控制权交接与证据复核。策略尚未训练；本项使用固定骨盆、左食指单 `a` 的手工目标诊断。

## 实现与权限

| 文件（相对仓库根） | 职责 |
|---|---|
| `src/vskill_os/learning/policy_manifest.py` | 版本化 `ActionSchema`、`JointMask`、逐关节尺度、控制权与记录 DTO；标准库 |
| `src/vskill_os/adapters/isaac/learning/joint_action.py` | `JointActionMapper` 和监督端 `JointActionSession`；策略仅获得 `NormalizedPolicyPort` |
| `src/vskill_os/learning/action_evaluation.py` | 独立重算掩码、限幅、反馈与生命周期，不调用映射器或其 Guard |
| `src/vskill_os/adapters/isaac/learning/action_{smoke,verify,runner}.py` | 有时限物理采集、源文件/资产绑定、五案例独立验收 |
| `configs/learning/verification_feedback/action_scale_v1.json` | 23 维尺度及参考周期；实际限位来自 OEM URDF 与固定物理配置 |
| `tests/test_learning_action_mapping.py` | 软件契约、边界、交接、安全锁定与证据篡改负例 |

调用链：低层策略 → 归一化动作端口 → 具名映射 → 独立 Guard → PD/PhysX → 每个物理子步安全监测。Agent 仍只调用 L2/L3 技能。本项没有增加可供 Agent 直接操纵关节的工具，也没有实现 PPO、奖励或训练成功判断。

VF-01 的物理、反馈、批环境和相机采集源文件保持不变。新证据以独立 `action_mapping.json` 和 `action_manifest.json` 绑定原物理 bundle，旧报告可继续按其原始来源复核。

## 23 维动作与控制权

有序动作包括左臂/腕 7 维及 Dex5 独立驱动 16 维：肩 pitch/roll/yaw、肘、腕 roll/pitch/yaw，接着 thumb 0–3，再接 index/middle/ring/little 各 0–2。耦合末节不进入动作向量。schema 拒绝重排或只检查维数的映射。

当前 `joint_policy` 只拥有 `left_hand_index_1_joint`，其余 22 个目标由 `frozen_body_pd` 保持；23 维之外的身体与耦合坐标沿用 VF-01 的固定身体 PD 和资产耦合。控制权声明必须与 schema 的具名分区完全相同。重叠、遗漏、额外关节或错误控制者在 reset/恢复前被拒绝，不叠加两套目标。

冻结关节的归一化输入可非零，但映射强制屏蔽并记录原始提议；物理后端再次保护其写权限。非有限输入、范围超出 `[-1,1]`、乱序、过期或错误周期都拒绝。拒绝不会推进仿真、改变目标或消费动作序号。

## 周期、限幅与积分反馈

每关节的参考尺度默认 `0.01 rad / (1/60 s)`。实际步长为：

```text
alpha(dt) = delta_rad * dt / reference_dt_s
proposed  = last_executed_target + alpha(dt) * normalized_action
masked_delta[inactive] = 0
rate_limited_delta = clip(masked_delta, -maximum_speed * dt, +maximum_speed * dt)
filtered  = clip(last_executed_target + rate_limited_delta, URDF_lower, URDF_upper)
```

当前独立目标速度上限为 `0.3 rad/s`，因此满幅提议 `0.6 rad/s` 会被限速。30/60/120 Hz 的实际周期分别为 `1/30、1/60、1/120 s`，物理频率始终 240 Hz。尺度和速度约束同时随周期换算。

每步保存输入、未掩码的 proposed、filtered、后端动作索引，以及原始物理反馈中的实测位置/速度/执行目标。下一步直接从最终执行目标计算；不会累积被截掉的增量，也不会忽略 float32 回写。后端反馈与获准目标不符、时钟/回合不符或后端异常会锁定控制端口。

reset 沿用已审计的手工直接关节初态，读取稳定后的实测关节和实际命令目标；初始跟踪误差必须不超过 `0.05 rad`。初始化与动作路径不读取旧 IK 姿态或轨迹。

## 停止、保持、恢复

监督端 `pause()` 立即撤销旧端口及令牌。暂停期间监督端按控制周期执行 `hold_step()`：保持最后获准目标，继续 PD 和每个物理子步的安全监测。

恢复必须重新检查控制权、至少取得一次暂停后的新物理观测、确认实测限位，然后颁发新令牌，策略序号从零开始；物理动作序号连续。交接不改写关节状态或当前目标。受压关节允许正常 PD 跟踪误差，恢复不会偷偷把目标重置到实测位置。

暂停只保持目标，**不是按键释放，也不是真机急停**。若手指仍压住键，必须在后续动作中实际释放，Verifier 才计数。安全停止、后端异常和失配会锁定，不能用 resume 清除；必须由监督端 reset。时间上限保留 `truncated`，物理安全停止保留 `terminated`。

策略端口只公开 `observe/step`，不公开 reset、resume、安全配置、文本真值或后端对象。这是同进程 API 权限边界，不构成对恶意 Python 代码的操作系统隔离。本项为单 worker 仿真交接；跨进程真机资源锁和墙钟看门狗仍按运行时任务单独验收。

## 验收与重跑

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p test_learning_action_mapping.py -v
make isaac-learning-action-health
make verify-learning-action-health LEARNING_ACTION_RUN=/absolute/path/to/run
make check
make demo
```

物理入口使用现有 Isaac 5.1 / Isaac Lab 2.3.2 环境及固定 G1/Dex5 资产；每个周期在独立 worker 中运行，默认每个 worker 最长 600 秒。非零退出、超时、缺少记录或 `failure.json` 均不能验收通过，包括 Kit 错误退出时仍返回零的情况。

冻结案例：120 Hz 正常触键；60 Hz 正常触键、受压暂停恢复、重新 reset 后触键；30 Hz 正常触键。独立评估器同时要求原始物理完整按下—释放成功、全部动作都有对应原始记录、拒绝输入没有移动物理环境、控制权跨恢复/reset 不复用。

关节极限两端、全部 23 个动作列与后端故障注入在 CPU 合成契约中检查；物理诊断只开放左食指，不驱使机械关节撞限位。受压暂停案例另用原始键程与 Down/Up 时序证明暂停中键仍被压住，恢复后的真实释放才完成周期。

结果入口：[耐久化验收报告](../../reports/verification_feedback/vf02_t01_action_health.json)。原始动作、物理 JSONL、资产绑定与 worker 日志保留在 `artifacts/verification_feedback/vf02/action_health/`，由 Git 忽略；克隆后的重验需恢复这些记录及资产或重新采集。

本次运行：`run-20260926T220126-196863`（UTC 文件名；本地日期为 2026-09-27）。RTX 5090，Isaac 5.1 / Lab 2.3.2；五个案例全部通过原始物理与动作映射双重检查。

| 控制频率 | 案例 | 策略步 / 保持步 | 拒绝输入 | 结果 |
|---|---|---|---|---|
| 120 Hz | `normalized_press` | 264 / 0 | 0 | 完整按下释放 |
| 60 Hz | `normalized_press` | 132 / 0 | 0 | 完整按下释放 |
| 60 Hz | `paused_press` | 132 / 6 | 9 | 受压保持 0.1 s，恢复后真实释放 |
| 60 Hz | `press_after_reset` | 132 / 0 | 1 | reset 后旧端口拒绝，完整按下释放 |
| 30 Hz | `normalized_press` | 66 / 0 | 0 | 完整按下释放 |

合计 732 次物理控制步、2669 条原始物理样本、10 次不推进物理的拒绝输入。所有策略步均输入 22 个非零冻结列，最终目标始终保持；最大执行目标浮点回写差约 `1.26e-8 rad`。极限裁剪与立即反向另在 CPU 合成测试验证，没有把它们算作真机或物理撞限位证据。

本轮 `make check`：257 项，256 通过、1 项可选桌面依赖跳过；新增动作映射 19 项全部通过。`make demo` 通过。采集源码摘要为 `e8d25b1c05e45f2d5b83ba25105da55388a96a2d616f1eed3dfe830b11a05fa4`，报告另绑定每个 schema、mapping、物理 episode、原始资产和评估器摘要。

后续 [VF-02-T02：单键可学性诊断](SINGLE_KEY_PPO.md) 已实现观测/奖励、PPO 与 `10^6` 聚合环境转移，并完成独立复验；当前下一项为 **VF-02-T03：单键重复验收**。动作映射健康不能代替学习进展或多种子策略验收。

返回[阶段 2](README.md)和[研究总览](../README.md)。
