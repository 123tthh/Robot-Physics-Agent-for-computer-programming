# 连续坐下、触键与虚拟计算机

本轮按“先导出连续动作录像，再接虚拟屏幕”的顺序推进。机器人保持 G1 29DOF + 双 Dex5-1，用户已确认不切回 Dex3。原自由坐下和受支撑十指打字证据均保留；它们不能代替新的连续运行验收。

后续方向更新（2026-09-23）：用户要求不使用 IK，采用学习训练。新的[调研与实验方案](../../research/learning/DEX5_LEARNING_WITHOUT_IK.md)已经单独记录，尚未训练或替换控制器；本文及其录像保留为原有混合控制基线。

## 控制方法

- 坐下：GR00T 平衡网络以本体状态和有界高度/躯干指令生成下肢目标，由关节驱动在 PhysX 中执行，不使用骨盆轨迹或外部托举。
- 手部：测量坐稳后的自由底座姿态，使用 URDF 正运动学和有界最小二乘求解准备姿态，再用指尖位置雅可比做阻尼最小二乘 IK。IK 输出仍经关节驱动执行，不能直接移动指尖或键帽。
- 按键：仅键帽位移、指尖接触、迟滞和防抖生成 KeyDown/KeyUp。姿态规划完成、IK 收敛或文字目标都不作为按键成功的证据。
- 衔接：独立坐姿验证通过后才允许移手。全程保持下肢平衡控制、座面/足底观测、碰撞及倾角守卫。

## 几何变化与边界

座面名义高度从 0.4775 m 降至 0.44 m：缩短气柱，并下移座面和靠背，轮子及轮轴位置保持原值。验证器使用每条记录中的座面局部高度，并与报告中的椅子几何交叉检查。

新版手部控制不能直接复用固定骨盆的旧姿态。桌前距离、自由底座姿态和启用的自碰撞都会改变可达性。闲置手指需展开、错开高度，只让当前手指接近目标键；不能把宽于键距的手指强行挤到同一排键上。打字接触暂沿用明确标注的 6 mm 指尖球形代理，保留手指关节、惯量、其他碰撞及自由底座。

`--resume-diagnostic` 只用于从保存坐姿调试手部。此类运行缺少站立到坐下的物理过程，独立验收会明确拒绝。最终视频必须不带该参数，从初始站姿重新完整运行。

## 已通过的连续运行

```bash
make isaac-free-sit FREE_SIT_RUN_DIR=artifacts/free-sit/continuous-candidate \
  FREE_SIT_ARGS='--seat-height .44 --height .50 --hold-load-fraction .35 --typing-height-drop .02 --duration 60 --type-text "asdf jkl; " --room --desk-offset-x .12 --video'
PYTHONPATH=src python3 tools/verify_continuous_typing.py \
  artifacts/free-sit/continuous-candidate/report.json
```

已验收运行：`artifacts/computer/continuous-v2-seed7`，录像 60.3 s（含首尾定格），物理时间 60 s。十个手指各完成一次按下/释放，3000 条物理观测通过自由坐下和最终 2 s 稳定检查。最后窗口最小座面支撑 126.38 N、最大椅速 0.0071 m/s。此为一次完整验收，尚未声称两次连续十指运行逐样本一致。检查包括自由坐下、最终坐稳、十指接触与键程、全部事件保留、逐键释放和无键滞留；两段视频拼接不符合要求。

## 虚拟计算机接入顺序

```text
Isaac Sim 5.1 / Isaac Lab 2.3.2
  房间、工位、桌椅、G1 + 双 Dex5-1
  平衡与手部控制、指尖接触、被动键帽
       ↓ 实测 KeyDown / KeyUp
虚拟计算机
  Pygame 画面 ← PTY 真实 Shell
  可选本地代码 Agent / Codex → 独立测试执行器
       ↓ 帧编号、时间戳、屏幕像素
仿真显示器纹理 → 机器人 RGB-D 相机 → Verifier
```

已有 `PtyDesktopWorker` 提供真实 Shell 事件入口。终端直接文本、屏幕纹理像素和机器人相机观测须分别标注来源；读取 PTY 文本不能冒充相机识字。代码 Agent 的“完成”也不能替代独立测试退出状态。真实导航 rollout 与主动滑椅仍是单独阶段，不用重置位姿冒充移动。

