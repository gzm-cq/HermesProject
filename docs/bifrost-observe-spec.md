# Bifrost 优化验证与加固 SPEC（2026-08-27）

> 定位：既有 /root/bifrost-src/docs/bifrost-enhancement-spec.md（per-key 断路器 + 错误分类器 + per-model/TTFB 超时，已实现约90%）的配套验证与加固 SPEC。
> 背景：TTFB/熔断/fallback 改造已部署并测试通过，进入真实环境观察期。本文档基于今日源码核实 + 真实环境数据，明确「已验证项 / 待观察项 / P0-P2加固项」，供两天后对比效果。
> ⚠️ v2 (13:12)：基于五路交叉验证修订 v1。核心变更：
>   1) P0-A「待实施」表述失实 → 实际已半落地（routing-engine-log Info级在，TTFB/trip日志缺失）；
>   2) V3/V4验收标准当前不可达的原因从「没跑两天」修正为「字符串不存在于二进制」；
>   3) 新增认知澄清 C3：sensenova key×model独立配额 → 「合并slot」建议作废；
>   4) P-C agnes归属改为待决策项而非断言bug。
> ⚠️ v2初稿(13:08/13:10)曾因输出污染产生乱码版本文档，13:12已整体重写覆盖；本文件为最终干净版。
> ⚠️ v2.2 (13:36)：P0-A日志补齐已实现并部署——TTFB超时Warn + TripKey Warn + fallback Debug→Info三处日志落地，编译/单测全绿，新二进制PID76432运行，实测'trying fallback'Info级可见。V3/V4验收观测手段就位。
> ⚠️ v2.1 (13:22)：用户拍板两项决策并已落地——①agnes从全部fallback链移除(config.json+SQLite+运行时内存三态一致)；②cooldown_seconds维持300s(5分钟,已是当前值无需改)。
> 状态：观察期 Day-1（2026-08-27）

---

## 一、当前部署状态（已核实）

A1 TTFB源码实现 ✅
   core/schemas/account.go L158-160（三字段）
   core/providers/utils/utils.go L487-529（DoStreamingRequest timer + ConnectionClosed所有权接管）
   L3487+ ReleaseStreamingResponse适配

A2 TTFB专项测试 ✅
   streamingclient_test.go: TestDoStreamingRequest_TTFBTimeoutAborts / _TTFBNoOverridePassthrough

A3 TTFB配置生效 ✅
   config.json: sensenova全部6个key配 ttfb_timeout_seconds:60 + timeout_override_seconds:60 + idle_timeout_override_seconds:120

A4 TTFB二进制部署 ✅
   strings确认运行中二进制含 ttfb_timeout_seconds；PID=42025,09:53启动稳定

A5 fallback链可用性实测 ✅
   s-glm→DeepSeek-V4-Flash-0731、s-deepseek-v4-flash→agnes、scnet→DeepSeek-V4-Flash-0731、
   deepseek→deepseek-v4-flash、agnes→agnes，全部200实测通过

A6 TTFB超时→fallback链路逻辑成立性核实 ✅
   ErrTimeout(L529) → isNetworkError → Retryable(同key) → max_retries=0无预算 → shouldTryFallbacks放行(nil默认允许)

---

## ⚠️二、真实环境基线（Day-1, PID≥38206新窗口）

### Day-1关键数据

新窗口内最终失败给客户端：0条
>30s请求：36条，全部200成功
其中 >60s：97.5s /71.2s /71.0s /68.8s /67.7s×2 /66.2s ...
TTFB超时触发日志：0条（debug级未开，无法确认是否真未触发）

### Day-1发现的问题

P0 — [可观测性] retry/rotate/circuit/TTFB/failover日志全为Debug级
   代码位置：
     core/bifrost.go L6379/L6418/L6811 — logger.Debug(...)
     core/providers/utils/utils.go — DoStreamingRequest内部无Info级事件日志
   影响：
     当前 LOG_LEVEL=info → 自愈链路是否真触发完全不可见。无法区分：
       (a) fallback链也全挂；(b)客户端提前取消；(c)路由规则未匹配；(d)TTFB正常没触发。
     这是「跑2天看效果」的最大障碍——没有观测手段。

