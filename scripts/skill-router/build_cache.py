#!/usr/bin/env python3
"""SkillRouter 全量 skill embedding 缓存构建脚本（一次性，后台运行）。

用法:
    python3 scripts/skill-router/build_cache.py

构建 ~/.hermes/models/skillrouter/skill_embeddings.npz（423 条 skill，CPU 约 15-20 分钟）。

增量恢复：每 ENCODE_BATCH(32) 条写一次 checkpoint 到 skill_embeddings.ckpt.npz，
中断后重启会读取已有 checkpoint 跳过已编码批次，不会白跑。
全部完成后写入最终 skill_embeddings.npz 并删除 checkpoint。
"""
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "plugins/knowledge-navigation/src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import backend
import knowledge_navigation.core.skill_matcher as sm

CKPT_PATH = "/root/.hermes/models/skillrouter/skill_embeddings.ckpt.npz"
FINAL_PATH = "/root/.hermes/models/skillrouter/skill_embeddings.npz"
ENCODE_BATCH = 32  # 每批编码条数；checkpoint 写间隔 = ENCODE_BATCH * 2


def load_ckpt() -> tuple[list[str], np.ndarray] | None:
    """读取 checkpoint 中已编码的 (texts, embeddings)。"""
    if not Path(CKPT_PATH).exists():
        return None
    try:
        data = np.load(CKPT_PATH, allow_pickle=True)
        return list(data["texts"]), data["emb"].astype(np.float32)
    except Exception as e:
        print(f"[build] checkpoint 读取失败（忽略，重新构建）: {e}")
        return None


def save_ckpt(texts: list[str], emb: np.ndarray) -> None:
    Path(CKPT_PATH).parent.mkdir(parents=True, exist_ok=True)
    np.savez(CKPT_PATH, emb=emb, texts=np.array(texts, dtype=object))


def main() -> None:
    sm.ensure_index()
    skill_list = sm._get_skill_list()
    all_texts = [f"{s.get('name', '')} {s.get('description', '')}" for s in skill_list]
    total = len(all_texts)
    print(f"[build] 目标: {total} 个 skill")

    # 增量恢复
    done_texts, done_emb = load_ckpt() if Path(CKPT_PATH).exists() else (None, None)
    if done_texts:
        done_set = set(done_texts)
        remaining = [t for t in all_texts if t not in done_set]
        print(f"[build] checkpoint 已有 {len(done_texts)} 条，续编码 {len(remaining)} 条")
    else:
        remaining = list(all_texts)
        done_texts, done_emb = [], np.zeros((0, 1024), dtype=np.float32)

    if not remaining:
        print("[build] checkpoint 已完整，跳过编码")
    else:
        t0 = time.time()
        # 分批编码 + checkpoint
        for i in range(0, len(remaining), ENCODE_BATCH):
            chunk = remaining[i : i + ENCODE_BATCH]
            emb_chunk = backend.embed_texts(chunk, is_query=False, batch_size=16)
            done_texts.extend(chunk)
            done_emb = np.concatenate([done_emb, emb_chunk], axis=0)
            if (i // ENCODE_BATCH) % 2 == 1 or i + ENCODE_BATCH >= len(remaining):
                save_ckpt(done_texts, done_emb)
                print(f"[build] checkpoint @ {len(done_texts)}/{total} ({time.time()-t0:.0f}s)")

    # 完成：写最终缓存，删 checkpoint
    save_ckpt(done_texts, done_emb)  # 确保最终数据在 ckpt
    Path(FINAL_PATH).parent.mkdir(parents=True, exist_ok=True)
    np.savez(FINAL_PATH, emb=done_emb, texts=np.array(done_texts, dtype=object))
    if Path(CKPT_PATH).exists():
        Path(CKPT_PATH).unlink()
    print(f"[build] ✅ 完成: {FINAL_PATH} shape={done_emb.shape} 用时未知（含恢复）")


if __name__ == "__main__":
    main()