# 屏幕像素 → 观察 → 编辑 → 再验证

这部分实现本地安全文本编辑器的可执行反馈循环。默认演示将 `helo world` 修正为 `hello world`：实际渲染 Pygame RGB 图像，Tesseract 从图像读出文字，规划 7 次退格和 `lo world`，消费键盘事件，再从新图像独立验证最终文本。

这是**真实的软件渲染和像素 OCR**。默认 CPU 输入是合成键盘事件，不是机器人实际接触，也没有启动 Isaac 或摄像机。报告中保留 `physical_acceptance=false`。后续 Isaac 集成必须把物理键盘桥接产生的事件交给同一个编辑器，并另外验证机器人接触、释放及安全条件。

## 运行

本次项目内安装了可选 Pygame 2.6.1，目录为 `artifacts/desktop-deps`。系统已有 Tesseract 和 Pillow 10.2.0，没有修改全局 Python 包或启动 GPU 进程。

```bash
PYTHONPATH=src:artifacts/desktop-deps \
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy \
python3 -m vskill_os.adapters.pygame_desktop \
  --backend pygame --initial 'helo world' --target 'hello world' \
  --output reports/feedback

PYTHONPATH=src:artifacts/desktop-deps \
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy \
python3 -m unittest discover -s tests -p test_screen_feedback.py -v
```

`--backend pygame` 明确要求 Pygame，缺失时不会伪装成功；`--backend pillow` 使用 Pillow 无窗口渲染；`--backend auto` 优先选 Pygame，否则选 Pillow，并记录实际后端。Pygame 默认仅创建软件 Surface，不打开显示窗口；需要显示时显式加 `--visible`，并在有图形桌面的会话运行。

新环境可使用隔离虚拟环境安装可选依赖：

```bash
python3 -m venv .venv-desktop
.venv-desktop/bin/python -m pip install -r requirements-desktop.txt
PYTHONPATH=src .venv-desktop/bin/python -m vskill_os.adapters.pygame_desktop --backend pygame
```

OCR 通过已安装的本地 `tesseract` 可执行文件处理 RGB 裁剪，不需要 `pytesseract` 包。若缺失 Tesseract，命令会明确失败，不能退回内部文字冒充视觉。当前经过校验的字体是 `/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf`。

## 输出证据

`reports/feedback/` 包含：

- `before/frame-00000001.png`：编辑前的实际渲染像素。
- `before/frame.json`：编辑前帧的图像摘要、尺寸、时钟和事件水位。
- `after/frame-00000003.png`：编辑后的实际渲染像素；中间第 2 帧用于编辑前冲突检查。
- `after/frame.json`：编辑后传输元数据。
- `report.json`：前后 OCR 观察、修正计划、验证结果、输入来源和验收范围。

PNG 编码和校验只使用标准库。传输先写不可变的按帧编号命名的 PNG，再原子更新 `frame.json`。manifest 中**没有编辑器文字**；字段包括：

| 字段 | 作用 |
| --- | --- |
| `frame_id` | 同一 PixelDesktop 会话内递增的帧号 |
| `captured_at_s` | 本机 monotonic 捕获时刻 |
| `sha256` | PNG 文件字节的 SHA-256 |
| `pixel_sha256` | 解码后 RGB 像素的 SHA-256 |
| `pixel_size` | 宽、高；当前校准布局 1024 × 320 |
| `file` | 同目录中的 PNG 文件名 |
| `source` | 实际来源：`pygame_desktop` 或 `pillow_desktop` |
| `consumed_event_count` | 捕获时编辑器已消费的键盘事件数 |
| `provenance` | 渲染像素来源、本机时钟、`text_oracle=false` |

`decode_png_rgb()` 检查签名、CRC、尺寸、解压长度和帧格式。此轻量传输格式只接收 RGB8、非交错、filter=0 的 PNG；不把任意外部 PNG 当作同一受支持格式。

## 编辑和观察边界

编辑器是一个最多 48 个可打印 ASCII 字符的单行 append/backspace 模型，光标始终在末尾。支持普通字符、空格、退格和 Shift；不支持 End、Home、方向键、多行、Ctrl/Alt 快捷键或任意命令执行。分号、引号等只作为文字保存；模型没有 PTY、shell、网络或运行代码能力。