P1 — [残留失败]新窗口内仍有429/422/404快速失败
   09:33:42  429  3.3s   OpenAI/Python     ← s-glm限流? rotate_key应换key再试
   09:40:52  429  0.4s   Python-urllib     ← 
   09:40:52  422  0.1s   Python-urllib     ← 
   09:44:12  404  1.0s   Python-urllib     ← model不存在?
   这些是快速失败(非长挂起)，说明有请求没走通fallback链或模型名不匹配。需排查归属模型。

P2 — [配置显式化] network_config={}依赖代码默认值
   现状："network_config": {}
   影响：DefaultMaxRetries=0、DefaultRequestTimeoutInSeconds=300等全走代码常量。无法运行时调整。

### ⚠️认知澄清（防误判）

C1 >30S成功请求 ≠ TTFB失效
  今天36条>30S请求全部200成功——这是预期行为而非缺陷：
    - TTFB只截断「首字节等待」(60S)，不限制整体生成时长
    - s-glm thinking首字节在60S内到达后，整体生成90+S仍会正常返回200
    - Hermes侧若设更短读超时(<90S)，仍可能客户端超时——那是Hermes侧问题非Bifrost

C2 今早6×502@120S+504@185S发生在旧二进制(PID时间线)
  07:18 PID206启动(旧二进制,不含TTFB)
  08:09–08:11 ×502@120S ← PID206旧进程内发生(历史数据)
  09:07 PID38206启动(TTFB新二进制)
  09:14/43/45/52多次重启(TTFB部署迭代)
  10:+ PID42025稳定运行,无最终失败
  今早的故障不能证明「现在还有问题」，它们是修复前的历史数据。

C3 【关键·新增】商汤限额粒度 = key × model独立配额池。
  两key各自账户(key1×env.SN_API_KEY账户A + key2×字面量sk-iK账户B)；每个key下三个模型(s-glm/s-deepseek/lite)**各自有独立额度**,互不共享(Hindsight回忆+用户今日确认)。
  
  推论⇒上一轮讨论中提出的「把6 slot合并成按凭据bucket」建议**作废且有害**:若glm@accA撞限流并熔断其bucket,会连带停止同acc另两个模型的自然流量——而它们各自额度充足却被误伤扩散至不需要处;breaker按slot UUID隔离恰好让『glm撞墙』不影响同账号deepseek/lite其余额度继续工作(SQLite config_keys已验证6 UUID互异,E2)。
  
  结论:sensenova现有6 slot配置在独立配额语义下是正确设计,**保持原样不动**,无需任何修改。(此条同时推翻此前对同一问题的相反判断。)

---

## ⚠️三、加固方案

### P0-A：关键决策日志 Debug→Info【⚠️v2修订：实际已半落地】

涉及文件：
1. core/bifrost.go
2. core/providers/utils/utils.go

**v2核实结论（2026-08-27 13:12，五路验证E3/E4/E5）：**

```
✅ AppendRoutingEngineLog(schemas.RoutingEngineCore, schemas.LogLevelInfo,
      "Trying fallback…")               bifrost.go L527≈6407 ——info级journal可见(runtime log已验证)
❌ logger.Debug("trying fallback provider…")        L527b       ←仍Debug,journal info下不可见
❌ logger.Debug("sleeping for %s before retry")                 ←仍Debug
❌ TTFB超时分支(case<-timer.C, utils.go ~L514)                   ←连一行log都没有(strings binary zero match)
❌ TripKey调用处(core/bifrost.go breaker.Manager.TripKey点 ~L676…)←无任何级别日志(strings binary zero match)
```

即 spec §五阶段一的“~20 Go”实仅完成约一半(routing engine log)；剩余两个关键事件源【①TTFB触发②breaker trip/release】依然零可见性。

**剩余缺口（需补的日志点）：**
```go
// core/providers/utils/utils.go DoStreamingRequest TTFB超时分支(case <-timer.C)内:
logger.Warn("ttfb timeout after %v for stream request", ttfb)

// core/bifrost.go TripKey调用处(熔断触发):
logger.Warn("circuit breaker tripped key %s for %d seconds (cooldown)", currentKey.ID, currentKey.CircuitBreaker.CooldownSeconds)

// core/bifrost.go "trying fallback provider" Debug→Info:
bifrost.logger.Info("trying fallback provider %s with model %s", fallback.Provider, fallback.Model)
```

验收标准：
journalctl -u bifrost --since today --no-pager | grep -iE 'circuit.*trip|rotat.*key|trying fallback|ttfb timeout'
应能统计出事件次数。

