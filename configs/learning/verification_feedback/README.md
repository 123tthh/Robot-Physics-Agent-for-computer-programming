# VF-01 配置

- `single_key_contract_v1.json`：字符键 `a/s/d` 与 Backspace，监控全部 74 键；目标为 `a`。唯一关节为合成字段，没有 G1/Dex5 可达性或控制含义。
- `evaluation_v1.json`：键程迟滞、释放保持时间、采样完整性、接触力和屏幕新鲜度的固定测试判据。

这些文件是 VF-01-T01 的配置快照。它们不能当作物理标定或 PPO 训练配置。回放生成器为重复字符案例另存目标 `aa`，每个案例导出独立任务及其哈希。

`single_hand_physics_v1.json` 是独立的 VF-01-T02 固定工位物理配置：240 Hz、23 维有序关节目标、单食指开放、固定骨盆与明确接触模型。运行 `make isaac-learning-single-hand`，复核入口与适用范围见[物理环境说明](../../../verification-feedback-skill-learning/01_environment_and_evidence/PHYSICAL_ENVIRONMENT.md)。这不是 PPO 超参数配置。

`text_feedback_v1.json` 是 VF-01-T03 的独立冻结设置：文本目标/字符绑定、观察权限、请求次数、等待预算、反馈延迟、置信度与最大年龄。物理诊断按固定案例派生并保存软件丢失、失焦、缺失等扰动；验证时从外部设置重算，不能信任回合自行放宽权限。入口 `make demo-learning-feedback`、`make isaac-learning-feedback`，见[文本与反馈隔离](../../../verification-feedback-skill-learning/01_environment_and_evidence/TEXT_FEEDBACK.md)。读数为文本状态机模拟，不是相机 OCR。

`batch_v1.json` 是 VF-01-T04 的冻结批量诊断配置：1/16/64 工位、3 m 间距、独立种子、144 测量控制周期、有界记录块和明确的 PhysX 接触缓冲容量。入口 `make isaac-learning-batch`、`make verify-learning-batch`；见[批环境与吞吐说明](../../../verification-feedback-skill-learning/01_environment_and_evidence/BATCH_ENVIRONMENT.md)。规模由 runner 逐项覆盖，其余配置保持一致；不是 PPO 训练配置。

```bash
PYTHONPATH=src python3 -m vskill_os.learning demo \
  --policy configs/learning/verification_feedback/evaluation_v1.json \
  --output artifacts/verification_feedback/vf01/contracts
```

完整格式、限制和独立复核命令见[实现说明](../../../verification-feedback-skill-learning/01_environment_and_evidence/IMPLEMENTATION.md)。
