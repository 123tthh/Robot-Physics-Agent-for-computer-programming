# VF-01-T01：任务与离线证据实现

状态：`DONE`，限标准库数据契约和合成回放验收。2026-09-25，起点提交 `e2ec5b8`。此处保留 T01 当时的契约验收；后续 [T02 单环境物理 reset/step](PHYSICAL_ENVIRONMENT.md) 已交付。阶段 VF-01 整体仍为 `PARTIAL`，尚未训练策略。

## 运行与复核

在项目根目录执行：

```bash
make demo-learning-evidence
```

输出到 `artifacts/verification_feedback/vf01/contracts/`：公共 `policy.json`、总报告 `report.json`，以及每个案例的 `task.json`、`episode.json`、`evaluation.json`。输出目录可通过 `LEARNING_EVIDENCE_DIR` 指定；文件属于本地生成物，不上传录像、权重或整批轨迹。

独立重新评估成功案例：

```bash
PYTHONPATH=src python3 -m vskill_os.learning evaluate \
  --task artifacts/verification_feedback/vf01/contracts/correct_press/task.json \
  --episode artifacts/verification_feedback/vf01/contracts/correct_press/episode.json \
  --policy artifacts/verification_feedback/vf01/contracts/policy.json
```

将案例路径改成 `timeout_release`，应得到非零退出码和 `ADMINISTRATIVE_RELEASE`、`STUCK_KEY`。退出码：`0` 为契约通过；`1` 为证据拒绝；`2` 为 JSON/字段/文件错误。demo 的 `0` 表示正例被接受、负例被拒绝，不能解读为所有输入轨迹都成功。

Python 调用入口：

```python
from vskill_os.learning import EpisodeRecord, TaskSpec, IndependentTypingEvaluator

task = TaskSpec.read("task.json")
episode = EpisodeRecord.read("episode.json")
report = IndependentTypingEvaluator().evaluate(task, episode)
print(report.contract_passed, report.findings)
```

## 已实现模块

| 代码（相对仓库根目录） | 实现范围 |
|---|---|
| [contracts.py](../../src/vskill_os/learning/contracts.py) | `TaskSpec`、`RunManifest`、`RecordStamp`、关节/键帽记录、`ObservationFrame`、键事件、文本 Oracle、验证请求、屏幕记录、`EpisodeRecord` |
| [serialization.py](../../src/vskill_os/learning/serialization.py) | 严格 JSON 导入导出、版本/字段/类型校验、有限数值、重复字段拒绝、确定性内容哈希 |
| [evaluation.py](../../src/vskill_os/learning/evaluation.py) | `EvaluationPolicy`、独立物理事件重算、文本/反馈/预算检查、带分类的 `EvaluationResult` |
| [replay.py](../../src/vskill_os/learning/replay.py) | 手工键帽样本经现有 `KeyboardEventBridge` 生成数字事件，稳定 ID，正负例注入 |
| [cli.py](../../src/vskill_os/learning/cli.py) | 导出案例和独立评估文件；不调用 Isaac/Torch |
| [test_learning_evidence.py](../../tests/test_learning_evidence.py) | 证据伪成功、缺失、串扰、屏幕延迟和命令行回归 |
| [配置快照](../../configs/learning/verification_feedback/README.md) | v1 合成任务和评估阈值；不是已标定物理设置 |

根版本为 `vf-evidence/1`；评估策略版本为 `vf-offline/1`。任务由调用者单独提供，记录携带任务哈希，不能通过修改轨迹内的目标或键集合放宽验收。报告绑定任务、轨迹、判据以及评估源码的哈希。生成器记录自身与事件桥源码哈希；`code_revision=unrecorded` 明示未绑定发布提交。哈希用于内容对应，不是传感器签名或来源认证。

## v1 时间与证据约束

