# 灵巧手选型与键盘任务验收

本文面向实体键盘操作选择灵巧手。选型目标不是最大抓力或最像人手，而是稳定完成单键、修饰键保持、双手组合键和连续文本。

## 1. 结论

- **近期 MVP：双 Unitree Dex3-1。** G1、DDS、Isaac Lab 和 XR 数据采集链路最完整，三指/手足以完成组合键。
- **G1 长期候选：Unitree Dex5-1P。** 自由度、横摆、重复定位和小间隙设计更适合精细键盘，但必须先确认公开仿真、G1 法兰、电源和触觉 SDK 的完整性。
- **跨平台高自由度候选：Tesollo DG-5F-S。** 20 个独立关节、绝对编码器和较轻重量适合研究非人类指法与自适应指派，但 G1 集成工作更多。
- **探索性候选：Wuji Hand 2。** 直驱、力—位置混控和仿真模型有吸引力，但当前为 Beta，不宜直接作为唯一真机路线。

建议先用一套 Dex3 完成软件和仿真闭环，同时借测或采购一只高自由度手做台架对比，再决定是否采购左右手整套。

资产侧已经完成独立审计与 P1 运行验收：官方现成 G1+Dex3 USD/URDF 使用旧的
mode 5 / 4010 身体；本项目已用 mode 15 / 5010 body 与左右 Dex3-1 独立 URDF
生成 29+14 轴目标 URDF/USD，并在 Isaac Sim 5.1 中验证全部 14 个手部关节运动。
来源、哈希和验证边界见 [`DEX3_ASSET_AUDIT.md`](../experiments/dex3/DEX3_ASSET_AUDIT.md)。

## 2. “间隙小”需要拆成四个指标

供应商所说的“小间隙”可能含义不同，采购时必须分别询问：

1. **传动回差**：关节换向后，电机动了而指尖尚未移动的死区；
2. **负载下迟滞**：指尖受键帽反力时，去程和回程轨迹的差异；
3. **重复定位误差**：相同命令多次落到键帽上的离散程度；
4. **几何间隙与接触斑**：手指外壳是否碰邻键、软指垫压缩后是否跨键。

关节编码器分辨率高不等于指尖回差小。对键盘而言，负载下的指尖毫米误差比电机角度分辨率更重要。

## 3. 候选型号

| 型号 | 官方关键参数 | 键盘任务判断 |
|---|---|---|
| Unitree Dex3-1 | 3 指、7 DoF、710 g、指尖重复定位 ±2 mm、33 个触觉点、1000 Hz 通信 | 最快进入 G1 实验；横向指间调整有限，应依靠双手分区、腕部移动和闭环修正 |
| Unitree Dex5-1/1P | 20 DoF（16 主动+4）、1.1 kg、四指横摆 ±22°、重复定位 ±1 mm；官方描述低阻尼小间隙减速器和微间隙结构 | 最贴近“高灵活度+小间隙”，但比 Dex3 重，公开 G1/Isaac 工具链需确认 |
| Tesollo DG-5F-S | 5 指 20 个独立关节、880 g、500 Hz、绝对编码器、ROS 2、触觉/力传感选项 | 独立手指控制强，适合组合键和策略学习；厂商称降低回差但未给定量负载曲线 |
| Wuji Hand 2 Beta 2 | 20 主动 DoF、约 800 g、1000 Hz、可回驱直驱、MIT 力—位置混控、URDF/MJCF/USD | 仿真和学习接口好；Beta 阶段、软垫和接口仍可能变化 |
| Shadow Dexterous Hand | 20 主动 DoF、24 关节、多种触觉、1 kHz、完整手约 4.3 kg | 研究能力强，但重量、成本和 G1 集成代价过高 |
| BrainCo Revo 2 | 11 DoF、6 主动、383 g，可选触觉/EtherCAT | 很轻，但四指主要只有独立屈伸，密集键盘横向定位能力受限 |
| Inspire RH56BFX | 6 主动 DoF、约 540 g，厂商定位“钢琴手”并强调速度 | 可做轻量速度基线，但主动自由度不足以充分研究多指独立落点 |

官方资料：

