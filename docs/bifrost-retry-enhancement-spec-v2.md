# Bifrost Retry/Fallback/熔断增强 — 实施 SPEC v2

## 目标
让 retry/fallback/熔断策略可配置、可组合、向后兼容，核心诉求：
**「先试完本 provider 所有 key，全部失败才走 routing rule fallback」**

## 现状（已逐行验证）
- `core/schemas/provider.go` L585-633: RetryAction(3种) + ServerPermanentAction(2种) + RetryPolicy
- `core/bifrost.go` L6060-6097: resolveRetryAction — error class → action (硬编码)
- `core/bifrost.go` L6241-6247: executeRequestWithRetries 循环退出条件
- `core/bifrost.go` L6763-6776: rotationBudget (max_retries=0才增长)
- `transports/config.schema.json` L1088-1145: retry_policy schema
- **约束**: TestConfigSchemaSync (config_test.go L18263) 强制 schema↔Go struct 同步

## Step S1: ErrorClassifier v2 — timeout独立分类 + actions策略表

### S1.1 core/schemas/provider.go — RetryPolicy v2 struct

```go
// ErrorClass identifies the failure category for policy resolution.
type ErrorClass string

const (
    ErrorClassNetwork         ErrorClass = "network"
    ErrorClassRateLimit       ErrorClass = "rate_limit"
    ErrorClassAuth            ErrorClass = "auth"
    ErrorClassTimeout         ErrorClass = "timeout"      // NEW: TTFB超时/408/504-timeout独立出来
    ErrorClassTransientServer ErrorClass = "transient_server"
    ErrorClassPermanentRequestErrorClass = "permanent_request"
    ErrorClassUnknown         ErrorClass = "unknown"
)

// ActionSpec maps an error class to a concrete behavior.
type ActionSpec struct {
    Action       RetryActionV2 `json:"action,omitempty"`
    TripCircuit *bool          `json:"trip_circuit,omitempty"` // nil=default per class
}

// RetryActionV2 extends the legacy three-state action with try_all_keys.
type RetryActionV2 string

const (
    ActionRetrySameKey        RetryActionV2 = "retry_same_key"
    ActionRotateNextKey       RetryActionV2 = "rotate_next_key"
    ActionTryAllKeysThenFallback RetryActionV2 = "try_all_keys_then_fallback" // NEW
    ActionDirectFallback      RetryActionV2 = "direct_fallback"
)

// Legacy mapping:
//   rate_limit_action=rotate_key → actions["rate_limit"]={action:"rotate_next_key", trip_circuit:true}
//   server_permanent_action=retry → actions["timeout"]={action:"retry_same_key"}
//   server_permanent_action=direct_fallback → actions["permanent_request"]={action:"direct_fallback"}
```

### S1.2 core/utils.go — error sets扩展

```go
var timeoutStatusCodes = map[int]bool{408: true} // request timeout; TTFB超时单独识别(无status或504+ErrProviderRequestTimedOut)
```

### S1.3 core/bifrost.go — classifyError()新函数

```go
func classifyError(bifrostError *schemas.BifrostError) schemas.ErrorClass {
    if isNetworkError(bifrostError) { return schemas.ErrorClassNetwork }
    if isRateLimitError(bifrostError) { return schemas.ErrorClassRateLimit }
    if isAuthError(bifrostError) { return schemas.ErrorClassAuth }
    if isTimeoutError(bifrostError) { return schemas.ErrorClassTimeout } // NEW, before transient check!
    
    注意顺序：timeout必须在transient之前检查（504同时属于两者）
    
    判断isTimeout:
      - bifrostError.StatusCode==408 
      - OR bifrostError.Error.Type==RequestTimedOut (TTFB超时NewBifrostTimeoutError设置此Type)
      - OR message含"request timed out"/ErrProviderRequestTimedOut
    
    然后:
      if isTransientServerError(...) { return TransientServer }
      if isServerPermanent... 
}
```

### S1.4 resolveRetryAction v2 —策略表驱动

