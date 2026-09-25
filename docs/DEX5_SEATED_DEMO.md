# G1 + 双 Dex5-1：受支撑坐下与十指打字 Demo

状态：`DONE`（受支撑仿真基线）。任务编号：`SIM03-D5` / `P4-D5`。状态快照：2026-09-23。

普通椅子与该录像已归档；去除骨盆支撑、采用 P1 轮式椅子的后续工作见 [自由平衡坐下实验](FREE_BALANCE_SITTING.md)。两条支线分别验证，本文的十指成功记录不证明自由平衡。

本演示沿用仓库的 Agent → 有限时域 Skill → 独立 Verifier → 低层安全控制分层，新增 **G1 29DoF mode 15 / 5010 + 左右两只官方 Dex5-1**。目标是在 Isaac Sim 5.1 中观察受支撑的坐下动作、双手预定位和逐键按压，并保存可检查的证据。

Isaac `accepted-seed7` 完整运行已通过独立验证：执行受支撑坐下与预定位，双手十指实际输入 `"asdf jkl; "`，10 次 KeyDown/KeyUp 对应匹配，结束时全部按键释放。物理时长 26.454 秒、峰值键帽接触力约 1.384 N；保存的 1280×560 双视角录像长约 27.47 秒（包含阶段截图停帧）。此前单指与十指诊断用于调试，早期失败报告保持 `failed`，不混入验收次数。是否完成某次运行，应同时检查该次报告、退出码、接触/键程/事件轨迹与可见画面；不能用回放通过、资产加载或视频文件存在替代。

证据入口：[两次完整运行的独立验收](../reports/isaac_dex5_acceptance.json)、[首次完整原始记录](../reports/isaac_dex5_runs/seed7-video.json)、[复跑原始记录](../reports/isaac_dex5_runs/seed7-no-video.json)、[完成画面](../reports/isaac_dex5_runs/finished.png)、[本机录像](../artifacts/dex5/accepted-seed7/demo.mp4)。135 项测试、`make demo` 和 `make demo-dex5` 均通过；这与 Isaac 物理验收分别记录。

| 完整运行（种子 7，相同实现/配置哈希） | 物理时长 | 峰值键帽力 | 结果 |
| --- | --- | --- | --- |
| `accepted-seed7`，持续录像 | 26.454 s | 1.384 N | 十指 10/10，事件与文字一致，全部释放 |
| `repeated-seed7`，仅阶段截图 | 32.396 s | 0.779 N | 十指 10/10，事件与文字一致，全部释放 |

两次均由控制器退出后的记录复核通过，完整原始观测保存在仓库 `reports/` 中。复现结论是此环境、配置与种子下两次成功，**不宣称逐帧、跨硬件位级确定性**；持续渲染与仅截图的执行存在时序差异。这两个样本也不足以估计总体成功率。

## 演示边界

| 项目 | 本 Demo 的范围 | 尚未证明的能力 |
| --- | --- | --- |
| 机器人与手 | 官方 G1 身体与双 Dex5-1 URDF/STL，固定源提交和文件哈希 | 真实转接板、装配惯量与硬件兼容性 |
| 坐下 | 独立命名的骨盆前后平移与升降支撑机构配合腿部轨迹，固定椅子 | 自由基座平衡、真实坐下策略、载人移动椅动力学 |
| 手指执行 | 双手各五指，有限时域笛卡尔目标；关节控制位于 Isaac Adapter | 真机闭环、学习策略或人类速度的连续打字 |
| 接触 | 真实 Dex5 末节刚体附着半径 6 mm 的球形接触垫，PhysX 计算碰撞与键程 | 原生指腹接触精度及真实摩擦、触觉标定 |
| 文本证据 | 键程/接触产生事件，事件接收端保存字符；视频叠加显示读回文本 | 相机 OCR、桌面终端焦点、真实代码 Agent 工作流 |
| 验证来源 | Isaac articulation/contact Oracle；回放另用 `replay` 标签 | camera-only、HIL 或真机可观测验收 |

报告中的 `dynamic_sitting_verified`、`camera_ocr_verified`、`real_robot_verified` 保持 `false`。即使受支撑的打字实验成功，也不会自动提升原 P4 坐下任务为 `DONE`。原双 Dex3 路线及其 `PARTIAL` 状态单独保留。

## 资产与环境

资产细节见 [Dex5-1 资产审计](DEX5_ASSET_AUDIT.md) 与 [资产锁文件](../configs/simulation/g1_29dof_5010_dex5_assets.json)。关键约束如下：

