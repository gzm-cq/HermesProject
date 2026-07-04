# Deploy Plan: Flywheel Code Review Fixes (19 Issues)

**Date**: 2026-07-04
**Commits**: 
- `581658b` — 19 code review fixes (B1-B3, S1-S11, N1-N5)
- `e6a95c8` — docs sync
- `40112f9` — deploy plan doc
- `a29e797` — kn-router-health-check 24h window filter
- `bd0138c` — extract _expand_multi_hop
- `89defa0` — extract _execute_recall
- `9207fe2` — extract _assemble_xml_output
- `ac2d6d5` — extract _dedup_and_budget
- `c0f3274` — extract _post_process_recall (final)

**Status**: ✅ Deployed + Verified (md5sum 17/17, 292 tests)

---

## 1. Scope

19 code review issues from `code-review-report.md`:
- P0 Blockers (3): B-1 PG thread safety, B-2 cache unbounded, B-3 shell syntax
- P1 Suggestions (8): S-1 refactor, S-2 pool, S-4 executor, S-5 centroid, S-6 MMR, S-7 cache invalidation, S-9 semaphore, S-11 feedback
- P2 Suggestions (4): S-3 text_utils, S-8 dedup persistence, S-10 prompt YAML
- P3 Nits (5): type annotations, structured logging, heredoc, imports

## 2. S-1 Refactor: pre_llm_call 518→~60 lines

Extracted 7 sub-functions:

| Function | Lines | Purpose |
|----------|-------|---------|
| `_pass_gates` | ~50 | Three-layer gate control + eval bypass |
| `_get_router_mask` | ~20 | Router LLM decision |
| `_execute_recall` | ~95 | 3-way parallel/serial recall dispatch |
| `_expand_multi_hop` | ~25 | Multi-hop knowledge tree expansion |
| `_post_process_recall` | ~185 | Fallback + filtering + boost + causal chain + compression + cross-domain dedup + KT alignment |
| `_dedup_and_budget` | ~90 | Turn-to-turn dedup + text dedup + token budget |
| `_assemble_xml_output` | ~105 | XML tag assembly + logging + return |

**pre_llm_call body**: ~60 lines (88% reduction)

## 3. Health Check Fix

`kn-router-health-check.sh` recall statistics: grep -c over full trace.log → python3 JSON parser filtering by timestamp (24h window). Fixed: commit `a29e797`.

## 4. Files

### New (3)
| File | Deploy Path |
|------|-------------|
| `core/text_utils.py` | `/root/.hermes/plugins/knowledge-navigation/src/knowledge_navigation/core/text_utils.py` |
| `prompt_loader.py` | `/root/.hermes/scripts/self-evolving/src/self_evolving/prompt_loader.py` |
| `prompts.yaml` | `/root/.hermes/scripts/self-evolving/config/prompts.yaml` |

### Modified (14 source + 3 test)
See git commits above for full diff.

## 5. Deployment

| Module | Files | Backup | Status |
|--------|-------|--------|--------|
| cron-common | 2 | 20260704-062756 | ✅ |
| cron-wrappers | 15 | 20260704-070040 | ✅ |
| knowledge-navigation | 32 | 20260704-104138 | ✅ gateway restarted |
| knowledge-tree-plugin | 14 | 20260704-063102 | ✅ gateway restarted |
| self-evolving | 28 | 20260704-063004 | ✅ |
| skillopt-runner | 2 | 20260704-063008 | ✅ |

**md5sum verification**: 17/17 source→target match

## 6. Rollback

```bash
bash deploy/deploy.sh rollback knowledge-navigation 20260704-104138
bash deploy/deploy.sh rollback knowledge-tree-plugin 20260704-063102
bash deploy/deploy.sh rollback self-evolving 20260704-063004
bash deploy/deploy.sh rollback skillopt-runner 20260704-063008
bash deploy/deploy.sh rollback cron-wrappers 20260704-070040
bash deploy/deploy.sh rollback cron-common 20260704-062756
```

## 7. Test Results

| Suite | Passed | Total |
|-------|--------|-------|
| knowledge-navigation | 198 | 198 |
| knowledge-tree-plugin | 47 | 47 |
| self-evolving | 47 | 47 |
| **Total** | **292** | **292** |

## 8. Remaining Work

1. ✅ **Smoke test** — pending (no production traffic since deploy)
2. ✅ **Unit tests for _post_process_recall and _dedup_and_budget** — pending
3. ❌ **SiliconFlow rerank 400** — not in scope (knowledge-tree-builder cli.py, unchanged)
