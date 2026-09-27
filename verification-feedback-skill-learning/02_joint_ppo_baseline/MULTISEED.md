# VF-02-T03：单键重复验收与远端运行

状态：`PARTIAL`。三种子协议、独立验收入口与单 GPU 启动隔离已实现；29 节点处于运行环境复验阶段，尚无三种子物理验收成绩。此前 T02 的单种子结果仍以 [SINGLE_KEY_PPO.md](SINGLE_KEY_PPO.md) 为准。

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

## 29 节点部署

已确认 8 张 RTX PRO 5000 72GB；GPU 6 在接入时有 2762 个 volatile uncorrectable ECC 错误，本项目不使用该卡。计划将三个独立种子放在健康卡上，具体 UUID 由启动前查询确定，不能只依据旧截图。

节点上可读取 Isaac Lab 2.3.2 和用户环境中的 Isaac Sim 5.1.0、PyTorch 2.7.0+cu128。共享目录的原生库和 Python 文件读取存在明显延迟；运行库缓存与代码工作目录放在节点本地盘，训练结果需归档到持久存储。启动环境需优先选择 Isaac Sim 自带 Warp 1.8.2；不能误用环境中另一份 Warp。缓存原文件，不原地升级已有项目依赖。

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

三目录都完成后，在同版源码和资产环境中执行：

```bash
python -m vskill_os.adapters.isaac.learning.multiseed_runner \
  --aggregate-runs /path/to/seed-7 /path/to/seed-17 /path/to/seed-29 \
  --report /path/to/multiseed-suite.json
```

汇总入口重新调用独立验证器，不接受三个手写成功率作为物理证据。原始运行目录、兄弟目录中的 `.launch.json / .sources / .worker.json / .kit` 以及最终评估的启动收据应一起保留。源码快照中包含原始绝对路径；跨机器归档后需按来源目录映射恢复后再重算，不能改写历史清单来绕过校验。

当前范围限于固定骨盆、单活动关节、单键 `a`、特权状态。多键、退格、视觉策略和运行时技能发布仍是后续任务。

## 本轮软件验证

`make check`：278 项测试，277 通过、1 项可选桌面测试跳过；`make demo` 通过。28 个 T02 训练/采集来源文件与提交 `43173f8` 逐字节一致。远端 GPU 0 的实际 CUDA 矩阵运算已通过，Python 3.11.16、Torch 2.7.0+cu128，只可见指定卡；完整探测约 9.63 秒。上述结果尚不代表 29 节点已通过物理复验。
