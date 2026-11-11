"""Versioned domain playbooks, progressively loaded by the scoped exploration planner."""

from .runtime_context import digest

SKILLS = {
    "exclusion_review": {
        "name": "章注排他核查",
        "description": "候选冲突、准入或排他依据不足时，核对当前候选的章注和条文。",
        "tools": ["lookup_notes", "compare_candidates", "finish"],
        "instructions": "仅核对当前允许品目，区分排他、准入与优先关系；条文未检索到不能解释为不存在。法律依据必须逐字引用当前来源；无法排除冲突则交专家。",
    },
    "processing_comparison": {
        "name": "加工状态与案例辨析",
        "description": "加工、用途或形态相近而候选难区分时，比较条文及历史申报参考。",
        "tools": ["lookup_notes", "search_cases", "compare_candidates", "finish"],
        "instructions": "保持原文的未、非、不含及数值条件；只比较当前候选。历史申报标签是参考，不是法规或当前商品事实；相似案例的属性不得自动补入当前申报。",
    },
    "clarification": {
        "name": "缺项澄清",
        "description": "当前候选的关键差异依赖尚未提供的商品属性时，提出一个有区分度的问题。",
        "tools": ["ask_clarification", "compare_candidates", "finish"],
        "instructions": "每次只问一个影响候选区分且当前未提供的属性，优先加工状态、材质或用途；不替企业猜答案，不要求重复提供已确认要素。",
    },
}


def catalog():
    return [
        {"id": key, "name": value["name"], "description": value["description"]}
        for key, value in SKILLS.items()
    ]


def load_skill(key):
    value = SKILLS[key]
    return {"id": key, **value, "version": digest(value)}
