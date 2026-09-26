# 共同接口、证据与模块边界

状态：完整接口蓝图；其中 VF-01-T01 已实现 v1 离线子集，VF-01-T02 已实现单环境物理调用契约。实际字段和限制见[离线实现说明](01_environment_and_evidence/IMPLEMENTATION.md)、[物理环境说明](01_environment_and_evidence/PHYSICAL_ENVIRONMENT.md)与 [contracts.py](../src/vskill_os/learning/contracts.py)。下表未逐项实现，不能整体当作 Python API。各阶段在此约定上扩展，不重复定义另一套任务事实；接入 Runtime 时适配现有 `Evidence`、`SkillContract`、`StateSnapshot` 和 `WorkerMessage`。

当前 `ObservationFrame` 是监督侧全量记录。T02 已实现 `JointTargetAction` 和物理 `PolicyObservation`；T03 已实现白名单 `ActorObservation`、请求队列、验证预算与独立 `FeedbackEpisode`，见[文本与反馈隔离](01_environment_and_evidence/TEXT_FEEDBACK.md)。`DecisionRequest`、学习技能 `SkillOutcome` 与恢复预算仍待实现。T01 接受合成契约记录，T02/T03 另行绑定真实 PhysX 原始记录；T03 读数明确来自模拟文本状态机，不能当作相机证据。

## 1. 四条独立通路

| 通路 | 生产者 → 消费者 | 权限 |
|---|---|---|
| 控制 | 策略 → 独立安全 Adapter → 物理执行 | 仅低层可见关节动作；单关节只有一个当前控制者 |
| 在线观察 | 可用传感器 / 按需读屏 → ObservationFilter → 策略 | 严格依据本次实验的观测清单；不随调试开关扩权 |
| 监督记录 | 物理真值 / 事件 / 文本真值 → Recorder → 训练与评估 | 可使用特权信息，但不得回流为部署观察 |
| 任务事实 | 原始记录 → 独立 Verifier → Runtime / Ledger | 策略的概率、奖励和完成请求不是事实写入授权 |

`BeliefState` 是模型对输入正确性的估计，`VerifiedState` 是有时效与证据的事实。二者使用不同类型和存储位置；模型预测不得填入正式前置条件。

## 2. 消息草案

| 消息 | 主要字段 | 关键约束 |
|---|---|---|
| `TaskSpec` | schema_version、字符键集合、物理 Backspace 键、目标串、姿态/手型、截止时间、验证/恢复预算、传感器清单 | 4–8 字符是短串阶段范围；单键阶段独立任务集；键位可达性需实测 |
| `RunManifest` | run_id、代码/资产/配置/策略哈希、后端版本、训练与评估种子、支持方式、控制权、无 IK 来源审计 | 固定身体策略另记来源；未知来源不填成已审计 |
| `ObservationFrame` | env_id、episode_id、sequence、clock_domain、capture_time、arrival_time、本体/传感器字段、缺失 mask | 发布到 actor 前逐字段过滤；禁止把真值藏入 info 或辅助头 |
| `JointTargetAction` | action_schema、关节名顺序、增量、dt、valid_until、控制者令牌 | 仅在低层，映射后经过限位/限速；记录实际执行目标 |
| `PhysicalKeyRecord` | 键号、接触/力、键程、sample_id、对应时间、观测来源 | 记录非目标键；无法观测时保持未知，不伪造“无误触” |
| `KeyEventRecord` | env/episode、event_id、单调 seq、Down/Up、key、reason、关联 sample_ids | 保留事件原义与派生的物理/管理分类，不靠 source 名称认证 |
| `ScreenObservation` | request_id、frame_id、图像哈希、capture/arrival_time、OCR 文本与置信度、missing、读屏成本 | 缺失不等于空文本；旧帧不刷新证据年龄；不提供文本真值 |
| `DecisionRequest` | continue(k)、verify、recover(kind, args)、request_finish | k∈{1,2,4}；参数合法性、资源与调用上限由 Runtime 限制 |
| `SkillOutcome` | 声明、独立物理结果、时长、能否安全交接、证据引用 | 普通按键完成不能泄漏未经请求的新屏幕结果 |
| `EpisodeRecord` | 所有消息索引、目标、所有事件、初次输入/修复边界、动作时长、失败和终止类型 | 不覆盖失败轨迹，数据跨环境隔离 |
| `EvaluationResult` | 文本/释放/安全/时间/预算门槛、成功与失败分类、最终证据 | 由独立评估生成；训练奖励不能设置 passed |

所有时间单位为秒、角度为 rad、长度为 m、力为 N；关节顺序与坐标系随 schema 版本锁定。训练记录使用 env/episode/seq 关联，不把随机 UUID 当成确定性复现失败。

## 3. 时间、延迟与批环境

仿真时钟、主机单调时钟和相机时钟不默认相同。每条原始记录保留自己的时钟域；物理 step 与文本消费使用逻辑 tick/事件关联，相机与外部进程另记录映射及误差。验证使用采集时刻与请求时刻判断新鲜度，不能只看文件落盘时间。

