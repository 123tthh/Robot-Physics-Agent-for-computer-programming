# Isaac Sim 6.0.1：站姿双 Dex3-1 食指协同打字

本分支按“站立、不坐凳子、每只手只用一根食指”的要求构建独立 demo。
机器人为 G1 29DoF mode 15 / 5010 腕部，双手为 Dex3-1，场景不生成椅子。
躯干/基座固定在站姿，因此这一阶段验证双手规划和接触打字，不验证自由站立平衡。
最新运行目标是 **Isaac Sim 6.0.1 standalone**，不依赖原项目的 Isaac Lab 2.3.2 或其 Python 3.11 conda 环境。

## 当前验收边界

2026-09-25 已在安装的 Isaac `6.0.1-rc.7+release.42383.32955d8d.gl` 上，完成两次站姿双食指 `ak` 的物理通过报告。使用 CPU PhysX、关闭相机；左右食指各按一键，74 键接触/行程、事件顺序及完全释放均通过独立校验。固定基座、读屏和行走是不同验收范围。

连续 `afjk` 测试已保留失败：输入 `af` 后进入右手接近阶段超时，清理停车也超时，因此完整报告不通过。默认演示调整为已通过的 `ak`；不能宣称任意文本输入或自由站立已完成。已有 P1/P2 报告仍是历史基线。

```bash
timeout --signal=INT --kill-after=15s 90s \
  ./scripts/run_standing_demo.sh --headless --no-cameras \
  --maximum-steps 5000 --text ak --output reports/standing/ak.json
```

结果与两次复现对比见 `reports/feedback/physical_reproducibility.json`。本次未运行 GPU 训练，也没有改动现有 Isaac 5.x 环境。

| 层级 | 可检查的内容 | 不能据此宣称 |
|---|---|---|
| 规划 | 固定输入的手指分配、按键顺序、预定位和资源约束 | 真实机械臂可达、已物理触键 |
| CPU 合成回放 | 事件顺序、遗漏/错误事件、Verifier 契约 | PhysX 接触通过、训练成功 |
| Isaac 实际运行 | 本次键程、接触、指尖位置、事件、释放和图像证据 | 自由基座平衡、真机安全或 sim-to-real |
| 学习框架 | 环境接口、奖励和观测定义、数据/训练入口 | 已有可用的学习策略 |

任务成功由独立 Verifier 判断，控制器返回“完成”或画面看起来正确均不足以通过。
失败、未知和未运行状态需要保留在报告中，不得被合成事件覆盖。

## 分支与来源

交付目录为 `/home/descfly/humanoid-computer-agent-dex3-standing`，从原项目工作目录复制到独立分支。
`reports/standing_source_snapshot.json` 记录原始提交、当时未提交变更和复制范围。
本地运行所需的 Git 忽略资产随工作目录复制；它们不会自动出现在普通 `git clone` 结果中。

所有相对路径以新项目根目录为准。不要在原项目中替换环境、改动 `_isaac_sim` 链接或覆盖运行中的输出。

## 一、无需仿真器的检查

```bash
cd /home/descfly/humanoid-computer-agent-dex3-standing
make check
make demo
make standing-plan
make standing-replay
make standing-runtime
```

`standing-plan` 产生 `reports/standing/plan.json`；`standing-replay` 产生
`reports/standing/synthetic_report.json`，明确标识为合成证据。
默认短文本为 `afjk`，用于让左右手都参与；更长文本可直接传参：

```bash
./scripts/run_standing_demo.sh --plan-only --text 'hello world' --output reports/standing/hello_plan.json
./scripts/run_standing_demo.sh --synthetic --text 'hello world' --output reports/standing/hello_synthetic.json
```

规划不引入关节命令。每只手只有 `index` 可触键，空格、退格及修饰键同样遵守这一约束。
双手可以提前接近下一目标，键盘输入的提交顺序必须等于目标文本顺序。
跨越共享区域需避免双手互穿；需要同时按住超过两个键的组合不能在双食指约束下执行。
物理执行器只负责有限时域的运动和证据采样，Agent 不能直接控制关节。

## 二、Isaac 6.0.1 运行入口

默认安装路径：

```text
/home/descfly/isaac-sim-standalone-6.0.1-linux-x86_64
```

本机 `VERSION` 为 `6.0.1-rc.7+release.42383.32955d8d.gl`。
启动脚本检查 `VERSION` 必须属于 6.0.1，并使用该安装自带的 `python.sh` / Python 3.12。
子进程清除旧 Python、conda、Isaac Lab 和动态库路径，不修改当前 shell 或系统安装。
`--check-runtime` 只读取路径、版本及进程/GPU 占用信息，不运行 `python.sh`，不证明 PhysX 可用。
当前兼容适配器使用 CPU PhysX（`--device cpu`），RTX 渲染仍需要 GPU；未提供 GPU PhysX 性能验收。

```bash
ISAAC_SIM_ROOT=/home/descfly/isaac-sim-standalone-6.0.1-linux-x86_64 \
  ./scripts/run_standing_demo.sh --check-runtime
```

以下命令会启动仿真，仅在已有仿真/训练任务结束后运行；启动守卫会在发现忙碌进程时拒绝启动，
不杀进程、不等待抢占、不降级偷偷使用 Isaac 5.1。

