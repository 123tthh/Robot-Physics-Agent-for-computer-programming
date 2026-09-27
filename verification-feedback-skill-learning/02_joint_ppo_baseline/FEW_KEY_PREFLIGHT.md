# VF-02-T04：少量键与真实退格的前置检查

状态：`PARTIAL / OFFLINE_PREFLIGHT`，2026-09-27。已实现可重复的离线 FK 采样检查，尚无多键、重复字符或 Backspace 的新物理验收，也未启动新训练。T03 最终汇总已在原节点独立重验通过（三种子各 100/100），详见 [MULTISEED.md](MULTISEED.md)。

## 已完成的检查

入口 [`few_key_preflight.py`](../../src/vskill_os/adapters/isaac/learning/few_key_preflight.py) 使用当前固定骨盆 URDF、`direct_joint_initialization` 手工关节初态、实际 6 mm 指尖球体及其局部偏移。只计算正向运动学，在已开放的左食指第一关节完整限位 `[0, 1.5708]` 取 301 个样本；不调用 IK、旧校准姿态或示范轨迹，也不产生机器人命令。NumPy/SciPy 仅用于可选适配器，由 `learning-preflight` extra 声明；核心包不增加依赖。

```bash
PYTHONPATH=src python3 -m vskill_os.adapters.isaac.learning.few_key_preflight \
  --report reports/verification_feedback/vf02_t04_geometry_preflight.json
```

结果保存于[带资产及源码哈希的报告](../../reports/verification_feedback/vf02_t04_geometry_preflight.json)：

| 候选键 | 距 `a` 键中心 | 采样的最小指尖球—静止键帽间隙 |
|---|---:|---:|
| `a` | 0 mm | −6.00 mm（几何重叠） |
| `s` | 19.05 mm | 6.00 mm |
| `d` | 38.10 mm | 24.45 mm |
| `f` | 57.15 mm | 42.91 mm |
| `backspace` | 227.06 mm | 199.67 mm |

这支持先扩大并验收关节控制范围，再做多键训练。离散静态采样不证明物理可达或不可达；它没有评估动力学、桌面/自碰撞、跟踪误差、键程、其他手指和释放。负间隙不是可执行姿态，不能把这些样本作为策略初始化、轨迹或成功标签。报告始终保留 `accepted=false` 和 `physical_reachability_verified=false`。

## 后续实现边界

1. **冻结 T03 证据和旧接口。** 当前 reset、actor 特征、奖励进度、采集和物理验收均显式绑定单 `a`。不能只替换任务标签；T04 使用新版本配置与独立适配文件，旧 28 个来源文件及原检查点继续可复验。
2. **逐步开放具名关节并实际验收。** 从已有 23 维 schema 中选择最小子集，保留独立限位/限速/接触/支撑保护、执行目标反馈和控制权检查。以 `a`、邻键 `s`、标准布局 Backspace 为候选；实际字符集合在相同场景的无 IK 物理接触与释放检查后冻结。保持标准键盘布局与真实 Backspace 身份。
3. **建立目标条件的 v2 采集与复验。** 目标键身份和几何进入特权观测；实际文本真值仍只进入监督记录。验证器独立重算每次正确 Down→Up→持续净空，要求 actor 输入/输出与原始物理及检查点一致。
4. **先做完整小案例。** 单 `a`、单邻键、`aa`、`as`/`sa`、从 `aa` 通过真实 Backspace 变成 `a`、误字退格重输。重复字符必须有两次真实释放；退格必须有原始接触/键程、物理 KeyDown 和关联文本变化，不能调用文本 API 代替。
5. **保留拒绝案例再训练。** 覆盖卡键、管理 KeyUp、无真实退格事件、软件丢字、邻键误触、源码/资产/检查点错配。每个键和连续序列单独验收，不用总体均值掩盖失败；冻结训练预算和最终评估集后才启动新的目标条件 PPO。

T05 技能包发布以及 VF-03 验证/恢复决策学习仍以这些实际物理门槛通过为前提。
