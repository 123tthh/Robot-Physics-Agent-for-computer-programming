# 分支与工作树登记

盘点时间：2026-09-25。以下来自本地 `git branch -avv`、`git worktree list`、提交图和两个工作树状态。`origin/main` 仅是本地保存的远端跟踪引用，本轮未联网 fetch；不宣称服务器当前状态。

## 发布布局

用户随后要求将整理结果及所有分支推送至现有 GitHub 仓库。本次发布把当前工作树的源码、配置、测试、研究资料和报告保存到 `codex/learning-system-architecture`，并将 `main` 快进到同一整合提交；站姿工作树的待保存内容单独提交到 `codex/dex3-standing-bimanual`。其他三个历史分支保持原提交位置，一并发布。

以下第 1–3 节是**提交前盘点快照**，其中“未提交/未跟踪”描述整理时的状态；不代表发布后的文件仍未保存。新的准确提交关系以 Git 为准，不在本文硬编码本文所属提交的哈希。现有 `artifacts/` 忽略规则继续保留，录像、权重和运行工作区不随普通 Git push 上传。

## 1. 提交关系

```text
main                         d4f4f71   历史核心脚手架
  └─ feat/simulation-platform 45fc687   历史仿真平台
       └─ …                  e81b6f3   origin/main（本地快照）
                              ├─ feat/g1-5010-control-stack
                              ├─ codex/robot-chair-interaction
                              ├─ codex/learning-system-architecture（当前，含大量未提交成果）
                              └─ d5a2b6c → ca415d7
                                  codex/dex3-standing-bimanual（独立工作树）
```

`e81b6f3` 是合并提交，上图省略合并侧历史，只表示祖先关系。三个分支名指向同一提交，不代表椅子与学习成果已经分别提交到独立分支。未提交文件属于当前工作树；切换同提交分支不会自动隔离它们。

| 引用 | 本地提交 | 相对本地 `origin/main` | 处理原则 |
|---|---|---|---|
| `main` | `d4f4f71` | 落后 6 个提交 | 历史基线；不要把它误当最新整合点 |
| `feat/simulation-platform` | `45fc687` | 落后 5 个提交 | 内容已在共同祖先链中，保留历史来源 |
| `feat/g1-5010-control-stack` | `e81b6f3` | 相同 | 旧运控主题名，不再作为所有任务默认起点 |
| `codex/robot-chair-interaction` | `e81b6f3` | 相同 | 椅子新增内容实际在当前树，尚无专属提交 |
| `codex/learning-system-architecture` | `e81b6f3` | 相同 | 当前树承载 Dex5、椅子、学习/RSI 文档及本次整理 |
| `codex/dex3-standing-bimanual` | `ca415d7` | 领先 2 个提交 | 独立工作树；有自身未提交改动，需逐模块整合 |

## 2. 当前工作树

路径：`/home/descfly/humanoid-computer-agent`。

开始整理时已有以下工作，必须按内容识别归属，不能按当前分支名推断：

| 工作包 | 代表路径 | 保存情况 |
|---|---|---|
| Dex5 / 自由坐下 / 连续输入 | `adapters/isaac/{dex5_demo,free_sitting_scene,free_typing,balance_policy}.py`、`simulation/{dex5_assets,dex5_typing,free_sitting}.py`、相关配置/工具/测试/报告 | 大量未跟踪文件，并有 README/Makefile 等改动 |
| 桌面与代码端口 | `adapters/{virtual_computer,local_code_agent}.py`、`adapters/isaac/virtual_screen.py` | 未跟踪 |
| 椅子研究 | `chair/`、`adapters/chair_*.py`、`configs/chair/`、研究文档和测试 | 未跟踪，关联已有入口修改 |
| 学习 / RSI | `docs/LEARNING_SYSTEM_ARCHITECTURE.md`、`DEX5_LEARNING_WITHOUT_IK.md`、`RSI_*.md` | 规划文档未跟踪，不是训练实现 |
| 原始提案 | `docs/ 项目建议书.md`、`docs/项目/`；原 `docs/PROJECT_PITCH_CN.md` 已删除 | 用户已有变动，本轮保留 |
| 本次结构整理 | README、统一规划、文档目录、历史快照、Make 帮助 | 本轮新增/更新，验收见[整理记录](REORGANIZATION.md) |

