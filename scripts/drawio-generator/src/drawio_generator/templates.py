#!/usr/bin/env python3
"""图类型预设模板 — 快速生成常见架构图结构"""


def microservices_template(
    title="微服务架构",
    services=None,
    with_db=True,
    with_cache=True,
    with_mq=True,
):
    """
    微服务架构模板。

    参数:
        services: 业务服务名称列表，默认 ["用户服务", "订单服务", "支付服务"]
        with_db: 是否包含数据库层
        with_cache: 是否包含缓存层
        with_mq: 是否包含消息队列层
    """
    if services is None:
        services = ["用户服务", "订单服务", "支付服务"]

    nodes = []
    edges = []
    nid = 1

    # 网关层
    gw_id = f"svc{nid}"
    nid += 1
    nodes.append({
        "id": gw_id, "label": "API 网关", "shape": "hexagon",
        "color": "node_blue"
    })

    # 业务服务层
    svc_ids = []
    for sname in services:
        sid = f"svc{nid}"
        nid += 1
        nodes.append({
            "id": sid, "label": sname, "shape": "rect",
            "color": "node_green"
        })
        edges.append({"from": gw_id, "to": sid, "label": "HTTP"})
        svc_ids.append(sid)

    # 消息队列（可选）
    mq_id = None
    if with_mq:
        mq_id = f"svc{nid}"
        nid += 1
        nodes.append({
            "id": mq_id, "label": "消息队列", "shape": "cloud",
            "color": "node_yellow"
        })
        for sid in svc_ids:
            edges.append({"from": sid, "to": mq_id, "label": "Pub/Sub", "dashed": True})

    # 缓存层（可选）
    cache_id = None
    if with_cache:
        cache_id = f"svc{nid}"
        nid += 1
        nodes.append({
            "id": cache_id, "label": "Redis 缓存", "shape": "cylinder",
            "color": "node_cyan"
        })
        for sid in svc_ids:
            edges.append({"from": sid, "to": cache_id, "label": "Cache", "dashed": True})

    # 数据库层（可选）
    db_id = None
    if with_db:
        db_id = f"svc{nid}"
        nid += 1
        nodes.append({
            "id": db_id, "label": "主数据库", "shape": "cylinder",
            "color": "node_red"
        })
        for sid in svc_ids:
            edges.append({"from": sid, "to": db_id, "label": "SQL"})

    return {
        "title": title,
        "palette": "tech",
        "auto_layout": True,
        "layout_direction": "vertical",
        "nodes": nodes,
        "edges": edges,
    }


def network_topology_template(
    title="网络拓扑",
    has_cdn=True,
    has_firewall=True,
    app_server_count=2,
    db_replica=True,
):
    """
    网络拓扑模板。

    参数:
        has_cdn: 是否包含 CDN
        has_firewall: 是否包含防火墙
        app_server_count: 应用服务器数量（1-4）
        db_replica: 是否有数据库从库
    """
    app_server_count = max(1, min(4, app_server_count))

    nodes = []
    edges = []
    nid = 1

    # 互联网/CDN
    if has_cdn:
        cdn_id = f"net{nid}"
        nid += 1
        nodes.append({"id": cdn_id, "label": "CDN", "shape": "cloud", "color": "node_cyan"})
        internet_id = f"net{nid}"
        nid += 1
        nodes.append({"id": internet_id, "label": "互联网", "shape": "cloud", "color": "node_blue"})
        edges.append({"from": internet_id, "to": cdn_id})
    else:
        internet_id = f"net{nid}"
        nid += 1
        nodes.append({"id": internet_id, "label": "互联网", "shape": "cloud", "color": "node_blue"})
        cdn_id = internet_id

    # 防火墙
    fw_id = None
    if has_firewall:
        fw_id = f"net{nid}"
        nid += 1
        nodes.append({"id": fw_id, "label": "防火墙", "shape": "hexagon", "color": "node_red"})
        edges.append({"from": cdn_id, "to": fw_id})

    # 负载均衡
    lb_id = f"net{nid}"
    nid += 1
    nodes.append({"id": lb_id, "label": "负载均衡", "shape": "hexagon", "color": "node_orange"})
    edges.append({"from": fw_id or cdn_id, "to": lb_id})

    # Web 服务器
    web_ids = []
    for i in range(2):
        wid = f"net{nid}"
        nid += 1
        nodes.append({"id": wid, "label": f"Web 服务器 {i+1}", "shape": "rect", "color": "node_green"})
        edges.append({"from": lb_id, "to": wid})
        web_ids.append(wid)

    # 应用服务器
    app_ids = []
    for i in range(app_server_count):
        aid = f"net{nid}"
        nid += 1
        nodes.append({"id": aid, "label": f"应用服务器 {i+1}", "shape": "rect", "color": "node_yellow"})
        for wid in web_ids:
            edges.append({"from": wid, "to": aid})
        app_ids.append(aid)

    # 数据库
    db_master_id = f"net{nid}"
    nid += 1
    nodes.append({"id": db_master_id, "label": "数据库主库", "shape": "cylinder", "color": "node_purple"})
    for aid in app_ids:
        edges.append({"from": aid, "to": db_master_id})

    if db_replica:
        db_slave_id = f"net{nid}"
        nid += 1
        nodes.append({"id": db_slave_id, "label": "数据库从库", "shape": "cylinder", "color": "node_purple"})
        edges.append({"from": db_master_id, "to": db_slave_id, "label": "同步", "dashed": True, "bidirectional": True})

    return {
        "title": title,
        "palette": "business",
        "auto_layout": True,
        "layout_direction": "vertical",
        "nodes": nodes,
        "edges": edges,
    }


