"""Shared heading recall used by both the production workflow and offline evaluations."""

from functools import lru_cache

from .history import History, catalog_settings


@lru_cache(maxsize=8192)
def catalog_document(knowledge, code):
    entry = knowledge.entries[code]
    chapter = knowledge.entries.get(code[:2])
    children = [e for e in knowledge.entries.values() if e.code.startswith(code) and e.code != code]
    # Actual catalog descriptions only: no held-out case text or generated aliases.
    parts = [chapter.text if chapter else "", entry.text]
    parts.extend(dict.fromkeys(e.text for e in children))
    return "。".join(dict.fromkeys(parts))[:2600] + "。目录编码：" + code


def fuse_headings(catalog_codes, references, valid_codes, limit=40):
    """Historical labels propose candidates; legal notes retain final decision authority."""
    case_codes = list(dict.fromkeys(r["reference_code"][:4] for r in references))
    scores = {}
    for weight, ranking in ((1.0, catalog_codes), (0.8, case_codes)):
        for rank, code in enumerate(ranking, 1):
            if code in valid_codes:
                scores[code] = scores.get(code, 0) + weight / (60 + rank)
    return sorted(scores, key=lambda code: (-scores[code], code))[:limit]


def recall(settings, knowledge, models, query, original=None, limit=40, trace=None, include_references=True):
    query_vector = None

    def embed_query(texts):
        nonlocal query_vector
        if texts != [query]:
            raise ValueError("Request-scoped query embedding cache mismatch")
        if query_vector is None:
            query_vector = models.embed_query(texts)
        return query_vector

    history = []
    if settings.history_enabled:
        index = History(settings)
        try:
            history = index.search(
                query,
                embed_query if settings.dense_enabled and settings.history_dense_enabled else None,
                limit=40,
                exclude_description=original,
            )
        finally:
            index.close()
    if settings.qdrant_catalog_enabled:
        index = History(catalog_settings(settings), kind="tariff_rule")
        try:
            codes = [
                r["reference_code"]
                for r in index.search(
                    query, embed_query if settings.dense_enabled else None, limit=max(80, limit)
                )
            ]
        finally:
            index.close()
    else:
        codes = knowledge.search(query, None, limit=max(80, limit))
    if trace is not None:
        trace["catalog_recall"] = list(codes)
        trace["history_heading_recall"] = list(dict.fromkeys(r["reference_code"][:4] for r in history))
    if history:
        codes = fuse_headings(codes, history, {e.code for e in knowledge.headings}, max(80, limit))
    if trace is not None:
        trace["fused_recall"] = list(codes)
    if settings.rerank_enabled and codes:
        order = models.rerank(query, [catalog_document(knowledge, c) for c in codes[:100]])
        codes = [codes[i] for i in order]
    # Keep candidate suggestions separate from the small legal-reference context sent to the LLM.
    references = history[:3]
    if settings.rerank_enabled and history and include_references:
        pool = history[:20]
        order = models.rerank(query, [r["description"] for r in pool])
        references = [pool[i] for i in order[:3]]
    if trace is not None:
        trace["reranked"] = codes[:limit]
    return codes[:limit], references
