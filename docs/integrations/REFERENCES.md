# 参考工作与设计映射

本工程不是对单篇论文的复现，而是将多条研究线索组织为一个可验证的系统边界。

本地论文、项目快照和阅读笔记按 P0-P6 阶段存放在 [`references/`](../../references/README.md)。本文件维护设计映射，资料目录保存原始材料、版本信息与复现笔记。

| 参考方向 | 借鉴点 | 本项目进一步关注的问题 |
|---|---|---|
| [SayCan](https://arxiv.org/abs/2204.01691) | 语言模型结合可执行技能 | Skill 的显式前置/后置条件和跨域证据 |
| [Inner Monologue](https://arxiv.org/abs/2207.05608) | 环境反馈驱动闭环规划 | 独立 Verifier、结构化失败码和检查点 |
| [VLAs-as-Tools](https://arxiv.org/abs/2605.13119) | 高层模型调用专门 VLA | VLA 作为有限时域 Skill，而不是安全/验证边界 |
| [Being-0](https://arxiv.org/abs/2503.12533) | 人形 Foundation Model 与模块化能力 | 实体计算机、数字子智能体和软件结果验收 |
| [OmniH2O](https://arxiv.org/abs/2406.08858) | 重定向、运动跟踪和 Sim2Real | 坐下—工作姿态—精细操作的任务约束 |
| [HOVER](https://arxiv.org/abs/2410.21229) | 统一全身控制接口 | 接触模式、视野与下一 Skill 就绪性 |
| [VideoMimic](https://arxiv.org/abs/2505.03729) | 环境条件化全身技能 | 工位条件下的坐姿和双手可达性 |
| [Stay Seated](https://arxiv.org/abs/2608.28090) | 无动力万向轮椅上的全向坐姿移动、非对称 actor–critic、SY/FS/CC 消融 | 有限时域坐姿移动 Skill、独立进度/接触验证和失败恢复 |
| [ROS 2 Actions](https://docs.ros.org/en/humble/Concepts/Basic/About-Actions.html) | 长任务反馈、取消与抢占 | 后续真实 Skill Runtime 适配协议 |
| [Nav2 Behavior Trees](https://docs.nav2.org/behavior_trees/overview/detailed_behavior_tree_walkthrough.html) | 确定性执行与恢复 | Agent 之下的局部恢复层 |

## 当前代码中的对应关系

- SayCan/工具选择层：`SkillGraph`、`GraphSupervisor`；
- 环境反馈层：`Evidence`、`RuleBasedVerifier`；
- 技能工具层：`SkillContract`、`SkillRuntime`；
- 局部恢复层：`RecoveryManager`、`recovery_memory`；
- 长程状态层：`EventLedger`、`TaskCheckpoint`；
- VLA/控制接口占位：`RobotAdapter`、`PerceptionAdapter`；
- 数字 Agent 监管：`SubmitTask`、`RunTest`、`RequestFix`；
- 评测层：场景文件、`FaultInjector`、`benchmark.py`。
