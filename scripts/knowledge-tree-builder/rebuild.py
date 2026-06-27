"""全量重建 — 委托 CLI 运行多个目录"""
import os, sys, subprocess

os.environ['KT_DB_URL'] = 'postgresql://postgres@127.0.0.1:5434/hindsight'
os.environ['LITELLM_MASTER_KEY'] = 'sk-lit...2026'

# 所有源目录
SOURCE_DIRS = [
    '/mnt/c/Users/1/Desktop/AI/my-knowledge/axiom/wiki/pages/analyses/context-engineering',
    '/mnt/c/Users/1/Desktop/AI/my-knowledge/axiom/wiki/pages/analyses/runtime',
    '/mnt/d/HermesProject/.qoder/repowiki/zh/content',
]

for src_dir in SOURCE_DIRS:
    print('\n=== %s ===' % src_dir.rsplit('/', 1)[-1], flush=True)
    result = subprocess.run(
        [sys.executable, '-m', 'knowledge_tree_builder.cli', 'run',
         '--input-dir', src_dir,
         '--config', 'config/default.yaml',
         '--merged', '--verbose'],
        cwd='/mnt/d/HermesProject/scripts/knowledge-tree-builder',
        capture_output=False,
    )
    if result.returncode != 0:
        print('  exit code: %d' % result.returncode, flush=True)

print('\n全量重建完成', flush=True)
