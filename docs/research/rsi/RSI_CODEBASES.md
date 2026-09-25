# Robot–Physics–Agent / RSI 可用 Codebase 清单

核对日期：2026-09-25 · 状态：**上游代码与集成决策清单，不代表本机已安装或复现**

本文回答三个问题：哪些代码可以近期采用，哪些需要通过 Adapter 选择性迁移，哪些只适合方法参考。机器可读条目位于 [`configs/research/projects.json`](../../../configs/research/projects.json)。任何上游在使用前都要固定 commit、保存许可证和资产/权重条款，并通过最小 smoke test。

## 1. 推荐的最小可行技术栈

近期不需要把所有项目都接进来。建议先采用：

```text
本仓库：Skill Runtime + Verifier + Ledger + Promotion/rollback contract
Isaac Sim 5.1 / Isaac Lab 2.3.2：当前 PhysX 与视觉基线
Unitree Sim IsaacLab：G1/Dex3 资产、DDS 等价边界和采集参考
RSL-RL：状态策略/PPO 与教师—学生基线
Unitree XR Teleoperate + LeRobot：示范、接管和统一 episode 数据
robomimic 或 LeRobot：BC / ACT / Diffusion Policy 离线基线（二选一为主）
HIL-SERL：只迁移 actor–learner、replay 和 intervention 机制
```

SOAR、RISE、Newton、OpenPI、Eureka、OMNI-EPIC 和 DGM 都放在闭环基线之后。它们分别解决自主采集、世界模型、新物理后端、大 VLA、奖励搜索、开放任务生成和代码改进器问题，不能替代本项目当前缺少的 episode 规范、冻结评测与晋升门禁。

## 2. 选型等级

| 等级 | 含义 |
|---|---|
| A 近期采用 | 与现有技术栈相邻，可直接依赖或用薄 Adapter 接入 |
| B 选择性迁移 | 有可复用子系统，但机器人、动作空间或依赖不匹配 |
| C 复现/研究参考 | 适合形成后续实验，不应成为当前关键路径 |
| D 暂不采用 | 已弃用、归档，或直接接入会破坏本项目边界 |

## 3. 仿真、全身控制与真机边界

