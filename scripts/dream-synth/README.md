# Dream Synth — 每日梦境流水线

从 Hermes 对话记录中提炼知识，写入 SAG，精炼沉淀到 Wiki。

## 流水线（每日 16:00 串行）

```
dream-daily.py
  │
  ├─ Phase 1: synthesize
  │   读当天新增 session → LLM 过滤 → LLM 提炼 → 写入 SAG（tag: dream-synth）
  │
  ├─ Phase 2: patterns
  │   查近期反思笔记 → LLM 发现跨 session 重复主题 → 写入 SAG（tag: dream-pattern）
  │
  ├─ Phase 3: promote
  │   从本周未归档反思中 → LLM 判断是否值得归档 → 写入 axiom-wiki
  │
  └─ Phase 4: feishu push
      取 top-5 未归档反思 → 推送飞书
```

## 配置

见 `config.yaml`。

## 部署

```bash
./deploy/deploy.sh deploy dream-synth --yes
```

## 依赖

- state.db（Hermes session 库）
- LiteLLM 网关（127.0.0.1:4142）
- axiom-wiki（物理路径 + MCP）
- 飞书（lark-cli）