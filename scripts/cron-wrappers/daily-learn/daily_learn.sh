#!/bin/bash
# 每日在线学习 — 自动从网上收集AI/CS知识入树
#
# 部署路径: /root/.hermes/scripts/daily-learn/daily_learn.sh
# 依赖公共库: /root/.hermes/lib/cron_common.sh（由 cron-common 项目部署）
# 调度建议:   0 3 * * * (每日 03:00)
set -euo pipefail

# ===== 加载公共库 =====
_CRON_LIB="${CRON_LIB:-/root/.hermes/lib/cron_common.sh}"
if [[ -f "$_CRON_LIB" ]]; then
    # shellcheck disable=SC1090
    source "$_CRON_LIB"
else
    echo "错误：找不到 cron_common.sh (${_CRON_LIB})" >&2
    echo "请先部署 cron-common 项目: deploy.sh deploy cron-common" >&2
    exit 2
fi

# ===== 初始化 =====
cron_init "daily-learn"

TMP_DIR=$(mktemp -d /tmp/daily-learn-XXXX)
trap 'rm -rf "$TMP_DIR"' EXIT

KB_DIR="/root/.hermes/scripts/knowledge-tree-builder"
DATE=$(date +%Y-%m-%d)
export TMP_DIR DATE

# ===== 步骤 1: ArXiv 最新论文 =====
cron_section "ArXiv 论文收集"
python3 << PYEOF
import urllib.request, xml.etree.ElementTree as ET, os

DATE = os.environ.get('DATE')
TMP = os.environ.get('TMP_DIR')

url = 'https://export.arxiv.org/api/query?search_query=cat:cs.AI+OR+cat:cs.LG&sortBy=submittedDate&sortOrder=descending&max_results=5'
try:
    resp = urllib.request.urlopen(url, timeout=30)
    root = ET.fromstring(resp.read())
except Exception as e:
    print(f'  ⚠ ArXiv 拉取失败: {e}，跳过')
    root = ET.fromstring('<feed xmlns="http://www.w3.org/2005/Atom"></feed>')
ns = {'a': 'http://www.w3.org/2005/Atom'}

entries = root.findall('a:entry', ns)
for i, entry in enumerate(entries):
    title = entry.find('a:title', ns).text.strip().replace('\n', ' ')
    summary = entry.find('a:summary', ns).text.strip()[:500]
    authors = ', '.join(a.find('a:name', ns).text for a in entry.findall('a:author', ns)[:3])
    
    md = f"""# ArXiv {i+1}: {title}

**来源:** ArXiv
**日期:** {DATE}
**作者:** {authors}

{summary}
"""
    path = os.path.join(TMP, f'arxiv-{i+1}.md')
    with open(path, 'w') as f:
        f.write(md)
    print(f'  ✓ {title[:60]}...')

print(f'  拉取 {len(entries)} 篇')
PYEOF

_arxiv_count=$(grep -c '✓' "$CRON_LOG_FILE" 2>/dev/null || echo 0)
cron_ok "ArXiv 收集完成"
_STEP_RESULTS+=("✅ ArXiv 论文收集")

# ===== 步骤 2: GitHub Trending =====
cron_section "GitHub Trending 收集"
python3 << PYEOF
import urllib.request, json, os

TMP = os.environ.get('TMP_DIR')
api_url = 'https://api.github.com/search/repositories?q=topic:ai+topic:llm&sort=stars&order=desc&per_page=3'
headers = {'Accept': 'application/vnd.github.v3+json'}
github_token = os.environ.get('GITHUB_TOKEN', '')
if github_token:
    headers['Authorization'] = f'token {github_token}'
req = urllib.request.Request(api_url, headers=headers)
try:
    resp = urllib.request.urlopen(req, timeout=30)
    data = json.loads(resp.read())
except Exception as e:
    print(f'  ⚠ GitHub 拉取失败: {e}，跳过')
    data = {'items': []}

for i, repo in enumerate(data.get('items', [])[:3]):
    name = repo['full_name']
    desc = repo.get('description', '') or ''
    stars = repo.get('stargazers_count', 0)
    repo_url = repo.get('html_url', '')
    
    md = f"""# GitHub: {name}

**来源:** GitHub Trending
**日期:** {os.environ.get('DATE')}
**描述:** {desc}
**Stars:** {stars}
**链接:** {repo_url}
"""
    path = os.path.join(TMP, f'github-{i+1}.md')
    with open(path, 'w') as f:
        f.write(md)
    print(f'  ✓ {name} ({stars}★)')

print(f'  拉取 {min(len(data.get("items",[])), 3)} 个')
PYEOF

cron_ok "GitHub Trending 收集完成"
_STEP_RESULTS+=("✅ GitHub Trending 收集")

# ===== 步骤 3: 提取入树 =====
cron_section "知识树入库"
cd "$KB_DIR"
source venv/bin/activate

# .env 加载已由 cron_common.sh 在 source 时统一处理，无需重复加载

: "${KT_DB_URL:?KT_DB_URL is required. Set it in /root/.hermes/.env}"
: "${LITELLM_MASTER_KEY:?LITELLM_MASTER_KEY is required. Set it in /root/.hermes/.env}"

if python3 -m knowledge_tree_builder.cli run \
  --input-dir "$TMP_DIR" \
  --merged -j 2 2>&1 | tail -5; then
    cron_ok "知识树入库完成"
    _STEP_RESULTS+=("✅ 知识树入库")
else
    rc=$?
    cron_err "知识树入库失败 (exit=$rc)"
    _STEP_RESULTS+=("❌ 知识树入库 (exit=$rc)")
fi

# ===== 完成 =====
cron_finish