- 每条流的 `sequence` 从零连续递增，所有记录 ID 在回合内唯一；`env_id`、`episode_id`、`clock_domain` 必须一致。v1 拒绝混合时钟，尚不实现相机/主机时钟换算。
- 每个物理帧完整记录任务声明的键集合和有序关节；示例监控现有键盘的全部 74 键。示例关节名为 `synthetic_finger_joint`，没有机器人关节运动或可达性含义。
- 物理帧覆盖回合首尾，采样间隔不得超过冻结阈值。事件必须关联同一时刻的原始帧，且不能先于原始采样到达。
- 评估器独立从键程和接触重算按下/释放事件，不调用事件桥来生成参考答案。KeyUp 后还需持续无接触且处于释放键程区间，才能计入完整动作并允许重复字符。
- 任意管理释放、缺失/重复事件、非允许键误触、过力、安全停止或未释放终态都拒绝。当前安全检查仅审阅记录的停止信号和接触力，关节限位/限速与实际独立保护仍由后续物理 Adapter 交付。
- 文本规则限定为末尾追加/退格。初始及最终快照必需；每个 KeyDown 恰好对应一次有因果引用的文本结果。软件丢失标为 `delivery=dropped`；不把软件扰动冒充物理漏触。
- 必须有一次最终验证；新帧需在请求后采集、结束前到达，并晚于最后一次输入处理。缺失帧保留 `None`，不能冒充空文本；新鲜度按采集时刻计算。
- 验证次数包含最终请求和未返回请求。验证时间为每次请求到返回的时长之和，未返回项计到回合终点；这是合成时钟的延迟成本，不是硬件能耗。总时限包含等待时间。

`ObservationFrame` 和 `EpisodeRecord` 是监督侧记录，包含特权信息，**不是可直接交给 actor 的观察 API**。本 T01 子集不包含在线权限隔离；随后交付的 [T03](TEXT_FEEDBACK.md) 已实现文本隐藏、反馈队列与策略过滤，恢复决策预算仍属于 VF-03。

## 验收记录

| 合成案例 | 预期 |
|---|---|
| `correct_press` | 完整按下/释放、文本与屏幕匹配，通过 |
| `timeout_release` | 数字清理后仍在接触，拒绝 |
| `duplicate_event` / `missing_event` | 事件与原始行程不一致，拒绝 |
| `delayed_feedback` | 结束时最终反馈未到达，拒绝 |
| `wrong_text` | 实际文本错误，拒绝 |
| `delayed_feedback_waited` | 等待反馈并仍在预算内，通过 |
| `repeated_character` | 两次完整释放后的 `aa`，通过 |
| `backspace_recovery` | `s → Backspace → a`，通过且记录三次动作 |
| `dropped_then_retried` | 第一次软件丢字、第二次完整触键补输，通过且保留丢失与额外按键 |

完成检查：10 个案例全部符合预期；新增 24 个针对性测试通过；`make check` 共 181 个测试，180 通过、1 个可选桌面依赖测试跳过；`make demo` 返回 `succeeded`。可版本化摘要见[验收报告](../../reports/verification_feedback/vf01_t01_contracts.json)。没有运行物理实验、相机识别或 PPO。

所有报告固定 `capability=contract_replay`、`physical_execution_verified=false`。v1 明确拒绝宣称 Isaac/相机/真机的输入，不能靠更改 `source` 或 manifest 标签升级能力。合成屏幕的摘要只指向合成载荷，不代表真实图像已被检查。结构校验失败会提前返回，诊断计数可能不完整，不能把这类记录的计数直接作为方法评测成绩。

## 后续实现

VF-01-T02 已完成：[单环境物理 reset/step 与验收](PHYSICAL_ENVIRONMENT.md)，包含直接关节驱动、真实原始记录、管理释放负例与重置复现；[T03 文本与反馈隔离](TEXT_FEEDBACK.md) 和 [T04 批环境与吞吐](BATCH_ENVIRONMENT.md) 也已交付。[T05 真实终端与机器人相机复验](DESKTOP_CAMERA.md) 已完成单键六案例范围。当前下一项为 VF-02-T01：动作映射健康检查。

返回[阶段 1](README.md)或[研究主线](../README.md)。
