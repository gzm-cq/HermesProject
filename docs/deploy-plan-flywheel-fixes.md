# Deploy Plan: Flywheel Code Review Fixes (19 Issues)

**Date**: 2026-07-04
**Commits**: `581658b` (code), `e6a95c8` (docs)
**Status**: ✅ Deployed + Verified (md5sum 17/17)

---

## 1. Scope

19 code review issues from `code-review-report.md`:
- P0 Blockers (3): B-1 PG thread safety, B-2 cache unbounded, B-3 shell syntax
- P1 Suggestions (8): S-1 partial refactor, S-2 pool, S-4 executor, S-5 centroid, S-6 MMR, S-7 cache invalidation, S-9 semaphore, S-11 feedback
- P2 Suggestions (4): S-3 text_utils, S-8 dedup persistence, S-10 prompt YAML, S-11 (counted in P1)
- P3 Nits (5): type annotations, structured logging, heredoc, imports

## 2. Files

### New (3)
| File | Deploy Path |
|------|-------------|
| `core/text_utils.py` | `/root/.hermes/plugins/knowledge-navigation/src/knowledge_navigation/core/text_utils.py` |
| `prompt_loader.py` | `/root/.hermes/scripts/self-evolving/src/self_evolving/prompt_loader.py` |
| `prompts.yaml` | `/root/.hermes/scripts/self-evolving/config/prompts.yaml` |

### Modified (14 source + 3 test)
See git commit `581658b` for full diff.

## 3. Deployment

| Module | Files | Backup | Status |
|--------|-------|--------|--------|
| cron-common | 2 | 20260704-062756 | ✅ |
| cron-wrappers | 15 | 20260704-062824 | ✅ |
| knowledge-navigation | 32 | 20260704-062847 | ✅ gateway restarted |
| knowledge-tree-plugin | 14 | 20260704-063102 | ✅ gateway restarted |
| self-evolving | 28 | 20260704-063004 | ✅ |
| skillopt-runner | 2 | 20260704-063008 | ✅ |

**md5sum verification**: 17/17 source→target match

## 4. Rollback

```bash
bash deploy/deploy.sh rollback knowledge-navigation 20260704-062847
bash deploy/deploy.sh rollback knowledge-tree-plugin 20260704-063102
bash deploy/deploy.sh rollback self-evolving 20260704-063004
bash deploy/deploy.sh rollback skillopt-runner 20260704-063008
bash deploy/deploy.sh rollback cron-wrappers 20260704-062824
bash deploy/deploy.sh rollback cron-common 20260704-062756
```

## 5. Monitoring Points

| Risk | Metric | Where |
|------|--------|-------|
| B-1 thread-local leak | PG connection count | `SELECT count(*) FROM pg_stat_activity` |
| S-4 executor starvation | timeout warnings in log | `journalctl -u hermes-gateway` |
| S-5 centroid cache hit | cache miss rate | trace.log |
| S-9 semaphore queue | extract latency | trace.log |
| B-2 LRU eviction | cache size stable | memory profiling |

## 6. Test Results

| Suite | Passed | Total |
|-------|--------|-------|
| knowledge-navigation | 198 | 198 |
| knowledge-tree-plugin | 47 | 47 |
| self-evolving | 47 | 47 |
| **Total** | **292** | **292** |