```go
func resolveRetryAction(err *schemas.BifrostError, config *schemas.ProviderConfig) (schemas.RetryAction, bool, bool /*tripCircuit*/) {
    
    返回三元组(action, isPerKeyFailure, tripCircuit)
    
    逻辑:
      1. classify err → class
      2. rp := config.RetryPolicy; rp!=nil && rp.Enabled?
         YES → look up rp.Actions[class] → spec.Action / spec.TripCircuit(default per-class table)
         NO  → legacy hardcoded mapping (preserve current behavior exactly!)
      
      3. map V2 action back to legacy tri-state for downstream loop compat:
         retry_same_key              → legacy retriable(false), trip=false(default transient no trip)
         rotate_next_key             → legacy rotate(true), trip=true(default rate-limit/auth trip)
         try_all_keys_then_fallback  → special flag on context; loop treats as rotate until pool exhausted then break→fallback; trip=false unless class default says otherwise.
         direct_fallback             → legacy direct(false), no loop entry.
}
```

## Step S3: executeRequestWithRetries循环改造（核心）

### S3.1 budget计算重构（替换L6229-L6239）

```go
effectiveMaxAttempts := effectiveMaxRetries + extraAttempts + rotationBudget // legacy path unchanged when TryAllKeys not set.

tryAllKeys := false
for _, spec := range activeActions {
   if spec.Action == TryAllKeysThenFallback { tryAllKeys=true; break }
}

loopBudget := effectiveMaxAttempts // default legacy semantics preserved.

When tryAllKeys mode active for the current error class:
   budget becomes dynamic per iteration:
   eligiblePoolSize := count of keys in supportedKeys that are NOT dead AND NOT tripped AND NOT used this cycle.
   
   exit condition changes from fixed attempts>budget to:
     exit when len(eligiblePoolSize)==0 && usedKeyIDs covers all pool keys that were ever eligible,
     i.e., every key has been tried at least once and none succeeded.
     
Implementation approach (minimal invasive):
   Keep existing loop structure & attempt counter & used/dead/tripped bookkeeping EXACTLY as-is.
   Only change the exit-check expression at top of loop:

   ```go
   for attempts=0;;attempts++{
       shouldExit := attempts > effectiveMaxRetries+extraAttempts+rotationBudget // LEGACY default
        
       if tryAllKeysModeForThisRequest {
           shouldExit = !hasAnyEligibleUntriedKey(pool, usedKeyIDs, deadKeyIDs, breakerMgr)
           // where hasAnyEligibleUntriedKey returns true iff exists k in pool with !dead[k.ID] && !used[k.ID] && !breaker.IsTripped(k.ID).
           //
           // NOTE subtlety: after a full cycle where every key got tried once and all failed,
           // usedKeyIDs contains all pool IDs -> hasAnyEligibleUntriedKey=false -> exit -> fallback chain runs.
           //
           // If some keys are breaker-tripped they're excluded from eligibility but ALSO not counted as 'tried'
           // so we don't spin forever trying them again within same request? Actually tripped keys are skipped by selection anyway;
           // they just reduce eligible set size naturally via IsTripped filter inside keyProvider closure already present at L7111/L7131.
           
           边界情况A(single-key pool): len(pool)==1 => after first fail used={k} => eligible empty => exit immediately => fallback chain runs once instead of looping forever on same single key with max_retries=N? 
                This matches user intent? For single-key provider user probably wants max_retries honored rather than immediate fallback...
                Decision: TRY_ALL mode only activates when len(pool)>1 AND configured explicitly via policy field try_all_keys_before_fallback=true OR any action==try_all_keys_then_fallback present in enabled policy's actions map for the matched class.
                
           边界情况B(session stickiness canRotate=false): supportedKeys returned as single-element [sticky], canRotate=false => TRY_ALL degrades to LEGACY_BUDGETED automatically because len(pool)==1 triggers boundary A guard above... but sticky means we WANT same-key retries honoring max_retries not immediate fallback! So guard must be 'len(pool)>1' not 'canRotate'. Sticky returns len==1 so guard correctly keeps LEGACY path with its own budget semantics unchanged.

           边界情况C(errAllKeysDead sentinel): keyProvider returns errAllKeysDead when all dead/tripped => handled by existing early-exit at L6322 returning upstream_credentials_exhausted BEFORE reaching our new exit check? Need verify ordering... The err comes from select step inside loop body which breaks out via continue->next iteration->top-of-loop check sees shouldExit true because eligible empty -> exits normally -> falls through to routing rule fallbacks instead of returning err directly?? That would CHANGE behavior vs today where errAllKeysDead short-circuits to immediate response without trying routing rules!
                MUST preserve today's semantics here!! Today when all keys dead/tripped it returns upstream_credentials_exhausted immediately WITHOUT consulting routing rules?? Let me re-read code around select failure handling...
                
                From earlier read at L6304-L6335: errors.Is(err,errAllKeysDead)->return zero,&BifrostError{502 upstream_credentials_exhausted}. That RETURN exits executeRequestWithRetries entirely -> caller then decides whether AllowFallbacks routes further or surfaces directly. So TODAY err-all-dead does NOT auto-continue into routing rules from within this function; it bubbles up and caller's inference layer handles fallbacks separately based on AllowFallbacks flag & routing rule config.

                Therefore our TRY_ALL change must keep this contract intact! When every key genuinely failed with per-key errors AND none left untried/un-tripped/un-dead => we should surface same terminal state that today's code produces after exhausting rotationBudget... which today IS falling through to normal completion path (bifrost_error non-nil returned upward). The distinction between 'all dead' vs 'all tried-but-failed-transiently' matters only for status code choice downstream.

                Simplest correct approach preserving ALL existing contracts:
                   Do NOT touch select-failure early returns AT ALL.
                   Only relax the TOP-OF-LOOP budget check under explicit opt-in flag,
                   keeping everything else byte-for-byte identical.

                   具体实现：
                   ```
                   var tryAll bool 
                   ...compute from policy...
                   在循环顶部：
                   if tryAll && attempts>0 {
                       stillHaveCandidate := false
                       for _,k:=range supportedPoolSnapshot {
                           if !dead[k.ID]&&!used[k.ID]&&!breaker.IsTripped(k.ID){stillHaveCandidate=true;break}
                       }
                       shouldExitNow := !stillHaveCandidate || attempts >= maxTryAllCap(len(supportedPoolSnapshot)+extraAttempts+rotationBudget?)  
                       其中maxTryAllCap防止无限循环：上限=max(len(pool), configured_max_attempts_if_set)+extra。
                       当 stillHaveCandidate==false => break正常结束=>bubble up=>routing rules按AllowFallbacks处理。
                       当 stillHaveCandidate==true但attempts已达cap=>break同样结束。
                       两种都保持与今天一致的向上冒泡语义。
                   } else {
                       shouldExitNow := attempts > effectiveMaxRetries+extraAttempts+rotationBudget  
                       ...legacy exact...
                   }
                   ```
                   这样TRY_ALL只是放宽了预算上限到pool大小，其余一切不变——最安全最小侵入。
```

### S3.2 rotationBudget逻辑(L6767)——保持不动！
因为TRY_ALL模式不依赖rotationBudget；LEGACY路径完全不变。零回归风险。

## Step S4: Circuit Breaker v3 — per-error-class trip决策 + release策略(可选增强)

本期最小实现：仅支持actions[].trip_circuit布尔开关控制是否对某error class触发TripKey。
release/half-open/consecutive-threshold留作v4。

改动点：
- resolveRetryAction返回的tripCircuit传入现有TripKey调用点(L6728/L6787)。
- schema新增circuit_breaker_v3字段？NO——复用现有circuit_breaker.cooldown_seconds即可；只加actions[].trip_circuit控制何时trip。

## Step S5: Schema同步(config.schema.json)

在现有retry_policy对象内新增字段(向后兼容，旧字段保留):

```jsonc
{
  "enabled": true,
  
  /* ---- NEW ---- */
  
  

  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
  
}
```
