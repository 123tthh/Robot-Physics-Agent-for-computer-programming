# VF-01-T02：单环境物理 reset/step

状态：`DONE`，限固定工位单 `a` 的 Isaac 物理原语。2026-09-26，从 `746dee2` 建立 `codex/vf01-single-hand-env`。本项实现固定工位、单左手的直接关节环境与独立物理证据复核。发布验收结果见[报告](../../reports/verification_feedback/vf01_t02_physics.json)。阶段 VF-01 整体仍为 `PARTIAL`。

## 运行

本机使用 Isaac Lab 2.3.2 / Isaac Sim 5.1.0、RTX 5090。先准备仓库已有的固定版本 Unitree 资产，再运行：

```bash
make dex5-assets
make isaac-learning-single-hand
```

第二条命令在 `artifacts/verification_feedback/vf01/physics/run-<UTC>/` 新建独立运行目录，执行 10 个诊断回合并由父进程独立验收。支持通过 `ISAACLAB_ENV_PREFIX`、`ISAACLAB_ACTIVATE`、`ISAACLAB_PYTHON` 指定现有 Isaac 安装；不会自动安装或更换环境。每次运行有 600 秒上限，原始失败目录保留。

按输出中的完整目录复核，无需启动 Isaac/GPU：

```bash
make verify-learning-single-hand LEARNING_PHYSICS_RUN=artifacts/verification_feedback/vf01/physics/run-<UTC>
```

也可只评估一个回合：

```bash
PYTHONPATH=src python3 -m vskill_os.adapters.isaac.learning.verify \
  artifacts/verification_feedback/vf01/physics/run-<UTC>/press_1
```

复核仍需匹配的本地源码、固定资产、转换后 USD 和原始文件。Git 中的小型摘要不能单独重建物理证据；换机器时需准备资产并重新采集。`0` 表示独立验收满足该入口的判据；套件入口要求正例通过、负例按预期拒绝，不表示每个回合都成功。

## 实际验收

运行 `run-20260926T092016-114661` 的 10 个案例均符合预期：3 个完整触键正例通过、7 个失败/截断回合拒绝；两次重置对照的 69 维初始关节位置、速度、指尖及事件时间差均为零。正例每回合 2.1 秒仿真时间，最大键程约 2.615 mm、峰值接触力约 0.921 N。

`make check` 共 196 项，195 通过、1 项因可选桌面依赖缺失跳过；`make demo` 返回 `succeeded`。新增 15 项 CPU 回归用于动作保护、原始/派生记录一致性、文件修改拒绝与工作进程异常。诊断没有进行策略训练或成功率统计。

## 固定的物理设置

| 项目 | 当前值 / 边界 |
|---|---|
| 支撑 | 骨盆世界坐标 `(0, 0, 0.625)` 米，固定基座；躯干、双腿与非操作关节由 PD 保持 |
| 椅子 / 桌面 | 椅面 0.44 米；桌面 0.766 米；单键实验不证明自由坐姿稳定 |
| 手型与资产 | Unitree G1 29 身体坐标 + 双 Dex5 40 手指坐标，共 69；8 个末端关节使用已审计的近似耦合 |
| 动作 | 左臂/腕 7 + 左手 16 个独立驱动，固定有序 23 维；本次仅开放 `left_hand_index_1_joint` |
| 初态 | 手工声明直接关节角，未使用旧 IK 姿态、控制循环、示范或初始化数据 |
| 执行 | 有界目标增量 → 关节限位 / 0.3 rad/s 目标限速 → PD + 重力补偿；掩码、所有权、序号、有效期始终检查 |
| 时间 | 240 Hz 物理，4 子步 / 动作，60 Hz 控制；reset 先稳定 240 子步；所有子步均执行独立安全检查 |
| 接触 | 6 mm 指尖球形碰撞近似；身体碰撞接触偏移明确设为 1 mm；键帽行程 4 mm、弹簧 350 N/m、阻尼 2 N·s/m，键帽目标始终为零 |
| 键位 | `a` 键体中心 `(0.5661, 0.1298875, 0.791)` 米，顶面 0.796 米；记录全部 74 键 |
| 诊断动作 | 食指目标从 0 缓慢增至 0.26 rad，再回到 0；它是工程诊断，不是训练所得策略 |
| 安全停止 | 关节位置 / 速度、键帽行程 / 零目标、12 N 接触力、骨盆位移及躯干姿态；策略不能关闭 |

几何和动作配置冻结在 [single_hand_physics_v1.json](../../configs/learning/verification_feedback/single_hand_physics_v1.json)。本次设置只承诺 `a` 的单次原语，未验证 Backspace、相邻字符、少量键可达集合或短串。

## 工程边界与调用契约

