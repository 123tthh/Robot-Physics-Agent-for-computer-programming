# 站立双 Dex3-1 食指敲键训练框架

目标是 G1 站姿、双手各一根食指进行协调预定位、按键和 Shift 组合键。当前实现包含可直接运行的 CPU 训练、检查点续训、隔离种子的评估、课程学习，以及 Isaac Sim 6.0.1 环境接入协议。**没有启动 Isaac、没有占用 GPU，也没有得到 Isaac 物理训练结果。**

默认环境 `synthetic` 是明确标注的非物理运动学替身，用于验证训练流程和任务合约。它没有质量、摩擦、真实接触求解、关节动力学或站立平衡；力值由几何压入量合成，屏幕文本也由合成键盘事件生成。它不能证明真实机器人、物理仿真、OCR 或真实桌面输入通过。所有日志中的 `physical_acceptance` 始终为 `false`；外部适配器声明 `physics_sensor` 也不会自动提升这一标志。

## 运行与复现

在新工作副本根目录执行，仅需 Python 3.11+ 标准库：

```bash
PYTHONPATH=src python3 -m vskill_os.training train \
  --config configs/training/dex3_standing.json \
  --output artifacts/standing-training --iterations 3

PYTHONPATH=src python3 -m vskill_os.training evaluate \
  --checkpoint artifacts/standing-training/checkpoint.json \
  --episodes 8 --output artifacts/standing-training/evaluation.json

PYTHONPATH=src python3 -m vskill_os.training train \
  --resume artifacts/standing-training/checkpoint.json \
  --output artifacts/standing-training --iterations 2

PYTHONPATH=src python3 -m vskill_os.training evaluate \
  --checkpoint artifacts/standing-training/checkpoint.json \
  --text 'Hi!' --episodes 8 \
  --output artifacts/standing-training/custom-evaluation.json
```

`--iterations` 表示本次新增的 CEM 代数，续训会保留已有代数、课程阶段、策略、搜索方差和随机数状态。未提供 `--config` 时自动使用检查点配置；若同时提供配置，必须与检查点完全一致，避免无声改变复现实验。已有检查点或日志的目录必须显式 `--resume`，新实验必须使用新的输出目录，避免混合实验。

`--episodes 8` 默认表示**每个课程阶段**评估 8 次，总计 32 次；自定义 `--text` 时总计 8 次。训练、课程晋级验证、最终评估使用互不重叠的种子命名空间。验证仅控制晋级，最终评估不参与参数搜索；这是随机化条件的隔离，不是对未见词汇泛化的证明，默认词表较小且跨 split 共用。

输出文件：

| 文件 | 内容 |
| --- | --- |
| `checkpoint.json` | 合约版本、全部配置、12 个策略参数、搜索方差、RNG 状态、下一课程阶段、最近晋级验证 |
| `episodes.jsonl` | 每个候选的训练回合和验证回合：种子、目标、输出、事件数、失败码、奖励、耗时、力值、证据类别 |
| `evaluation.json` | 独立评估回合、各阶段统计、成功率、字符编辑错误率、成功回合字符/分钟、安全违例率、失败原因 |

检查点通过临时文件与原子替换写入。单元测试验证“连续训练 2 代”与“训练 1 代、读盘、续训 1 代”的参数、方差、RNG、阶段、验证结果一致。JSONL 是追加的诊断日志；突发进程退出发生在日志写入后、检查点替换前时可能出现重复代数记录，可用 `iteration/candidate/seed` 去重。

## 训练的内容与边界

训练基线采用标准库实现的 Cross-Entropy Method（CEM），并非 PPO。每一代生成参数候选，在完全相同的随机化种子上比较回报，选出精英，重新估计均值和方差。搜索保留当前均值候选，并给方差设置下限。CEM 适合这里的 12 维小型残差控制器，便于在 CPU 上快速验证整个流程；大规模学习可通过相同环境合约增加 PPO/SAC 训练器。

每个食指的三个轴都有一个误差增益和一个偏置，共 12 个参数。策略观察的是名义指尖目标和实际指尖的误差，输出两个三维 Cartesian residual。每只手残差的欧氏范数最多 4 mm；单控制周期移动上限默认 6 mm。残差只修正控制器的有限时域路点，**Agent 和训练策略都不产生关节命令**。IK、关节控制、碰撞约束、机械限位和急停应由独立控制适配器执行。

名义路点控制器已经提供基本的接近、按下、释放行为。训练优化的是跟踪误差、偏置和速度表现；不能将其描述为从零学会双臂运动、学习全身平衡，或已经通过 sim-to-real。固定基座站姿也是演示范围的一部分；后续自由站立需要专门的平衡控制器与传感验证。

规划直接复用 `StandingTypingPlanner` 的全局双手分配及预定位波次。可并行移动的左右食指先一起到达目标上方，键盘提交仍严格保持文本顺序。Shift 必须由另一只手的食指保持，依次执行 Shift down → 主键 down → 主键 up → Shift up。两根食指不能执行三键同时按住的组合。

课程按以下顺序、在独立验证成功率达到 80% 后晋级：

1. 单键：验证到达、按下、释放。
2. 双手交替：例如 `flfl`、`akak`；通过全局规划形成双手预定位和有序敲击。
3. 短文本：例如 `hello`、`robot`、`skill`。
4. Shift 组合：例如 `Hi!`、`Aa`、`Go!`。