- [Unitree Dex3-1](https://unitree-robot.com/mobile/Dex3-1/index.html)
- [Unitree Dex5-1](https://unitree-robot.com/mobile/Dex5-1/index.html)
- [Tesollo DG-5F-S](https://www.tesollo.com/products/grippers/dg-5f-s)
- [Tesollo ROS 2](https://tesollodelto.github.io/tesollo_ros2/grippers/dg5f_s.html)
- [Wuji Hand 2](https://docs.wuji.tech/docs/en/wuji-hand/latest/overview/)
- [Shadow Dexterous Hand](https://shadowrobot.com/dexterous-hand-series/)
- [BrainCo Revo 2](https://www.brainco-hz.com/docs/revolimb-hand-dev/en/revo2/parameters.html)
- [Inspire 产品选型](https://en.inspire-robots.com/wp-content/uploads/2024/04/Inspire-Robots-Products-Selection-Guide-V17.pdf)

## 4. 已有按键先例

- DART 手在标准键盘上进行闭环打字，单手达到约 20 WPM：[论文页面](https://experts.umn.edu/en/publications/design-and-implementation-of-a-dexterous-anthropomorphic-robotic-/)；
- 双臂人形机器人打字研究报告仿真和实机实验，但主要解决双臂参考键与键盘位姿，并非完整多指高速打字：[论文](https://www.cambridge.org/core/journals/knowledge-engineering-review/article/hierarchical-deep-reinforcement-learning-algorithm-for-typing-with-a-dualarm-humanoid-robot/8C4C2260F9C2F73FA8B9006C6EF9238D)；
- 三指钢琴和弦证明了多指协调压键的可行性，但钢琴键尺寸和力学条件不能直接代表电脑键盘：[论文](https://www.frontiersin.org/journals/robotics-and-ai/articles/10.3389/frobt.2024.1463744/full)。

目前不要把演示过“触键”视为已经证明连续电脑操作。采购和论文评测都应基于本项目自己的键盘台架。

## 5. 台架测试

### 5.1 测试装置

- 与目标相同的键盘，刚性固定；
- 键盘事件记录器；
- 顶视相机和侧视相机；
- 指尖或腕部力/触觉数据；
- 可测量指尖换向位移的视觉标记；
- 单手固定支架，先排除 G1 全身扰动。

### 5.2 必测项目

1. 指尖空载和受力换向回差；
2. 同一键重复落点分布；
3. 单键 Down/Up 成功率和双击率；
4. 邻键误触率；
5. 修饰键持续保持能力；
6. `Ctrl+Alt+T` 组合键成功率；
7. 30 分钟重复按键后的温漂、过热和误差；
8. 左右手时间同步、通信掉包和异常释放；
9. 指尖软垫磨损和替换成本；
10. G1 腕部负载、法兰、线缆和峰值功率。

### 5.3 建议的项目门槛

这些是项目验收目标，不是厂商保证：

- 单键连续 500 次成功率至少 99%；
- 邻键数字事件误触率低于 0.5%；
- 组合键连续 200 次成功率至少 98%；
- 卡住的修饰键事件为 0，异常时 `all_keys_up` 可用；
- 指尖负载换向回差目标小于 1 mm；
- 键盘轻微位姿扰动后能够通过重标定恢复；
- 连续运行后性能下降可量化且不会越过安全力限制。

## 6. 供应商询价/借测问题

1. 是否提供左、右手及其 G1 法兰 CAD？
2. 整手重量是否包含线缆、软垫、触觉和转接板？
3. 负载下指尖回差、迟滞和重复定位如何测量？
4. 触觉数据频率、分辨率、时间戳和原始接口是什么？
5. 是否支持同步发送多关节命令以及异常时整手快速卸力？
6. 是否提供 URDF、MJCF、USD、碰撞体和惯量参数？
7. ROS 2、Python/C++ SDK、Isaac Sim 示例和许可证是什么？
8. 峰值功率、持续功率、热限制和推荐控制频率是什么？
9. 指尖模块、电机、减速器和软垫是否可单独更换？
10. 能否使用本项目键盘台架现场完成单键与组合键测试？

## 7. 决策门槛

只有候选手通过台架测试后，才能据此决定：

- 是否从 Dex3 切换到五指手；
- 是否需要五指全部参与，还是采用三指子集；
- 是否值得训练连续文本策略；
- 哪些误差用机构解决，哪些用视觉/触觉闭环或残差策略解决。

不要依据 DoF 数量或厂商视频直接采购两只手。
