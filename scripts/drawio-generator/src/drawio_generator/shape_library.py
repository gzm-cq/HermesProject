"""形状库 — 40+ 形状索引 + 模糊搜索

索引结构: {
  shape_id: {
    "shape": str,        # 对应 shapes.py SHAPES 中的键
    "name": str,         # 中文名
    "keywords": [str],   # 同义词/别名
    "category": str,     # 分类
    "drawio": str,       # drawio style 字串
    "svg_render": bool,  # 是否有 SVG 渲染支持
  }
}
"""
from copy import deepcopy
from difflib import SequenceMatcher
import re

# 预编译正则，用于 _normalize 高频调用
_NORMALIZE_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")

# ===== 形状索引 =====
SHAPE_LIBRARY = {
    # ---- 基础矩形 ----
    "rect": {
        "shape": "rect",
        "name": "矩形",
        "keywords": ["rect", "rectangle", "box", "方形", "基础", "default", "块"],
        "category": "basic",
        "drawio": "rounded=1;whiteSpace=wrap;html=1;",
        "svg_render": True,
    },
    "card": {
        "shape": "card",
        "name": "圆角卡片",
        "keywords": ["card", "圆角", "rounded", "面板", "panel", "container", "展示", "ui"],
        "category": "basic",
        "drawio": "shape=card;whiteSpace=wrap;html=1;",
        "svg_render": True,
    },
    "process": {
        "shape": "process",
        "name": "流程步骤",
        "keywords": ["process", "步骤", "流程", "处理", "workflow", "step", "操作"],
        "category": "flowchart",
        "drawio": "shape=process;whiteSpace=wrap;html=1;",
        "svg_render": True,
    },

    # ---- 流程图类 ----
    "rhombus": {
        "shape": "rhombus",
        "name": "决策菱形",
        "keywords": ["diamond", "rhombus", "decision", "判断", "条件", "分支", "if", "判定"],
        "category": "flowchart",
        "drawio": "shape=rhombus;whiteSpace=wrap;html=1;",
        "svg_render": True,
    },
    "hexagon": {
        "shape": "hexagon",
        "name": "六边形",
        "keywords": ["hex", "hexagon", "六角", "准备", "prepare", "流程开始前"],
        "category": "flowchart",
        "drawio": "shape=hexagon;perimeter=hexagonPerimeter2;whiteSpace=wrap;html=1;",
        "svg_render": True,
    },
    "parallelogram": {
        "shape": "parallelogram",
        "name": "平行四边形 (I/O)",
        "keywords": ["io", "input", "output", "parallelogram", "输入", "输出", "数据"],
        "category": "flowchart",
        "drawio": "shape=parallelogram;perimeter=parallelogramPerimeter;whiteSpace=wrap;html=1;",
        "svg_render": True,
    },
    "step": {
        "shape": "step",
        "name": "步骤箭头",
        "keywords": ["arrow", "step", "箭头", "推进", "next", "方向", "阶段"],
        "category": "flowchart",
        "drawio": "shape=step;whiteSpace=wrap;html=1;",
        "svg_render": True,
    },
    "document": {
        "shape": "document",
        "name": "文档",
        "keywords": ["doc", "document", "文件", "报告", "report", "pdf", "输出文档"],
        "category": "flowchart",
        "drawio": "shape=document;whiteSpace=wrap;html=1;",
        "svg_render": True,
    },

    # ---- 数据/存储类 ----
    "cylinder": {
        "shape": "cylinder",
        "name": "数据库",
        "keywords": ["db", "database", "cylinder", "存储", "storage", "mysql", "postgres", "nosql", "圆柱"],
        "category": "data",
        "drawio": "shape=cylinder;whiteSpace=wrap;html=1;",
        "svg_render": True,
    },
    "note": {
        "shape": "note",
        "name": "便签",
        "keywords": ["note", "sticky", "备注", "注释", "todo", "便签", "贴"],
        "category": "data",
        "drawio": "shape=note;whiteSpace=wrap;html=1;",
        "svg_render": True,
    },
    "cube": {
        "shape": "cube",
        "name": "3D 立方",
        "keywords": ["cube", "3d", "三维", "立体", "box3d", "模块", "组件"],
        "category": "data",
        "drawio": "shape=cube;whiteSpace=wrap;html=1;",
        "svg_render": True,
    },
    "cloud": {
        "shape": "cloud",
        "name": "云服务",
        "keywords": ["cloud", "云", "aws", "阿里云", "互联网", "公有云", "私有云", "外部服务"],
        "category": "data",
        "drawio": "shape=cloud;whiteSpace=wrap;html=1;",
        "svg_render": True,
    },

    # ===== 新增 28+ 形状（drawio 原生 style，SVG 回退 rect 或多边形近似） =====
    # ---- 流程图补充 ----
    "terminator": {
        "shape": "rect",  # SVG 回退
        "name": "开始/结束",
        "keywords": ["start", "end", "terminator", "开始", "结束", "椭圆形", "oval", "ellipse"],
        "category": "flowchart",
        "drawio": "ellipse;whiteSpace=wrap;html=1;",
        "svg_render": False,
    },
    "delay": {
        "shape": "rect",
        "name": "延迟",
        "keywords": ["delay", "wait", "timer", "等待", "延迟", "sleep"],
        "category": "flowchart",
        "drawio": "shape=delay;whiteSpace=wrap;html=1;",
        "svg_render": False,
    },
    "data": {
        "shape": "parallelogram",
        "name": "数据 (平行四边形)",
        "keywords": ["data", "数据", "输入输出", "parallelogram", "flow_data"],
        "category": "flowchart",
        "drawio": "shape=parallelogram;perimeter=parallelogramPerimeter;whiteSpace=wrap;html=1;",
        "svg_render": True,
    },
    "decision2": {
        "shape": "rhombus",
        "name": "多条件决策",
        "keywords": ["switch", "case", "多分支", "多路", "decision2", "switch_case"],
        "category": "flowchart",
        "drawio": "shape=decision;whiteSpace=wrap;html=1;",
        "svg_render": True,
    },
    "stored_data": {
        "shape": "cylinder",
        "name": "存储数据",
        "keywords": ["stored", "file", "文件", "存储", "disk", "磁盘"],
        "category": "data",
        "drawio": "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=15;",
        "svg_render": True,
    },
    "direct_data": {
        "shape": "rect",
        "name": "直接访问存储",
        "keywords": ["das", "direct", "直接", "磁盘阵列", "raid"],
        "category": "data",
        "drawio": "shape=datastore;whiteSpace=wrap;html=1;",
        "svg_render": False,
    },

    # ---- 网络/架构类 ----
    "server": {
        "shape": "cube",
        "name": "服务器",
        "keywords": ["server", "服务器", "host", "主机", "backend", "后端", "服务端"],
        "category": "network",
        "drawio": "shape=mxgraph.azure.vm;whiteSpace=wrap;html=1;",
        "svg_render": True,
    },
    "database_server": {
        "shape": "cylinder",
        "name": "数据库服务器",
        "keywords": ["db_server", "数据库服务", "oracle", "sql server", "rds"],
        "category": "network",
        "drawio": "shape=mxgraph.azure.sqlDatabase;whiteSpace=wrap;html=1;",
        "svg_render": True,
    },
    "client": {
        "shape": "rect",
        "name": "客户端",
        "keywords": ["client", "客户端", "frontend", "前端", "browser", "浏览器", "app"],
        "category": "network",
        "drawio": "shape=mxgraph.azure.vmClassic;whiteSpace=wrap;html=1;",
        "svg_render": False,
    },
    "router": {
        "shape": "hexagon",
        "name": "路由器",
        "keywords": ["router", "路由", "gateway", "网关"],
        "category": "network",
        "drawio": "shape=mxgraph.cisco19.router;whiteSpace=wrap;html=1;",
        "svg_render": False,
    },
    "switch": {
        "shape": "rect",
        "name": "交换机",
        "keywords": ["switch", "交换机", "hub", "网桥"],
        "category": "network",
        "drawio": "shape=mxgraph.cisco19.genericSwitch;whiteSpace=wrap;html=1;",
        "svg_render": False,
    },
    "firewall": {
        "shape": "rect",
        "name": "防火墙",
        "keywords": ["firewall", "fw", "安全", "waf"],
        "category": "network",
        "drawio": "shape=mxgraph.cisco19.firewall;whiteSpace=wrap;html=1;",
        "svg_render": False,
    },
    "lb": {
        "shape": "rect",
        "name": "负载均衡器",
        "keywords": ["lb", "load balancer", "负载均衡", "nginx", "haproxy", "elb"],
        "category": "network",
        "drawio": "shape=mxgraph.azure.loadBalancer;whiteSpace=wrap;html=1;",
        "svg_render": False,
    },
    "dns": {
        "shape": "note",
        "name": "DNS 服务",
        "keywords": ["dns", "域名", "domain"],
        "category": "network",
        "drawio": "shape=mxgraph.azure.dns;whiteSpace=wrap;html=1;",
        "svg_render": False,
    },
    "cdn": {
        "shape": "cloud",
        "name": "CDN",
        "keywords": ["cdn", "content", "加速", "缓存"],
        "category": "network",
        "drawio": "shape=mxgraph.azure.cdn;whiteSpace=wrap;html=1;",
        "svg_render": True,
    },
    "vpn": {
        "shape": "rect",
        "name": "VPN 网关",
        "keywords": ["vpn", "专网", "加密通道"],
        "category": "network",
        "drawio": "shape=mxgraph.azure.vpnGateway;whiteSpace=wrap;html=1;",
        "svg_render": False,
    },

    # ---- UML/类图类 ----
    "uml_class": {
        "shape": "rect",
        "name": "UML 类",
        "keywords": ["class", "uml", "类", "面向对象"],
        "category": "uml",
        "drawio": "shape=umlLifeline;whiteSpace=wrap;html=1;collapsible=1;container=1;",
        "svg_render": False,
    },
    "uml_interface": {
        "shape": "rect",
        "name": "UML 接口",
        "keywords": ["interface", "接口", "implements"],
        "category": "uml",
        "drawio": "shape=umlInterface;whiteSpace=wrap;html=1;",
        "svg_render": False,
    },
    "uml_state": {
        "shape": "rect",
        "name": "状态节点",
        "keywords": ["state", "状态机", "statechart"],
        "category": "uml",
        "drawio": "rounded=1;whiteSpace=wrap;html=1;",
        "svg_render": False,
    },
    "package": {
        "shape": "rect",
        "name": "包 / 命名空间",
        "keywords": ["package", "包", "namespace", "模块"],
        "category": "uml",
        "drawio": "shape=package;whiteSpace=wrap;html=1;",
        "svg_render": False,
    },
    "actor": {
        "shape": "rect",
        "name": "参与者 (用例)",
        "keywords": ["actor", "参与者", "用例", "人", "用户"],
        "category": "uml",
        "drawio": "shape=umlActor;verticalLabelPosition=bottom;verticalAlign=top;html=1;outlineConnect=0;",
        "svg_render": False,
    },
    "usecase": {
        "shape": "rect",
        "name": "用例椭圆",
        "keywords": ["usecase", "use case", "用例", "椭圆"],
        "category": "uml",
        "drawio": "ellipse;whiteSpace=wrap;html=1;",
        "svg_render": False,
    },

    # ---- 商务/通用类 ----
    "user": {
        "shape": "rect",
        "name": "用户图标",
        "keywords": ["user", "用户", "person", "人", "账号"],
        "category": "general",
        "drawio": "shape=user;whiteSpace=wrap;html=1;",
        "svg_render": False,
    },
    "group": {
        "shape": "rect",
        "name": "用户组",
        "keywords": ["group", "team", "组", "团队", "部门"],
        "category": "general",
        "drawio": "shape=group;whiteSpace=wrap;html=1;",
        "svg_render": False,
    },
    "folder": {
        "shape": "rect",
        "name": "文件夹",
        "keywords": ["folder", "dir", "目录", "文件夹"],
        "category": "general",
        "drawio": "shape=folder;whiteSpace=wrap;html=1;",
        "svg_render": False,
    },
    "table": {
        "shape": "rect",
        "name": "数据表",
        "keywords": ["table", "表", "relation", "关系"],
        "category": "general",
        "drawio": "shape=table;whiteSpace=wrap;html=1;collapsible=1;container=1;expand=1;dropOn=1;tableRows=5;tablePosition=left;",
        "svg_render": False,
    },
    "message": {
        "shape": "note",
        "name": "消息气泡",
        "keywords": ["message", "消息", "chat", "聊天", "通知"],
        "category": "general",
        "drawio": "shape=message;whiteSpace=wrap;html=1;",
        "svg_render": False,
    },
    "callout": {
        "shape": "note",
        "name": "标注框",
        "keywords": ["callout", "标注", "引用", "说明", "解说"],
        "category": "general",
        "drawio": "shape=callout;whiteSpace=wrap;html=1;",
        "svg_render": False,
    },
    "database_alt": {
        "shape": "cylinder",
        "name": "替代数据库",
        "keywords": ["alt_db", "redis", "cache", "缓存", "kv", "nosql"],
        "category": "data",
        "drawio": "shape=datastore;whiteSpace=wrap;html=1;",
        "svg_render": True,
    },

    # ---- 架构/微服务类 ----
    "api": {
        "shape": "card",
        "name": "API 网关",
        "keywords": ["api", "api gateway", "接口", "kong", "apisix"],
        "category": "network",
        "drawio": "shape=mxgraph.archimate.applicationinterface;whiteSpace=wrap;html=1;",
        "svg_render": True,
    },
    "queue": {
        "shape": "rect",
        "name": "消息队列",
        "keywords": ["queue", "mq", "kafka", "rabbit", "消息队列"],
        "category": "architecture",
        "drawio": "shape=md1;whiteSpace=wrap;html=1;",
        "svg_render": False,
    },
    "function": {
        "shape": "rect",
        "name": "函数/服务",
        "keywords": ["lambda", "function", "函数", "faas", "serverless"],
        "category": "architecture",
        "drawio": "shape=f;whiteSpace=wrap;html=1;",
        "svg_render": False,
    },
    "event": {
        "shape": "rect",
        "name": "事件",
        "keywords": ["event", "事件", "trigger", "触发"],
        "category": "architecture",
        "drawio": "shape=event;whiteSpace=wrap;html=1;",
        "svg_render": False,
    },
    "s3": {
        "shape": "cube",
        "name": "对象存储",
        "keywords": ["s3", "oss", "对象存储", "bucket", "桶"],
        "category": "data",
        "drawio": "shape=mxgraph.aws4.s3;whiteSpace=wrap;html=1;",
        "svg_render": True,
    },
}


