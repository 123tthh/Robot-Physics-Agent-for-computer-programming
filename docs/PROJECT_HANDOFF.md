# 项目交接入口

新任务依次读取：

1. [仓库约束](../AGENTS.md)与[项目总览](overview.md)。
2. [分支登记](BRANCHES.md)，并检查实时工作树状态；主项目与站姿项目分别使用独立工作树，已发布内容及本地路径见分支登记。
3. [子项目登记](WORKSTREAMS.md)中的目标路线，以及[统一架构](ARCHITECTURE.md)。
4. [路线图](ROADMAP.md)与[开发协议](CODEX_WORKPLAN.md)，再进入对应专项设计。
5. 运行 `make check`、`make demo`，分别记录轻量回归与所需物理验收。

旧交接中“从 feat/g1-5010-control-stack 建分支”及多个“最佳下一项”已由上述统一入口替代。原内容保留在[历史快照](archive/2026-09-25/PROJECT_HANDOFF.md)，不要据旧快照覆盖现有未提交成果。
