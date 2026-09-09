# P1：Isaac Sim 与桌面混合仿真底座

状态：`DONE`。P1-T01 至 P1-T06 的轻量闭环、完整工位资产链和 Isaac/PhysX/RTX 验收均已完成。

详细设计见 [`../sim_guidance.md`](../sim_guidance.md)。

## 目标

让 Isaac 中的真实物理键帽位移产生可追踪的 `KEYDOWN/KEYUP`，驱动主机 Pygame/PTY 终端，同时保持开发 Oracle 与最终 Verifier 证据分离。

## 范围

- 固定基座或固定坐姿 G1；
- 固定基座 G1 mode 15 / 5010 与左右 Dex3-1；
- 固定桌面和键盘；
- 完整桌架、显示器支架和五脚万向轮办公椅；
- 进入工位、对椅、坐下与工作姿态的六阶段资产锚点；
- 8×6 m 四墙房间、三工位、多个出生点和导航入口锚点；
- Pygame 终端窗口与 PTY；
- 键帽事件桥；
- IPC Adapter、时间戳和证据记录。

## 不在范围

- 策略驱动的行走和真实导航 rollout；
- 真实坐下、载人脚轮动力学和坐姿移动策略；
- 高速连续文本；
- 真机 SDK；
- 端到端视觉语言动作模型。

## 任务

### P1-T01：定义混合仿真 Adapter 契约

状态：`DONE`

产物：

- Isaac worker、desktop worker 的协议数据类；
- 键帽状态、按键事件、画面帧和健康状态消息；
- 单调时间戳、调用 ID 和来源字段；
- 纯 Python fake worker 测试。

验收：重复、乱序或过期事件不会产生重复按键或错误状态推进。

### P1-T02：实现 Pygame/PTY 桌面 worker

状态：`DONE`

产物：

- 可启动、停止和恢复的终端 worker；
- `KEYDOWN/KEYUP`、修饰键状态和焦点状态；
- 屏幕帧或可测试文本快照；
- 受限工作目录和进程生命周期管理。

验收：人工或测试事件能够打开终端、输入短文本并得到 PTY 输出；异常退出后没有残留按键状态。

### P1-T03：实现键帽物理事件桥

状态：`DONE`

产物：

- 键帽 Released/Contact/Actuated/Held/Releasing 状态机；
- 按下/释放迟滞、防抖和 `all_keys_up`；
- Isaac Adapter 或独立可回放记录；
- 单键、长按、抖动和邻键测试。

验收：一次完整物理按压只产生一对 Down/Up；接触但未触底不能产生事件。

### P1-T04：导入 G1 与键盘资产

状态：`DONE`

产物：

- 锁定 Isaac Sim、Isaac Lab 和 Unitree 上游 commit；
- 独立环境说明；
- G1/键盘 USD 资产与惯量、碰撞配置；
- 头部、键盘和全景相机；
- 最小启动与健康检查。

当前实现锁定 Unitree G1 29DoF、5010 腕部、`mode_machine=15`、固定基座和双
Dex3-1。官方现成 G1+Dex3 是 mode 5 / 4010，因此 P1 使用官方 mode 15 body 与
左右 Dex3 独立描述重新组合，再由本机 Isaac Lab URDF Importer 转换为分层 USD。
`configs/simulation/g1_29dof_5010_p1.json` 固定源码、URDF/USD 哈希、Isaac Sim
5.1.0、Isaac Lab 2.3.2/commit 和 29+14 关节集合。

程序化 ANSI-like 键盘包含 74 个独立键帽、74 个随键运动的高对比几何字模，
每键一个 4 mm 棱柱关节和 PhysX 接触报告。空格行固定在机器人侧，ESC→F12 和
Ctrl/Win/Alt→Space→Alt/Menu/Ctrl 的左右序由清单与验收报告同时约束。P1 用
确定性 key-switch drive 产生接触/键程样本，以隔离测试事件桥；
Dex3 14 个关节在同一场景中全部驱动并验证，但“指尖实际按键”保留给 P2。
详见 [`../DEX3_ASSET_AUDIT.md`](../DEX3_ASSET_AUDIT.md)。

验收：固定场景可重复启动，关节顺序与兼容性 Manifest 一致，探针和键帽接触可观测。

### P1-T05：补齐进入工位至坐姿工作位的资产链

状态：`DONE`（资产等级，不代表动作策略完成）

产物：

- `room_entry → workstation_approach → chair_alignment → pre_sit → seated_stabilization → work_pose` 六阶段锚点；
- 五脚万向轮办公椅，含 11 个刚体、5 个转向关节和 5 个滚动关节；
- 座面顶面 0.4775 m、自由根，以及通过 120 N 水平短推得到的 0.1272 m 位移和 2.08% 滚动贡献证据，整体为滑动主导；
- 座面、靠背和五个脚轮接触面；
- 完整桌腿、显示器支架以及版本化尺寸/质量/摩擦配置；
- 独立资产链契约与 P1 验收字段。

