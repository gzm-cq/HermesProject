# SPEC: Auto-Tuner 参数自优化修复

## 问题

1. **显示 bug**: 第 904 行 `${DRY_RUN:+(DRY-RUN)}` 在 `DRY_RUN=false` 时仍显示 `(DRY-RUN)`
2. **核心 bug**: `extract_metrics_for_tuning()` 按日期查找指标，但 cron 在 CN 08:00 运行，当天数据尚未写入，fallback 逻辑未生效

## 修复方案

### Fix 1: 显示 bug
**文件**: `D:\HermesProject\scripts\auto-tuner.sh`
**位置**: 第 904 行
**当前**: `echo "  Auto-Tuner 开始 — ${today}${DRY_RUN:+(DRY-RUN)}"`
**修复**: 改为 `if [[ "$DRY_RUN" == "true" ]]; then ... else ... fi`

### Fix 2: 指标数据获取
**文件**: `D:\HermesProject\scripts\auto-tuner.sh`
**位置**: `extract_metrics_for_tuning()` 函数（约 650 行）
**修复**: 从按日期查找改为读取历史文件中最后 2 条 `scheduled` 记录，无论日期