# VF-02-T03：单键重复验收与远端运行

状态：`DONE`（固定场景单键范围）。2026-09-27 19:22（北京时间）在三个原工作目录完成独立重验，种子 `7 / 17 / 29` 各 `100/100`，跨目录汇总 `accepted=true`。本次新增训练转移为零。原先因工作目录绑定错误导致的汇总失败已修复；原始证据、旧失败记录及三份原归档均保留。此前 T02 的单种子结果仍以 [SINGLE_KEY_PPO.md](SINGLE_KEY_PPO.md) 为准。

## 最终独立结果

| 训练种子 | 训练转移 / 更新 | 最终评估 | 证据 / 完整周期 | 失败类型 |
|---|---:|---:|---|---|
| 7 | 1,000,000 / 123 | 100/100 | 全部有效 / 通过 | 无 |
| 17 | 1,000,000 / 123 | 100/100 | 全部有效 / 通过 | 无 |
| 29 | 1,000,000 / 123 | 100/100 | 全部有效 / 通过 | 无 |

每种子独立满足 `95/100` 门槛，平均成功周期约 1.0833 秒仿真时间。三进程重验耗时约 869 秒。[原始套件报告](../../reports/verification_feedback/vf02_t03_suite.json)与[重验摘要及证据哈希](reports/node29-reverification-20260927.json)均已同步回本地。原节点独立校验原始物理记录；本地重新计算报告汇总，计数和门槛判定一致。Python 版本的浮点求和造成平均耗时约 `1.33e-15` 秒的描述统计差异，原报告字节保留，未修改验收规则。

重验后的保全检查覆盖 750 个清单、资产、权重、来源及原单种子报告文件，全部未变；旧协调日志和三份原归档的 SHA-256 也未变。旧 `completion.json` 保留 `accepted=false`；本次新目录 `logs/multiseed-20260927-reverify-20260927T110501Z/` 保存新的 `suite.json / completion.json / integrity.json`、逐种子重验报告、执行收据与旧派生报告备份。新归档为持久存储中的 `multiseed-20260927/reverify-20260927T110501Z.tar.gz`。

本地证据副本位于 `artifacts/verification_feedback/vf02/remote_node29/reverification-evidence/`；三份完整原归档位于同级 `multiseed-20260927-original/`。文件均已按归档收据核对哈希。下一项为 [T04 少量键与真实退格](FEW_KEY_PREFLIGHT.md)，当前已完成离线几何前置检查。

## 2026-09-27 汇总修复

原始 `completion.json` 为 `aggregate_returncode=1, accepted=false`。17、29 的失败为 `ASSET_HASH: converted USD bundle differs`，不能解释为物理策略 0/100。各工作目录的转换资产分别匹配本目录采集清单，但转换后的 `g1_dex5_direct_joint_base.usd` 跨目录字节不同。原三份完整归档和历史完成记录保留。

三份原始归档已下载到本地并逐份匹配旧完成记录的 SHA-256。[归档资产绑定审计](../../reports/verification_feedback/vf02_t03_asset_binding.json)逐项读取全部 300 份物理采集清单，确认它们都匹配各自 USD 文件集合；三个目录两两比较均仅上述 `base.usd` 不同。这是资产来源核对，不代替物理回合的独立重验。

[`multiseed_replay.py`](../../src/vskill_os/adapters/isaac/learning/multiseed_replay.py) 读取每个运行的 `.placement.json`，绑定训练种子、原代码目录及协议/基线哈希，然后在对应目录创建新的 Python 进程。进程优先导入自己的 `src`，重新执行既有训练、检查点、策略推理、原始物理和资产哈希校验。独立验证器及编排源码也必须与协调器匹配；不修改任何历史 manifest，也不替换 USD 或跳过校验。28 个冻结训练/采集来源文件保持不变。

汇总只接收本次子进程新生成且身份、退出码和验证器哈希一致的报告。超时、缺报告、工作目录不匹配或验证器版本不同均失败。默认并行三个 CPU 验收进程，`--verification-workers 1` 可串行；每进程默认 7200 秒，可用 `--verification-timeout` 设置。验收不会启动训练或 PhysX 采集。

每次汇总需使用新的报告路径，旁边自动保存 `<报告名>.replays/seed-N.json`、日志和执行收据，已有输出拒绝复用。`--verify-only --report NEW_PATH` 保留原 `multiseed.json`；但既有评估器仍会重写每回合派生的 `policy_evaluation.json`，执行前应备份这些派生文件。新报告、修复源码和备份另行归档。

