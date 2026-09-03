# V-Skill OS

Verifier-grounded Agent–Skill 工程骨架，用于 Unitree G1 通过实体键盘和屏幕操作并监管数字代码智能体。

## 当前目标

先建立与硬件、模型供应商无关的最小闭环：

```text
Skill 调用
→ 前置条件检查
→ 闭环执行
→ 独立状态验证
→ 事件账本
→ 失败恢复
```

当前仓库实现了：

- 类型化 Skill 契约与版本化注册表；
- 异步 Skill Runtime、资源锁和前置条件检查；
- 基于证据的规则 Verifier；
- SQLite 事件账本；
- 失败码到恢复 Skill 的映射；
- “打开终端—调用代码 Agent—测试失败—修复—测试通过”的模拟闭环；
- 标准库 `unittest` 回归测试。

## 快速运行

```bash
cd /root/humanoid-computer-agent
make check
make demo
```

也可以安装为可编辑包：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
vskill-demo
```

## 目录

```text
configs/skills/       Skill 契约配置示例
docs/                 架构、接口与构建顺序
src/vskill_os/        核心 Python 包
tests/                单元与端到端测试
```

## 下一步接入

1. 用 ROS 2 Action 适配器替换模拟 Skill；
2. 实现 `press_chord → terminal_ready` 的视觉/键盘双路验证；
3. 接入真实 Codex 沙箱和测试执行器；
4. 接入键盘标定、按键与工作姿态控制；
5. 最后加入 G1 导航、坐下和全身模仿策略。

项目总体设计见 [docs/PROJECT_OVERVIEW.md](docs/PROJECT_OVERVIEW.md)，接口约定见 [docs/SKILL_SPEC.md](docs/SKILL_SPEC.md)。