| 模块 | 作用 |
|---|---|
| [environment.py](../../src/vskill_os/learning/environment.py) | 标准库 `ResetSpec / JointTargetAction / JointControlSpec / PolicyObservation / StepResult / TypingEnvironment`；独立动作过滤 |
| [physical.py](../../src/vskill_os/learning/physical.py) | 原始 PhysX 数据、实际目标、动作执行记录和安全检查 |
| [single_hand_env.py](../../src/vskill_os/adapters/isaac/learning/single_hand_env.py) | Isaac 建场、唯一仿真所有者、reset/step/finish/close、逐子步事件和记录 |
| [config.py](../../src/vskill_os/adapters/isaac/learning/config.py) | 固定 OEM 来源、69 坐标无支撑滑轨资产、手工初态与 23 维映射 |
| [physical_evaluation.py](../../src/vskill_os/learning/physical_evaluation.py) | 从原始张量重算键程、接触、世界坐标、安全与所有实际目标；独立检查事件和完整释放 |
| [verify.py](../../src/vskill_os/adapters/isaac/learning/verify.py) | 文件 / 源码 / 资产 / 场景对应检查，单回合物理原语验收 |
| [smoke.py](../../src/vskill_os/adapters/isaac/learning/smoke.py)、[runner.py](../../src/vskill_os/adapters/isaac/learning/runner.py) | 有限诊断动作；父进程限时收集并检查确切目录，避免 Isaac 退出码掩盖异常 |

```text
reset(ResetSpec) -> PolicyObservation, EpisodeMetadata
step(JointTargetAction) -> StepResult
finish() -> 保存终态，仍需独立 Verifier 判定
close() -> CleanupRecord，可重复调用
```

这些关节接口只供低层适配器使用，不注册为 Agent 可直接调用的动作。高层依然通过 L2/L3 技能；本项尚未交付学习技能或 Runtime 集成。`reward_terms` 为空，不预设 PPO 奖励。

`PolicyObservation` 仅含本体、目标键、接触/键程和上一目标增量。这是明确声明的特权状态输入，未包含文本 Oracle、屏幕、验证结论或隐藏 `info`。监督侧记录与策略观察分开；按请求反馈、延迟/缺失、焦点与完整的权限隔离属于下一项 T03。

reset 只在回合边界写入根位姿与关节状态；先保存上一终态，再清理数字状态，然后产生新的控制令牌。step 不写位姿，不重置键帽，不自动重试。过期 / 映射错误 / 旧令牌动作在物理推进前终止；时间上限返回最后观察并标记 `truncated`。物理错误与初始化错误都保留记录，零时长回合仅允许取消或安全停止。

## 证据与验收

每回合保存 `task.json`、`episode.json`、`trace.json`、`config.json`、`reset.json`、逐步 `physics.jsonl` 和来源清单 `manifest.json`；整个运行保存 `scene.usda`、`process.json`、`collection.json`、`suite.json`。数字收尾另存 `cleanup.json`，`physical_release_claimed` 固定为 `false`。

时间使用 `isaac_sim_seconds`，每回合从零开始，记录按环境 / 回合 / 流隔离。每个物理子步保存 69 个实际关节状态和目标、74 个原始键帽位置/目标/世界坐标/接触力，以及指尖、骨盆与躯干姿态。关键文件和相关源码、URDF、转换 USD、场景均绑定哈希；哈希提供内容对应，不能认证传感器真实性。

评估器不调用事件桥计算参考答案。它从原始键程/接触重算 KeyDown/KeyUp，要求恰好一次 `a` 完整按下、释放后持续无接触、全部键释放、文本状态机因果结果为 `a`，并检查动作经过声明的限位限速。`maximum_hold`、shutdown 或 reset 的管理释放均不能满足此判据。键帽世界坐标也必须与冻结场景一致，防止只检查局部键程而漏掉底座错位。

| 诊断回合 | 判据 |
|---|---|
| `press_1 / press_2 / press_after_reset` | 同种子完整触键并释放；比较初态、指尖与事件时间 |
| `miss` | 幅度不足，不能凭“动作执行完”通过 |
| `stuck` | 文本为 `a`，但未物理释放，拒绝 |
| `timeout_release` | 数字层超时 KeyUp 后仍保持接触，拒绝 |
| `wrong_mapping / stale_owner / expired_action` | 立即停止，零物理推进，保留最后观察 |
| `time_limit` | 0.05 秒准确截断；保留终态，不作为成功 |

复现容差在验收前固定：关节角 `1e-4 rad`、速度 `1e-3 rad/s`、指尖 `1e-4 m`、事件时间一个物理步。它们用于同场景重置检查，不是学习成功率或泛化区间。

开发阶段的失败保留在 `artifacts/verification_feedback/vf01/physics-development/`：包括初态与椅面碰撞、无效支撑配置、键盘底座被默认初态移到原点，以及行程不足。另外，`physics/run-20260926T090912-791262`、`run-20260926T091346-797081`、`run-20260926T091702-217274` 的回合判据符合预期，但重置复现失败，原报告保留。定位到凸包碰撞与椅缘重叠后，将固定骨盆和工作面整体上移 15 mm；同时对导入碰撞体取消实例化以落实并审计 1 mm 接触偏移。最终验收仍使用原来的严格复现容差。

本项不声称完成视觉读屏、真实桌面、PPO、双手、自由坐姿或 16/64 环境。下一项为 **VF-01-T03：文本状态与反馈隔离**，之后实施批环境与吞吐测试。

返回[阶段 1](README.md)或[研究主线](../README.md)。
