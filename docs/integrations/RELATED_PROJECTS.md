# 相关项目与集成决策

机器可读目录位于 `configs/research/projects.json`，可运行：

人工审阅的项目快照、许可证、固定 commit 和适配笔记按阶段存放在 [`references/`](../../references/README.md)；机器可读清单和本地资料索引应相互引用，但职责保持分离。

```bash
PYTHONPATH=src python3 -m vskill_os.cli list-projects
PYTHONPATH=src python3 -m vskill_os.cli list-projects --subsystem sitting
PYTHONPATH=src python3 -m vskill_os.cli list-projects --subsystem seated_locomotion
```

## 直接采用或实现 Adapter

| 项目 | 本项目中的角色 | 采用边界 |
|---|---|---|
| [Unitree G1 description](https://github.com/unitreerobotics/unitree_ros/blob/master/robots/g1_description/README.md) | 机型与资产事实源 | mode/profile 强校验，不复制控制代码 |
| [Unitree RL Lab](https://github.com/unitreerobotics/unitree_rl_lab) | 现有 G1 velocity/mimic 策略 | 通过 ONNX executor adapter；保留低维根意图 |
| [Unitree Sim IsaacLab](https://github.com/unitreerobotics/unitree_sim_isaaclab) | DDS 等价仿真、Dex3 资产、采集 | 独立锁版本环境，不污染核心包 |
| [WBC-AGILE](https://github.com/nvidia-isaac/WBC-AGILE) | 坐下/站起、全身 motion tracking 首选 | 实现 sit policy adapter；复用任务而非 fork 进核心 |
| [GR00T Whole-Body Control](https://github.com/NVlabs/GR00T-WholeBodyControl) | G1 decoupled WBC、遥操作、数据导出 | 作为整身控制 adapter 候选；必须校验机型、手部惯量与关节温升 |
| [Unitree XR Teleoperate](https://github.com/unitreerobotics/xr_teleoperate) | Dex3 键盘示范采集 | 转换为统一 episode schema |
| [Unitree LeRobot](https://github.com/unitreerobotics/unitree_lerobot) | ACT/Diffusion Policy 与数据基线 | 与几何+残差方案对比 |
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | 边缘 VAD/ASR/TTS | 进程内或本地服务 adapter |
| [Qwen3](https://github.com/QwenLM/Qwen3) + [llama.cpp](https://github.com/ggml-org/llama.cpp) | 本地高层工具规划 | loopback 服务；输出永不作为完成证据 |

## 作为基线或方法参考

| 项目 | 可借鉴内容 | 不直接解决的问题 |
|---|---|---|
| [BeyondMimic](https://github.com/HybridRobotics/whole_body_tracking) | 大范围全身动作跟踪与 ONNX 部署 | 毫米级键盘任务精度和跨域验证 |
| [VideoMimic](https://arxiv.org/abs/2505.03729) | 视频重建、场景条件化动作模仿 | Agent/Skill 交接、键盘事件和软件验证 |
| [ResMimic](https://arxiv.org/abs/2510.05070) | motion prior + task residual | 实体电脑 GUI/软件闭环 |
| [HoST](https://github.com/InternRobotics/HoST) | 跌倒后的站起恢复 | 正常坐下与稳定工作姿态 |
| [Isaac Lab G1 environments](https://isaac-sim.github.io/IsaacLab/main/source/overview/environments.html) | 固定上身 IK、locomanipulation、三指手任务 | 完整实体打字任务定义 |
| [LeRobot G1](https://huggingface.co/docs/lerobot/en/unitree_g1) | 通用机器人接口、遥操作和仿真流程 | 本项目的 Verifier/账本/Skill 契约 |
| [G1 Piano Play](https://github.com/meetsitaram/g1-piano-play) | 坐姿、触键资产和 contact reward 起点 | 当前主要是 reach/contact，尚非成熟连续打字系统 |
| [Stay Seated](https://arxiv.org/abs/2608.28090) | G1 在无动力五万向轮座椅上的全向坐姿移动、96/217 维非对称观测和训练消融 | v1 未发布代码/权重；真机仅定性演示，必须在本项目机型与座椅上重新量化 |

## 明确不采用的路线

- 不让单一 VLA 从语音直接输出 29 个身体关节和 14 个手部关节；
- 不以 LLM 的自然语言“完成了”作为任务成功；
- 不把 Isaac Sim 5.x 与 6.x 依赖强行合并；
- 不把 `29DOF 5010` 当作足够的策略兼容性标签；
- 不先追求自由文本高速打字，优先建立单键、组合键和错误恢复的可验证闭环；
- 不把实验性 GitHub 项目当作官方能力，所有复用项先经过最小复现和许可证检查。