```bash
# 无界面物理验收，保留报告和固定镜头帧
./scripts/run_standing_demo.sh --headless --text afjk \
  --config configs/simulation/dex3_standing_demo.json --seed 0 \
  --device cpu --output reports/standing/isaac_report.json \
  --record-dir reports/standing/frames

# GUI 中检查手指接触、站姿和标准键盘朝向
./scripts/run_standing_demo.sh --no-headless --text afjk

# 仅场景检查仍会启动 Kit，也受进程/GPU 守卫约束
./scripts/run_standing_demo.sh --headless --inspect-only
```

记录时至少检查：左右食指都参与；非食指未成为输入来源；目标键达到实际键程；
所有键都被监测以发现误触；KeyDown/KeyUp 次数和顺序一致；最终文本正确；双手与所有按键完成释放。
桥接器清空逻辑按键状态不等于物理手指已经离开键帽。
镜头帧用于人工核对动作，不能单独替代物理证据。

## 三、资产可复现性

配置入口为 `configs/simulation/dex3_standing_demo.json`；目标机器人资产与上游版本清单为
`configs/simulation/g1_29dof_5010_dex3_assets.json`，详细背景见 [Dex3 资产审计](DEX3_ASSET_AUDIT.md)。
默认使用复制的 mode 15 / 5010 + 双 Dex3 分层 USD：

```text
artifacts/isaac-p1/assets/g1_mode15_dex3_p1/g1_mode15_dex3_p1.usd
artifacts/isaac-p1/assets/g1_mode15_dex3_p1/configuration/
artifacts/simulation-assets/unitree_ros_7d6075f7/robots/
```

`configs/simulation/dex3_standing_asset_lock.json` 固定复制资产的全部五个 USD 层哈希，启动时同时检查缺失、新增和被修改的层。
复制时必须保留全部 USD 层及其相对依赖，只复制顶层 `.usd` 不足以运行。
`reports/standing/usd_static_inspection.json` 保存本轮 OpenUSD 静态检查：五个文件层均位于复制目录内，
USD 层引用为相对路径，43 个旋转关节和两个刚体食指末端存在；移动后的固定根锚点与站姿一致。
这项检查未启动 Kit/PhysX。材质标识 `OmniPBR.mdl` 在普通 OpenUSD 下未解析，已找到 Isaac 安装中的对应文件，
实际材质解析与渲染仍须运行验证。
现成的官方 `g1_29dof_with_hand_rev_1_0` 是 mode 5 / 4010，只可作参考，不能替换目标资产。
原清单中的哈希属于历史生成资产；使用 Isaac 6 重新生成的 USD 应保存新的构建报告，不能宣称与历史二进制逐字节相同。

若新机器没有复制资产，可以从固定上游提交重新获取源文件：

```bash
mkdir -p artifacts/simulation-assets
git clone https://github.com/unitreerobotics/unitree_ros.git \
  artifacts/simulation-assets/unitree_ros_7d6075f7
git -C artifacts/simulation-assets/unitree_ros_7d6075f7 checkout --detach \
  7d6075f7f58588b189b940130e3edab3c839b2df
```

使用现有 `compose_mode15_g1_with_dex3` 组合器和 Isaac 6 自带 URDF importer 重建。
该入口会启动 Kit，当前不应与已运行仿真并行执行：

```bash
./scripts/run_standing_demo.sh --build-assets \
  --unitree-ros-root artifacts/simulation-assets/unitree_ros_7d6075f7 \
  --output-dir artifacts/standing/rebuilt-assets
```

构建器同时支持官方仓库的 `robots/dexterous_hand_description/dex3_1` 和历史复制包的
`robots/dex3_1` 布局；有 Git 元信息时强校验固定提交。
它调用标准库组合器验证 29 + 14 个主动关节，再通过 Isaac 6 导入并记录 USD 层哈希。
输出目录必须为空，避免覆盖已有验收资产。`asset_build_report.json` 中 `output_usd` 是生成资产路径；
将站立配置的 `robot.usd_path` 指向该文件，`robot.asset_manifest` 指向本次 `asset_build_report.json`，
并保留构建报告与各层目录，再运行新的物理验收。
此构建入口仅完成代码/接口核对，尚未在当前任务启动执行。

## 四、训练框架

训练接口、观测/动作、奖励和继续训练说明见 [Dex3 训练指南](DEX3_TRAINING_CN.md)。
CPU 默认环境是合成训练替身，用于检查接口与断点恢复，输出不能作为物理策略能力证明。

```bash
make standing-training-smoke
# 或直接指定配置与产物目录
PYTHONPATH=src python3 -m vskill_os.training train \
  --config configs/training/dex3_standing.json \
  --output artifacts/standing-training --iterations 3
PYTHONPATH=src python3 -m vskill_os.training evaluate \
  --checkpoint artifacts/standing-training/checkpoint.json \
  --episodes 8 --output artifacts/standing-training/evaluation.json
```

框架提供 `--adapter module:factory` 形式的外部环境接入点。
本阶段尚未交付已验证的 Isaac 6 物理训练环境或训练好的控制策略；后续必须通过同一独立 Verifier 的物理证据门禁。

## 五、重复运行与验收记录

使用同一文本、配置、seed、源资产和运行版本生成相同规划；GPU 物理数值并不保证跨驱动逐位一致。
物理运行应记录实际版本、资产哈希、配置、双手目标时间线、按键事件、错误码、释放结果与图像位置。
首次报告不通过时保留原报告，改用新的输出目录重新运行；不得手工修改报告为成功。

保留旧的 `make check`、`make demo` 作为核心回归。新增物理入口与历史 `isaac-p1` / `isaac-p2`
相互独立，避免旧坐姿、多手指和组合键假设混入站立双食指任务。
