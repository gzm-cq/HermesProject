# Hermes 插件模板（hermes-plugin-template）

> 官方推荐的 Hermes 插件（Python）开发模板。复制本目录即可快速搭建一个新插件。

## 目录结构

| 文件 | 职责 |
|------|------|
| `plugin.yaml` | Hermes 插件注册元信息：`name` / `version` / `description` / `hooks` |
| `main.py` | 插件主逻辑：`register(ctx)` 为插件注册入口，在此注册钩子函数 |
| `__init__.py` | 包初始化（可留空） |
| `pyproject.toml` | Python 包配置，含 `[project.entry-points."hermes.plugins"]` 入口点 |
| `README.md` | 本说明 |

## 快速开始

1. 复制本目录并重命名为你的插件名（如 `my-plugin`）
2. 修改 `pyproject.toml`：
   - `name` / `version` / `description`
   - `[project.entry-points."hermes.plugins"]` 中的入口点，格式为 `"插件ID" = "python包名.模块名:函数名"`（如 `"mytool" = "my_plugin.main:register"`）
3. 在 `main.py` 的 `register(ctx)` 中实现并注册钩子函数：

   ```python
   def register(ctx) -> None:
       ctx.register_hook("pre_llm_call", pre_llm_call)   # LLM 调用前
       ctx.register_hook("post_llm_call", post_llm_call) # LLM 调用后
   ```

4. 在 `plugin.yaml` 中声明要启用的钩子：

   ```yaml
   name: my-plugin
   version: "0.1.0"
   hooks:
     pre_llm_call:
       callback: pre_llm_call
       enabled: true
   ```

5. 将插件目录放入 Hermes 的插件加载路径，重启 Hermes 后自动加载。

## 钩子参考

| 钩子 | 时机 | 典型用途 |
|------|------|----------|
| `pre_llm_call` | 每次 LLM 调用前 | 上下文注入（知识召回、记忆补充）、请求改写 |
| `post_llm_call` | LLM 调用完成后 | 结果后处理、增量学习（知识入库）、质量记录 |

> 具体钩子协议与上下文对象 `ctx` 提供的能力，参考 Hermes 插件开发文档。

## 部署

```bash
cd /mnt/d/HermesProject && bash deploy/deploy.sh deploy <插件名> --yes
```

## 参考实现

- [knowledge-navigation](../knowledge-navigation/README.md) — 五路召回（mask 四路 + CodeGraph 符号级）的完整插件示例
- [knowledge-tree-plugin](../knowledge-tree-plugin/README.md) — `post_llm_call` 增量学习示例
