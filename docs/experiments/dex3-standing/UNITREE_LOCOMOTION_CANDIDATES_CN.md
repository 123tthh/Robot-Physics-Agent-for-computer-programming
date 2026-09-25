# Unitree 官方行走策略：本地定位与接入边界

2026-09-25 已找到官方 **Unitree RL Lab**，不是缺少行走模型。本机位置是 `/home/descfly/projects/unitree_rl_lab`，remote 为 `https://github.com/unitreerobotics/unitree_rl_lab.git`，本地提交 `4960b84732b0c2ec593dccbfe963fda1bcd7b1e3`。本地仅修改了机器人资产目录的环境变量配置；下述 ONNX 和部署 YAML 与该提交逐字节相同。

## 选用的候选

- 任务：`Unitree-G1-29dof-Velocity`。
- 权重：`deploy/robots/g1_29dof/config/policy/velocity/v0/exported/policy.onnx`。
- 契约：同目录上级 `params/deploy.yaml`。
- 官方训练资产：`G1/29dof/usd/g1_29dof_rev_1_0/g1_29dof_rev_1_0.usd`。
- 本地还存在 `mimic/dance_102`、`mimic/gangnam_style` 的 ONNX；它们是动作模仿策略，不用于工位导航。

官方仓库和部署配置：[Unitree RL Lab](https://github.com/unitreerobotics/unitree_rl_lab)、[固定提交的部署契约](https://github.com/unitreerobotics/unitree_rl_lab/blob/4960b84732b0c2ec593dccbfe963fda1bcd7b1e3/deploy/robots/g1_29dof/config/policy/velocity/v0/params/deploy.yaml)。上游说明使用 Isaac Lab 训练、MuJoCo 做 sim2sim；这不能证明当前双 Dex3 资产和 Isaac 6.0.1 已兼容。

## 已完成的实际工作

权重、YAML 和 Apache-2.0 许可证已复制到独立分支的 `artifacts/unitree-velocity-v0/`，原仓库未修改。`configs/robots/unitree_g1_velocity_candidate.json` 固定来源提交、文件 SHA-256 和完整部署参数。模型资产被 Git 忽略，可用导入脚本重新生成。

`src/vskill_os/adapters/unitree_velocity.py` 已实现真正的 ONNX CPU 推理和观察编码，并完成 12 次推理（停止/前进两组，各六步）。输入是测试机体状态，未连接仿真或实机，也不发关节命令。报告为 `reports/unitree_policy/cpu_inference.json`。

| 项目 | 已核实值 |
|---|---|
| 控制频率 | 50 Hz，20 ms |
| ONNX 输入 | `obs`，float32 `[1,480]` |
| ONNX 输出 | `actions`，float32 `[1,29]` |
| 单步观察 | 3 维角速度＋3 维重力＋3 维速度指令＋29 维相对关节角＋29 维关节速度＋29 维上一动作＝96 |
| 历史 | 每个观察项各存五步，旧到新，然后按观察项拼接；不是五个完整 96 维帧直接拼接 |
| 观察缩放 | 角速度 0.2；关节速度 0.05；其他项 1.0 |
| 初始历史 | 首次观察重复填满五步；上一动作重置为零 |
| 动作 | `q_target = default + 0.25 × raw_action`，随后映射回 SDK 电机顺序 |
| 输入关节顺序 | 必须按 YAML `joint_ids_map` 映射；不是 SDK 顺序，也不包含 14 个 Dex3 关节 |
| 指令范围 | 前后速度 `[-0.5,1.0]` m/s，横向 `[-0.3,0.3]` m/s，偏航 `[-0.2,0.2]` rad/s |
| PD 增益 | YAML stiffness/damping 使用 SDK 电机顺序；默认关节角、动作使用策略顺序 |

编码规则同时核对了官方 C++ `ObservationManager`、`ObservationTermCfg`、`BaseArticulation` 与 `State_RLBase`。八项测试覆盖历史方向、关节映射、原始上一动作、重置、时间连续性、命令越界、非有限值和错误契约。

## 复现 CPU 检查

```bash
make unitree-policy-import
make unitree-policy-inspect \
  UNITREE_POLICY_PYTHON=/home/descfly/miniconda3/envs/isaaclab-2.3.2/bin/python
```

这个解释器本地具备 ONNX Runtime 1.29.0、NumPy 和 PyYAML；本命令只使用单线程 CPU provider，不导入 Isaac、不访问 CUDA，也不改变环境。其他机器可以在独立 Python 环境安装这些可选库后覆盖 `UNITREE_POLICY_PYTHON`。

重新获取上游时先检出上述固定提交，再运行 `python3 scripts/import_unitree_velocity.py --source /path/to/unitree_rl_lab`。导入脚本会核对所有文件哈希后才复制，不能以新版同名文件悄悄替代。

## 接入 G1＋双 Dex3 的具体顺序

1. **原始资产基线。** 在独立环境复现原 G1 rev1.0 的自由基座速度跟踪。官方训练入口为 `python scripts/rsl_rl/train.py --headless --task Unitree-G1-29dof-Velocity`；`play.py` 读取训练检查点，不能把部署 ONNX 当作 RSL-RL 训练 checkpoint 传入。
2. **目标资产迁移。** 核对 mode15/5010 与 rev1.0 的传动比、惯量、关节限位和驱动参数；显式加入双 Dex3 质量与惯量及 14 个被保持的手关节。复制完整资产依赖哈希，进行自由站立、前进、侧移、转向、减速停止测试，再决定是否微调。
3. **统一时钟。** 原策略训练使用 200 Hz 物理步进、四步一次推理。当前固定基座打字驱动是 120 Hz；浮动基座整合建议采用一个 200 Hz 世界和明确 decimation，不能让两个控制器分别推进世界或把 20 ms 当成整数个 120 Hz 步长。
4. **控制权交接。** 该策略输出全部 29 个机体关节，包含双臂和腰部；零速度指令仍然会产生手臂目标。不能保留策略的手臂输出、再同时叠加打字 IK。需先验证站立平衡模式，或训练包含上肢目标的全身策略，并由唯一关节控制器执行资源交接。
5. **独立验收。** 观察足底接触、机体倾角、足滑、基座速度与实际位移；禁止移动根位姿冒充行走。完成目标资产迁移验收后，才接入 `StandingNavigationSkill` 的物理适配器及后续敲键盘流程。

候选清单明确保留 `target_mode15_dex3_compatible=false`、`physical_walking_verified=false`。没有把上游仓库提交冒充训练提交，也没有把模型输出有限当成已会行走。

另外两个官方路线：[unitree_rl_gym](https://github.com/unitreerobotics/unitree_rl_gym) 的 G1 默认部署配置是 12 动作、47 维观察；[unitree_rl_mjlab](https://github.com/unitreerobotics/unitree_rl_mjlab) 是 MuJoCo 训练路线。它们可作后续对照，但不能混用观察维度、关节顺序或权重。本次优先使用已在本机核实的 RL Lab 29DOF 候选。