改动量 ~15行Go。风险低。

### P1-A：残留失败排查脚本

新建 /root/.hermes/scripts/bifrost-failure-audit.py：

功能：
```
journalctl -u bifrost --since <window> --no-pager \
解析 request-completed JSON:
输出表:
时间戳 HTTP码 duration user_agent trace_id request_id model?(从routing log补)

分类:
>=400且duration<10 => "快速失败"(429限流/422驳返/404不存在)
>=400且duration>30 => "长挂起后失败"
<400且duration>60 => "慢成功"(需人工判断是否正常长思考)
```

验收标准：脚本能输出上述三类清单；配合P0-A的Info日志能归属到具体model/provider/key。
改动量 ~80行Python。风险低。

### P2-A：network_config显式化

config.json providers.sensenova.network_config:
```
{
  "max_retries": null,
  "default_request_timeout_in_seconds": null,
}
```
先保持null(=代码默认)，仅文档化各provider实际生效值；如需调整再改。
目的：消除隐式依赖，让运维知道当前实际值是多少。
改动量 ~10行JSON+文档。风险极低。

---

## ⚠️四、明确不做的事（防过度设计）

D1 server_permanent_action对504改direct_fallback的优先级调整(isNetworkError抢先问题)
-> spec §5阶段七已隐含处理；且今早504@185S发生在旧二进制，新窗口无此现象——先观察再定。

D2 Hermes侧读超时调优(<90S场景)
-> Hermes侧独立问题，不在本SPEC范围；如用户要求另立SPEC。

D3 enterprise circuit breaker集成(circuit-breaker policy重定向fallback)
-> OSS per-key breaker已够用；enterprise特性互补不冲突(spec §7已述)，本期不动。

---

## ⚠️五、两天观察期执行计划

每日快照命令(bash):
```bash
LOG=/root/.hermes/logs/bifrost-observe.log
echo "=== $(date '+%F %T') ===" >> "$LOG"
journalctl -u bifrost --since "24 hours ago" --no-pager \
 | grep 'chat/completions' \
 | python3 /root/.hermes/scripts/bifrost-observe.py >> "$LOG"
```

bifrost-observe.py核心逻辑(python):
```python
import subprocess, json, datetime as dt

def fetch(since):
    out = subprocess.run(['journalctl','--no-pager','--since',since,'--unit','bifrost'],
                         capture_output=True).stdout.decode(errors='replace')
    rows=[]
    for ln in out.splitlines():
        try:
            j = json.loads(ln[ln.index('{'):])
        except Exception:
            continue
        if '/chat/completions' not in j.get('http.target',''):
            continue
        rows.append(j)
    return rows

rows = fetch('24 hours ago')
fast_fail=[r for r in rows if r.get('http.status_code',200)>=400 and (r.get('http.request_duration_ms') or 99999)<10000]
slow_fail=[r for r in rows if r.get('http.status_code',200)>=400 and (r.get('http.request_duration_ms') or 0)>30000]
slow_succ=[r for r in rows if r.get('http.status_code',200)<400 and (r.get('http.request_duration_ms') or 0)>60000]

print(f"[{dt.datetime.now():%F %T}] chat total={len(rows)}")
print(f"fast_fail(<10s & >=400): {len(fast_fail)}")
for r in fast_fail[:15]:
    print(" ",r['time'][11:-9],r['http.status_code'],f"{r['http.request_duration_ms']}ms",r['trace_id'][ :12])
print(f"\nslow_fail(>=30s & >=400): {len(slow_fail)}")
for r in slow_fail[:15]:
    print(" ",r['time'][11:-9],r['http.status_code'],f"{r['http.request_duration_ms']}ms",r['trace_id'][ :12])
print(f"\nslow_succ(<400 & >=60s): {len(slow_succ)}")
for r in slow_succ[:15]:
    print(" ",r['time'][11:-9],f"{r['http.request_duration_ms']}ms",r['trace_id'][ :12])
```

配合P0-A落地后追加统计：
ttfb timeout事件数(grep)、circuit.*trip / rotated key / trying fallback provider事件数(grep)。

Day-D对比方法：保留Day-1 baseline(本文第二节)，Day-D跑同一脚本逐项对比。

---

## ⚠️六、验收标准(Day-N vs Day-D)

