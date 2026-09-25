# 站立工位：运动、双手敲击和屏幕反馈

本分支在 `humanoid-computer-agent-dex3-standing` 独立目录开发，目标版本为 Isaac Sim 6.0.1。机器人站立操作，不创建椅子。左右 Dex3-1 各使用食指，其他手指不参与输入。

## 当前可复现内容

| 组件 | 已实现及验证范围 | 尚不能认定的结果 |
|---|---|---|
| 工位接近与停止 | SE(2) 目标跟踪、速度/加速度限制、接触与倾斜检查、稳定停止的独立校验；CPU 运动学测试 | 真实腿部步态和自由站立平衡 |
| 两手协调 | 动态规划、同步预定位、按序输入；Isaac 6 中左右食指 `ak` 真实接触验收通过 | 较长 `afjk` 暴露姿态/跟踪超时，不能推广为任意文本通过 |
| 桌面修改 | 实际 Pygame 图像→Tesseract OCR→修正计划→按键事件→新图像→再次 OCR | 不能将桌面原图等同于机器人相机视野 |
| Isaac 屏幕 | 版本化 PNG 传输、USD 显示器材质、头部相机挂载、帧时间与输入事件水位 | 纹理提交不是相机已看到文字的证据 |
| 训练 | 固定种子、课程、CEM 有界残差策略、独立安全检查、检查点/恢复、独立评估集 | 合成训练成绩不能代表 PhysX 或实机成绩 |

`workstation_mission.py` 已串联完整反馈任务，默认使用运动学行走和合成按键执行器。导航未到位、未稳定或停止未验证时禁止进入输入阶段；物理输入执行器拒绝合成导航证据。任务最多执行一次有界修改，失败保留原因和按键状态，不自动无限重试。

## 快速复现

在本分支目录执行，使用普通 Python 3.12；桌面库安装到项目目录，不修改正在使用的 IsaacLab 环境：

```bash
python3 -m pip install --target artifacts/desktop-deps -r requirements-desktop.txt
make standing-navigation
make desktop-feedback
make workstation-feedback
make standing-training-smoke
make check
make demo
```

OCR 使用主机的 `tesseract` 和英语语言包。字体固定为 DejaVu Sans Mono。当前容器已经具备 OCR、字体和项目内 Pygame 2.6.1。桌面演示默认离屏运行；需要窗口时，用 `python3 -m vskill_os.workstation_mission --visible` 并设置 `PYTHONPATH=src:artifacts/desktop-deps`，不要设置 `SDL_VIDEODRIVER=dummy`。

任务把 `helo world` 读出后，保留公共前缀、退格并补入剩余字符，最终 OCR 必须精确得到 `hello world`。修改界面采用单行、末尾光标模式，最长 48 个可打印 ASCII 字符；不发送操作系统快捷键，不接管用户真实桌面。此约束使双食指键位覆盖与修改行为有明确边界。

输出：

- `reports/workstation_mission/report.json`：导航观测、独立验证、修改计划、两手任务、输入事件、最终读屏结果。
- `reports/workstation_mission/before/` 和 `after/`：实际图像和带摘要的元数据。
- `reports/feedback/`：单独的桌面反馈验证及本次 Isaac 检查日志。

## 物理运动与屏幕路径

高层任务只发出工位目标和技能请求。`StandingNavigationSkill` 输出机体速度命令；低层 `LocomotionPolicyAdapter` 负责匹配策略的关节控制，安全检查独立于策略。已找到本机官方 Unitree RL Lab 29DOF 行走策略，并实现 480 维观察到 29 动作的 CPU 推理。其原始 rev1.0 资产和全身控制权仍需向 mode15/5010＋双 Dex3 迁移；见 [候选与接入步骤](UNITREE_LOCOMOTION_CANDIDATES_CN.md)。

```mermaid
flowchart LR
  A[工位目标] --> N[导航技能与停止校验]
  N --> O[屏幕像素 OCR]
  O --> P[文字修改与双手规划]
  P --> C[双食指控制器]
  C --> D[Isaac 关节与 PhysX]
  D --> K[按键行程和接触校验]
  K --> E[Pygame 编辑器]
  E --> T[PNG 纹理传输]
  T --> M[Isaac 显示器]
  M --> R[机器人相机新帧]
  R --> O
```

CPU 验证直接从 Pygame 捕获像素。Isaac 路径由 `IsaacMonitorScreen` 提供独立材质和相机适配器，只有中央控制器推进世界时间；相机证据检查纹理提交之后的新渲染帧、输入水位和图像摘要。相机画面还需要屏幕定位与透视校正，再做 OCR；当前任务报告始终保留 `robot_camera_verified=false`，不会因为成功写入纹理就标记机器人看到了屏幕。

`PhysicalTypingExecutor` 可把控制器产生的实际按键事件转发到编辑器，并独立保存本次技能的空起始文字记录。已有文档内容只由外层 OCR 验证。底层事件使用仿真时间，编辑器另记主机接收时间，避免不同时间轴混用导致旧图像被接受。

## 与现有仿真共存

默认启动器仍拒绝任何已有 Isaac/计算进程。用户明确允许并发检查后，才能指定已识别的驻留 PID 使用受限共享模式：CPU PhysX、无相机、无渲染着色、最多 3000 控制步、至少 12 GiB 空闲显存。Kit 本身仍初始化图形环境，因此不是零 GPU 占用承诺。PID 每次运行前重新确认，不沿用报告里的旧 PID。

```bash
timeout --signal=INT --kill-after=15s 120s \
  ./scripts/run_standing_demo.sh --headless --no-cameras \
  --shared-cpu-physics --resident-pid CURRENT_RESIDENT_PID \
  --maximum-steps 800 --inspect-only \
  --output reports/feedback/isaac6_inspection.json
```

初次检查发现默认地面可能同步等待远程资产，已改为本地生成地面。入口记录阶段和超时堆栈，并恢复 Python 的中断处理以进入控制器释放流程。完成场景检查之后仍需独立取得接触敲击通过报告，不能把 `--inspect-only` 等同于敲击成功。

进一步接口与验收规则见 [行走控制](STANDING_LOCOMOTION_CN.md)、[桌面反馈](SCREEN_FEEDBACK_CN.md)、[Isaac 显示器桥](ISAAC_DESKTOP_BRIDGE_CN.md)、[训练框架](DEX3_TRAINING_CN.md)。
