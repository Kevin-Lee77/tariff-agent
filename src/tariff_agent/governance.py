import re
from collections import Counter
from functools import lru_cache
from typing import Annotated

import jieba
import numpy as np
from pydantic import Field

from .domain import Element, Strict


def tokens(text):
    return [x.lower() for x in jieba.lcut(text) if x.strip()]


def negative(text):
    # Intentional correction to legacy 'any character 不/非/否': fixed words are not negations.
    return bool(re.search(r"^(?:非(?!金属矿)|不含|无|未|否)|不适用|非专用", text))


Word = Annotated[str, Field(min_length=1, max_length=200)]
Aliases = Annotated[list[Word], Field(max_length=300)]


class TermRequest(Strict):
    synthetic: bool = False
    known: dict[Word, Aliases] = Field(default_factory=dict, max_length=2000)
    pending: list[Word] = Field(max_length=500)
    frequencies: dict[Word, Annotated[int, Field(ge=0)]] = Field(default_factory=dict, max_length=20000)
    threshold: float = Field(default=0.9, ge=0, le=1)


class RiskRule(Strict):
    knowledge_id: str
    fit_area: str = Field(pattern=r"^\d{2,10}$")
    element_code: str
    vocabulary: str = Field(min_length=1)
    threshold: float = Field(default=0.95, ge=0, le=1)
    review_comment: str = ""


class RiskRequest(Strict):
    synthetic: bool = False
    code: str = Field(pattern=r"^\d{2,10}$")
    code_origin: str = Field(pattern=r"^(declared|candidate)$")
    elements: list[Element] = Field(max_length=50)
    rules: list[RiskRule] = Field(max_length=2000)


def normalize_terms(req, embed):
    groups = {k: list(dict.fromkeys(v)) for k, v in req.known.items()}
    pending = list(dict.fromkeys(w.strip() for w in req.pending if w.strip()))
    features = {}
    for main, aliases in groups.items():
        bag = Counter()
        for word in [main] + aliases:
            for token in tokens(word):
                bag[token] += max(1, req.frequencies.get(word, 1))
        features[main] = bag
    unknown = []
    for word in pending:
        choices = []
        for main, bag in features.items():
            if negative(word) != negative(main):
                continue
            score = sum(v for k, v in bag.items() if k in tokens(word)) / max(1, sum(bag.values()))
            choices.append((score, main))
        if choices and max(choices)[0] > 0.1:
            main = max(choices)[1]
            if word != main and word not in groups[main]:
                groups[main].append(word)
        else:
            unknown.append(word)
    independent = []
    if unknown:
        vectors = embed(unknown)
        sim = vectors @ vectors.T
        used = set()
        for i, word in enumerate(unknown):
            if i in used:
                continue
            cluster = [word]
            used.add(i)
            for j in range(i + 1, len(unknown)):
                if j not in used and negative(word) == negative(unknown[j]) and sim[i, j] > req.threshold:
                    used.add(j)
                    cluster.append(unknown[j])
            if len(cluster) == 1:
                independent.append(word)
            else:
                main = max(cluster, key=lambda w: (req.frequencies.get(w, 0), w))
                groups[main] = [w for w in cluster if w != main]
    # Frequency-weighted overlapping clusters produce proposals, not destructive automatic merges.
    proposals = []
    mains = list(groups)
    for i, a in enumerate(mains):
        for b in mains[i + 1 :]:
            shared = set(tokens(a)) & set(tokens(b))
            if shared and negative(a) == negative(b):
                proposals.append({"left": a, "right": b, "shared_tokens": sorted(shared)})
    return {
        "synthetic": req.synthetic,
        "groups": groups,
        "independent_words": independent,
        "merge_proposals": proposals,
        "negative_terms": [w for w in pending if negative(w)],
        "version": "governance-v1",
    }


def check_standard(elements, chapter, rules):
    if not elements:
        return {"status": "not_covered", "issues": [{"reason": "no_elements"}]}
    chapter_rules = rules.get(chapter)
    if chapter_rules is None:
        return {"status": "not_covered", "issues": []}
    issues = []
    for e in elements:
        allowed = chapter_rules.get(e.name)
        general = chapter_rules.get("ALL", [])
        if allowed is None and not general:
            issues.append({"element": e.name, "value": e.value, "reason": "rule_not_covered"})
        elif e.value not in (allowed or []) and e.value not in general:
            issues.append({"element": e.name, "value": e.value, "reason": "non_standard_value"})
    return {"status": "checked", "issues": issues}


@lru_cache(maxsize=2)
def load_w2v(path):
    # Operator-provided trusted local artifact only; never accept model pickle uploads.
    from gensim.models import Word2Vec

    return Word2Vec.load(path)


def word_vector(text, model):
    rows = [model.wv[t] for t in tokens(text) if t in model.wv]
    if not rows:
        return None
    vec = np.asarray(rows).mean(axis=0)
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else None


def risk_check(req, model):
    hits, unknown, compared = [], [], 0
    for e in req.elements:
        for rule in req.rules:
            if e.code != rule.element_code or not req.code.startswith(rule.fit_area):
                continue
            a, b = word_vector(e.value, model), word_vector(rule.vocabulary, model)
            if a is None or b is None:
                unknown.append(
                    {"element": e.code, "knowledge_id": rule.knowledge_id, "reason": "out_of_vocabulary"}
                )
                continue
            compared += 1
            score = float(a @ b)
            if score >= rule.threshold:
                hits.append(rule.model_dump() | {"element_value": e.value, "similarity": score})
    return {
        "synthetic": req.synthetic,
        "code": req.code,
        "code_origin": req.code_origin,
        "status": "checked" if compared else "not_covered",
        "compared": compared,
        "hits": hits,
        "unknown": unknown,
    }