本地回归覆盖不同目录各自匹配的 USD、真实资产改动拒绝、错误工作目录/种子/配置、验证器差异、超时/缺少新报告和旧输出复用。软件测试不是物理验收证据。

修复后的 `make check`：286 项，285 通过、1 项可选桌面测试跳过；`make demo` 通过。T04 已完成[离线几何前置检查](FEW_KEY_PREFLIGHT.md)，多键与真实退格仍未通过物理门槛。

## 固定实验

配置为 [`multiseed_v1.json`](../../configs/learning/verification_feedback/multiseed_v1.json)。训练种子固定为 `7 / 17 / 29`，每个从随机初始化训练 `1,000,000` 聚合环境转移，64 工位。沿用 T02 的动作、观测、奖励、网络和优化参数，仅改变训练种子及最终评估列表。已有 T02 权重只用于远端迁移复验，不作为新训练的初始化。

每个最终百万转移检查点在原单工位环境中执行 `2001–2100` 共 100 次评估。独立评估器从原始关节、接触、键程、键事件和策略推理记录重算结果。必须逐训练种子达到 `95/100`，三个种子全部通过才通过本阶段；不能用池化平均掩盖一个失败种子，不能选择表现最好的检查点或替换失败种子。

场景和 reset 几何仍固定，评估种子只是预先冻结的案例身份，不意味着 100 个独立随机场景。报告中的 Wilson 区间是二项 IID 参考区间，不能据此声称未见键盘布局、相机策略或真机泛化。所有失败种子和失败类型均保留。

## 工程边界

| 模块 | 职责 |
|---|---|
| `learning/multiseed.py` | 严格协议、逐种子门槛、失败类型和参考区间；标准库实现 |
| `adapters/isaac/learning/multiseed_runner.py` | 有界训练、最终 100 回合采集、独立重算及三种子汇总 |
| `adapters/isaac/learning/isolated_worker.py` | 单 GPU UUID、启动前 ECC/CUDA 检查、独立 Kit 缓存、启动收据 |
| T02 的 `ppo_worker / ppo_evaluate / ppo_verify` | 保持原始源码；分别执行训练、原始物理采集与独立验收 |

一个种子使用一个完整且独立的代码/资产工作目录，避免已有资产转换器共用同一 USD 输出。每个工作进程只可见一个 GPU UUID，并关闭 Kit 自动多 GPU。启动收据记录运行参数、设备和包装器源码哈希；训练配置、采集源码快照、检查点和物理原始证据仍由原验证器绑定。训练进程只产生候选策略，不自动发布在线技能。

## 29 节点部署（启动时记录）

已确认 8 张 RTX PRO 5000 72GB；GPU 6 在接入时有 2762 个 volatile uncorrectable ECC 错误，本项目不使用该卡。2026-09-27 07:39 UTC（北京时间 15:39）启动种子 `7 / 17 / 29`，分别绑定 GPU `0 / 1 / 2` 的完整 UUID；启动前重新检查 ECC，实际 CUDA UUID 与指定卡一致。三者各用 64 个工位，已分别记录 73,728 次环境转移和九次 PPO 更新（07:43:56 UTC 快照）；当前约每卡每秒 450 次转移，仅作为启动阶段吞吐快照。

运行代码冻结于 `652fade`，没有从 T02 权重继续训练。三个零转移检查点的 SHA-256 不同，完整 GPU 绑定与启动状态见 [启动快照](reports/node29-launch-20260927.json)。训练计数和训练内成功信号不能替代最终 100 回合的独立验收。

节点本地运行根目录为 `/home/yuxin/workspace/robot-physics-agent-node29`，每种子代码位于 `workspaces/seed-{7,17,29}`，各自运行目录为 `artifacts/verification_feedback/vf02/multiseed/run-node29-20260927`。一次性协调进程在 `tmux` 会话 `vf02-t03-20260927` 中执行；状态与协调日志位于根目录的 `logs/multiseed-20260927/`，每种子训练进度位于运行目录内的 `training_metrics.jsonl`。任务有固定预算及超时，不自动追加预算或重试失败种子。

原协调流程在每个种子训练结束后采集最终 100 回合并独立重算，再将完整工作目录归档到 `/mnt/workspace/users/yuxin/workspace/robot-physics-agent-node29/multiseed-20260927/`。其最后的跨目录汇总失败，记录保留；本轮修复与新验收结果见本文开头，不能把旧启动快照或旧 `completion.json` 当作当前状态。

