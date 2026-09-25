# P4 坐下、座椅与工作姿态

对应规划：[`docs/phases/phase_4_posture_and_chair.md`](../../docs/phases/phase_4_posture_and_chair.md)

2026-09-22 专题：[机器人—椅子交互研究](../../docs/CHAIR_INTERACTION_RESEARCH.md)、[工程与训练接口](../../docs/CHAIR_INTERACTION_ENGINEERING.md)、[BibTeX](chair_interaction.bib)。新增 seated walk / Learning to Sit 对照，区分实机坐姿移动、虚拟人体坐下与通用训练框架；本轮未运行测试或 demo。

## 收录范围

- 坐下/站起、运动重定向和全身模仿；
- 多接触稳定、屏幕可见和双手可达；
- 无动力万向轮椅坐姿移动；
- 移动结束后的工作姿态再验证与局部恢复。

## 初始参考清单

- Stay Seated；
- OmniH2O；
- HOVER；
- VideoMimic；
- BeyondMimic / Whole Body Tracking；
- WBC-AGILE；
- GR00T Whole-Body Control；
- ResMimic；
- HoST（仅作为跌倒恢复对照）。

Stay Seated 资料需明确标记：RSJ 公告与 arXiv 属同一研究项目；截至当前未发现作者公开的座椅训练代码或权重，`mjlab` 和 Unitree 工程只能作为底层框架/部署参考。

2026-09-23：[自由平衡坐下实验](../../docs/FREE_BALANCE_SITTING.md)已接入独立来源的 GR00T Balance ONNX 候选，固定提交及哈希见 [模型锁文件](../../configs/simulation/free_sitting_policy.json)。[GR00T_LICENSE.txt](GR00T_LICENSE.txt)保留该提交的官方代码/模型许可证。它不包含 Stay Seated 的任务策略。