验收证明资产/关节拓扑可加载，并证明无载椅在 PhysX 水平力下能够移动；G1 在 P1 仍为固定基座检查姿态，不用传送、外力探针或事件注入冒充机器人行走、坐下或载人椅子移动。详见 [`../P0_P1_ASSET_CHAIN.md`](../P0_P1_ASSET_CHAIN.md)。

### P1-T06：补齐第一阶段导航场景资产

状态：`DONE`（资产等级，不代表导航策略完成）

产物：

- 8×6×2.8 m 米制/Z-up 房间和 1.2 m 入口；
- 四个逻辑墙面、6 个实际 PhysX 墙体碰撞段；
- A/B/C 三个唯一工位，其中 A 为全量交互工位、B/C 为静态导航代理；
- 3 个机器人出生点与每工位的接近、对椅、工作姿态锚点，共 12 个导航锚点；
- 1 个带碰撞的静态文件柜障碍；
- 模块化房间配置、纯 Python 约束模型、Isaac authoring 模块和 OpenUSD 验收字段。

验收只证明导航场景资产、语义 ID、锚点和碰撞几何可用；没有创建地图、规划轨迹，也没有声明 `workstation_reached`。详见 [`../P1_NAVIGATION_ROOM.md`](../P1_NAVIGATION_ROOM.md)。

## 当前完成证据

- `make check`：核心单元与集成测试通过；
- `make demo-p1`：`Ctrl+Alt+T` 的键帽回放产生平衡的 Down/Up，打开 PTY，输入 `printf p1-ready` 并观察到执行输出；
- 事件桥覆盖 Released/Contact/Actuated/Held/Releasing、迟滞、防抖、释放反弹、长按保护、邻键接触和 `all_keys_up`；
- Pygame 作为 desktop worker 的可选渲染器惰性加载，核心测试不要求安装；
- PTY 字符流和画面快照使用不同来源标识；当前画面快照仍是开发证据，不冒充相机/OCR；
- OpenUSD：`/World` defaultPrim、米制/Z-up、全部关系可解析；工位拓扑报告记录 1095 prim、7 层依赖；
- Isaac/PhysX：G1 43 个主动关节中，29 个身体关节与 14 个 Dex3 关节精确匹配，双手每个关节均产生可测运动；
- 键盘：74/74 个键帽关节和 74/74 个接触体初始化，物理回放产生 19 对平衡 Down/Up，无残留按键；
- PTY：物理 `Ctrl+Alt+T` 打开终端，物理键帽序列输入并执行 `printf p1-ready`；
- RTX：头部、键盘相机输出 1280×720，全景相机输出 960×540 RGB 非空有限值图像；三路 PNG 和哈希均写入验收报告；
- G1 清单能力等级为 `physics_validation=verified_local_isaac_runtime`，结构化证据见 `reports/isaac_p1_acceptance.json`。
- 工位链：六个阶段锚点、五脚轮椅 11 个刚体/10 个被动关节、0.4775 m 座高和无载移动探针全部写入验收报告。
- 导航资产：四墙/6 碰撞段、A/B/C 三工位、3 个出生点、12 个导航锚点和静态障碍均写入验收报告。
- `make isaac-p1-validate`：独立重开 USD，检查 defaultPrim、单位、阶段元数据、椅子刚体/关节/接触面、自由根、座高、桌面支撑、房间、工位、锚点和障碍碰撞。

完成记录：
- 任务：P1-T01/P1-T02/P1-T03/P1-T04/P1-T05/P1-T06
- 状态：`DONE`
- 能力等级：契约 / 纯 Python / 本地 PTY / Isaac Sim 5.1 / PhysX / RTX 相机；非 HIL、非真机。
- 剩余风险：导航/坐下策略、载人椅子接触动力学、真实 Dex3 指尖触键、camera-only 键盘定位/OCR 和 sim-to-real 均未由 P1 证明。
- 下一可领取任务：先在当前资产链上实现入口到椅前的真实 locomotion rollout，再接坐下转换；P2 指尖触键维持 `PARTIAL`，等待可见物理动作复核。

## 阶段退出条件

1. Isaac 中的按压能够驱动 Pygame/PTY；
2. 事件具有确定时序且可回放；
3. Oracle 与相机/接触证据来源不同；
4. worker 崩溃或超时会释放按键并返回结构化失败；
5. 核心 `make check`、`make demo` 不要求安装 Isaac。