表内源码路径相对 `src/vskill_os/`。无归属的临时文件（如 `:memory:.ses`）不纳入架构，也不自动删除。

## 3. Dex3 站姿工作树

路径：`/home/descfly/humanoid-computer-agent-dex3-standing`；分支 `codex/dex3-standing-bimanual`。

专属提交：

- `d5a2b6c`：站姿双手规划、控制、残差训练框架。
- `ca415d7`：Unitree velocity 候选、工作站/屏幕反馈框架。

相对共同基线改变 76 个文件，约 2.7 万行（包含大量报告），不适合按提交大小误判为已完成的通用能力。该树另有未提交的 Dex5 资产/几何和原始提案，不能被整树复制掩盖。

关键位置均相对该工作树：

| 范围 | 位置 | 已知边界 |
|---|---|---|
| 物理入口 | `docs/STANDING_DEX3_DEMO_CN.md`、`scripts/run_standing_demo.sh` | Isaac 6.0.1，固定站姿、每手一根食指 |
| 最新物理记录 | `reports/feedback/physical_reproducibility.json` | 两次 `ak` 通过；`afjk` 为 `MOTION_TIMEOUT` 且未完全释放 |
| 早期框架记录 | `reports/standing/{delivery_status,validation_summary}.json` | 当时未运行物理，只记录 CPU 合成训练；不能覆盖后续物理报告，也不能把合成分数当物理能力 |
| 站姿域与训练 | `src/vskill_os/{standing_control,standing_typing,workstation_mission}.py`、`training/` | 与当前拟建 `learning/` 有重叠，整合前先对齐来源和契约 |
| 桌面与相机 | `adapters/pygame_desktop.py`、`adapters/isaac/monitor_screen.py`、`simulation/screen_feedback.py` | 与当前 `virtual_computer` / `virtual_screen` 职责重叠，先做端口对照 |
| 导航候选 | `control/standing_locomotion.py`、`adapters/unitree_velocity.py` | 候选与合成反馈框架不证明自由站立/行走 |

跨工作树文件不写成当前仓库的相对链接，避免普通 clone 后产生假入口。本轮只读取该树，未修改文件、运行其仿真或移动分支。

## 4. 建议的整合顺序

1. **固化当前成果**：按 Dex5、DESKTOP、CHAIR、规划四类整理提交清单；保留原作者改动和已删除文件状态，先确认每包所需配置/测试/报告齐全。
2. **选共同整合基线**：记录明确提交 SHA。获取远端最新状态后再决定是否更新本地 `main`；旧交接文档中“总从 feat 分支起步”的约定作废。
3. **优先统一证据与端口**：资产身份、事件桥、屏幕接口和报告来源先对齐；保留各后端启动与环境锁。
4. **逐项接站姿能力**：规划/Verifier → 桌面与反馈 → 物理 Adapter → 训练候选，逐项运行回归；同时保留 `afjk` 失败样例。
5. **最终收敛引用**：整合验收后才处理重复分支与旧入口；本轮不删除、重置、合并或强推任何分支。

后续功能分支使用 `codex/<能力>-<任务>`，每个并行实现使用独立工作树；起点是已记录的整合提交，而不是名字看起来最新的分支。共享文档状态由子项目登记维护，各分支只追加自己的运行证据和限定范围。

## 5. 刷新盘点

```bash
git status --short
git branch -avv
git worktree list --porcelain
git log --all --oneline --decorate -18
git rev-list --left-right --count origin/main...codex/dex3-standing-bimanual
```

这些是只读本地查询。重新开发前刷新结果；本文件是带日期的快照，不代替实时 Git 状态。
