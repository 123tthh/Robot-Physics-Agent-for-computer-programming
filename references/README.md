# 参考论文与项目资料库

本目录按照 [`docs/phases/README.md`](../docs/phases/README.md) 的 P0-P6 技术阶段存放外部参考资料。阶段表示资料主要服务的首次实现阶段，不表示论文或项目只能被该阶段使用。

## 目录结构

| 阶段 | 本地目录 | 主要主题 |
|---|---|---|
| P0 | [`P0_runtime_baseline/`](P0_runtime_baseline) | Agent-Skill 契约、Verifier、恢复与账本 |
| P1 | [`P1_hybrid_simulation/`](P1_hybrid_simulation) | 混合仿真、机器人资产、接触与 Adapter |
| P2 | [`P2_bimanual_typing/`](P2_bimanual_typing) | 双手多指、键盘操作、视觉伺服与示范学习 |
| P3 | [`P3_code_agent_loop/`](P3_code_agent_loop) | 隔离代码 Agent、独立测试与软件证据 |
| P4 | [`P4_posture_and_chair/`](P4_posture_and_chair) | 坐下、工作姿态、坐姿移动与全身控制 |
| P5 | [`P5_navigation_and_voice/`](P5_navigation_and_voice) | 导航、ROS 2 Action、本地语音与工具规划 |
| P6 | [`P6_sim2real_self_improvement/`](P6_sim2real_self_improvement) | Sim-to-real、安全评测、消融与受约束更新 |

每个阶段包含：

```text
papers/    论文 PDF、作者公开补充材料或合法取得的论文源文件
projects/  上游项目快照、许可证、commit 记录和本项目适配说明
notes/     阅读笔记、复现差异、实验记录和采用决策
```

## 存放规范

- 论文文件名使用 `年份_第一作者_短标题.pdf`，例如 `2026_Yanagida_Stay_Seated.pdf`。
- 每篇论文在同阶段 `notes/` 下建立同名 Markdown，至少记录来源 URL、获取日期、版本、许可证、核心结论和本项目采用边界。
- 上游项目不要只保存仓库名；至少记录仓库 URL、固定 commit/tag、许可证、依赖版本和是否包含权重/数据。
- 同一资料跨阶段使用时只保留一份主副本，其他阶段在 README 或笔记中相对链接，避免多个版本静默漂移。
- 未公开、需登录或许可证不明的内容只记录书目信息和访问状态，不绕过访问控制，也不提交来源不明的副本。
- 外部项目运行结果、论文作者声明和本项目验证结果必须分开记录；论文或模型存在不等于本项目已经复现。

机器可读的上游项目目录仍位于 [`configs/research/projects.json`](../configs/research/projects.json)，本目录用于保存可审阅的原始资料和研究笔记。
