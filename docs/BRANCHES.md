# 分支与工作树登记

更新：2026-09-26，VF-01-T05 实现阶段。从已发布的 T04 提交 `bd5d42f` 新建真实终端与机器人相机复验分支。下面记录职责与本地位置，准确提交以 Git 为准，历史未提交盘点保留在 `fe8e78e` 的本文件中。

## 1. 分支职责

| 分支 | 最近发布基线 | 当前用途 |
|---|---|---|
| `main` | `e2ec5b8` | 项目整合入口，包含 Dex5、桌面、椅子研究与统一规划 |
| `codex/learning-system-architecture` | `e2ec5b8` | 研究架构与目录整理基线；本轮实现另开功能分支 |
| `codex/vf01-evidence-contracts` | `746dee2` | 六阶段研究说明、VF-01-T01 离线契约和独立评估 |
| `codex/vf01-single-hand-env` | `ca7c02b` | VF-01-T02 直接关节单环境、原始物理记录与独立验收 |
| `codex/vf01-text-feedback-isolation` | `3794312` | VF-01-T03 文本状态、反馈权限/预算与物理集成验收 |
| `codex/vf01-batch-isolation-throughput` | `bd5d42f` | VF-01-T04 批物理环境、局部 reset 隔离、1/16/64 吞吐与资源验收 |
| `codex/vf01-desktop-camera-revalidation` | `bd5d42f`（开发起点） | 主工作树当前分支；VF-01-T05 真实 PTY、机器人 RGB-D/OCR、反馈预算与六案例独立验收 |
| `codex/dex3-standing-bimanual` | `7fc204c` | 独立站姿路线，保留自身代码、环境、报告和目录整理 |
| `codex/robot-chair-interaction` | `e81b6f3` | 保留的历史主题引用；后来保存的椅子代码已进入整合入口 |
| `feat/g1-5010-control-stack` | `e81b6f3` | 历史运控基线 |
| `feat/simulation-platform` | `45fc687` | 历史仿真平台基线 |

“最近发布基线”标识本次目录整理的起点，后续布局提交不会反向改写这些提交。主项目和站姿路线分别推进，目录相同不表示实现已经合并。历史分支不是当前工作目录的副本，不为了目录一致而重写历史。

## 2. 主项目工作树

路径：`/home/descfly/humanoid-computer-agent`。

| 内容 | 本地入口 | 保存方式 |
|---|---|---|
| 总体规划、架构、状态 | [文档总目录](README.md)、[项目总览](overview.md) | 版本控制 |
| 公共规范与仿真 | [core/](core/README.md)、[simulation/](simulation/README.md) | 版本控制 |
| Dex5、自由坐下与连续输入 | [Dex5 实验](experiments/dex5/README.md) | 源码/配置/测试与小型报告版本控制 |
| 椅子、学习与 RSI | [研究目录](research/README.md) | 研究设计与实现范围分别记录 |
| 原始提案与 PDF | [proposals/](proposals/README.md) | 保留作者内容，文件名移除前导空格 |
| 物理报告与截图 | `reports/` | 保留原路径和字节，供原验收复核 |
| 录像、权重与运行目录 | `artifacts/` | Git 忽略，本地保存，不自动上传 |
| 临时会话文件 | `artifacts/local-archive/2026-09-25/:memory:.ses` | 从仓库根归档，未删除内容 |

旧的平铺专项文档归入对应目录，仓库内链接已同步。`src/`、`configs/`、`tests/`、`tools/` 和原始报告未因目录整理迁移，避免破坏运行入口、资产引用及证据哈希。

## 3. Dex3 站姿工作树

路径：`/home/descfly/humanoid-computer-agent-dex3-standing`；分支 `codex/dex3-standing-bimanual`。

该树采用同一文档目录分类，但只包含自身已有内容。入口为该树的 `docs/README.md`，站姿专项归入 `docs/experiments/dex3-standing/`；不会整树复制主项目的控制器、资产或状态表。

| 范围 | 相对站姿工作树的路径 | 已知边界 |
|---|---|---|
| 运行说明 | `docs/experiments/dex3-standing/STANDING_DEX3_DEMO_CN.md` | Isaac 6.0.1，固定站姿，每手一根食指 |
| 运行入口 | `scripts/run_standing_demo.sh` | 原路径和环境隔离不变 |
| 物理记录 | `reports/feedback/physical_reproducibility.json` | 两次 `ak` 通过；`afjk` 超时且未完全释放 |
| 早期框架记录 | `reports/standing/{delivery_status,validation_summary}.json` | CPU 合成训练，不可冒充物理能力 |
| 领域与训练 | `src/vskill_os/{standing_control,standing_typing,workstation_mission}.py`、`training/` | 独立实现，待逐项对齐主项目契约 |
| 桌面与导航候选 | `adapters/pygame_desktop.py`、`adapters/isaac/monitor_screen.py`、`adapters/unitree_velocity.py` | 保留接口差异，尚不证明自由站立或行走 |

上表简写源码路径相对该树 `src/vskill_os/`。站姿树中的 Dex5 支持代码及原始提案已在 `5989816` 保存，不再是未跟踪成果。

## 4. 后续整合规则

1. 以明确提交为起点，在独立工作树完成单项能力。
2. 先对齐证据来源、资产身份、事件桥与桌面端口，再接站姿物理控制和训练候选。
3. 保留 5.1 / 6.0.1 环境隔离和失败样例；每项整合分别运行核心回归与所需物理验收。
4. 新目录布局只整理资料，不表示后端整合、无 IK 学习、导航或真机已完成。
5. 新功能分支使用 `codex/<能力>-<任务>`；不删除历史引用，不强推覆盖他人提交。

## 5. 刷新盘点

```bash
git status --short --branch
git branch -avv
git worktree list --porcelain
git log --all --oneline --decorate -18
```

上述为本地查询；检查服务器实际状态可使用 `git ls-remote --heads origin`。本文件不替代实时 Git 状态。当前能力与下一项工作分别见 [子项目登记](WORKSTREAMS.md) 和 [路线图](ROADMAP.md)。