V1 最终失败给客户端数
   Day-N现状: ≤若干次/day(新窗口0)
   Day-D目标: <5次/day且无长挂起失败(>30S)
   判定: journalctl grep status≥500 排除health/governance

V2 >90S慢成功占比
   Day-N现状: ~17%(6条/36条)
   Day-D目标: <20%(区分正常长思考vs异常)
   判定: observe.py slow_succ分布

V3 TTFB截断事件数可观测性(P0-A后)
   Day-N现状: 不可见(Debug级)
   Day-D目标: 可见且≤合理值(商汤thinking卡住时60S即fallback)
   判定: grep 'ttfb timeout'

V4 熔断轮换事件数可观测性(P0-A后)
   Day-N现状: 不可见(Debug级)
   Day-D目标: 可见且≤合理值(key限流自动换key不误伤健康key)
   判定: grep 'circuit trip' / 'rotated key'

通过判据(V1达标 + V2无明显恶化 + V3/V4可观测到真实自愈事件证明链路真在工作。)

---

## ⚠️七、实施顺序与工作量估算(按依赖排序)

阶段一 P0-A 关键决策日志Debug->Info(core两个文件~20Go)低 commit+deploy重启 grep可见即过;
阶段二 P1-A failure-audit审计脚本(~80Py)极低 commit+deploy pycompile+pytest轻量单测覆盖解析函数;
阶段三 P2-A network_config显式化(config.json+spec文档~10JSON)极低 commit 热加载或restart读取生效;
阶段四 两日观察 cron可选每日跑observe.py记录logs/, Day-D对比验收表;

每步独立commit便于回滚;总工作量约110行Go/Python加少量JSON文档修改;全程不改外部服务源码只在桥接层(Bifrost自身)。

---

## ⚠️八、(补充)P0-A落地之后才能做的进一步分析方向【预留】

以下方向需要P0-A可观测性落地后才能推进,P0-A之前不做:

R1. RetryPolicy优先级调整(isNetworkError抢先于server_permanent导致504不走direct-fallback)——需先看到真实流量里504出现频率再定;
R2. Hermes侧读超时对齐(Hermes ReadTimeout vs Bifrost整体生成时长)——跨组件协调另立SPEC;
R3. streaming partial-timeout(timeout_with_partial直接fallback)——spec §1.2标注本期不实现保留扩展点;

以上三项均标注为【预留】不在本次两日观察期内执行避免范围蔓延。


---

## ⚠️九、决策记录（2026-08-27）

### D1 agnes从fallback链移除 ✅已落地
- **决策**：用户拍板「agnes从fallback中移除」
- **执行**：config.json + SQLite routing_rules 同步修改，两条含agnes的链（rule-s-deepseek-fallback / rule-sensenova-lite-fallback）均移除 `agnes/agnes-2.5-flash`，保留 `scnet → deepseek`
- **验证**：运行时内存态(from_memory=true)确认无任何规则含agnes；Bifrost health ok；PID稳定42025(热加载生效未重启)
- **备份**：config.json.bak-agneremove-20260827-132139

### D2 cooldown_seconds维持300s(5分钟) ✅已确认
- **决策**：用户拍板「cooldown先定为5分钟」
- **现状**：6个key的circuit_breaker.cooldown_seconds全部=300s，已是目标值无需改动
- **语义匹配**：商汤429为瞬时RPM限流(非配额耗尽)，300s冷却与恢复时间不确定的特性匹配；两key独立账户×独立模型额度下rotate_key有效

### D3 P0-A日志补齐 ✅已实现并部署
- **改动**（~15行Go）:
  - core/providers/utils/utils.go DoStreamingRequest TTFB超时分支(case <-timer.C)补 `getLogger().Warn("ttfb timeout after %v for stream request", ttfb)`
  - core/bifrost.go TripKey调用处补 `logger.Warn("circuit breaker tripped key %s ...")`
  - core/bifrost.go "trying fallback provider" Debug→Info
- **验证**: go build ./core/... OK; TestDoStreamingRequest / breaker / TestExecuteRequestWithRetries全绿; make build LOCAL=1成功
- **部署**: /usr/local/bin/bifrost新二进制(PID76432), strings确认三个字符串存在, health ok
- **实测**: journalctl可见 'trying fallback provider sensenova with model s-deepseek-v4-flash'(Info级)
- **备份**: /usr/local/bin/bifrost.bak-p0a-20260827-133155
