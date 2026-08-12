# SPEC: env_loader 类型扩展

## 问题
`env_loader.py` 当前提供 `get_env()`、`get_env_int()`、`get_env_float()` 三个工具函数，但缺少 `get_env_bool()` 和 `get_env_list()`。cron 脚本中频繁需要解析 `ENABLE_X=true`、`SKIP_STEPS=a,b,c` 这类环境变量，目前每次手动解析，代码重复且易出错。

## 目标
为 `env_loader.py` 新增两个函数：
1. `get_env_bool(key: str, default: bool) -> bool` — 解析 `true/false/1/0/yes/no`，大小写不敏感
2. `get_env_list(key: str, default: list[str], separator: str = ",") -> list[str]` — 按分隔符拆分，strip 空白，过滤空值

## 要求
- 返回值类型与签名一致，不抛异常
- 函数签名与现有 `get_env_int`/`get_env_float` 风格一致
- 单元测试覆盖：正常值、边界值、缺失值、异常值
- 测试文件：`plugins/knowledge-navigation/tests/test_env_loader.py`
- 保持 `from __future__ import annotations`

## 文件
- 修改：`plugins/knowledge-navigation/src/knowledge_navigation/core/env_loader.py`
- 新增：`plugins/knowledge-navigation/tests/test_env_loader.py`

## 验证
```bash
cd /mnt/d/HermesProject
python3 -m pytest plugins/knowledge-navigation/tests/test_env_loader.py -v --tb=short
```