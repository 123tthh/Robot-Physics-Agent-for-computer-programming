# Benchmark 设计

## 目标

Benchmark 用于测量 Agent–Skill–Verifier 系统在长程任务和多域故障下的可靠性，不评测真实机器人动力学。

## 配置

```bash
PYTHONPATH=src python3 -m vskill_os.cli benchmark \
  --episodes 50 \
  --profile recovery \
  --seed 7 \
  --output artifacts/benchmark.json
```

相同的 `profile`、`episodes` 和 `seed` 会生成相同故障序列。

## Profile

| Profile | 特征 |
|---|---|
| `nominal` | 不注入故障，代码首稿正确 |
| `recovery` | 以中等概率注入可恢复故障和错误代码 |
| `stress` | 高概率故障，并可能注入没有默认恢复路径的 `WRONG_KEY` |

## 当前指标

- `success_rate`：完成全部图节点的比例；
- `false_completion_count`：未测试通过却产生完成报告的次数；
- `mean_skill_calls`：每个任务的平均 Skill 调用数；
- `mean_recoveries`：每个任务的平均恢复 Skill 数；
- `mean_duration_seconds`：单任务平均墙钟时间；
- `failure_codes`：所有结构化失败码计数；
- 每轮注入故障数、调用数、恢复数和失败节点。

## 论文实验扩展

后续应在相同 Benchmark API 下加入：

- 无 Verifier；
- 只有局部后置条件；
- 加入下一技能就绪性；
- 无恢复记忆；
- 平铺历史与结构化恢复记忆；
- 数字 API Oracle；
- 不同 Agent/规划器；
- Isaac Lab 全身控制场景与真机结果。
