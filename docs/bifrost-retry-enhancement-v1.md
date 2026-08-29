# Bifrost Retry/Fallback/熔断增强方案 v1

## 背景与问题

当前 Bifrost retry_policy 已支持 rate_limit_action / server_permanent_action / max_retries_override / backoff，
但存在以下局限（2026-08-27 实测验证）：

### L1: rotationBudget 与 max_retries 互斥（已确认）
- `max_retries=0` → rotationBudget 自动增长 → **逐个试完所有 key**
- `max_retries=1` → rotationBudget 永不增长 → **只试固定次数就 fallback**
- sensenova (3 key × model) + max_retries=1 = key1→key2→fallback，**key3没试**

### L2: TTFB超时/504不能 rotate_key（已确认）
- TTFB超时 → NewBifrostTimeoutError(StatusCode=504)
- retry_policy.enabled + isServerPermanentError(504) → resolveServerPermanentAction
- ServerPermanentAction **类型系统禁止 rotate_key**（L6108注释："rotate_key not valid for this class"）
- TTFB卡60s后只能重试同key或直接fallback，不能换到另一个商汤key

### L3: action只有3种原子动作（设计局限）
```
Retryable(retry same key) | RotateKey(retry diff key) | DirectFallback(skip loop)
```
没有组合语义："先rotate完所有可用key，再fallback"

### L4: fallback链是路由规则级配置
routing_rules.fallbacks = [provider/model, ...]，与 retry_policy (provider级)分离。
无法表达"本provider内多key都失败后才走fallback链"的完整语义。

## 目标

设计一个可配置、可组合、向后兼容的 retry/fallback/熔断策略引擎：

```
[请求失败]
   │
   ├─► error_classifier ──► {rate_limit, auth, timeout, transient, permanent}
   │        │                    （可扩展自定义分类器）
   │        ▼
   │   policy_resolver ──► action = f(error_class, policy)
   │        │                    （策略表驱动：error_class × condition → action）
   │        ▼
   ├─► action_executor ──► { retry_same_key | rotate_next_key | try_all_keys_then_fallback |
   │                          direct_fallback | circuit_trip }
   │        ▲
   └────────┘   循环直到 budget_exhausted OR success OR fallback

budget = f(max_attempts, max_keys_to_try, per_error_class_budget)
```

## 设计方案

### A. RetryPolicy v2 — schema扩展（向后兼容）

```jsonc
{
  "enabled": true,
  
  // ---- A1. error class → action mapping (策略表驱动) ----
  "actions": {
    "rate_limit":      {"action": "rotate_next_key", "trip_circuit": true},
    "auth":            {"action": "rotate_next_key", "trip_circuit": true},
    "timeout":         {"action": "try_all_keys_then_fallback", "trip_circuit": false},
    "transient_server":{"action": "retry_same_key"},
    "permanent_request":{"action":"direct_fallback"}
    // custom: {"match_status":[400],"match_type":"invalid_request","action":"direct_fallback"}
  },
  
  // ---- A2. budget控制 ----
  // max_keys_to_try > max_attempts? try_all_keys优先 : attempt预算优先
  // nil = provider默认(max_attempts=max_retries+1)
  
}
```

### B. Error Classifier v2 —可扩展分类器链

```go
type ErrorClassifier interface {
    Classify(err *schemas.BifrostError) ErrorClass
}

// built-in chain:
// network_error > rate_limit > auth > timeout > transient_server > permanent_request > unknown

// config-driven custom classifiers:
// [{name:"custom_timeout", match:{status:[408], type:"request_timeout"}, class:"timeout"}]
```

### C. Action Executor v2 —组合动作原语

```go
type Action int64 // bitmask组合?
const (
    RetrySameKey       Action = iota // attempt+1 on same key (with backoff)
    RotateNextKey                   // select next non-tried/non-tripped key (with backoff)
    TryAllKeysThenFallback          // exhaust all eligible keys in pool before falling back; 
                                    // respects circuit breaker tripped keys as excluded but NOT as terminal;
                                    // if all keys tripped/dead -> fallback immediately without extra attempts beyond budget cap.
                                    //
                                    // Semantics vs current behavior:
                                    // - current: rotate until usedKeyIDs/deadKeyIDs exhausted OR attempts>budget -> break -> fallback.
                                    // - new:     same loop but budget is derived from len(pool)+extraAttempts instead of fixed MaxRetries,
                                    //            so with N keys you get up to N attempts before fallback.
)

type PolicyDecision struct {
    Action       Action
    TripCircuit bool     // whether to trip the breaker for this error class on this key after failure.
}
```