- 官方 `unitreerobotics/unitree_ros` 固定提交为 `7d6075f7f58588b189b940130e3edab3c839b2df`；下载后逐文件检查哈希，并保留 BSD-3-Clause 许可证。
- 每手有 20 个运动坐标，其中 16 个主动、4 个耦合；不能将 20 个坐标写成 20 个独立电机。两手合计 32 个主动与 8 个耦合坐标。
- 源 URDF 没有给出完整末节传动关系；当前按关节限位比添加线性 `mimic`。右手部分零力矩/零速度上限和左手一个非标准关节名有明确的仿真修复记录。
- 双手安装位姿为仿真近似；前后平移支撑 `seating_fixture_slide_joint` 和升降支撑 `seating_fixture_lift_joint` 是两个额外棱柱坐标，不属于 G1 或 Dex5。机器人从椅前 `x=0.25 m` 生成，先弯腿，再执行向后平移与下降，避免初始站姿腿部与座面重叠。
- 使用 Isaac Sim **5.1.0**、Isaac Lab **2.3.2**。默认本机环境为 `/home/descfly/IsaacLab-2.3.2` 与 `/home/descfly/miniconda3/envs/isaaclab-2.3.2`。实际 GPU、Torch、种子、物理步长和 URDF/USD 哈希由场景报告记录。
- 物理步长为 `1/240 s`，两路 RTX 相机提供总览和键盘近景；视频生成需要 `ffmpeg`。纯 Python 回放不依赖 Isaac、Torch 或 GPU。

Isaac Lab 2.3.2 的 `UrdfConverterCfg.convert_mimic_joints_to_normal_joints` 字段名与当前实际传参行为容易混淆：本版本将该值直接传入导入器的 `set_parse_mimic`，本机 Isaac Sim 5.1 中设为 `True` 才保留 PhysX mimic。Adapter 在 USD 加载后独立要求恰好 8 个 mimic schema；实际诊断运行已记录 `mimic_joint_count=8`。这是已核对的当前版本行为，不应仅按字段名称更改，也不能推断其他版本相同。

导入器默认产生柔顺 mimic（自然频率 25、阻尼比 0.005），在本实验中会出现明显末节偏差。场景将八处属性均设为 0，按 PhysX 定义采用硬联动，使实际约束与运动学规划使用的近似比例一致；预检查核对引用、比例及这两个属性，姿态初始化后还检查实测比例残差不超过 0.02 rad。该硬联动仍是仿真假设，不代表官方真实传动已标定。

末节原始碰撞子树显式停用，检查包含 USD 实例代理，确保每个末节只保留一个球形接触垫；官方视觉与惯量保留。按压阶段经指尖 Jacobian 转置施加关节力前馈，法向上限 1.2 N、爬升 2 N/s；横向使用 500 N/m 刚度、10 Ns/m 阻尼和每轴 2 N 限幅，抑制受力后向邻键偏移。耦合子关节力矩折算到主动关节；键盘只具有零位弹簧，不接收下压目标。所有接触力仍由 PhysX 独立测量并通过 12 N 守卫。机器人与场景发生外部碰撞，但机器人自身碰撞关闭；本实验不验证全身自碰撞安全。

[离线姿态种子](../configs/simulation/dex5_postures.json) 保存抬手、键盘上方预定位和安全坐下上肢姿态，绑定合成 URDF 的 SHA-256、指尖局部偏移及参考骨盆位姿。有限关节范围内的运动学求解与手/腕 AABB 净空检查只提供初始化候选；不证明执行时没有下垂、碰撞或过力。Adapter 检查 URDF 哈希后执行有界插值，再使用实测状态细化；重力前馈留在低层 Adapter 内，且不能替代接触与安全守卫。

## 复现步骤

在仓库根目录执行：

```bash
cd /home/descfly/humanoid-computer-agent
make check
make demo
make dex5-assets
make demo-dex5
make isaac-dex5-inspect
make isaac-dex5
```

需要可见窗口时执行 `make isaac-dex5-gui`。一次只运行一个 Isaac 实例，以免共享 GPU、场景输出或 Kit 资源产生干扰。

| 入口 | 作用 | 如何解释结果 |
| --- | --- | --- |
| `make dex5-assets` | 下载缺失的锁定资产、检查哈希、组合双 Dex5 与显式支撑机构 | 资产准备结果，不证明物理动作成功 |
| `make demo-dex5` | 生成确定性十指规划、回放观测和独立判定 | `capability=replay`，不能冒充 Isaac |
| `make isaac-dex5-inspect` | 导入资产、执行受支撑坐下与双手预定位，保存检查产物 | `status=inspected` 只表示检查运行完成 |
| `make isaac-dex5` | 无界面物理演示、渲染记录与独立验证 | 仅 `status=passed` 且证据完整才算该次受支撑实验通过 |
| `make isaac-dex5-gui` | 使用相同场景提供可见窗口 | 仍需报告与可见物理动作一致 |