| 等级 | Codebase | 可用内容 | 本项目接法 | 主要限制与许可证提示 |
|---|---|---|---|---|
| A | [当前仓库](../../../README.md) | Agent–Skill–Verifier、证据账本、恢复、键盘—PTY、当前 G1/Dex5 场景 | 作为唯一系统编排与治理层；补学习协议，不被外部框架替换 | 当前学习训练后端与候选档案尚未完成 |
| A | [Isaac Lab](https://github.com/isaac-sim/IsaacLab) | 向量化环境、接触/相机、RL/IL、Mimic/SkillGen | 当前论文基线继续锁定 2.3.2/Isaac Sim 5.1；新版本独立迁移 | 主框架 BSD-3；Mimic Apache-2.0；PhysX/RTX/Isaac Sim 含专有条款；当前 `develop` 面向 Isaac Sim 6.1，不宜无回归升级 |
| A | [Unitree Sim IsaacLab](https://github.com/unitreerobotics/unitree_sim_isaaclab) | G1 29DoF、Dex3、DDS 等价仿真、相机、采集/回放 | 资产与 DDS Adapter；借鉴任务组织，不让其绕过 Runtime | 支持 4.5/5.x，官方对 RTX 50 系建议 5.0；权重仅声明用于仿真；根许可证和资产需逐项保存 |
| A | [Unitree RL Lab](https://github.com/unitreerobotics/unitree_rl_lab) | G1 29DoF 速度策略、RSL-RL 训练、MuJoCo sim-to-sim、部署 | 导航/全身基础策略 Adapter 与兼容性 Manifest | Apache-2.0；必须固定 G1 profile、资产、关节序和 Isaac Lab 版本 |
| B | [GR00T Whole-Body Control](https://github.com/NVlabs/GR00T-WholeBodyControl) | G1 decoupled WBC、GEAR-SONIC、训练/部署和遥操作 | 作为下肢/全身控制候选，不负责毫米级键盘验证 | 代码 Apache-2.0、权重 NVIDIA Open Model License；部分 decoupled 路线含上身 IK，不满足严格无 IK 实验时需隔离为对照 |
| B | [WBC-AGILE](https://github.com/nvidia-isaac/WBC-AGILE) | G1 坐下/站起、速度—高度跟踪、教师学生和 sim-to-MuJoCo | P4 坐下/站起首选上游之一，单独 Sim 6 环境接 Adapter | 目标 Isaac Sim 6.0、Isaac Lab 3.0.0-beta2、Python 3.12；大部分 Apache-2.0，RSL-RL patch BSD-3；不能与当前 5.1 环境硬合并 |
| B | [Newton](https://github.com/newton-physics/newton) | GPU 物理、OpenUSD、MuJoCo Warp、G1/Allegro/contact/camera 示例、可微仿真 | 第二物理后端、高吞吐训练和 sim-to-sim 鲁棒性研究 | Apache-2.0；先验证键程、摩擦、迟滞、多指接触和屏幕/传感器链，暂不替代当前 PhysX 验收真值 |
| B | [Unitree MuJoCo](https://github.com/unitreerobotics/unitree_mujoco) | SDK2 低层接口和快速 sim-to-sim | 部署前控制/关节序回归 | 不是完整键盘/屏幕学习环境 |

## 4. 数据、示范与基础策略学习

| 等级 | Codebase | 可用内容 | 本项目接法 | 主要限制与许可证提示 |
|---|---|---|---|---|
| A | [Unitree XR Teleoperate](https://github.com/unitreerobotics/xr_teleoperate) | G1 29DoF + Dex3 遥操作、仿真与真机录制 | 采集键盘示范/纠正；转换为统一 `EpisodeRecord` | 不能只存关节轨迹；必须补接触、键事件、屏幕、接管和 Verifier 标签；许可证/子模块逐项核对 |
| A | [LeRobot](https://github.com/huggingface/lerobot) | 标准数据集、采集/训练/部署、ACT/SmolVLA/HIL-SERL、Unitree G1 接口 | 优先作为 episode 媒体/状态数据与轻量 IL 工程底座；通过 Adapter 接 Runtime | Apache-2.0；官方 G1 当前支持 23/29 DoF，但其 G1 arm 流程含 IK，严格无 IK 研究只复用数据/接口或另写策略；不要让其直接发布真机策略 |
| A | [RSL-RL](https://github.com/leggedrobotics/rsl_rl) | PPO、学生—教师蒸馏、多 GPU、高吞吐机器人学习 | Isaac 状态教师、坐下/全身策略和视觉学生基线 | BSD-3；与当前 Isaac Lab 版本固定兼容组合 |
| A/B | [robomimic](https://github.com/ARISE-Initiative/robomimic) | BC、BC-RNN/Transformer、Diffusion Policy、IQL/CQL、数据工具 | 小规模离线学习与公平算法基线；从本项目 episode 转换 | MIT；不含 G1/Dex5 环境，需要自定义观测/动作与 rollout Adapter |
| B | [Diffusion Policy](https://github.com/real-stanford/diffusion_policy) | 动作序列扩散、公开配置/日志/checkpoint | 作为强 IL 对照；若 LeRobot/robomimic 已满足需求可不重复接入 | MIT；原环境与机器人不匹配，不能视作自我改进机制 |
| B | [RoboPianist](https://github.com/google-research/robopianist) | 双手多指、频繁接触、时序/指法 RL Benchmark | 借鉴任务、奖励、指法和多指评测；可做相邻领域复现 | Apache-2.0（部分资产/依赖另有条款）；MuJoCo Shadow Hand/钢琴，不是电脑键盘或 G1 |

## 5. 在线改进、自主采集与世界模型

| 等级 | Codebase | 可用内容 | 本项目接法 | 主要限制与许可证提示 |
|---|---|---|---|---|
| B | [HIL-SERL](https://github.com/rail-berkeley/hil-serl) | actor/learner 异步结构、replay、示范、奖励分类器、人工接管 | 移植 learner/replay/intervention 语义；重写 G1/Dex 手、动作、安全和 Gym Adapter | Apache-2.0；官方机器人基础设施面向 Franka；JAX 环境独立部署；有人工接管，不等于无人自学习 |
| C | [SOAR](https://github.com/rail-berkeley/soar) | VLM 语义自主采集、RLDS 转换、策略训练、自动 reset 检测 | 借鉴 ExploreScheduler、采集编排、异常通知和自主数据学习 | MIT；面向 WidowX 桌面抓取和 JAX/Flax，代码不是通用机器人自学习框架；VLM 弱标签不能替代本项目 Verifier |
| C | [RISE](https://github.com/OpenDriveLab/RISE) | 动作条件动力学、进度价值、想象 rollout、在线策略提升 | R4 以后比较“低维模型 vs 大世界模型”；只在小基线证明瓶颈后接入 | 代码/资产默认 Apache-2.0；数据和 checkpoint CC BY-NC-SA 4.0；OpenPI/RLinf/视频模型依赖重、需要多 GPU |
| C | [openpi](https://github.com/Physical-Intelligence/openpi) | π 系列 VLA、LeRobot 数据转换、远程推理、LoRA/全量微调 | 后期语言条件策略或 RISE 依赖；不作为首个按键残差模型 | 代码 Apache-2.0，Gemma/权重另有条款；官方估计推理 >8 GB、LoRA >22.5 GB、全量训练 >70 GB；目标机器人差异大 |

## 6. 自动课程、奖励搜索与递归改进

| 等级 | Codebase | 可用内容 | 本项目接法 | 主要限制与许可证提示 |
|---|---|---|---|---|
| C | [OMNI-EPIC](https://github.com/maxencefaldor/omni-epic) | 代码生成环境、interestingness、任务档案、开放式课程 | 参考受限任务生成、档案与 learnability 打分；任务空间必须白名单化 | Apache-2.0；依赖外部模型 API、Apptainer 和 DreamerV3；原环境不是 Isaac/G1 |
| C | [Eureka](https://github.com/eureka-research/Eureka) | LLM 生成奖励、批量 RL 评估、奖励反思 | 后期离线生成 reward 候选，再由冻结 Verifier 评测策略 | MIT；基于已归档路线的 Isaac Gym Preview 4，不应直接并入当前 Isaac Lab；不可允许其改最终成功判据 |
| C | [DGM](https://github.com/jennyzzt/dgm) | 代码 Agent 自修改、开放候选档案、编程基准筛选 | 只借鉴父子版本、候选档案和外层评测；用于 RSI-T12 沙箱 | Apache-2.0；证据来自软件基准，不是机器人；绝不能持有硬件、Verifier 或部署权限 |
| C | [Voyager](https://github.com/MineDojo/Voyager) | 自动课程、技能库、环境反馈迭代 | 高层技能库组织参考 | Minecraft 证据不能外推到实体机器人；不作为直接依赖 |

## 7. 明确不作为新主线依赖

| 等级 | Codebase | 原因 | 替代 |
|---|---|---|---|
| D | [SERL](https://github.com/rail-berkeley/serl) | 上游 README 已声明弃用 | 使用 HIL-SERL 或 LeRobot 的相关实现 |
| D | [IsaacGymEnvs](https://github.com/isaac-sim/IsaacGymEnvs) | 上游已归档；Eureka 等旧项目仍依赖它 | 在 Isaac Lab 重写最小任务，只参考算法逻辑 |
| D | 任意“LLM 直接输出全身关节”的 Agent | 破坏 Agent–Skill 边界，难以验证和安全恢复 | LLM 只选 L2/L3 Skill，低层由版本化策略 Adapter 执行 |
| D | 直接把 DGM/Eureka 接真机 | 候选可修改奖励、代码或权限，缺少物理治理 | 无硬件凭证的离线沙箱 + 独立 Promotion Gate |

## 8. 推荐的代码复用粒度

| 上游 | 建议复用 | 不建议复制 |
|---|---|---|
| Isaac Lab / Unitree | 环境结构、资产、传感器、向量化、策略部署接口 | 把整个仓库 fork 进核心包 |
| LeRobot / robomimic | 数据格式、采集/训练工具、基线策略 | 用其任务成功布尔量替代独立 Verifier |
| HIL-SERL | actor–learner、replay、接管数据语义 | Franka 硬件栈、原动作空间与安全假设 |
| SOAR | 采集编排、任务提议、reset/通知设计 | VLM 单独决定成功 |
| RISE | 模型/价值/想象改进的实验结构 | 未经小实验就迁移完整大模型集群 |
| OMNI-EPIC / Eureka / DGM | 候选生成、档案、外层评测思想 | 让生成器修改冻结测试、安全和发布配置 |

## 9. 环境隔离建议

| 环境 | 固定内容 | 连接方式 |
|---|---|---|
| `core` | 本仓库标准库 Runtime、Verifier、Ledger、报告 | 本地 Python API / JSON 消息 |
| `isaac51` | Isaac Sim 5.1、Isaac Lab 2.3.2、当前 PhysX 资产 | Isaac Adapter worker |
| `isaac6-agile` | WBC-AGILE、Isaac Sim 6.0、Lab 3.0 beta 组合 | Policy worker / ONNX 或版本化动作协议 |
| `newton` | Newton 固定 release/commit | Simulation Adapter；只返回标准观测和证据 |
| `il` | LeRobot 或 robomimic、PyTorch | Dataset/Learner Adapter |
| `hil-rl` | HIL-SERL、JAX/CUDA 固定版本 | 异步 learner worker，不持有真机使能权 |
| `vla-world-model` | openpi / RISE 及多 GPU 依赖 | 远程候选训练/推理服务 |
| `meta-improver` | Eureka/OMNI-EPIC/DGM 派生实验 | 无网络或受限网络、无硬件凭证、只写候选目录 |

重型框架之间通过显式 schema 通信，不共享可变 Python 环境。策略包至少携带模型哈希、观测/动作 schema、归一化、频率、关节序、资产版本、训练数据与评测报告。

## 10. 引入一个上游项目前的检查表

1. 记录仓库 URL、commit/tag、获取日期和所有 submodule；
2. 保存源代码、模型、数据、资产和依赖的不同许可证；
3. 在独立环境复现官方最小例子，不先改本仓库；
4. 明确复用的是资产、算法、数据格式、权重还是部署代码；
5. 定义 Adapter 输入/输出、超时、取消、失败码和能力等级；
6. 核对机器人型号、关节序、频率、动作单位、归一化和控制模式；
7. 增加无硬件 smoke test、故意错误配置测试和回滚路径；
8. 用本项目 Verifier 重新验收，不能继承上游“success=True”；
9. 更新机器可读目录和阶段文档；
10. 只有在独立回归通过后，才允许进入候选晋升链。

## 11. 最终选型建议

- **现在就做**：本仓库治理层 + Isaac 5.1 基线 + RSL-RL + LeRobot/robomimic + 参数校准。
- **第一个学习闭环后做**：HIL-SERL 接管机制、SOAR 式课程调度、Newton/第二引擎回归。
- **数据和算力充分后做**：RISE/openpi 世界模型或 VLA。
- **最后做**：Eureka/OMNI-EPIC/DGM 式 Meta-Improver，而且只在隔离环境提出候选。

这一路线把上游项目当作可替换部件，而不是让任何一个外部框架成为新的系统控制中心。