默认随机化包含键盘三轴平移 ±4 mm、控制跟踪偏置 ±1 mm、跟踪增益 0.65–1.0、合成接触力比例 0.75–1.25。每次 `reset` 的局部 RNG 来自记录的种子，不修改 Python 全局 RNG。物理适配器必须说明实际支持的随机化项目；摩擦、质量、关节时延、键帽弹簧参数等真实物理随机化尚未在 CPU 环境中实现。

## 独立验证与终止

训练器计算奖励，`TrainingVerifier` 独立计算成功与失败。适配器没有 `done=True` 或“成功完成”可以直接被信任的接口。

奖励包含已验证事件增量 `+3`、每步 `-0.02`、指尖目标距离代价 `-0.5 × 距离`、残差变化代价 `-2 × 变化量`、完整验证成功 `+30`、验证失败 `-30`。奖励高并不等于成功。

验证需要满足：

- 连续、不可重复的 observation tick，reset 时没有残留事件/按键/文本。
- 每次 keydown 必须有**指定手的 index** 接触、至少 2 mm 键程、至少 0.05 N 法向力，以及一致的当前按下集合。
- 每次 keyup 必须移除对应按键和接触；最终所有键释放、所有指尖接触解除。合成适配器使用 3 mm 离面释放滞回。
- Shift 和主键的 down/up 顺序完整匹配规划；文本每个前缀与已验证字符一致。
- 姿态、滑移、禁碰、手间距、接触力、每周期位移通过独立安全阈值检查。最大力 8 N、两手最小距离 25 mm。

失败立即 `terminated=true`；步数上限单独标为 `truncated=true` / `TIME_LIMIT`。失败、超时或异常都会调用适配器的有界 `emergency_release()`，记录最终实际接触、按下集合、指尖坐标及 tick；释放失败会停止训练，不开始下一回合。原始异常保留，释放异常附加说明。清理不会把失败升级为成功。4 mm 残差上限还受代码中的固定安全最大值限制，配置无法将其提高。

主要失败码有 `KEYDOWN_WITHOUT_INDEX_CONTACT`、`KEY_EVENT_ORDER_MISMATCH`、`KEYUP_WITHOUT_PHYSICAL_RELEASE`、`DISPLAY_TEXT_MISMATCH`、`STANDING_STABILITY_LOST`、`FOOT_SLIP`、`HAND_SEPARATION_VIOLATION`、`FINGERTIP_VELOCITY_LIMIT`、`CONTACT_FORCE_LIMIT`。

## Isaac Sim 6.0.1 接入协议

核心包不导入 Isaac Lab、Torch 或 Isaac Sim。训练器支持显式工厂：

```bash
# 示例接口名；需要先实现对应模块。本仓库不伪装已有物理训练适配器。
PYTHONPATH=src /path/to/isaac-sim-6.0.1/python.sh -m vskill_os.training train \
  --config configs/training/dex3_standing.json \
  --adapter your_package.isaac6_training:make_environment \
  --output artifacts/isaac6-training --iterations 3
```

外部模块需要实现以下协议，完整类型在 `src/vskill_os/training/contracts.py`：

```python
def make_environment(*, config: TrainingConfig) -> TypingEnvironment:
    ...

class Isaac6Environment:
    backend_name = "isaac_sim_6_0_1_dex3_index"
    evidence_kind = "physics_sensor"

    def reset(self, *, goal: TypingGoal, seed: int,
              randomization: DomainRandomization) -> Observation:
        ...  # 清理场景、文本和传感状态；tick=0；按 seed 随机化

    def step(self, action: CartesianResidual) -> Observation:
        ...  # 独立安全限幅 -> 控制器/IK -> 固定 dt 物理步进 -> 读取传感器

    def emergency_release(self) -> Observation:
        ...  # 有限步数撤离；实际传感器验证释放，禁止伪造 all-keys-up

    def close(self) -> None:
        ...
```

`Observation` 必须包含左右食指实测世界坐标、控制器名义路点、逐步接触与键盘边沿事件、当前按键集合、文本证据、站姿与安全观测。工厂通过关键字参数接收配置，创建顺序和生命周期由适配器负责；`reset/step/emergency_release/close` 已通过独立假适配器测试。

物理接入时需要完成以下具体映射：

1. 复用站立演示的 G1 + Dex3-1 articulation、键盘位置和全局双手计划，为每个波次设置有限时域目标。
2. 将 residual 加在左右 index 路点上；把 IK 和关节控制限制在适配器内部，禁用 thumb/middle 的敲击接触。
3. 从实际食指接触传感器和键帽关节位移读出 `ContactEvidence`，不得从目标、动作或策略输出推导“接触成功”。
4. 从键盘边沿/键程状态产生 `KeyEvent`，文本采用独立终端/显示读回或 OCR；合成环境内的字符串缓存不能替代这一步。
5. 明确固定基座范围，读取基座姿态、脚部状态和碰撞；在控制器内实施关节级安全，训练器只负责额外的合约验证。
6. 在独立 GPU/仿真窗口中先校验传感证据、释放滞回、坐标与时间步，再开展物理训练和验收。本次未运行该集成，不应干扰现有 Isaac Sim 5.0.1 任务。

由于本次遵循“正在运行的 Isaac Sim 不争抢资源”，物理训练适配器仍是清楚标注的接入扩展点，不是完成的 Isaac 训练后端。

## 验证命令

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p test_standing_training.py -v
make check
make demo
```

专项测试覆盖四阶段、双手/Shift 分工、边界和 NaN 动作、伪造屏幕完成、无接触按键、错误手指、未释放接触、错序 Shift、陈旧帧、安全故障、超时、域随机化、split 隔离、断点续训一致性，以及外部环境工厂和资源关闭。
