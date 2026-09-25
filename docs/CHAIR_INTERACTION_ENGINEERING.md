# 机器人—椅子交互工程与训练接口

状态：2026-09-22 初稿完成；2026-09-25 随全仓 `make check` 通过轻量测试，**本次未运行椅子 demo 或物理训练**。代码位于 `src/vskill_os/chair/`；初稿曾使用 `codex/robot-chair-interaction` 名称，实际提交与工作树归属见 [分支登记](BRANCHES.md)。研究依据见 [调研与实验路线](CHAIR_INTERACTION_RESEARCH.md)。

## 1. 架构与文件

```text
Agent 的平面速度与时域
    → ChairRepositionSkill（资源、守卫、停止请求）
    → VelocityExecutor（策略/WBC/硬件，内部才允许关节命令）
世界 → ChairObserver → 原始时间序列 → ChairVerifier → Runtime 事实

TrainingConfig → ChairTrainingEnv → PhysicsBackend → 独立仿真进程
                     ↑                    ↓
              OnPolicyTrainer ← actor / critic / reward
                     ↓
           Learner（可选 Torch PPO / 外部算法）
```

| 文件 | 职责 |
|---|---|
| `chair/model.py` | SI 单位、速度请求、96/217 维观测、原始观测、轨迹序列化 |
| `chair/verification.py` | 独立安全守卫、正确方向进度、停止和工作姿态验证 |
| `chair/skill.py` | 版本化技能、Executor/Observer 协议、显式 verifier 装配 |
| `chair/training.py` | `reset/step/close`、奖励、域随机化、命令切换、终止/截断、课程接口 |
| `chair/runner.py` | rollout、GAE、Learner/evaluator 接口、checkpoint 与运行记录 |
| `adapters/chair_torch.py` | 可选的单环境 PPO 基线，actor/critic 分开、tanh 分布与概率修正 |
| `adapters/chair_worker.py` | Linux 标准库 JSONL 子进程传输；无仿真器依赖 |
| `chair/proxy.py` / `chair/demo.py` | 运动学替身、故障注入、Runtime 和 SQLite 账本演示 |
| `chair/cli.py` | 计划生成、工厂装配、训练入口与代码来源记录 |
| `configs/chair/` | 研究配置、基线、worker 示例、评测计划 |
| `tests/test_chair_research.py` | 契约、故障、取消、训练接口与 GAE 的轻量回归测试 |

新模块不自动替换现有 simulation 技能，也不改动默认 verifier 的信任源。研究 demo 显式创建独立 Registry 和 Verifier；已有 `make demo` 行为不变。

## 2. 坐标与数据契约

- 命令：躯干坐标系 `+x` 前、`+y` 左、`+z` 上；正 yaw 绕 +z。
- `robot_pose`：世界系下被跟踪躯干参考点的 `(x, y, unwrapped_yaw)`。
- `chair_pose`：世界系座面中心的 `(x, y, unwrapped_yaw)`；可旋转座面的 yaw 不等于椅架 yaw。worker 应另外记录椅架与脚轮状态。
- `time_s`：同一 episode 的仿真或同步采样时钟；步长 0.02 s；必须单调，不允许缺样后插值伪造原始证据。
- `pelvis_offset_m`：骨盆接触区域与座面安全中心的水平偏移，不是骨盆到地面高度。
- 倾角：相对重力轴的非负角度，rad；接触丢失持续时间为连续累计秒数。
- 关节顺序：沿用 `robot.g1.G1Joint` 的 29 个 body 关节；手部关节不进入这 29 维动作。Dex3/Dex5 的质量、惯量、姿态必须在资产 manifest 中另行固定。

Actor：躯干角速度 3、投影重力 3、关节位置 29、关节速度 29、上一动作 29、速度命令 3，共 96 维。Critic：无噪 actor + 已有 `EXPECTED_CRITIC_ONLY_OBSERVATIONS` 的 121 维，共 217 维。完整字段顺序直接复用 `robot/seated_policy.py`，避免两份清单漂移。

`Observation.pack(actor, clean_actor, privileged)` 会拒绝未知字段、错维、NaN/Inf。给 actor 混入 `chair_pose` 将直接报错。传入干净 actor 的责任属于 worker；软件类型隔离不能证明上游传感器或网络实现没有泄漏。

内部动作：29 维 `[-1,1]` 归一化位置残差。worker 将其映射为 `q_default + action_scale * action`，在自身适配层执行关节限位、速度/力矩限位和 PD/WBC。默认坐姿、动作尺度、增益、力矩限制必须来自当前资产，不能照抄论文或 29DOF 标签猜测。

