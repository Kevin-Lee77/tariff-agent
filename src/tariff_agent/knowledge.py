import hashlib
import json
import os
import tempfile
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from .domain import Corpus
from .governance import tokens


class EvidenceError(ValueError):
    pass


_cache_lock = threading.Lock()


@lru_cache(maxsize=4)
def _cached_knowledge(path, allow_test, modified, size):
    return Knowledge(Path(path), allow_test)


def cached_knowledge(path, allow_test=False):
    info = path.stat()
    with _cache_lock:
        return _cached_knowledge(str(path.resolve()), allow_test, info.st_mtime_ns, info.st_size)


class Knowledge:
    def __init__(self, path, allow_test=False):
        raw = path.read_text(encoding="utf-8")
        self.fingerprint = hashlib.sha256(raw.encode()).hexdigest()
        self.corpus = Corpus.model_validate_json(raw)
        if self.corpus.jurisdiction != "CN":
            raise ValueError("This deployment requires a CN tariff corpus")
        if self.corpus.test_only and not allow_test:
            raise ValueError("Test corpus prohibited in production")
        self.entries = {e.code: e for e in self.corpus.entries}
        self.formal_codes = set(self.entries)
        self.entries.update({g.code: g for g in self.corpus.groups})
        for e in self.corpus.entries:
            if e.code[:-1] in self.entries:
                self.entries[e.code] = e.model_copy(update={"parent": e.code[:-1]})
        self.sources = {n.id: n.text for n in self.corpus.general_notes}
        for e in self.entries.values():
            self.sources[e.source_id] = e.text
            self.sources.update({n.id: n.text for n in e.notes})
        self.headings = [e for e in self.corpus.entries if len(e.code) == 4]
        if not self.headings:
            raise ValueError("Corpus contains no headings")
        self.texts = [e.text for e in self.headings]
        self.bm25 = BM25Okapi([tokens(t) for t in self.texts])
        self.dense = None

    def build_dense(self, embed, directory=None, model_id=""):
        fingerprint = hashlib.sha256(
            json.dumps([model_id, self.texts], ensure_ascii=False).encode()
        ).hexdigest()
        target = directory / (fingerprint + ".npy") if directory else None
        if target and target.exists():
            matrix = np.load(target, allow_pickle=False)
        else:
            matrix = np.asarray(embed(self.texts), dtype=np.float32)
            if target:
                target.parent.mkdir(parents=True, exist_ok=True)
                fd, temp = tempfile.mkstemp(dir=target.parent, suffix=".npy")
                try:
                    with os.fdopen(fd, "wb") as stream:
                        np.save(stream, matrix, allow_pickle=False)
                    os.replace(temp, target)
                finally:
                    if os.path.exists(temp):
                        os.unlink(temp)
        if matrix.ndim != 2 or len(matrix) != len(self.texts) or not np.isfinite(matrix).all():
            raise ValueError("Invalid dense index")
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        if (norms == 0).any():
            raise ValueError("Zero vector in index")
        self.dense = matrix / norms
        return len(matrix)

    def search(self, query, embed=None, limit=40, cache_directory=None, model_id=""):
        sparse = np.argsort(-self.bm25.get_scores(tokens(query))).tolist()
        rankings = [sparse]
        if embed:
            if self.dense is None:
                self.build_dense(embed, cache_directory, model_id)
            q = embed([query])[0]
            rankings.append(np.argsort(-(self.dense @ q)).tolist())
        rrf = {}
        for ranking in rankings:
            for rank, idx in enumerate(ranking, 1):
                rrf[idx] = rrf.get(idx, 0) + 1 / (60 + rank)
        return [
            self.headings[i].code for i in sorted(rrf, key=lambda i: (-rrf[i], self.headings[i].code))[:limit]
        ]

    def brief(self, codes, notes=False, query=""):
        result = []
        for code in codes:
            e = self.entries[code]
            row = {
                "code": code,
                "text": e.text,
                "source_id": e.source_id,
                "structural_group_only": code not in self.formal_codes,
            }
            if notes:
                chapter = self.entries.get(code[:2])
                available = (
                    self.corpus.general_notes
                    + (chapter.notes if chapter and chapter.code != e.code else [])
                    + e.notes
                )
                if query and available:
                    index = BM25Okapi([tokens(n.text) for n in available])
                    scores = index.get_scores(tokens(query))
                    available = [available[i] for i in np.argsort(-scores).tolist()]
                # Bound context. Full page-version evidence stays in the corpus and tools.
                row["notes"] = [n.model_dump() for n in available[:8]]
            result.append(row)
        return result

    def children(self, code):
        return [e.code for e in self.entries.values() if e.parent == code]

    def path(self, code):
        if code not in self.entries:
            raise ValueError("Unknown code")
        result = []
        while code:
            e = self.entries[code]
            result.append(e.code)
            code = e.parent
        return list(reversed(result))

    def allowed(self, codes, attrs):
        kept, rejected = [], []
        for code in codes:
            e = self.entries[code]
            notes = self.entries[code[:2]].notes + e.notes
            blocked = False
            for n in notes:
                actual = str(attrs.get(n.attribute, "")).strip().casefold()
                expected = n.value.strip().casefold()
                # Only exact known structured attributes drive hard filters. Missing != false.
                if (
                    actual
                    and expected
                    and (
                        (n.kind == "exclude" and actual == expected)
                        or (n.kind == "require" and actual != expected)
                    )
                ):
                    rejected.append({"code": code, "source_id": n.id, "reason": "structured_rule"})
                    blocked = True
            if not blocked:
                kept.append(code)
        return kept, rejected

    def validate_ranking(self, ranking, allowed, accessible=None):
        if len(ranking.codes) != len(set(ranking.codes)) or not set(ranking.codes) <= set(allowed):
            raise ValueError("Model returned duplicate/out-of-scope codes")
        for citation in ranking.citations:
            if accessible is not None and citation.source_id not in accessible:
                raise EvidenceError("Citation not supplied to this stage")
            if citation.source_id not in self.sources:
                raise EvidenceError("Unknown citation source")
            source = self.sources[citation.source_id]
            if citation.quote not in source:
                positions = [i for i, char in enumerate(source) if not char.isspace()]
                compact_source = "".join(source[i] for i in positions)
                compact_quote = "".join(citation.quote.split())
                start = compact_source.find(compact_quote) if compact_quote else -1
                if start < 0:
                    raise EvidenceError("Citation not present verbatim in source")
                citation.quote = source[positions[start] : positions[start + len(compact_quote) - 1] + 1]

    @classmethod
    def import_file(cls, source, target):
        corpus = Corpus.model_validate_json(source.read_text(encoding="utf-8"))
        if corpus.jurisdiction != "CN":
            raise ValueError("This deployment only accepts explicitly CN corpora")
        target.parent.mkdir(parents=True, exist_ok=True)
        import os
        import tempfile

        fd, temp = tempfile.mkstemp(dir=target.parent, suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(corpus.model_dump_json(indent=2))
            os.replace(temp, target)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        return len(corpus.entries)
