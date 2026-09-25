# Isaac 6 显示器、桌面像素与机器人相机

这个桥接器把真实桌面渲染得到的 PNG 更新到 Isaac 场景中的显示器，再从机器人相机和全景相机保存独立画面。
它不从编辑器对象读取文本作为视觉证据，也不根据“纹理设置成功”判定机器人已经看清屏幕。

代码入口：

- `src/vskill_os/adapters/pygame_desktop.py`：桌面渲染与物理键盘事件消费。
- `src/vskill_os/simulation/screen_feedback.py`：RGB 像素、PNG 发布、OCR 及反馈契约。
- `src/vskill_os/adapters/isaac/monitor_screen.py`：安全帧读取、显示器材质、相机记录与同步门禁。
- `tests/test_monitor_screen.py`：无需 Isaac 的传输和相机同步测试。
- `scripts/monitor_bridge_example.py`：只读离线检查，以及供已有场景调用的单一时钟示例。

## 证据边界

| 记录 | 它能说明什么 | 尚不能说明什么 |
|---|---|---|
| 桌面 PNG | pygame/Pillow 实际绘制的像素；已消费的按键事件计数 | 机器人相机看到了屏幕 |
| 材质更新记录 | 指定 PNG 已提交给 3D 显示器材质 | GPU 已加载这一纹理；屏幕文字可读 |
| Isaac 相机 PNG | 相机在新渲染时间产生的 RGB 画面及位姿 | OCR 正确、焦点正确、任务成功 |
| 独立图像识别结果 | 从指定像素文件提取的文本/焦点观察 | 指尖接触和物理按键已经通过 |

相机文件中的 `texture_submission_frame_id` 表示最近提交的桌面帧编号。
这不是从相机画面识别出来的帧编号。`display_content_verified` 保持 `false`，直到上层独立视觉验证器检查相机像素。
`screen_text_from_privileged_state` 固定为 `false`，相机报告不包含编辑器内部文本。

## 桌面帧协议

生产者调用 `publish_frame(frame, directory)`，先原子写入 `frame-00000001.png`，再原子替换 `frame.json`。
PNG 是 RGB8、无隔行、filter 0 的受限格式，便于标准库完成校验。

清单版本为 `schema_version: 1`，包含：

```json
{
  "schema_version": 1,
  "frame_id": 1,
  "captured_at_s": 1234.5,
  "consumed_event_count": 2,
  "pixel_size": [1024, 320],
  "format": "png",
  "file": "frame-00000001.png",
  "source": "pygame_desktop",
  "sha256": "PNG 文件的 SHA-256",
  "pixel_sha256": "解码 RGB 像素的 SHA-256",
  "provenance": {
    "capture": "rendered_rgb_pixels",
    "renderer": "pygame_desktop",
    "clock": "local_monotonic",
    "text_oracle": false
  }
}
```

`captured_at_s` 使用同一机器上的 `time.monotonic()`，不能混用 Unix 时间或仿真时间。
`consumed_event_count` 是桌面已经消费的键盘事件水位，不是推测屏幕文本。
Pillow 回退渲染器必须标记为 `pillow_desktop`，不能冒充 pygame 或 Isaac 相机。

消费者 `FrameReceiver` 会拒绝：版本错误、相同/倒退帧号、倒退事件水位、过期/未来时间、路径越界、符号链接、非普通文件、超限尺寸、PNG CRC 错误、PNG 哈希或解码像素哈希不符。
失败帧不推进接收序列。生产者重启并重置帧号时必须新建接收器，不能静默接受旧序列。

## Isaac 集成顺序

该模块顶层没有 Isaac、OpenUSD、NumPy 或 Pillow 导入。
以下代码由已经获得运行准入并启动 `SimulationApp` 的主场景调用；桥接器本身不启动仿真、不推进物理时钟。

```python
from pathlib import Path
from vskill_os.adapters.isaac.monitor_screen import IsaacMonitorScreen, ScreenFrameError
from vskill_os.simulation.screen_feedback import publish_frame

# world.reset() 已完成。desktop 为 PygameDesktop 等真实像素生产者。
monitor = IsaacMonitorScreen(
    stage,
    transport_dir=Path("reports/desktop_transport"),
    output_dir=Path("reports/monitor"),
    center=(1.08, 0.0, 1.03),
    width_m=0.48,
    height_m=0.27,
)
monitor.add_camera(
    "robot",
    eye_world=(0.42, 0.0, 1.22),
    target_world=(1.08, 0.0, 1.03),
    mount_prim_path="/World/G1/d435_link",
    resolution=(960, 540),
)
monitor.add_camera(
    "overview",
    eye_world=(-0.7, -1.15, 1.5),
    target_world=(0.7, 0.0, 0.9),
    resolution=(960, 540),
)

# 必须在 SimulationApp 完成启动后重新采集；启动前的帧可能已经过期。
pixels = desktop.capture()
publish_frame(pixels, Path("reports/desktop_transport"))
monitor.update_from_transport(
    simulation_time_s=world.current_time,
    minimum_event_count=pixels.consumed_event_count,
)

# 由已有中央步进器执行；不要在两个手臂控制器中各自推进仿真。
world.step(render=True)
try:
    observation = monitor.capture(
        "robot",
        physics_tick=world.current_time_step_index,
        simulation_time_s=world.current_time,
    )
except ScreenFrameError:
    # 初始纹理/相机可能尚未产生新像素。由主循环按预算继续推进，不能伪造成功。
    observation = None
```