## 3. Skill 与状态交接

名称 `reposition_while_seated`，版本 `0.2.0`。输入仅接受 `vx_mps/vy_mps/yaw_rate_rps/duration_s`。时域 0.2–5 s 且按 0.02 s 对齐；平移向量模长不超过 0.5 m/s，yaw 不超过 0.5 rad/s。非零任务需求必须达到 0.02 m 或 0.02 rad，零指令应走姿态保持技能。

前置条件：`robot_safe + seated_stable + work_pose_ready`。共享机器人身体、移动、双臂、手、键盘和相机资源。结束后先使旧坐姿、旧键盘标定、键位可达与 terminal-ready 失效，再由独立原始轨迹验证器建立新的坐姿与工作姿态；键盘标定必须另行重做。

验证器从两个相邻世界位姿计算速度并旋转到前一躯干坐标系；积累与命令同向的进度，避免路径长度把往返摆动当成成功。机器人和座面都需达到对应需求的 50%；各速度分量 RMSE ≤0.20；接触置信度 ≥0.85、偏移 ≤0.05 m、倾角 ≤0.20 rad，并有足底推进接触。

移动后采集 0.5 s 零命令窗口；机器人与椅子平移速度 ≤0.03 m/s、yaw ≤0.05 rad/s，窗口内屏幕可见且双手可达。若减速较慢，本版本会拒绝交接；后续可以增加“先减速、再计稳定窗口”的独立阶段，不能直接放宽停止标准。

运行时立即停止请求：碰撞、急停、接触置信度 <0.65、连续接触丢失 >0.25 s、偏移 >0.08 m 或倾角 >0.35 rad。训练终止阈值更宽，不能作为部署守卫。

`VelocityExecutor.stop()` 必须非阻塞、幂等地切换到安全保持；**不是置零所有力矩**。所有正常/失败/取消路径都会调用它。失败会保留原始 trace 并失效旧状态；停止请求失败还会撤销 `robot_safe`。恢复只记录建议，不在接触未知时自动继续运动。

当前 demo 只支持串行调用。现有通用 Runtime 的前置状态快照在资源锁之前读取；并发排队技能可能携带旧快照。真实组合部署前需解决锁内重检和执行中状态撤销，并接入外部 watchdog。源名称不是身份认证；实机还需要可信通道、同步时钟、数据年龄和序列号。**此研究接口目前不能直接接硬件运行。**

## 4. 训练环境与算法

`PhysicsBackend.reset(ResetSpec, command)` 初始化自由机器人根与无驱动椅子；`step(action, command)` 推进一个策略周期；`read(command)` 只更新命令观测，不推进物理或改变原始 sample；`close()` 释放后端。

`ResetSpec` 包含种子、足底/座面摩擦、椅质量比例、脚轮阻尼比例、座高扰动与 5 个脚轮初始方向。这些数值是本项目起始假设，不是作者公开的资产参数。worker 必须实际应用这些字段并记录最终值，不能只接收后忽略。

训练奖励由速度跟踪、座面接触与偏移、倾角、动作变化、机械功以及可选足底滑移组成，按 dt 缩放。权重见 `research.json`；这些是工程基线，不是原论文逐项复刻。Actor 中不放入这些特权奖励信息。

训练 episode 20 s；每 2 s 更新命令，包含停止命令。碰撞、急停、持续接触丢失或大倾角产生 `terminated`，时间上限产生 `truncated`。`OnPolicyTrainer` 对截断保留终端观测并 bootstrap，对物理终止不 bootstrap；GAE 不跨 reset 传播。

`Learner` 接口：

```python
act(actor96, critic217) -> Decision(action29, log_probability, value)
value(critic217) -> float
update(rollout, advantages, returns) -> dict[str, float]
save(checkpoint_path) -> None
```

`act` 参数包含两种输入以便同时计算动作和值；实现必须保证 actor 网络只使用 actor96。当前 Torch adapter 用两个独立 MLP。checkpoint 是研究训练产物，**不是可直接部署的 G1 权重**。

Torch 基线为 CPU 单环境、全 batch PPO，主要用于接口接通和小规模基线；未加入观测归一化、GPU 批量环境或恢复训练入口，不应按论文大规模吞吐预期使用。依赖仅在 `chair-training` 可选安装和显式训练时加载。它拒绝 SY/CC=true；论文实验需替换 Trainer。CC 的评估接口每 100 次迭代调用独立评估器，至少 20 个 episode、成功率 ≥0.9 且静止率 ≤0.05 才提升一级。该门槛是项目提案。