无需下载即可执行回放；需要完整轨迹时可直接指定输出：

```bash
PYTHONPATH=src python3 -m vskill_os.simulation.dex5_typing \
  --output artifacts/dex5/replay.json

# 预期失败：应输出 NO_CONTACT，进程退出码为 1。
PYTHONPATH=src python3 -m vskill_os.simulation.dex5_typing \
  --inject-fault no_contact \
  --output artifacts/dex5/replay-no-contact.json
```

Isaac Adapter 入口为 `python -m vskill_os.adapters.isaac.dex5_demo`，须在已激活的 Isaac Lab 环境中运行。支持 `--output-dir`、`--seed`、`--text`、`--phase-timeout`、`--inspect-only`、`--no-headless`、`--no-video` 与 `--rebuild-assets`。`--start-seated` 仅用于跳过坐下路径的按键诊断，报告会记录 `seating_transition_executed=false`，完整验收不得使用它。`--no-video` 仅渲染阶段截图，保留全部物理采样。首次由资产组合到 USD 的转换应保存报告；改变组合资产后可用 `--rebuild-assets` 显式重建。

默认 Make 环境可以通过 `ISAACLAB_ROOT`、`ISAACLAB_ACTIVATE`、`ISAACLAB_PYTHON` 覆盖；每次实验建议使用独立目录，例如 `make isaac-dex5 DEX5_RUN_DIR=artifacts/dex5/repeat-02`。附加选项使用 `DEX5_ARGS`，例如 `DEX5_ARGS='--seed 7 --no-video'`。

完整录像和同种子复跑可按以下顺序执行；第二次保留物理与截图证据，减少持续渲染开销：

```bash
make isaac-dex5 DEX5_RUN_DIR=artifacts/dex5/accepted-seed7
make isaac-dex5 DEX5_ARGS='--seed 7 --no-video' \
  DEX5_RUN_DIR=artifacts/dex5/repeated-seed7
PYTHONPATH=src python3 tools/verify_dex5_recording.py \
  artifacts/dex5/accepted-seed7/report.json \
  artifacts/dex5/repeated-seed7/report.json \
  --video artifacts/dex5/accepted-seed7/demo.mp4 \
  --output reports/isaac_dex5_acceptance.json
```

最后一步在控制器退出后重新读取全部保存观测、运行独立 Verifier、交叉核对事件流，并要求十指默认序列、实际执行坐下路径和资产/硬联动检查通过。单指或 `--start-seated` 诊断即使打字成功，也不满足完整验收。它检查记录内部的一致性，不认证被人为篡改的传感器数据。生成的 USD 场景可能含本机资产路径；跨机器复现应从锁定资产重新生成，不能只复制 `scene.usda`。

预定位参数也可重新计算，不依赖手工复制关节值：

```bash
/home/descfly/miniconda3/envs/isaaclab-2.3.2/bin/python \
  tools/calibrate_dex5_postures.py --seed 7 \
  --output artifacts/dex5/postures-candidate.json
```

标定工具仅需可选 NumPy/SciPy，使用 URDF 正运动学、关节限位、末节耦合和手/腕碰撞包围盒约束。种子 7 的重新计算与当前配置中 92 个关节参数完全一致，两段各 101 个插值点的净空检查也一致；这只证明离线标定可复现。默认输出候选文件，评估后再替换配置，物理门禁仍须重跑。

运行保留 URDF 驱动限值，并使用模型重力前馈。驱动与前馈合计的实际关节力矩尚未独立验证，不能据此推断硬件电机限值已满足。

## 十指规划与合理时序

默认字符串精确为 **`"asdf jkl; "`**：中间和末尾各一个空格，共 10 次按键。可视化写法为 `asdf␠jkl;␠`，其中 `␠` 仅用于标识空格，不是输入字符。

| 按键序列 | 执行者 |
| --- | --- |
| `a → s → d → f` | 左小指 → 左无名指 → 左中指 → 左食指 |
| 第一个空格 | 左拇指 |
| `j → k → l → ;` | 右食指 → 右中指 → 右无名指 → 右小指 |
| 第二个空格 | 右拇指 |

双手五指共同参与，但按键顺序保持串行，使字符顺序、邻键误触和失败归因都可以明确检查。每键依次经过：

```text
受支撑工作姿态 + 全键释放
→ 目标指尖悬停，其他指尖保持净空
→ 接触并压下目标键
→ 独立确认指定手指接触 + 键程 + 唯一 KeyDown
→ 抬指与键帽回弹
→ 唯一 KeyUp + 全键释放 + 十指净空 + 文本前缀一致
→ 下一键
```