`mount_prim_path` 必须指向当前场景中实际存在的机器人部件。初始位姿用世界坐标设置，之后相机随父部件运动；相机标定与安装偏移仍须根据实际模型检查。
显示器采用面向机器人 `-X` 的 UV 网格，`+Y` 为操作者左侧。材质使用发光 `UsdPreviewSurface`，纹理采用内容哈希构成的新文件路径，避免覆盖同名 PNG 后渲染器仍缓存旧图。
背板目前是视觉几何；若后续动作可能触碰显示器，主场景需另行配置碰撞和安全守卫。

Isaac 6.0.1 的兼容 `Camera` 实现将 `rendering_frame` 写成包含 `referenceTimeNumerator` / `referenceTimeDenominator` 的字典。
同步门禁把它作为有理数比较，不能直接转换成整数帧号，也不能只比较分子。
重复调用渲染但不推进仿真时间，可能仍得到同一时间戳，因此相机记录会拒绝旧像素。

相机结果写入 `reports/monitor/observations/` 的 PNG 和同名 JSON，包含相机路径、安装父节点、世界位姿、物理 tick、仿真时间、渲染参考时间、像素哈希和最近纹理提交信息。
渲染时间推进只证明新相机采样发生，不能证明异步纹理加载已经完成；后续 OCR/视觉校验必须读取相机 PNG。

`verify_robot_camera_text(metadata, expected_text=..., focus_region=..., text_region=...)` 会校验真实相机 PNG 的来源、安装父节点、时间与哈希，再调用本地 Tesseract 从相机像素识别焦点和文本。
只有文本和焦点均匹配时，它才输出 `robot_visual_verified: true`；桌面帧和没有机器人安装节点的全景相机不能走这个成功路径。
OCR 区域必须在真实相机画面中标定，不能直接复用桌面原图坐标；透视畸变较大时需要另行记录并验证图像矫正步骤。
该结果始终保留 `physical_acceptance: false`，物理键程和食指接触仍须由对应验证器判断。

## 不启动 Kit 的只读示例

```bash
PYTHONPATH=src python3 scripts/monitor_bridge_example.py \
  --transport reports/feedback/after --offline
```

`--offline` 只检查归档 PNG 与清单的完整性，输出 `live_freshness_checked: false`，不能作为实时屏幕状态。
去掉此标志则使用当前单调时钟检查新鲜度，过期帧会被拒绝。
脚本不会写入源帧、创建 `SimulationApp` 或 `World`，也不调用渲染。

在已经具有 OpenUSD 的普通 Python 环境中，可以附加 `--robot-usd PATH`，仅在内存 stage 中检查相机安装路径。
本机目标 G1 mode 15 + Dex3 资产已确认存在 `/World/G1/d435_link`；`find_robot_camera_mount` 按实际模型层级查找唯一的 `d435_link`，避免依靠硬编码假设。

脚本的 `attach(stage, transport_dir=..., output_dir=...)` 和 `central_monitor_cycle(world, monitor, desktop)` 是可导入的集成示例。
它们使用传入的已有世界；示例只有一个 `world.step(render=True)` 调用点。
需要同时执行机器人运动时，应将“发布像素 → 更新纹理 → 中央步进 → 获取相机 → OCR”顺序并入原控制循环，不能并行再启动一个循环推进同一世界。

## 低开销启动配置

从本机 Isaac 6.0.1 `simulation_app.py` 确认以下参数可用：

```python
SimulationApp({
    "headless": True,
    "renderer": "MinimalRendering",
    "multi_gpu": False,
    "max_gpu_count": 1,
    "width": 640,
    "height": 360,
    "anti_aliasing": 0,
    "disable_viewport_updates": True,
    "limit_cpu_threads": 4,
})
```

该配置用于降低初始化及画面成本，**不代表完全不占用 GPU**。CPU 物理仍需显式配置 `World(backend="numpy", device="cpu")`。
仅做物理诊断时不要创建相机，并使用 `world.step(render=False)`；相机验证需要真实渲染和 render product。
`disable_viewport_updates` 只关闭默认窗口的更新，不关闭相机输出；不能据此绕过运行准入或忽略其他 GPU 任务。
最终显示器材质、文字清晰度与相机可读性必须在实际采用的渲染模式下验收。

## 本模块检查结果

已执行 9 项 CPU 测试，覆盖生产者/接收器联通、哈希/CRC、过期和倒退、事件水位、路径与符号链接、相机时序、Isaac 6 有理数参考时间，以及拒绝将桌面/全景图片冒充机器人视觉。
另用本机 OpenUSD 构建显示器网格/材质并接收已生成的 pygame PNG，记录于 `reports/monitor_static/inspection.json`。
静态检查不启动 Kit，也不包含相机画面或渲染成功结论。实际仿真与相机验收由主场景的本次运行报告提供。