节点上可读取 Isaac Lab 2.3.2 和用户环境中的 Isaac Sim 5.1.0、PyTorch 2.7.0+cu128。共享目录的原生库和 Python 文件读取存在明显延迟；运行库缓存与代码工作目录放在节点本地盘，训练结果需归档到持久存储。启动环境需优先选择 Isaac Sim 自带 Warp 1.8.2；不能误用环境中另一份 Warp。缓存原文件，不原地升级已有项目依赖。

节点不能直接读取默认场景的在线地址。可设置 `VF_ISAAC_ASSET_ROOT` 为离线资产根目录，内部保留 `Isaac/Environments/Grid/` 层级。[`isaac_asset_mirror_v1.json`](../../configs/learning/verification_feedback/isaac_asset_mirror_v1.json) 固定原版 USD 及其三张纹理的下载地址和 SHA-256；启动前逐文件校验，通过 Kit 的资产根目录设置加载，不修改 USD、碰撞参数或原有采集代码。训练与评估的启动收据必须使用同一份镜像，独立验收再次检查文件哈希。运行时日志、Kit/Warp/CUDA 缓存及临时目录均按工作进程隔离。

本地部署包、连接信息、完整日志和权重放在 Git 忽略的 `artifacts/verification_feedback/vf02/remote_node29/`。SSH 私钥不复制到远端或仓库。训练根目录、Python 路径和设备选择通过环境配置提供，不写入核心模型。

迁移门槛为：CUDA 运算通过 → 实际 PhysX 步进通过 → T02 冻结策略在新节点复验 → 三个种子正式训练。启动超时不能算训练失败或策略失败，也不能算完成了任何环境转移。

## 运行入口

在各自工作目录激活经过验证的 Isaac 环境，并设置该进程独占的 `CUDA_VISIBLE_DEVICES=GPU-完整UUID`：

```bash
python -m vskill_os.adapters.isaac.learning.multiseed_runner \
  --seed 7 --run artifacts/verification_feedback/vf02/multiseed/seed-7
```

另外两个独立目录使用种子 17、29。训练默认超时 10800 秒，最终采集默认 7200 秒，均可显式调整且须为正有限值。运行目录不覆盖；中断目录保留失败记录，不伪装成精确续训。对已完成目录可加 `--verify-only`，只重算证据。

也可使用 `make isaac-learning-multiseed LEARNING_MULTI_SEED=7 LEARNING_MULTI_RUN=/path/to/new-run`。远端需将 `ISAACLAB_ACTIVATE` 和 `ISAACLAB_PYTHON` 指向项目自己的激活脚本与 Python；`make verify-learning-multiseed` 复用同一组变量，只执行证据重算。

三目录都完成后，将这次编排修复安装到三个工作目录，在匹配的运行环境中执行（运行路径指向各工作目录内的实际实验目录）：

```bash
python -m vskill_os.adapters.isaac.learning.multiseed_runner \
  --aggregate-runs /path/to/seed-7 /path/to/seed-17 /path/to/seed-29 \
  --report /path/to/multiseed-suite.json
```

汇总入口在每个原工作目录重新调用独立验证器，不接受三个手写成功率作为物理证据。原始运行目录、兄弟目录中的 `.placement.json / .launch.json / .sources / .worker.json / .kit` 以及最终评估的启动收据应一起保留。源码快照中包含原始绝对路径；跨机器归档后需按来源目录映射恢复后再重算，不能改写历史清单来绕过校验。

当前范围限于固定骨盆、单活动关节、单键 `a`、特权状态。多键、退格、视觉策略和运行时技能发布仍是后续任务。

## 启动时软件验证

`make check`：279 项测试，278 通过、1 项可选桌面测试跳过；`make demo` 通过。28 个 T02 训练/采集来源文件与提交 `43173f8` 逐字节一致。远端 GPU 0 的实际 CUDA 矩阵运算已通过，Python 3.11.16、Torch 2.7.0+cu128，只可见指定卡；完整探测约 9.63 秒。实际 PhysX 八步检查已通过，含启动约 39.84 秒。

T02 最终策略在节点 29 重新采集种子 1001–1008，独立验证全部通过，每回合完整周期均为 1.0833 秒仿真时间。[迁移报告](reports/node29-migration-20260927.json) 保存结果及证据哈希，原始回合、失败尝试、启动收据和配置已同步回本地 `artifacts/verification_feedback/vf02/remote_node29/preflight-evidence/`，并备份到节点持久存储。此结果是旧策略迁移检查，与三种子新训练的验收分开。
