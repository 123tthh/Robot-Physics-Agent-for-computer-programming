# 站姿工作树文档目录

本目录对应 `codex/dex3-standing-bimanual`，本地位置为 `/home/descfly/humanoid-computer-agent-dex3-standing`。这里保留独立运行环境、代码和报告，共享项目规划以[主项目入口](https://github.com/123tthh/Robot-Physics-Agent-for-computer-programming/blob/main/docs/overview.md)为准。

## 本地目录

```text
docs/
  README.md、overview.md、ARCHITECTURE.md 等  项目入口与治理
  core/                                  核心规范与轻量验证
  simulation/                            仿真底座与资产
  experiments/                           按手型和姿态划分的实验
  research/                              学习、椅子和自改进研究
  integrations/                          语音与上游项目
  proposals/                             原始提案和 PDF
  phases/                                P0–P6 任务细则
  archive/                               历史快照（主项目）
```

源码、配置、测试、工具、`reports/` 和 `artifacts/` 保持原路径。报告与资产中的哈希、录像路径和复现脚本继续有效。各专项目录有自己的 `README.md`，可直接按文件夹浏览。旧专项文档路径的迁移可在 Git 重命名记录中追溯。

## 实验与资料入口

- [站姿 Dex3 实验](experiments/dex3-standing/README.md)：运行、训练框架、桌面、读屏与导航候选。
- [Dex3 历史资产](experiments/dex3/README.md)、[仿真底座](simulation/README.md)。
- [核心规范](core/README.md)、[P0–P6 历史任务](phases/README.md)。
- [自改进研究](research/rsi/README.md)、[外部集成](integrations/README.md)。
- [原始提案与 PDF](proposals/README.md)。

本分支旧总览和阶段表保留为自身历史背景，不用于覆盖主项目的最新规划。目录同步不表示站姿代码已经并入主项目，也不升级固定基座、物理接触或机器人相机的验收范围。
