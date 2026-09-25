# P0–P1 工位资产与阶段链

状态：资产链 `DONE`；策略执行 `PLANNED`。

本链路先保证每个控制阶段都有对应的仿真资产、目标锚点和证据接口，再接入导航、坐下和坐姿移动策略。资产存在不等于机器人已经完成动作；P1 验收不得用传送、控制器完成文本或键盘事件注入证明行走/坐下成功。

## 阶段与资产

| 顺序 | 阶段 | 场景锚点 | 必需资产 | 退出证据 |
|---:|---|---|---|---|
| 1 | 进入房间 | `room_entry` | 地面、工位、移动椅 | 定位、碰撞净空 |
| 2 | 接近工位 | `workstation_approach` | 完整桌体、显示器、移动椅 | 工位相对位姿、碰撞净空 |
| 3 | 对齐椅子 | `chair_alignment` | 五脚轮椅、座面、脚轮接触面 | 椅子相对位姿、后向净空 |
| 4 | 坐下前姿态 | `pre_sit` | 座面、靠背、双足地面 | 座高、双足接触、椅子速度 |
| 5 | 坐姿稳定 | `seated_stabilization` | 座面/靠背/脚轮接触面 | 骨盆—座面接触、双足接触、躯干/椅子倾角 |
| 6 | 工作姿态 | `work_pose` | 桌、显示器、键盘、移动椅 | 坐姿稳定、屏幕可见、双手可达 |

配置源为 [`../configs/simulation/p0_p1_workstation_asset_chain.json`](../../configs/simulation/p0_p1_workstation_asset_chain.json)，纯 Python 校验位于 [`../src/vskill_os/simulation/workstation_assets.py`](../../src/vskill_os/simulation/workstation_assets.py)。

## 移动办公椅

P1 使用程序化五脚万向轮办公椅，不再用无轮方块占位：

- 1 个承载框架刚体；
- 5 个脚轮叉刚体和 5 个轮体，共 11 个刚体；
- 每个脚轮一个绕 Z 轴自由转向关节；
- 每个轮体一个被动滚动关节，共 10 个被动自由度；
- 座面、靠背和五个轮体均有碰撞/接触面标识；
- 座垫中心由 0.52 m 降为 0.44 m，座面顶面为 0.4775 m，靠背和支柱同步下调；
- 椅子根未用 FixedJoint 连接世界，轮—地摩擦和滚轴被动阻力按“滑动主导”标定；质量、尺寸、初始位姿和目标工作位姿均进入版本化配置；
- 滚动轴保留 0.08 的被动阻力，并以 20 deg/s 的被动速度上限抑制不符合本阶段抽象的持续空转；
- P1 对自由椅架施加 120 N、0.15 s 的水平测试力，当前验收实测平移 0.1272 m；平均脚轮等效滚动距离为 0.0026 m，滚动贡献 2.08%，以滑移贡献为主。

Isaac authoring 模块位于 [`../src/vskill_os/adapters/isaac/workstation_scene.py`](../../src/vskill_os/adapters/isaac/workstation_scene.py)。正式场景为 [`../artifacts/isaac-p1/scenes/p1_g1_dex3_keyboard.usda`](../../artifacts/isaac-p1/scenes/p1_g1_dex3_keyboard.usda)。

项目级 OpenUSD 验收命令为 `make isaac-p1-validate`，报告位于 [`../reports/isaac_p1_workstation_validation.json`](../../reports/isaac_p1_workstation_validation.json)。它会在新进程中重开 USD，并检查 defaultPrim、米制/Z-up、必需 prim、关节关系、接触面、座高、无世界固定关节和阶段元数据。动态移动证据位于 [`../reports/isaac_p1_acceptance.json`](../../reports/isaac_p1_acceptance.json)。

## 房间与多工位导航资产

P1 主场景同时包含 8×6×2.8 m 简化房间、1.2 m 宽入口、四个逻辑墙面（门洞拆分后为 6 个碰撞墙段）、A/B/C 三个唯一工位、3 个出生点、每工位的接近/对椅/工作位锚点，以及 1 个带碰撞的文件柜障碍。A 为加载完整 G1、Dex3、键盘和移动椅的目标工位；B/C 为低成本静态代理，用于导航几何与工位选择，不冒充完整交互资产。

配置、纯 Python 契约和 Isaac authoring 分别位于 [`../configs/simulation/p1_navigation_room.json`](../../configs/simulation/p1_navigation_room.json)、[`../src/vskill_os/simulation/navigation_assets.py`](../../src/vskill_os/simulation/navigation_assets.py) 和 [`../src/vskill_os/adapters/isaac/navigation_scene.py`](../../src/vskill_os/adapters/isaac/navigation_scene.py)。详细边界见 [`P1_NAVIGATION_ROOM.md`](P1_NAVIGATION_ROOM.md)。

NVIDIA CAD-to-SimReady 只读预检记录位于 [`../reports/simready/p0_p1_workstation/preflight.json`](../../reports/simready/p0_p1_workstation/preflight.json)。本机 OpenUSD 已就绪，但正式 Asset Validator 模块和 SimReady Foundation checkout 未安装，因此正式 `Prop-Robotics-Neutral` 合规状态明确记为 `blocked`；项目不把自身拓扑检查冒充正式 SimReady 认证。

## 当前能力边界

当前 USD 证明资产与关节拓扑可加载，并提供六个可见阶段锚点；无载荷外力探针进一步证明移动椅会以滑移为主产生平移，脚轮只保留少量被动转动。因此机器人碰到椅子时，PhysX 会通过碰撞把力传给椅子，但不应表现成脚轮持续空转。G1 仍是 P1 固定基座检查姿态；尚未声称：

- 从入口真实走到椅前；
- 真实坐下并建立骨盆—座面接触；
- 机器人真实接触后的受力曲线和载人状态下脚轮动力学稳定；
- 坐姿移动或移动后恢复键盘工作姿态。

这些动作必须由后续 locomotion/WBC/坐下策略执行，并由独立定位、接触和稳定性证据验收。