def dataflow_template(
    title="数据流架构",
    stages=None,
    with_warehouse=True,
    with_analytics=True,
):
    """
    ETL/数据流模板。

    参数:
        stages: ETL 阶段名称列表，默认 ["数据采集", "清洗转换", "质量校验"]
        with_warehouse: 是否包含数据仓库
        with_analytics: 是否包含分析层
    """
    if stages is None:
        stages = ["数据采集", "清洗转换", "质量校验"]

    nodes = []
    edges = []
    nid = 1

    # 数据源
    src_id = f"df{nid}"
    nid += 1
    nodes.append({"id": src_id, "label": "数据源", "shape": "document", "color": "node_blue"})

    # ETL 阶段
    prev_id = src_id
    stage_ids = []
    for sname in stages:
        sid = f"df{nid}"
        nid += 1
        nodes.append({"id": sid, "label": sname, "shape": "process", "color": "node_green"})
        edges.append({"from": prev_id, "to": sid})
        prev_id = sid
        stage_ids.append(sid)

    # 数据仓库（可选）
    wh_id = None
    if with_warehouse:
        wh_id = f"df{nid}"
        nid += 1
        nodes.append({"id": wh_id, "label": "数据仓库", "shape": "cylinder", "color": "node_purple"})
        edges.append({"from": prev_id, "to": wh_id})

    # 分析层（可选）
    if with_analytics:
        ana_id = f"df{nid}"
        nid += 1
        nodes.append({"id": ana_id, "label": "分析引擎", "shape": "hexagon", "color": "node_orange"})
        edges.append({"from": wh_id or prev_id, "to": ana_id})

        bi_id = f"df{nid}"
        nid += 1
        nodes.append({"id": bi_id, "label": "BI 报表", "shape": "note", "color": "node_cyan"})
        edges.append({"from": ana_id, "to": bi_id})

    return {
        "title": title,
        "palette": "academic",
        "auto_layout": True,
        "layout_direction": "horizontal",
        "nodes": nodes,
        "edges": edges,
    }


def er_diagram_template(
    title="ER 图",
    entities=None,
    relationships=None,
):
    """
    ER 图模板。

    参数:
        entities: 实体列表，每项为 dict {"name": "", "attributes": [...]}
                  默认包含 用户、订单、商品
        relationships: 关系列表，每项为 dict {"from": "", "to": "", "label": ""}
    """
    default_entities = [
        {"name": "用户", "attributes": ["ID", "姓名", "邮箱"]},
        {"name": "订单", "attributes": ["ID", "状态", "金额", "创建时间"]},
        {"name": "商品", "attributes": ["ID", "名称", "价格", "库存"]},
    ]
    if entities is None:
        entities = default_entities

    default_relationships = [
        {"from": "用户", "to": "订单", "label": "1:N"},
        {"from": "订单", "to": "商品", "label": "N:M"},
    ]
    if relationships is None:
        relationships = default_relationships

    nodes = []
    edges = []
    nid = 1

    for ent in entities:
        eid = f"er{nid}"
        nid += 1
        attr_text = "\\n".join(ent.get("attributes", []))
        label = f"{ent['name']}\\n{'-'*8}\\n{attr_text}"
        nodes.append({
            "id": eid,
            "label": label,
            "shape": "rect",
            "color": "node_blue",
        })

    # 建立 name -> id 映射
    name_to_id = {}
    for n in nodes:
        # label 第一行是实体名
        name = n["label"].split("\\n")[0]
        name_to_id[name] = n["id"]

    for rel in relationships:
        src = name_to_id.get(rel["from"])
        tgt = name_to_id.get(rel["to"])
        if src and tgt:
            edges.append({
                "from": src, "to": tgt,
                "label": rel.get("label", ""),
                "arrow_style": "none",
            })

    return {
        "title": title,
        "palette": "minimal",
        "auto_layout": True,
        "layout_direction": "auto",
        "nodes": nodes,
        "edges": edges,
    }


# ===== 模板注册表 =====
TEMPLATES = {
    "microservices": microservices_template,
    "network-topology": network_topology_template,
    "dataflow": dataflow_template,
    "er-diagram": er_diagram_template,
}


def apply_template(plan):
    """
    如果 plan 包含 template 字段，用对应模板生成基础结构，
    再用 plan 中的其他字段覆盖/合并。

    返回新的 plan dict（不修改原 plan）。
    """
    tmpl_name = plan.get("template")
    if not tmpl_name:
        return plan

    tmpl_func = TEMPLATES.get(tmpl_name)
    if tmpl_func is None:
        raise ValueError(f"未知模板 '{tmpl_name}'，可用: {list(TEMPLATES.keys())}")

    # 提取模板专属参数（以 tmpl_ 前缀或已知关键字）
    # 这里简单处理：将 plan 中除 template 外的字段作为 kwargs 传给模板函数
    # 但只传模板函数接受的参数
    import inspect
    sig = inspect.signature(tmpl_func)
    tmpl_kwargs = {}
    for key in list(plan.keys()):
        if key in sig.parameters:
            tmpl_kwargs[key] = plan[key]

    base = tmpl_func(**tmpl_kwargs)

    # 用户 plan 覆盖模板默认值（nodes/edges/layers 特殊处理：用户指定则完全替换）
    # 对 list 做浅拷贝，防止后续修改污染调用方原 plan
    result = dict(base)
    for key, val in plan.items():
        if key == "template":
            continue
        if key in ("nodes", "edges", "layers"):
            if val:
                result[key] = list(val)
        else:
            result[key] = val

    return result