规划只携带键名、手/指语义、前置/后置条件、资源、失败码、版本与阶段时限；不包含关节目标。每个阶段必须在有限时域内完成。当前基线支持小写、数字、无需 Shift 的符号、空格、回车和退格；大写、Shift 符号和组合键会明确拒绝，后续按独立任务扩展。

核心实现为 [规划与 Verifier](../src/vskill_os/simulation/dex5_typing.py)、[Isaac Adapter](../src/vskill_os/adapters/isaac/dex5_demo.py) 和 [独立场景几何](../src/vskill_os/adapters/isaac/dex5_scene_geometry.py)。

## 证据、失败与恢复

Isaac 默认输出目录为 `artifacts/dex5/run/`：

- `report.json`：版本/种子/哈希、状态、物理事件、观测与独立判定；包含 `implementation_sha256`（控制/几何/验证代码、资产锁文件和姿态种子）、`usd_bundle_sha256`（USD 层包）、`geometry`、`mimic_joint_count`、`support_trajectory` 与实际物理时长。异常时检查 `error`、`recovery_error`、残留按键和失败观测。
- `physics.jsonl`：按时间保存的物理诊断观测；必须结合阶段与事件时刻解读。
- `scene.usda`：本次场景；`00-standing.png`、`01-seated.png`、`02-prepose.png`、逐键按压图和 `last-frame.png` 用于检查资产、姿态及指尖位置。
- `demo.mp4`：总览与键盘近景。未禁用视频且运行能够渲染时生成；提前失败时可能只有部分图像/视频。

Verifier 不读取“控制器完成”文字作为成功证据。每份观测必须覆盖全部 74 个物理键与十指，不能只观察计划按下的键而遗漏邻键。它要求时间顺序与阶段顺序正确；按压瞬间的指定手指接触、至少 2 mm 键程与目标 KeyDown 一致；KeyUp 当帧键程不超过 1.2 mm，最终十指至少留有 4 mm 净空，且文本前缀与计划一致。默认安全门限包括 12 N 接触力和 0.45 rad 躯干倾角。它们是该受支撑仿真基线的工程参数，不是硬件安全认证值。

回放测试覆盖漏触、错指、邻键误触、卡键、过力、文本不一致、控制器来源冒充、超时与不安全姿态；还检查重复事件、缺失观测、乱序时间、非法数值和回放/Isaac 来源混用。失败保持失败，不因为计划发出了释放命令就改成成功。

恢复分两步：先停止继续打字并尝试抬指，重新观测是否全键释放；确认输入状态与姿态可用后，才能根据显示文本生成有界的最长正确前缀修复。例如 `codwx → codex` 对应两次物理退格，再输入 `ex`。该修复规划和独立回放已测试；Isaac 中的故障恢复成功率仍需专门实验，不能由回放推断。

## 验收与后续任务

| 任务 | 当前状态 | 退出条件 |
| --- | --- | --- |
| D5-01 官方双手资产与追溯 | 标准库检查/测试与 8 个 PhysX mimic 导入检查已通过 | 固定哈希、合法树、正确坐标与耦合数量；实际导入记录完整 |
| D5-02 十指规划与失败回放 | `DONE`，能力 C0/C1 | 默认十指全覆盖，所有证据门禁与失败类别可复现 |
| D5-03 Isaac 受支撑打字 | `DONE`，受支撑近似接触基线 | 完整实际运行逐键通过，双手十指可见，接触/键程/事件/文本/释放一致 |
| D5-04 自由坐下与真实工作姿态 | `PARTIAL`，见[自由坐下实验](FREE_BALANCE_SITTING.md) | 自由坐下单独验收；工作姿态、键盘可达和自由坐姿打字交接尚待通过 |
| D5-05 可观测验证与真机迁移 | `PLANNED` | 相机标定/OCR、真实触觉/关节反馈、Dex5 通信和 HIL 分阶段放行 |

下一步扩大不同种子、重复次数、键盘偏移与扰动实验；当前成功记录不构成统计成功率或扰动鲁棒性保证。随后标定真实安装与末节传动、替换球形接触垫近似，启用并验证全身自碰撞，再接入自由基座坐下控制器。最终通过与真机一致的传感器验证和 HIL 后，再分别放行单指、双手、固定坐姿与坐下组合任务。

本支线对应 [仿真专项规划](SIMULATION_PHASE_PLAN.md) 的 `SIM03-D5` 与 [P4](phases/phase_4_posture_and_chair.md) 的 `P4-D5`；它提供可检查的实验底座，不跳过既有阶段退出门槛。