关键实现点：
- `TryAllKeysThenFallback` = 「在当前 provider pool内轮换所有 eligible keys；全部失败才进入 routing rule fallbacks」
- budget计算改为 `effectiveMaxAttempts := min(maxAttemptsOverride?, len(pool)+extra)` 
- circuit breaker tripped keys在 TryAllKeysThenFallback下视为「排除但非终止」——若 pool中还有未tripped key则继续；全tripped则立即fallback。

### D. Circuit Breaker v2 —按需 trip + release策略

现状：TripKey(keyID, cooldownSeconds)，cooldown期间该slot被跳过。
增强：
```jsonc
{
  "circuit_breaker_v2": {
     "cooldown_on_rate_limit_ms":300000,
     ...
     /* per-error-class trip decision comes from actions[].trip_circuit */
     /* optional half-open probe after cooldown */
     /* optional consecutive-failure threshold before trip (default: trip on first failure of that class)*/
     /* optional success-threshold to reset counter */
     /* optional recovery mode: allow one probe request through during cooldown every X seconds */
     
     /* NEW granularity control */
     ...
     
     
     
     
     
     
     
     
     
     
      }
}
```

## E. Fallthrough semantics —「先试完本provider多key再走routing rule fallbacks」

这是本次最核心的用户诉求。当前行为：
```
request s-glm-5.2 -> sensenova provider -> select keyA -> fail -> [budget] -> break -> routing rule fb[0]...
```
期望行为：
```
request s-glm-5.2 -> sensenova provider -> select keyA(fail)->rotate->keyB(fail)->rotate->keyC(fail)-> ALL KEYS EXHAUSTED ->
routing rule fb[0]...
```

实现位置：executeRequestWithRetries循环退出条件改造。
当前退出条件(L6245): `attempts > effectiveMaxRetries+extraAttempts+rotationBudget`
新退出条件需要区分两种模式：
```go
mode := config.RetryPolicyV2.TryAllKeysBeforeFallback ? TRY_ALL : LEGACY_BUDGETED

switch mode {
case TRY_ALL:
    exit when len(nonTrippedNonDeadEligibleKeys)==0 && usedKeyIDs covers all eligible keys in pool.
case LEGACY_BUDGETED:
    exit when attempts > effectiveMaxRetries+extraAttempts+rotationBudget.
}
```

注意边界情况：
1. pool中只有一个eligible key且它tripped了 => TRY_ALL模式下立即exit->fallback（无其他可选）。
2. session stickiness pin了某个fixedKey => canRotate=false => TRY_ALL退化为LEGACY_BUDGETED对单键重试。
3. errAllKeysDead(errors.Is sentinel)=>立即exit->502 upstream credentials exhausted->routing rule可能继续?需确认AllowFallbacks语义。

## F. Config schema & UI支持范围决策

| item | scope |
|---|---|
| RetryPolicyV2 JSON schema | core/schemas/provider.go + transports/config.schema.json |
| config.json热加载 | source_of_truth=true已有机制 |
| UI表单编辑 | governance providers页面需新增字段渲染(vite React),本期可选 |

## G. Backward compatibility保证

| old field | new behavior |
|---|---|
| rate_limit_action=rotate_key | maps to actions["rate_limit"]={action:"rotate_next_key"} |
| server_permanent_action=direct_fallback | maps to actions["permanent_request"]={action:"direct_fallback"} |
| max_retries=N (>0) legacy path unchanged; N==0 legacy rotationBudget path unchanged unless TryAllKeysBeforeFallback=true explicitly set |

## H . Testing plan (pytest/go test)

新增单测文件 core/bifrost_test.go additions:
1.TestResolveRetryActionV2_RateLimitRotateNextKey_TripsCircuitOn429And401403402ButNotOnTimeoutOrTransient5xxOrNetworkErrorOrRequestBound4xxOrUnknownStatuses... etc.

实际测试用例清单见 spec §9.

---

*注：以上为v1草案。待用户review后细化每步实施计划。*
