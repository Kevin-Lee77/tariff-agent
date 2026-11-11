"""Shared business algorithms for independent Flask services and workflow tools."""

import hashlib
import json
import re

from .domain import CaseInput, Element
from .governance import RiskRequest, load_w2v, risk_check


def bundle(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def policy(settings, profile):
    if settings.business_assets_enabled:
        from .assets import Assets
        from .persistence import database_target

        return Assets(database_target(settings), auto_create=not settings.production).risk_policy(
            profile == "sandbox"
        )
    if profile == "sandbox":
        return {
            "version": "sandbox-v1",
            "synthetic": True,
            "approved": True,
            "coverage": [{"prefix": "02", "elements": ["0000"]}],
            "element_names": {"品名": "0000", "商品名称": "0000"},
            "rules": [
                {
                    "knowledge_id": "SANDBOX-02-001",
                    "fit_area": "02",
                    "element_code": "0000",
                    "vocabulary": "变质",
                    "threshold": 0.95,
                    "review_comment": "联调规则：请核实商品状态并修改申报，非正式海关风险认定",
                }
            ],
        }
    return bundle(settings.risk_rules)


def parse_declaration(req, rules):
    text = req.description
    code = req.declared_code
    if not code:
        match = re.search(r"(?:申报税号|商品编码|税号)\s*[:：]\s*(\d{2,10})(?!\d)", text)
        code = match.group(1) if match else None
    names = rules.get("element_names", {})
    elements = [e.model_copy(update={"code": e.code or names.get(e.name, "")}) for e in req.elements]
    for part in re.split(r"[\n;；|]+", text):
        match = re.fullmatch(r"\s*([^:：]{1,100})[:：]\s*(.+?)\s*", part)
        if match and match[1].strip() in names:
            name = match[1].strip()
            # Conflicting values stay visible to the precheck; no silent overwrite.
            elements.append(Element(name=name, value=match[2], code=names[name]))
    if not req.bill_id and not any(e.code == "0000" for e in elements):
        elements.append(Element(name="申报原文", value=text, code="0000"))
    return code, elements


def source_check(req: CaseInput, settings, model_loader=load_w2v):
    rules = policy(settings, req.governance_profile)
    code, elements = parse_declaration(req, rules)
    version = rules.get("version", "unavailable")
    synthetic = bool(rules.get("synthetic", False))
    base = {
        "original_text": req.description,
        "declared_code": code,
        "elements": [e.model_dump() for e in elements],
        "dictionary_version": version,
        "synthetic": synthetic,
        "hits": [],
        "annotations": [],
        "unknown": [],
    }

    def report(decision, reason, **extra):
        return (
            base
            | {
                "decision": decision,
                "reason": reason,
                "enterprise_feedback": {
                    "action": "退回整改"
                    if decision == "blocked"
                    else "通过现有规则校验"
                    if decision == "clear"
                    else "补充信息或人工核查",
                    "message": reason,
                    "dictionary_version": version,
                    "synthetic": synthetic,
                },
            }
            | extra
        )

    if req.bill_id and settings.business_assets_enabled:
        from .assets import Assets
        from .persistence import database_target

        stored = Assets(database_target(settings), auto_create=not settings.production).get_bill(req.bill_id)
        base["bill_id"] = req.bill_id
        base["import_id"] = stored["import_id"]
        if stored["alignment_status"] != "aligned":
            return report(
                "information_required", "原始bill的编码与值数量不一致，禁止按位置猜测；请先修订映射"
            )
        if bool(stored["synthetic"]) != (req.governance_profile == "sandbox"):
            return report("review_required", "bill数据环境与词典环境不一致")
        expected = [
            Element(name=e["name"] or "要素" + e["code"], code=e["code"], value=e["value"]).model_dump()
            for e in stored["elements"]
            if e["value"].strip()
        ]
        if (
            req.description != stored["description"].strip()
            or req.declared_code != stored["code_ts"]
            or [e.model_dump() for e in req.elements] != expected
        ):
            return report(
                "review_required", "提交内容与保存的bill不一致，请提交独立修订版本，禁止复用原bill标识"
            )

    if not rules.get("rules"):
        return report("review_required", "风险词典未配置，停止自动归类，等待核查")
    if not code:
        return report("information_required", "请补充申报税号，用于匹配税智源风险规则的适用范围")
    applicable = [r for r in rules["rules"] if code.startswith(r["fit_area"])]
    if not applicable:
        return report("review_required", "当前申报税号未获风险规则覆盖，停止自动归类")
    available = {e.code for e in elements}
    missing = sorted({r["element_code"] for r in applicable} - available)
    # Exact matches do not depend on vector availability. Semantic comparisons use legacy Word2Vec.
    exact, remaining = [], []
    for rule in applicable:
        matches = [e for e in elements if e.code == rule["element_code"] and rule["vocabulary"] in e.value]
        if matches:
            exact.extend(
                rule | {"element_value": e.value, "similarity": 1.0, "match_type": "exact"} for e in matches
            )
        else:
            remaining.append(rule)
    unknown = []
    compared = len(exact)
    hits = exact
    if remaining and not synthetic and rules.get("matching", "word2vec") != "exact":
        if not settings.word2vec.exists():
            unknown.append({"reason": "word2vec_unavailable"})
        else:
            result = risk_check(
                RiskRequest(code=code, code_origin="declared", elements=elements, rules=remaining),
                model_loader(str(settings.word2vec)),
            )
            hits = hits + result["hits"]
            unknown = result["unknown"]
            compared += result["compared"]
    annotations = []
    for hit in hits:
        # Spans refer to original declaration, never to a silently normalized copy.
        needle = hit["vocabulary"] if hit["vocabulary"] in req.description else hit["element_value"]
        for match in re.finditer(re.escape(needle), req.description):
            annotations.append(
                {
                    "start": match.start(),
                    "end": match.end(),
                    "text": match[0],
                    "knowledge_id": hit["knowledge_id"],
                    "comment": hit.get("review_comment", ""),
                }
            )
    base.update(hits=hits, annotations=annotations, unknown=unknown, compared=compared)
    if hits:
        return report("blocked", "发现风险命中，已停止后续术语处理与商品归类，请企业核实整改")
    if missing:
        return report(
            "information_required", "申报文本缺少可映射的风险校验要素：" + "、".join(missing), missing=missing
        )
    if unknown:
        return report("review_required", "存在无法完成比较的词或模型，停止自动归类")
    coverage = [r for r in rules.get("coverage", []) if code.startswith(r["prefix"])]
    covered = set().union(*(set(r["elements"]) for r in coverage)) if coverage else set()
    if not rules.get("approved") or not covered or not {r["element_code"] for r in applicable} <= covered:
        return report("review_required", "词典或覆盖范围尚未审核，当前无命中不能视为通过")
    return report("clear", "通过当前版本与适用范围内的风险规则校验；继续税智语和多智能体归类")


def language_check(req: CaseInput, settings, published=None):
    published = published or {}
    config = bundle(settings.language_rules)
    if settings.business_assets_enabled:
        from .assets import Assets
        from .persistence import database_target

        code = req.declared_code or parse_declaration(req, {})[0] or ""
        config = Assets(database_target(settings), auto_create=not settings.production).language_policy(
            req.governance_profile == "sandbox", code
        )
    elif req.governance_profile == "sandbox":
        config = {
            "version": "sandbox-v1",
            "synthetic": True,
            "groups": {"冷冻": ["冻制"], "去骨": ["剔骨"]},
            "independent_words": ["不锈钢"],
            "mutex_groups": [["冷冻", "冷藏"]],
        }
    groups = dict(config.get("groups", {}))
    independent = list(config.get("independent_words", []))
    scoped = []
    for entry in config.get("entries", []):
        if entry["kind"] in ("synonym", "standard"):
            if entry["element_code"] or entry["element_name"]:
                scoped.append(entry)
            else:
                groups.setdefault(entry["canonical"], [])
                if entry["term"] != entry["canonical"]:
                    groups[entry["canonical"]].append(entry["term"])
        elif entry["kind"] in ("independent", "negative"):
            independent.append(entry["term"])
    groups.update(published.get("groups", {}))
    aliases = {}
    for main, words in groups.items():
        for word in words:
            if word and word != main:
                aliases.setdefault(word, set()).add(main)
    # Preserve the full negated clause. "不含糖" must never turn into "含糖".
    protected = [
        (m.start(), m.end())
        for m in re.finditer(r"(?:不含|未添加|不适用|非专用|无|非(?!金属矿))[^，,。；;\n|]*", req.description)
    ]
    # Business identifiers are not terminology. A grade alias such as 3 -> 3+ must
    # never rewrite a batch number, task ID or contract reference in free text.
    metadata_fields = r"(?:联调批次|批次号?|订单号|报关单号|申报单号|单号|条码|合同号|序列号|任务ID|taskId|billCode|SKU|申报税号|商品编码|税号|HS编码|HSCODE)"
    for match in re.finditer(
        r"(?:^|[\n;；|])\s*" + metadata_fields + r"\s*[:：]([^;；\n|]*)", req.description, re.I
    ):
        protected.append((match.start(1), match.end(1)))
    for word in independent:
        protected.extend((m.start(), m.end()) for m in re.finditer(re.escape(word), req.description))
    changes, warnings = [], []
    patterns = sorted(aliases, key=len, reverse=True)
    if patterns:
        for match in re.finditer("|".join(re.escape(x) for x in patterns), req.description):
            if any(match.start() < b and match.end() > a for a, b in protected):
                continue
            choices = aliases[match[0]]
            if len(choices) != 1:
                warnings.append({"reason": "ambiguous_alias", "text": match[0]})
                continue
            main = next(iter(choices))
            from .governance import negative

            if negative(match[0]) != negative(main):
                warnings.append({"reason": "polarity_conflict", "text": match[0]})
                continue
            changes.append({"start": match.start(), "end": match.end(), "before": match[0], "after": main})
    for entry in scoped:
        if entry["term"] == entry["canonical"]:
            continue
        for field in re.finditer(r"(?:^|[\n;；|])\s*([^:：]+)[:：]([^;；\n|]*)", req.description):
            if field[1].strip() not in (entry["element_name"], entry["element_code"]):
                continue
            for match in re.finditer(re.escape(entry["term"]), field[2]):
                start, end = field.start(2) + match.start(), field.start(2) + match.end()
                if any(start < b and end > a for a, b in protected) or any(
                    start < r["end"] and end > r["start"] for r in changes
                ):
                    continue
                changes.append({"start": start, "end": end, "before": match[0], "after": entry["canonical"]})
    changes.sort(key=lambda r: r["start"])
    text, cursor = "", 0
    for row in changes:
        text += req.description[cursor : row["start"]] + row["after"]
        cursor = row["end"]
    text += req.description[cursor:]
    for group in config.get("mutex_groups", []):
        found = [w for w in group if w in text and not any(w in req.description[a:b] for a, b in protected)]
        if len(found) > 1:
            warnings.append({"reason": "mutually_exclusive_terms", "terms": found})
    scores = []

    def mentions(word):
        return any(
            not any(m.start() < b and m.end() > a for a, b in protected)
            for m in re.finditer(re.escape(word), req.description)
        )

    for entry in config.get("entries", []):
        if entry["kind"] == "nonstandard" and mentions(entry["term"]):
            warnings.append(
                {
                    "reason": "non_standard_value",
                    "text": entry["term"],
                    "dictionary_id": entry["dictionary_id"],
                }
            )
        if entry["kind"] in ("mutex", "classification_mutex"):
            payload = entry["payload"]
            left = [w for w in payload.get("group1", []) if mentions(w)]
            right = [w for w in payload.get("group2", []) if mentions(w)]
            if left and right:
                warnings.append(
                    {
                        "reason": "mutually_exclusive_terms",
                        "terms": left + right,
                        "dictionary_id": entry["dictionary_id"],
                    }
                )
        if entry["kind"] == "score" and mentions(entry["term"]):
            scores.append(
                {
                    "term": entry["term"],
                    "score": entry["payload"].get("score", 0),
                    "source": entry["dictionary_id"],
                }
            )
    version = hashlib.sha256(
        json.dumps([config, published], sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]
    return {
        "original_text": req.description,
        "normalized_text": text,
        "changes": changes,
        "protected_spans": [{"start": a, "end": b, "text": req.description[a:b]} for a, b in protected],
        "warnings": warnings,
        "rule_scores": scores,
        "dictionary_version": version,
        "synthetic": bool(config.get("synthetic")),
        "status": "review_required"
        if warnings
        else "processed"
        if config or groups
        else "dictionary_unavailable",
    }
