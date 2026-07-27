1|# Hermes-Kit v2 — 产品化改造方案
2|
3|> 从"shell 包装器"升级为"统合产品"
4|> 版本: 1.1 | 2026-07-25 | 含自审修正
5|
6|## 一、现状问题
7|
8|```
9|HermesProject/                             HermesProject/
10|├── plugins/                               ├── plugins/
11|│   ├── knowledge-navigation/   ← 独立     │   └── (软链 → hermes-kit/components/)
12|│   └── knowledge-tree-plugin/  ← 独立     └── (软链 → hermes-kit/components/)
13|├── scripts/                               ├── scripts/
14|│   ├── clustering-analysis-v3/ ← 独立     │   └── (各项目软链 → hermes-kit/components/)
15|│   ├── memory-cleanup/         ← 独立     ├── hermes-kit/             ← 产品
16|│   ├── skillopt-runner/        ← 独立     │   ├── components/          ← 所有子项目移入
17|│   ├── cron-wrappers/          ← 独立     │   ├── config.yaml          ← 唯一配置入口
18|│   ├── knowledge-tree-builder/ ← 独立     │   └── kit                  ← 统一 CLI
19|│   ├── dream-synth/            ← 独立
20|│   ├── self-evolving/          ← 独立
21|│   ├── system-health-check/    ← 独立
22|│   └── hermes-kit/             ← 包装器
23|│       └── install.sh
24|```
25|
26|## 二、目标架构
27|
28|```
29|hermes-kit/                                ← 产品根目录
30|├── kit                                    ← 统一 CLI（Python）
31|│   ├── install                            # 首次部署
32|│   ├── deploy [component]                 # 部署/更新指定组件
33|│   ├── status                             # 所有组件健康状态
34|│   ├── config                             # 查看/编辑配置
35|│   └── upgrade                            # 升级全部
36|├── config.yaml                            ← 唯一配置入口
37|├── components/                            ← 所有子项目搬到这里
38|│   ├── knowledge-navigation/              # 四路召回插件（目标：~/.hermes/plugins/）
39|│   │   ├── src/                           # 原 plugins/knowledge-navigation/
40|│   │   └── config.py                      # 改读 kit-config（已完成）
41|│   ├── knowledge-tree-plugin/             # 知识树插件（目标：~/.hermes/plugins/）
42|│   ├── knowledge-tree-builder/            # 知识树构建器
43|│   ├── clustering-analysis/               # 聚类分析
44|│   │   └── (原 scripts/clustering-analysis-v3/)
45|│   ├── memory-cleanup/                    # 记忆清理
46|│   ├── skillopt-runner/                   # Skill 优化
47|│   ├── skillopt-sleep/                    # SkillOpt 依赖库
48|│   ├── flywheel/                          # 飞轮健康报告
49|│   │   ├── flywheel-health-report.sh
50|│   │   ├── kn-router-health-check.sh
51|│   │   ├── knowledge-navigation-baseline.sh
52|│   │   ├── run-skill-eval.sh
53|│   │   ├── auto-tuner.sh
54|│   │   └── health-check-cron.sh
55|│   ├── cron/                              # 所有 cron wrapper
56|│   │   ├── cron-common.sh                 # 公共库
57|│   │   ├── cron_job_template.sh
58|│   │   ├── system-health.sh
59|│   │   ├── daily-learn/
60|│   │   │   └── daily_learn.sh
61|│   │   ├── dream-synth.sh
62|│   │   ├── cron-periodic-detect.sh
63|│   │   └── cron-boot-detect.sh
64|│   ├── dream-synth/                       # 梦境合成
65|│   ├── self-evolving/                     # 自进化研究
66|│   ├── system-health-check/               # 系统巡检
67|│   │   └── health-check-all.py
68|│   └── daily-learn/                       # 每日在线学习（原 cron-wrappers/daily-learn/）
69|├── manifests/                             # 部署清单
70|│   └── kit.manifest                       # 全量清单（含 target 字段区分 plugins/ 和 scripts/）
71|├── scripts/                               # 工具脚本
72|│   ├── kit-status.sh
73|│   └── kit-verify.sh
74|└── templates/
75|    └── .env.append                        # 仅密钥
76|```
77|
78|### cron-wrappers 拆分明细
79|
80|| 源路径（scripts/cron-wrappers/） | 目标路径（components/） |
81||--------------------------------|------------------------|
82|| `flywheel-health-report.sh` | `flywheel/flywheel-health-report.sh` |
83|| `kn-router-health-check.sh` | `flywheel/kn-router-health-check.sh` |
84|| `knowledge-navigation-baseline.sh` | `flywheel/knowledge-navigation-baseline.sh` |
85|| `run-skill-eval.sh` | `flywheel/run-skill-eval.sh` |
86|| `auto-tuner.sh` | `flywheel/auto-tuner.sh` |
87|| `health-check-cron.sh` | `flywheel/health-check-cron.sh` |
88|| `cron-common.sh` 公共库 | `cron/cron-common.sh` |
89|| `cron_job_template.sh` | `cron/cron_job_template.sh` |
90|| `cron-periodic-detect.sh` | `cron/cron-periodic-detect.sh` |
91|| `cron-boot-detect.sh` | `cron/cron-boot-detect.sh` |
92|| `cron-catchup-repair.sh` | `cron/cron-catchup-repair.sh` |
93|| `daily-learn/daily_learn.sh` | `cron/daily-learn/daily_learn.sh` |
94|| `clustering-analysis-v3/scripts/...` | `components/clustering-analysis/scripts/...` |
95|| `knowledge-tree-builder/scripts/...` | `components/knowledge-tree-builder/scripts/...` |
96|| `memory-cleanup/daily_dryrun.sh` | `components/memory-cleanup/daily_dryrun.sh` |
97|
98|## 三、配置统一
99|
100|```
101|~/.hermes-kit/config.yaml          ← 唯一入口
102|├── kit:                            # 自身配置
103|├── cron:                           # 调度时间
104|├── plugin_config:                  # 知识导航插件参数（已完成）
105|├── components:                     # 各子项目配置
106|│   ├── knowledge_tree:
107|│   │   min_sub_nodes: 5
108|│   │   max_split_size: 50
109|│   ├── clustering:
110|│   │   min_cluster_size: 5
111|│   │   silhouette_threshold: 0.05
112|│   ├── memory_cleanup:
113|│   │   vote_threshold: 1
114|│   ├── skill_optimization:
115|│   │   top_k: 5
116|│   └── system_health:
117|│       schedule: "0 8 * * 1-5"
118|├── notification:                   # 通知渠道
119|└── tuning:                         # auto-tuner 参数池
120|```
121|
122|各子项目不再有自己的 `config/` 目录。所有配置从 kit-config 读取。
123|Python 项目通过 `import yaml` 读取，bash 脚本通过 `kit config get <key>` 读取。
124|
125|## 四、部署方式变化
126|
127|```diff
128|- 当前：手动 deploy.sh deploy <每个项目>
129|- 目标：kit deploy [组件名]
130|```
131|
132|| 命令 | 功能 |
133||------|------|
134|| `kit deploy` | 检测所有组件变更，批量部署 |
135|| `kit deploy knowledge-navigation` | 仅部署指定组件 |
136|| `kit deploy --dry-run` | 预览变更 |
137|
138|**部署逻辑：**
139|1. 读取 `manifests/kit.manifest` 获取文件清单（含 `target:` 字段标识目标路径）
140|2. 对比运行时文件 hash
141|3. 只部署有变更的组件
142|4. 记录部署状态到 `~/.hermes-kit/deploy.state`
143|
144|**目标路径规则：**
145|- `knowledge-navigation`、`knowledge-tree-plugin` → `/root/.hermes/plugins/`
146|- 其他组件 → `/root/.hermes/scripts/`
147|- `cron-common.sh` → `/root/.hermes/lib/`
148|
149|## 五、实施路径
150|
151|### Phase 1+2（合并执行）：搬文件 + 改配置 + 更新 deploy 路径
152|
153|> Phase 1 和 Phase 2 合并执行，避免中间态。一次性完成搬移 + 配置改造 + deploy 路径更新。
154|
155|**搬移清单：**
156|
157|| 源路径 | 目标路径 | 目标部署路径 |
158||--------|----------|-------------|
159|| `plugins/knowledge-navigation/` | `components/knowledge-navigation/` | `/root/.hermes/plugins/` |
160|| `plugins/knowledge-tree-plugin/` | `components/knowledge-tree-plugin/` | `/root/.hermes/plugins/` |
161|| `scripts/knowledge-tree-builder/` | `components/knowledge-tree-builder/` | `/root/.hermes/scripts/` |
162|| `scripts/clustering-analysis-v3/` | `components/clustering-analysis/` | `/root/.hermes/scripts/` |
163|| `scripts/memory-cleanup/` | `components/memory-cleanup/` | `/root/.hermes/scripts/` |
164|| `scripts/skillopt-runner/` | `components/skillopt-runner/` | `/root/.hermes/skillopt-runner/` |
165|| `scripts/skillopt-sleep/` | `components/skillopt-sleep/` | `/root/.hermes/skillopt-sleep/` |
166|| `scripts/cron-wrappers/` | `components/flywheel/` + `components/cron/` | `/root/.hermes/scripts/` |
167|| `scripts/dream-synth/` | `components/dream-synth/` | `/root/.hermes/scripts/` |
168|| `scripts/self-evolving/` | `components/self-evolving/` | `/root/.hermes/scripts/` |
169|| `scripts/system-health-check/` | `components/system-health-check/` | `/root/.hermes/scripts/` |
170|
171|**每步操作：**
172|1. `git mv` 搬移文件到 components/（保留 git 历史）
173|2. 原位置保留为软链（过渡期回滚保障）
174|3. 更新 `deploy/manifests/` 下的 manifest 路径
175|4. 更新 `deploy/projects/` 下的部署脚本路径（`PROJECT_SRC_REL` 指向 `scripts/hermes-kit/components/<name>`）
176|5. 各子项目 config 改为读 kit-config（知识导航已完，其他逐步）
177|
178|### Phase 3: CLI 统一
179|
180|1. 创建 `kit` CLI 入口（Python，argparse）
181|2. 实现 `kit deploy`（批量检测 + 部署）
182|3. 实现 `kit status`（全组件健康）
183|4. 实现 `kit config`（查看/编辑/`get <key>` 子命令）
184|5. 实现 `kit install` / `upgrade`（复用现有脚本逻辑）
185|
186|### Phase 4: 清理
187|
188|1. 确认所有组件在新路径下正常工作
189|2. 删除原路径的软链
190|3. 删除 `deploy/manifests/` 下的独立 manifest（合并到 `kit.manifest`）
191|4. 更新文档
192|
193|## 六、注意事项
194|
195|| 事项 | 说明 |
196||------|------|
197|| git 历史 | 用 `git mv` 保留历史 |
198|| 回滚 | 每个组件搬移后保留原路径为软链，确认无误后删除 |
199|| 向后兼容 | deploy.sh 路径更新后仍可用，`kit deploy` 在过渡期走 deploy.sh |
200|| 插件路径 | 知识导航/知识树插件 → `/root/.hermes/plugins/`，kit deploy 区分 target |
201|| manifest | 合并为 `kit.manifest`，含 `target:` 字段标识部署目标路径 |
202|| cron-wrappers | 拆分为 `flywheel/`（报告类）和 `cron/`（调度类） |
203|| kit CLI | 用 Python 实现，bash 做入口包装 |
204|| skillopt-sleep | 作为 skillopt-runner 的依赖库，搬移后更新路径引用 |
205|| 配置读取 | Python 项目用 `import yaml`，bash 脚本用 `kit config get <key>` |
206|
207|## 七、风险与缓解
208|
209|### 风险矩阵
210|
211|| 风险 | 等级 | 影响 | 概率 | 缓解措施 |
212||------|:----:|------|:----:|----------|
213|| R1: Gateway 搬移后插件加载失败 | 🔴 P0 | 四路召回断链，用户无 recall 注入 | 中 | 见下方 R1 缓解 |
214|| R2: cron-common 路径变化致所有 cron 失效 | 🔴 P0 | 聚类/记忆清理/飞轮报告等全部 cron 停摆 | 高 | 见下方 R2 缓解 |
215|| R3: skillopt-sleep import 路径断裂 | 🟡 P1 | SkillOpt nightly run 失败 | 高 | 见下方 R3 缓解 |
216|| R4: deploy manifest glob 不匹配新路径 | 🟡 P1 | deploy.sh 漏文件或报错 | 高 | 见下方 R4 缓解 |
217|| R5: PyYAML 缺失致 kit CLI 不可用 | 🟢 P2 | kit config get 失败 | 中 | 见下方 R5 缓解 |
218|
219|### R1 缓解：Gateway 搬移保护
220|
221|```
222|搬移前：
223|  ├─ 备份 /root/.hermes/plugins/knowledge-navigation/ → /tmp/kn-plugin.bak
224|  ├─ 备份 /root/.hermes/plugins/knowledge-tree-plugin/ → /tmp/kt-plugin.bak
225|  └─ 检查 python3 -c "import yaml"（确保 PyYAML 可用）
226|
227|搬移（git mv + 更新 manifest）后立即验证：
228|  ├─ deploy/deploy.sh deploy knowledge-navigation --yes
229|  ├─ python3 -c "from knowledge_navigation.config import CONFIG; print(CONFIG.min_score)"
230|  └─ systemctl restart hermes-gateway && sleep 3 && systemctl is-active hermes-gateway
231|
232|回滚：
233|  ├─ cp -r /tmp/kn-plugin.bak /root/.hermes/plugins/knowledge-navigation/
234|  ├─ cp -r /tmp/kt-plugin.bak /root/.hermes/plugins/knowledge-tree-plugin/
235|  └─ systemctl restart hermes-gateway
236|```
237|
238|### R2 缓解：cron-common 公共库保护
239|
240|cron-common 的部署目标 `/root/.hermes/lib/` 不变，仅源码路径从 `scripts/cron_common.sh` 改为 `scripts/hermes-kit/components/cron/cron-common.sh`。
241|
242|```
243|搬移后验证：
244|  ├─ deploy/deploy.sh deploy cron-common --yes
245|  ├─ test -f /root/.hermes/lib/cron_common.sh
246|  └─ bash -c "source /root/.hermes/lib/cron_common.sh && echo 'OK'"
247|```
248|
249|### R3 缓解：skillopt-sleep 作为 skillopt-runner 子目录
250|
251|不将 skillopt-sleep 作为独立组件，而是作为 `components/skillopt-runner/skillopt-sleep/` 子目录，确保 import 相对路径不变。
252|
253|```
254|搬移后验证：
255|  ├─ deploy/deploy.sh deploy skillopt-runner --yes
256|  └─ python3 -c "import sys; sys.path.insert(0, '/root/.hermes/skillopt-runner'); from skillopt_runner import *; print('OK')"
257|```
258|
259|### R4 缓解：manifest 批量更新 check
260|
261|每个组件搬移后立即更新对应的 manifest，使用统一的 manifest 模板：
262|
263|```
264|# 模板：components/<name>/manifest
265|# 源: scripts/hermes-kit/components/<name>/
266|# 标: /root/.hermes/scripts/<name>/
267|# 注: 部署前先 diff 新旧 manifest，确认 glob pattern 匹配新路径
268|
269|验证：
270|  ├─ deploy/deploy.sh deploy <name> --dry-run
271|  └─ 确认输出中所有文件路径以 components/ 开头
272|```
273|
274|### R5 缓解：kit CLI 依赖检查
275|
276|```
277|kit CLI 安装时验证：
278|  ├─ python3 -c "import yaml" || pip install pyyaml
279|  └─ python3 -c "import argparse"  # Python 标准库，无需额外安装
280|```
281|
282|### 搬移前预检清单
283|
284|每个组件搬移前必须执行：
285|
286|```bash
287|# 1. 备份运行时文件
288|cp -r /root/.hermes/plugins/<name> /tmp/<name>.bak
289|
290|# 2. 确认 deploy.sh 可工作
291|deploy/deploy.sh deploy <name> --dry-run
292|
293|# 3. 确认 manifest 路径正确
294|grep 'PROJECT_SRC_REL' deploy/projects/<name>.sh
295|
296|# 4. 确认 cron 任务（如果涉及）
297|hermes cron list | grep <name>
298|
299|# 5. 搬移后验证
300|deploy/deploy.sh deploy <name> --yes
301|test -f /root/.hermes/<target>/<name>/<关键文件>
302|```
303|
304|## 八、实施细节补充
305|
306|### 8.1 cron-common 部署路径特殊处理
307|
308|cron-common 的 `PROJECT_SRC_REL` 当前为 `scripts`（太宽泛），搬移后改为：
309|
310|```bash
311|PROJECT_SRC_REL="scripts/hermes-kit/components/cron"
312|PROJECT_TGT="/root/.hermes/lib"
313|```
314|
315|注意：`cron_common.sh` 当前位于 `scripts/cron_common.sh`（**不在** cron-wrappers 目录下），搬移时需特殊处理：
316|
317|```bash
318|git mv scripts/cron_common.sh scripts/hermes-kit/components/cron/cron-common.sh
319|git mv scripts/cron_job_template.sh scripts/hermes-kit/components/cron/cron_job_template.sh
320|```
321|
322|manifest 不变（只部署 `cron-common.sh` + `cron_job_template.sh`）。
323|
324|### 8.2 cron-wrappers 拆分策略
325|
326|拆成两个独立 deploy 项目，而非一个：
327|
328|| 新 deploy 项目 | PROJECT_SRC_REL | PROJECT_TGT | manifest |
329||---------------|-----------------|-------------|----------|
330|| `flywheel.sh` | `scripts/hermes-kit/components/flywheel/` | `/root/.hermes/scripts/` | `flywheel.manifest` |
331|| `cron.sh` | `scripts/hermes-kit/components/cron/` | `/root/.hermes/scripts/` | `cron.manifest` |
332|
333|原 `cron-wrappers.sh` 和 `cron-wrappers.manifest` **删除**。
334|
335|**flywheel.manifest：**
336|```
337|flywheel-health-report.sh
338|flywheel-health-report.py      # Python 飞轮报告器，被 .sh 调用
339|kn-router-health-check.sh
340|knowledge-navigation-baseline.sh
341|run-skill-eval.sh
342|auto-tuner.sh
343|health-check-cron.sh
344|backfill-scope.py              # 作用域回填脚本
345|```
346|
347|**cron.manifest：**
348|```
349|cron-common.sh
350|cron_job_template.sh
351|cron-periodic-detect.sh
352|cron-boot-detect.sh
353|cron-boot-detect.service       # systemd 单元文件
354|cron-catchup-repair.sh
355|cron-jobs-config.md            # cron 配置锚点文档
356|README.md                      # 目录说明
357|daily-learn/daily_learn.sh
358|skillopt-runner/skillopt-nightly-run.sh  # 独立 cron wrapper
359|```
360|
361|注意：`cron-wrappers/` 下还有 3 个嵌套子目录的 wrapper 脚本已随对应组件搬移，不在此拆分范围内：
362|- `memory-cleanup/daily_dryrun.sh` → 随 memory-cleanup 搬移
363|- `knowledge-tree-builder/scripts/...` → 随 knowledge-tree-builder 搬移
364|- `clustering-analysis-v3/scripts/...` → 随 clustering-analysis 搬移
365|
366|### 8.3 daily-learn manifest 更新
367|
368|搬移后 `deploy/projects/daily-learn.sh` 的 `PROJECT_SRC_REL` 改为：
369|
370|```bash
371|PROJECT_SRC_REL="scripts/hermes-kit/components/cron/daily-learn"
372|```
373|
374|### 8.4 不纳入 kit 的项目清单
375|
376|以下项目在 HermesProject 中但**不属于 hermes-kit**，搬移时不动：
377|
378|| 项目 | PROJECT_SRC_REL（不变） |
379||------|------------------------|
380|| ai-report-system | `scripts/ai-report-system` |
381|| drawio-generator | `scripts/drawio-generator` |
382|| p0-benchmark | `scripts/p0-benchmark` |
383|| recall-eval | `scripts/recall-eval` |
384|
385|### 8.5 skillopt-sleep 保持兄弟目录关系
386|
387|skillopt-sleep **不作为 skillopt-runner 子目录**，而是保持独立兄弟目录关系。原因是代码 `skillopt_runner.py` 第 28-31 行将 skillopt-sleep 视为 `SKILLOPT_HOME.parent / 'skillopt-sleep'`（即 `/root/.hermes/skillopt-sleep`，兄弟目录）。
388|
389|搬移后：
390|- `components/skillopt-runner/` 和 `components/skillopt-sleep/` 作为同级目录
391|- `deploy/projects/skillopt-runner.sh`: `PROJECT_SRC_REL="scripts/hermes-kit/components/skillopt-runner"`
392|- `deploy/projects/skillopt-sleep.sh`: 保留，`PROJECT_SRC_REL="scripts/hermes-kit/components/skillopt-sleep"`
393|
394|### 8.6 PROJECT_SRC_REL 批量更新清单
395|
396|| 文件 | 旧值 | 新值 |
397||------|------|------|
398|| knowledge-navigation.sh | `plugins/knowledge-navigation` | `scripts/hermes-kit/components/knowledge-navigation` |
399|| knowledge-tree-plugin.sh | `plugins/knowledge-tree-plugin` | `scripts/hermes-kit/components/knowledge-tree-plugin` |
400|| knowledge-tree-builder.sh | `scripts/knowledge-tree-builder` | `scripts/hermes-kit/components/knowledge-tree-builder` |
401|| clustering-analysis-v3.sh | `scripts/clustering-analysis-v3` | `scripts/hermes-kit/components/clustering-analysis` |
402|| memory-cleanup.sh | `scripts/memory-cleanup` | `scripts/hermes-kit/components/memory-cleanup` |
403|| skillopt-runner.sh | `scripts/skillopt-runner` | `scripts/hermes-kit/components/skillopt-runner` |
404|| skillopt-sleep.sh | `scripts/skillopt-sleep` | `scripts/hermes-kit/components/skillopt-sleep`（保留独立部署，不删除） |
405|| dream-synth.sh | `scripts/dream-synth` | `scripts/hermes-kit/components/dream-synth` |
406|| self-evolving.sh | `scripts/self-evolving` | `scripts/hermes-kit/components/self-evolving` |
407|| system-health-check.sh | `scripts/system-health-check` | `scripts/hermes-kit/components/system-health-check` |
408|| cron-common.sh | `scripts` | `scripts/hermes-kit/components/cron` |
409|
410|### 8.7 搬移顺序建议
411|
412|逐个组件搬移，每个组件一个 git commit。**建议微调顺序：cron-common 先搬，插件最后搬。**
413|
414|```bash
415|# Phase 0: 全量备份（执行前必须做）
416|cp -r /root/.hermes /tmp/hermes-backup-$(date +%Y%m%d)
417|pip install pyyaml || true  # 确保 PyYAML 可用
418|
419|# Phase 1: 试点（先搬 1 个非关键组件验证流程）
420|git mv scripts/memory-cleanup scripts/hermes-kit/components/memory-cleanup
421|git commit -m "feat(kit): [试点] move memory-cleanup to components/"
422|# 验证：deploy/deploy.sh deploy memory-cleanup --dry-run
423|# 验证：deploy/deploy.sh deploy memory-cleanup --yes
424|# 验证：test -f /root/.hermes/scripts/memory-cleanup/daily_dryrun.sh
425|# 试点成功后继续批量执行
426|
427|# Phase 2: cron-common（第 0 步，所有 cron 的公共依赖）
428|git mv scripts/cron_common.sh scripts/hermes-kit/components/cron/cron-common.sh
429|git mv scripts/cron_job_template.sh scripts/hermes-kit/components/cron/cron_job_template.sh
430|git commit -m "feat(kit): move cron-common to components/cron/"
431|
432|# Phase 3: cron-wrappers 拆分（flywheel + cron）
433|mkdir -p scripts/hermes-kit/components/{flywheel,cron,cron/daily-learn}
434|# flywheel: 报告类 + auto-tuner + health-check + backfill-scope
435|mv scripts/cron-wrappers/flywheel-*.sh scripts/cron-wrappers/flywheel-*.py \
436|   scripts/cron-wrappers/*-baseline.sh scripts/cron-wrappers/*-eval.sh \
437|   scripts/cron-wrappers/auto-tuner.sh scripts/cron-wrappers/health-check-cron.sh \
438|   scripts/cron-wrappers/backfill-scope.py \
439|   scripts/hermes-kit/components/flywheel/
440|# cron: 调度类 + systemd 单元 + 文档
441|mv scripts/cron-wrappers/cron-*.sh scripts/cron-wrappers/cron-*.service \
442|   scripts/cron-wrappers/cron-jobs-config.md scripts/cron-wrappers/README.md \
443|   scripts/hermes-kit/components/cron/
444|mv scripts/cron-wrappers/daily-learn scripts/hermes-kit/components/cron/daily-learn/
445|mv scripts/cron-wrappers/skillopt-runner scripts/hermes-kit/components/cron/skillopt-runner/
446|rm -rf scripts/cron-wrappers && git add . && git commit -m "feat(kit): split cron-wrappers into flywheel + cron"
447|
448|# Phase 4: 独立脚本（7 个，含 skillopt-runner + skillopt-sleep）
449|for src in knowledge-tree-builder clustering-analysis-v3 skillopt-runner skillopt-sleep dream-synth self-evolving system-health-check; do
450|  git mv "scripts/$src" "scripts/hermes-kit/components/$src"
451|  git commit -m "feat(kit): move $src to components/"
452|done
453|
454|# Phase 5: 插件（最后搬，减少 Gateway 重启次数）
455|git mv plugins/knowledge-navigation scripts/hermes-kit/components/knowledge-navigation
456|git commit -m "feat(kit): move knowledge-navigation to components/"
457|git mv plugins/knowledge-tree-plugin scripts/hermes-kit/components/knowledge-tree-plugin
458|git commit -m "feat(kit): move knowledge-tree-plugin to components/"
459|
460|# Phase 6: 创建软链过渡期保护
461|cd /mnt/d/HermesProject/plugins
462|ln -s ../../scripts/hermes-kit/components/knowledge-navigation knowledge-navigation
463|ln -s ../../scripts/hermes-kit/components/knowledge-tree-plugin knowledge-tree-plugin
464|cd /mnt/d/HermesProject/scripts
465|for d in knowledge-tree-builder clustering-analysis memory-cleanup skillopt-runner skillopt-sleep dream-synth self-evolving system-health-check; do
466|  ln -s ../scripts/hermes-kit/components/$d $d
467|done
468|cd /mnt/d/HermesProject
469|```
470|
471|### 8.8 搬移后验证
472|
473|每个组件搬移后执行：
474|1. `deploy/deploy.sh deploy <name> --dry-run` — 确认路径正确
475|2. `deploy/deploy.sh deploy <name> --yes` — 部署到运行时
476|3. 确认运行时文件存在：`test -f /root/.hermes/<target>/<name>/<key_file>`
477|4. 如果涉及 Gateway：`systemctl restart hermes-gateway && systemctl is-active hermes-gateway`
478|
479|1|### 8.9 kit.manifest 格式设计
2|
3|```yaml
4|# kit.manifest — 全量组件清单（含 target 字段）
5|# 格式: component|source_path|target_path|target_type|files...
6|# target_type: plugins/ | scripts/ | lib/ | custom-path/
7|
8|knowledge-navigation|scripts/hermes-kit/components/knowledge-navigation/|/root/.hermes/plugins/knowledge-navigation/|plugins/|**/*.py,**/*.md,!__pycache__/**,!*.pyc
9|knowledge-tree-plugin|scripts/hermes-kit/components/knowledge-tree-plugin/|/root/.hermes/plugins/knowledge-tree-plugin/|plugins/|**/*.py,**/*.md,!__pycache__/**,!*.pyc
10|knowledge-tree-builder|scripts/hermes-kit/components/knowledge-tree-builder/|/root/.hermes/scripts/knowledge-tree-builder/|scripts/|**/*.py,**/*.sh,**/*.md,!__pycache__/**,!*.pyc
11|clustering-analysis|scripts/hermes-kit/components/clustering-analysis/|/root/.hermes/scripts/clustering-analysis-v3/|scripts/|**/*.py,**/*.sh,**/*.md,!__pycache__/**,!*.pyc
12|memory-cleanup|scripts/hermes-kit/components/memory-cleanup/|/root/.hermes/scripts/memory-cleanup/|scripts/|**/*.py,**/*.sh,**/*.md,!__pycache__/**,!*.pyc
13|skillopt-runner|scripts/hermes-kit/components/skillopt-runner/|/root/.hermes/skillopt-runner/|custom-path/|**/*.py,**/*.sh,**/*.md,!__pycache__/**,!*.pyc
14|skillopt-sleep|scripts/hermes-kit/components/skillopt-sleep/|/root/.hermes/skillopt-sleep/|custom-path/|**/*.py,**/*.sh,**/*.md,!__pycache__/**,!*.pyc
15|flywheel-health-report|scripts/hermes-kit/components/flywheel/|/root/.hermes/scripts/flywheel-health-report.sh|scripts/# ... (其他 flywheel 脚本类似)
16|cron-common-lib|scripts/hermes-kit/components/cron/cron-common.sh,/scripts/hermes-kit/components/cron/cron_job_template.sh|/root/.hermes/lib/cron_common.sh,/root/.hermes/lib/cron_job_template.sh|lib/# ... (其他 cron wrapper 类似)
17|daily-learn-script|scripts/hermes-kit/components/cron/daily-learn/daily_learn.sh|/root/.hermes/scripts/daily-learn/daily_learn.sh|scripts/# ... (其他 cron wrapper 类似)
18|dream-synth-script|scripts/hermes-kit/components/dream-synth/dream-daily.sh,/dream-daily.py,/README.md,/config/default.yaml,/manifests/dream-synth.manifest,/deploy/projects/dream-synth.sh,/deploy/projects/drawio-generator.manifest,/deploy/projects/recall-eval.manifest,/deploy/projects/p0-benchmark.manifest,/deploy/projects/self-evolving.manifest,/deploy/projects/system-health-check.manifest,/deploy/projects/memory-cleanup.manifest,/deploy/projects/clustering-analysis-v3.manifest,/deploy/projects/knowledge-tree-builder.manifest,/deploy/projects/knowledge-tree-plugin.manifest,/deploy/projects/knowledge-navigation.manifest,/deploy/projects/skillopt-runner.manifest,/deploy/projects/skillopt-sleep.manifest,/deploy/projects/daily-learn.manifest,/deploy/projects/cron-common.manifest,/deploy/projects/cron-wrappers.manifest,/deploy/projects/flywheel-manifest.manifest,/deploy/projects/cron-manifest.manifest,/deploy/projects/skilloopt-nightly-run-manifest.manifest,/deploy/projects/skilloopt-nightly-run-manifest.manifest,/deploy/projects/skilloopt-nightly-run-manifest.manifest,/deploy/projects/skilloopt-nightly-run-manifest.manifest,/deploy/projects/skilloopt-nightly-run-manifest.manifest,/deploy/projects/skilloopt-nightly-run-manifest.manifest,/deploy/projects/skilloopt-nightly-run-manifest.manifest,/deploy/projects/skilloopt-nightly-run-manifest.manifest,/deploy/projects/skilloopt-nightly-run-manifest.manifest,/deploy/projects/skilloopt-nightly-run-manifest.manifest
19|
20|
## 九、实施顺序
480|
481|```
482|Phase 1+2（合并，一次性完成）：搬文件 + 改配置 + 更新 deploy 路径
483|  ├─ 搬移 11 个组件到 components/
484|  ├─ 保留原路径为软链（过渡期）
485|  ├─ 更新 deploy manifest 和 project 脚本路径
486|  ├─ 各组件改为读 kit-config
487|  └─ 更新 kit.manifest（含 target 字段）
488|
489|Phase 3：CLI 统一
490|  ├─ kit 入口（Python）
491|  ├─ kit deploy（批量检测 + 部署）
492|  ├─ kit status / config / install / upgrade
493|  └─ kit config get <key>（供 bash 脚本读取配置）
494|
495|Phase 4：清理
496|  ├─ 确认无误后删除软链
497|  ├─ 删除原独立 manifest
498|  └─ 更新文档
499|```
500|
501|