# ===== 类别索引 =====
CATEGORIES = sorted({v["category"] for v in SHAPE_LIBRARY.values()})


def list_shapes(category=None, only_svg=False):
    """列出所有形状，可选按类别和 SVG 支持过滤

    返回深拷贝，防止外部修改污染全局 SHAPE_LIBRARY。
    """
    result = {}
    for sid, info in SHAPE_LIBRARY.items():
        if category and info["category"] != category:
            continue
        if only_svg and not info["svg_render"]:
            continue
        result[sid] = deepcopy(info)
    return result


def get_shape(shape_id):
    """按 shape_id 取形状元信息，不存在返回 None

    返回深拷贝，防止外部修改污染全局 SHAPE_LIBRARY。
    """
    item = SHAPE_LIBRARY.get(shape_id)
    return deepcopy(item) if item is not None else None


def _normalize(s):
    return _NORMALIZE_RE.sub(" ", s.lower()).strip()


def search_shape(query, limit=5, threshold=0.35):
    """模糊搜索形状

    优先级: 1) 精确匹配 shape_id / name  2) keywords 命中  3) 相似度
    """
    if not query:
        return []
    q = _normalize(query)
    if not q:
        return []
    tokens = q.split()

    scored = []
    for sid, info in SHAPE_LIBRARY.items():
        sid_n = _normalize(sid)
        name_n = _normalize(info["name"])
        keywords_n = {_normalize(kw) for kw in info["keywords"]}
        category_n = _normalize(info["category"])

        score = 0.0
        # 精确命中 shape_id
        if q == sid_n:
            score += 2.0
        elif q in sid_n:
            score += 0.8
        # 精确命中 name
        if q == name_n:
            score += 2.0
        elif q in name_n:
            score += 1.2
        # 类别包含
        if q in category_n:
            score += 0.3

        # keywords 包含
        for tok in tokens:
            if not tok:
                continue
            if tok in keywords_n:
                score += 0.9
            if tok in name_n:
                score += 0.5
            if tok in sid_n:
                score += 0.4

        # 相似度兜底
        combined = f"{sid_n} {name_n} {' '.join(keywords_n)} {category_n}"
        sim = max(
            SequenceMatcher(None, q, combined[:100]).ratio(),
            SequenceMatcher(None, q, name_n).ratio(),
            SequenceMatcher(None, q, sid_n).ratio(),
        )
        if sim > threshold:
            score += sim * 0.6

        if score > 0:
            scored.append((score, sid))

    # 去重并按分数降序
    seen = set()
    result = []
    for score, sid in sorted(scored, key=lambda x: (-x[0], x[1])):
        if sid in seen:
            continue
        seen.add(sid)
        result.append((sid, SHAPE_LIBRARY[sid], round(score, 3)))
        if len(result) >= limit:
            break
    return result


def shape_to_drawio_style(shape_id):
    """从 shape_library 取 drawio style；没命中时 fallback 到 shapes.py 的 SHAPES"""
    entry = SHAPE_LIBRARY.get(shape_id)
    if entry:
        return entry["drawio"]
    from .shapes import SHAPES as _SH
    if shape_id in _SH:
        return _SH[shape_id]["drawio"]
    return "rounded=1;whiteSpace=wrap;html=1;"  # rect fallback


def shape_svg_supported(shape_id):
    """SVG 渲染是否支持该 shape（不支持则 fallback 到矩形）"""
    entry = SHAPE_LIBRARY.get(shape_id)
    if entry:
        return entry["svg_render"]
    from .shapes import SHAPES as _SH
    return shape_id in _SH


def summary():
    """统计摘要"""
    total = len(SHAPE_LIBRARY)
    svg_ok = sum(1 for v in SHAPE_LIBRARY.values() if v["svg_render"])
    return {
        "total": total,
        "svg_supported": svg_ok,
        "drawio_fallback": total - svg_ok,
        "categories": CATEGORIES,
        "per_category": {
            c: sum(1 for v in SHAPE_LIBRARY.values() if v["category"] == c)
            for c in CATEGORIES
        },
    }
