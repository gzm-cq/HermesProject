# memory-weeder

> Vestige 遗忘机制运维脚本（数据飞轮增强方案 P0-3 配套）。
>
> 读取 Vestige 访问衰减状态（knowledge-navigation 插件维护的 `vestige_state.json`），报告长期未访问、已被降权的记忆（`low_priority`），供人工审计。

> ⚠️ **Vestige 的"遗忘"是软性的**：在 recall 阶段按 `access_weight` 降权，不删除记忆本身。本脚本仅做**报告**与**状态重置**，不修改 Hindsight 数据。

## 用法

```bash
python weed.py                # 报告当前衰减状态
python weed.py --stats        # 汇总统计
python weed.py --reset <id>   # 重置某记忆的访问计数（重新激活）
```

## 配置（环境变量）

| 变量 | 默认 | 说明 |
|------|------|------|
| `KN_VESTIGE_STATE` | `~/.hermes/knowledge-navigation/vestige_state.json` | 衰减状态文件 |
| `KN_VESTIGE_DECAY_BASE` | `0.9` | 每日衰减基数 |
| `KN_VESTIGE_LOW_THRESHOLD` | `0.2` | 低于此权重判定为 low_priority |

衰减权重计算：`access_weight = DECAY_BASE ^ (当前时间 - last_access)/86400`。

## 关联 Skill

本目录提供配套 Skill：`skills/devops/memory-weeder/SKILL.md`，供 Agent 直接执行遗忘审计。