每个环境独立持有事件桥、防抖状态、文本缓冲、延迟队列、相机请求、随机数、奖励进度和策略历史。reset 只重置指定环境；递归网络隐藏状态和待回传反馈也必须清空。回合末尾终态先保存，随后才 reset；截断 bootstrap 使用该终态，不能使用下一回合首帧。

## 4. 物理释放与重复字符

现有桥会生成 `reason=maximum_hold` 的超时 KeyUp，`all_keys_up()` 也可生成 `worker_shutdown` 等清理事件。它们可以清理数字连接，但不能证明手指/键帽已经离开。

物理释放判定至少要求：同一键的连续原始样本中，键程回到释放区间、指尖接触消失或满足明确净空判据，且时间/采样完整；同时事件序列与实际行程相符。若额外管理 KeyUp 参与该笔动作，保留其发生与原因，本笔动作不得以管理事件替代完整物理 Down/Up 对通过。

连续字符如 `aa` 必须包含两次真实按下，中间有物理释放与重新触发。不能把长按自动重复、事件去重错误或全局清理算作第二次正确触键。卡键停止、失去支撑或过力时，无论文本是否碰巧正确都不通过。

## 5. 观察权限和验证成本

| 内容 | 单键状态教师 VF-02 | 决策机制实验 VF-03/04 | 视觉学生 VF-05 | 独立评估 |
|---|---|---|---|---|
| 本体状态与自身动作历史 | 可见 | 可见摘要 | 可见 | 可见 |
| 精确键位、指尖相对位置、仿真接触/键程 | 可见并标记特权 | 仅声明允许的物理摘要；对照权限相同 | 不可见，除有真实对应传感器并显式申明 | 可见并注明来源 |
| 键事件桥原始事件 | 可记录/监督 | 是否在线可见必须冻结；可见也不等于窗口实际接收 | 仅部署传感器/接口清单允许时可见 | 可见 |
| 新屏幕 OCR | 按任务需要，不作为直接文本真值 | 仅请求验证后可见，带时延/缺失/成本 | 同一验证规则；独立的键盘视觉流不可夹带免费屏幕 ROI | 可读取原始图像 |
| 实际文本缓冲 / 最终答案 | 可作奖励与监督，不作为 actor 输入 | 仅离线标签/训练监督，不放入 actor 或可读 info | 不可作为部署输入 | 可作仿真/数字 Oracle，和相机结论分开 |
| 真实成功概率 / Verifier 内部标签 | 训练目标 | 训练目标；不得作为在线输入 | 训练目标 | 用冻结判据重新计算 |

评测的 `verify` 成本包括请求、采集、传输/渲染、识别及等待时间，并记录硬件固定流与增量读屏开销。真实相机一直采样时，研究应声明计价的是读取/处理动作，不虚称每次请求都节省全部相机功耗。

强制最终验证与安全采样对所有方法相同计入；任何额外读屏、后台 OCR 或低层自动检查文字都视为一次验证。危险保护始终执行，不受验证预算约束。若预算耗尽且仍不能确认结果，则失败或请求人工，不输出伪成功。

## 6. 有限时域技能与错误

初版拟建 `learned_press_key`、`type_chunk`、`observe_text`、`release_all_physical`、`backspace_n`、`request_finish`。每项补齐版本、前置/局部后置/下一技能就绪、资源、超时、失败码和取消路径，包装为现有 L2/L3 契约；名称不是当前已注册技能。

建议保留/新增的失败类别：`NO_CONTACT`、`MISSED_KEY`、`DUPLICATE_KEY`、`WRONG_KEY`、`STUCK_KEY`、`EXCESS_FORCE`、`SCREEN_STALE`、`SCREEN_UNREADABLE`、`TEXT_MISMATCH`、`FOCUS_LOST`、`BUDGET_EXHAUSTED`、`UNSAFE_POSTURE`、`INVALID_EVIDENCE`。映射到现有失败码时显式转换，不静默改语义。

安全失败中止技能；恢复先确认物理释放和稳定，再依据可用证据操作。屏幕未知时盲目重按/退格不是默认恢复。任务图不能绕过坐姿、标定及证据 TTL；物理多策略集成前补齐已有 Runtime 的锁内重检与状态撤销缺口。

## 7. 数据与评估约定

数据按完整 episode、目标串和扰动组划分，邻帧不得跨训练/验证/测试集。冻结测试清单只供最终比较，阈值和奖励在训练/验证集调整。每次评估记录硬件、后端、策略、环境步数、训练时间、标签/人工成本和软件/物理扰动来源。

主成功条件：截止时间内最终文本严格等于目标、全部键物理释放、无安全失败、证据完整且未超预算。单键控制成功与短串数字成功分别计分；短串失败包括漏字、重复、误字、卡键、焦点、过期反馈、超时和误判结束。

返回[总览](README.md)。