## 5. 仿真 worker 协议

目前实现了客户端传输，**没有提供已可用的 mjlab/Isaac 椅子物理 worker**。`worker.example.json` 使用显式占位路径，防止误启动已有不相关任务。实现者在自己的仿真 Python 环境中实现以下协议，再替换配置。

每行一个 UTF-8 JSON。stdout 专用于协议，stderr 写日志。父进程使用 argv 数组而非 shell。一个未完成请求期间不发送下一个请求；超时、进程退出和 oversized response 终止当前任务。

```json
{"id": 1, "method": "hello", "params": {"schema": "chair-worker-v1"}}
```

成功返回 `{"id":1,"result":{...}}`，失败返回 `{"id":1,"error":"CODE: description"}`。`hello.result` 必须包含：

| 字段 | 要求 |
|---|---|
| schema | `chair-worker-v1` |
| actor_dimension / critic_dimension / action_dimension | 96 / 217 / 29 |
| policy_dt_s | 0.02 |
| evidence_level | `simulation_oracle` |
| pelvis_fixed / chair_actuated | 均为 false |
| robot_asset_sha256 / chair_asset_sha256 | 实际资产的 SHA256；不是占位值 |
| simulator_version / worker_commit / joint_mapping_version | 锁定的环境与映射版本 |

`reset.params` 是 `{"reset": ResetSpec, "command": RootVelocityCommand}`；`step.params` 是 `{"action": [29 numbers], "command": ...}`；`read.params` 只有 command。

三者的 `result` 均是 `BackendFrame` 的传输形态：

```text
actor:        {观测字段名: 数值数组}
clean_actor:  {相同字段名: 无噪数值数组}
privileged:   {critic-only 字段名: 数值数组}
sample:       ChairSample 全部字段
joint_torques_nm:     29 个数值
joint_velocities_rps: 29 个数值
foot_slip_speed_mps:  左右足接触切平面速度模长
```

坐标与观测模式由本章约定，握手还应附加自由根、被动关节、手部载荷、碰撞网格、求解器、单位和噪声配置。客户端检查名称、维度和主要身份字段，但不能仅凭 worker 自报字段证明资产物理正确。先由独立资产审计核对接触与惯量，再准许训练。

## 6. 命令入口（本次整理未执行下列研究任务）

只生成实验计划：

```bash
make chair-plan
```

替身 demo（可用标准库，首次目录必须不存在）：

```bash
make demo-chair CHAIR_OUTPUT=artifacts/chair/demo-001
make demo-chair CHAIR_OUTPUT=artifacts/chair/stationary-001 CHAIR_FAULT=stationary
```

demo 保存 `report.json`、`trace.jsonl`、`ledger.sqlite`。所有文件标识 `contract_proxy`；`physical_success_verified=false`，无训练权重。故障用例退出码为 1 属于失败报告，正常用例预期 0，当前均未运行验证。

未来接入真实物理 worker 后，显式安装可选依赖并启动基线：

```bash
python3 -m pip install -e '.[chair-training]'
make chair-train CHAIR_BACKEND_CONFIG=/absolute/path/to/worker.json CHAIR_OUTPUT=artifacts/chair/train-001
```

外部 Trainer 工厂接受 `TrainingConfig`，返回具有 `train(env, output_dir)` 的实例；Backend 工厂接受 `(TrainingConfig, options_dict)`，返回 `PhysicsBackend`。可以在 CLI 使用 `--trainer your_package:create_trainer`、`--backend your_package:create_backend` 替换。工厂加载执行本地代码，应仅使用项目维护者信任的模块。

训练目录拒绝覆盖，保存 config、逐迭代 metrics、checkpoint、summary、worker-identity 和 provenance。`summary.status=training_completed` 只说明训练循环返回，不说明策略通过物理验收。`deployment_approved` 保持 false。

## 7. 后续验证清单

用户允许测试后，先运行新增的标准库测试，再运行完整 `make check` 和 `make demo`，最后运行 `demo-chair`。本轮不自动执行这些命令，也没有产生“测试通过”的报告。提交前仍须完成仓库规定检查。

物理 worker 需要另测：机器人不固定、椅子无驱动、旋转座面与椅架坐标正确、动作与关节映射、接触传感回放、脚轮滚滑分解、保持/急停/watchdog、随机化实际生效、特权观测不进入 actor、渲染与 raw trace 一致。只有这些通过后再进入短训练。