## 虚拟屏幕复现与验收范围

```bash
make virtual-computer-deps
make isaac-virtual-computer VIRTUAL_COMPUTER_RUN_DIR=artifacts/computer/virtual-new
make verify-virtual-computer VIRTUAL_COMPUTER_RUN_DIR=artifacts/computer/virtual-new
```

可选依赖装入项目 `artifacts/virtual-computer/deps`，使用 Isaac 环境的 Python 3.11，不能混用系统 Python 的二进制扩展。OCR 另需系统 `tesseract`。输出包含外部双视角视频、真实 PTY 日志、转发事件、Pygame 像素哈希、机器人 RGB PNG、深度 NPY、相机内外参以及独立验证结果。

机器人视角另可导出：`python3 tools/export_robot_camera_video.py <run>/report.json --output <run>/robot-view.mp4`。该视频按实际时间保持每个 1 Hz RGB 观测帧，编码为 20 fps，不生成中间视觉状态。`scene.usda` 是初始几何存档；动态 PTY 屏幕需通过运行入口启动，单独打开 USD 不会启动 Shell。离线屏幕像素另存为 PNG。

该屏幕验收任务是实体输入 `ls` 与 Enter，列出工作目录中预置的 `KEYBOARD_OK` 文件。只有键帽桥产生的事件可送入 PTY；文件名不出现在静态标题或期望文字中。验证器必须在输入前的机器人图像中找不到它，在输入后的图像中通过 Tesseract 找到它，且同时通过整段自由坐下与三次触键验收。此任务验证三个手指；十指证据来自前一条独立的连续录像。

动态纹理使用 `dynamic://vskill_terminal`，由 Pygame RGBA 像素更新 RTX 材质。头部相机使用固定安装变换；由于 Fabric 不保证同步 USD 父层级，每次渲染从 PhysX 实测 head_link 位姿组合该变换，绝不通过朝向显示器的 look-at 来调整视线。RGB-D 参数与姿态随帧记录。

`LocalCodexAdapter` 实现项目的 CodeAgentAdapter 接口，使用本机 `codex exec`、workspace-write 沙箱、标准输入任务及 JSON 事件；`LocalTestRunnerAdapter` 实现独立 TestRunnerAdapter。没有启动代码 Agent 模型任务，也没有在本次屏幕演示中生成程序；`code_agent_executed` 保持 false。PTY 工作目录不是操作系统安全沙箱。默认可信源不自动扩展为代码 Agent；任何代码任务成功状态仍需独立测试退出状态。

参考：[NVIDIA 动态纹理接口](https://docs.omniverse.nvidia.com/kit/docs/omni.ui/3.2.0/omni.ui/omni.ui.DynamicTextureProvider.html)、[USD 纹理材质](https://docs.omniverse.nvidia.com/dev-guide/latest/programmer_ref/usd/materials/create-usdpreviewsurface-material.html)、[Codex 非交互接口](https://learn.chatgpt.com/docs/non-interactive-mode)。本机实际 API 同时对照 Isaac Sim 5.1 的 Kit 2.27.1 接口和安装的 Codex CLI 帮助核查。

## 屏幕闭环验收结果

`artifacts/computer/virtual-wide-seed7` 已完成 40 s 连续运行，所有独立门禁通过：三次物理按键的六个事件原样转发，真实 Shell 返回 `KEYBOARD_OK`，机器人相机 OCR 在输入后读到该结果、输入前不存在；深度有效像素比例 94.68%。[验收记录](../../../reports/isaac_continuous_computer/virtual-screen-acceptance.json)与[机器人最终视角](../../../reports/isaac_continuous_computer/robot-camera-final.png)已保存。

相同物理配置的前一轮运行（48 s，较窄相机视野）也通过自由坐下与三键输入验收。两轮前 40 s 的 2000 条物理观测逐样本一致；相机焦距不同，因此该比较只证明物理控制复现，不声称相机图像一致，也不代替十指重复试验。十指运行和广角屏幕运行均另存匹配报告 SHA-256 的控制代码/配置快照于各自 `source/manifest.json`。