为了适配实际 74 键物理键盘，修正计划不包含 End。`EditPlan.typing_text` 直接提供 `"\b" * backspaces + suffix`，可交给 `StandingTypingPlanner` 转成双 Dex3-1 食指计划。最长正确前缀只基于 OCR 文本计算，不读取编辑器内部文本。

当前 OCR 明确限制为校准字体、固定单行布局和英文字符；前后空格不在可靠识别范围，目标会拒绝前导或尾随空格。这不是通用桌面视觉或任意应用自动化；OCR 在其他字体、缩放、遮挡、渲染条件下可能误读，失败应重新观察，而非强制判定成功。

`TesseractScreenObserver.observe(frame)` 只拿到 `PixelFrame` 的 RGB 字节和图像区域定义。它分别识别文本区及 `EDITOR FOCUSED APPEND MODE` 标记。编辑器存在显式测试辅助 `observe_text_oracle()`，其证据标为 `synthetic_text_oracle`，视觉规划与验证均拒绝接受。

## 验证与并发变化

编辑前使用 `assert_plan_current(plan, current_observation)` 检查屏幕像素摘要、事件水位、文本、焦点与模式没有变化。允许重新捕获相同内容的新帧；如果像素、已消费事件或焦点改变，则返回 `SCREEN_CHANGED_BEFORE_EDIT`，丢弃旧计划并重新观察。执行期间的事件仍需由上层任务控制器串行调度；这个检查不是操作系统层面的全局锁。

独立验证器检查：

1. OCR 证据与帧号、时间、像素 SHA 和事件数一致。
2. 后帧晚于前帧、未超时，且晚于最后一次输入的**主机接收时刻**。Isaac 仿真时间单独保存用于输入排序，不与主机时钟混用。
3. 需要修改时事件水位必须增加；原文已经正确的 no-op 允许水位不变。
4. 焦点和 append 模式正确，识别出的文字与目标完全一致。
5. 验证器再次直接对同一 RGB 图像运行 OCR。即使调用方伪造 `after.text` 并保留正确图像摘要，也不能通过。

常见失败码包括 `STALE_SCREEN_FRAME`、`FRAME_EVIDENCE_MISMATCH`、`FRAME_PRECEDES_INPUT`、`MISSING_INPUT_EVIDENCE`、`WRONG_EDITOR_FOCUS_OR_MODE`、`SCREEN_TEXT_MISMATCH`、`OCR_EVIDENCE_MISMATCH`、`NON_VISUAL_EVIDENCE`。

两次独立调用使用相同 OCR 引擎，因此可能有共同识别错误；这提供独立执行和像素来源隔离，不表示不同算法之间的独立性。物理任务验收还应结合键盘事件、接触与文本证据，不能单靠 OCR 返回“成功”。

## 与机器人任务连接

```python
editor = SafeTextEditor("helo world")
desktop = PixelDesktop(editor, backend="pygame")
observer = TesseractScreenObserver()

frame = desktop.capture()
before = observer.observe(frame)
plan = plan_text_edit(before, "hello world")
assert_plan_current(plan, observer.observe(desktop.capture()))

# 交给 StandingTypingPlanner 和物理控制器执行 plan.typing_text。
# 每个物理 KeyboardEventBridge 事件调用 editor.handle_event(event)。
# 不要在实际机器人路径调用 replay_text_events() 伪造物理输入。

after_frame = desktop.capture()
publish_frame(after_frame, output_directory)
after = observer.observe(after_frame)
verification = ScreenFeedbackVerifier().verify(
    expected_text="hello world", before=before, after=after, frame=after_frame,
    last_event_at_s=editor.last_event_at_s,
)
```

显示器纹理适配器可消费同一个 `frame.json` 和 PNG，并校验 SHA、时效和事件水位。这里只验证了本地软件渲染 → 像素 OCR → 文本编辑 → 新像素 OCR 的闭环；Isaac 中的屏幕材质、摄像机可见性和机器人运动闭环由各自适配器验证。
