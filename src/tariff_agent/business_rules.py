"""Validated cross-element conditions; configured scores are never model probabilities."""

import math
import re

from .store import Conflict

KINDS = {
    "synonym",
    "standard",
    "nonstandard",
    "negative",
    "independent",
    "mutex",
    "classification_mutex",
    "score",
    "element",
    "observed",
    "pending",
}


def validate_entries(kind, entries):
    if kind not in KINDS or not 0 < len(entries) <= 10000:
        raise Conflict("Unsupported dictionary type or entry count")
    seen = set()
    for row in entries:
        term = row.get("term", "")
        scope = row.get("scope", "")
        if (
            not isinstance(term, str)
            or not 0 < len(term.strip()) <= 1000
            or not re.fullmatch(r"\d{0,10}", scope)
        ):
            raise Conflict("Invalid term or chapter scope")
        canonical = row.get("canonical") or term
        if not isinstance(canonical, str) or not 0 < len(canonical.strip()) <= 1000:
            raise Conflict("Invalid canonical word")
        if kind in ("synonym", "standard"):
            from .governance import negative

            if negative(term) != negative(canonical):
                raise Conflict("Positive and negative words cannot share a group")
        identity = (term, row.get("element_code", ""), row.get("element_name", ""), scope)
        if identity in seen:
            raise Conflict("Duplicate term in the same element and scope")
        seen.add(identity)
        payload = row.get("payload", {})
        if not isinstance(payload, dict):
            raise Conflict("Rule payload must be an object")
        conditions = payload.get("conditions", [])
        if not isinstance(conditions, list) or len(conditions) > 10:
            raise Conflict("At most ten rule conditions")
        for condition in conditions:
            if not isinstance(condition, dict) or not (
                condition.get("element_code") or condition.get("element_name")
            ):
                raise Conflict("Each condition requires an explicit element")
            terms = condition.get("terms", [])
            if (
                not isinstance(terms, list)
                or not 1 <= len(terms) <= 30
                or any(not isinstance(t, str) or not 0 < len(t) <= 200 for t in terms)
            ):
                raise Conflict("Invalid condition terms")
        if kind in ("mutex", "classification_mutex") and conditions and len(conditions) < 2:
            raise Conflict("Cross-element mutex requires at least two conditions")
        if kind == "score":
            value = payload.get("score", 0)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or abs(value) > 1000
            ):
                raise Conflict("Rule score must be finite and within -1000..1000")


def match_conditions(conditions, req):
    """Match only explicit fields; do not borrow a word from another element or a negated span."""
    from .governance import negative

    fields = [(e.code, e.name, e.value) for e in req.elements]
    fields += [
        (match[1].strip(), match[1].strip(), match[2])
        for match in re.finditer(r"(?:^|[\n;；|])\s*([^:：]+)[:：]([^;；\n|]*)", req.description)
    ]
    matches = []
    for condition in conditions:
        hits = []
        for code, name, value in fields:
            if not (
                (condition.get("element_code") and condition["element_code"] == code)
                or (condition.get("element_name") and condition["element_name"] == name)
            ):
                continue
            for clause in re.split(r"[，,。;；\n]", value):
                if negative(clause):
                    continue
                hits += [term for term in condition["terms"] if term in clause]
        if not hits:
            return []
        matches.append(
            {
                "element": condition.get("element_code") or condition["element_name"],
                "terms": sorted(set(hits)),
            }
        )
    return matches
