"""Versioned Qdrant case retrieval: real sparse BM25, optional Qwen dense + RRF + rerank."""

import hashlib
import json
import sqlite3
import uuid
from collections import Counter
from functools import lru_cache
from pathlib import Path

import jieba
from qdrant_client import QdrantClient, models

from .agent_telemetry import span, trace_headers
from .benchmark_data import group_identity, rows, text_identity


def tokenize(text):
    return [word.casefold() for word in jieba.lcut(text) if any(c.isalnum() for c in word)]


@lru_cache(maxsize=4)
def load_vocabulary(path):
    # Generation-specific files are immutable; reuse safely across task/evaluation clients.
    return json.loads(Path(path).read_text(encoding="utf-8"))


class History:
    def __init__(self, settings, client=None, kind="historical_case"):
        self.s = settings
        self.kind = kind
        self.directory = settings.history_directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.client = client or QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key.get_secret_value() or None,
            timeout=120,
            headers=trace_headers(),
        )
        self.manifest_path = self.directory / "active.json"
        self.manifest = json.loads(self.manifest_path.read_text()) if self.manifest_path.exists() else {}
        self.vocabulary = None

    def status(self):
        if not self.manifest:
            self.client.get_collections()
        info = self.client.get_collection(self.manifest["collection"]) if self.manifest else None
        return {
            "backend": "qdrant",
            "active": bool(self.manifest),
            "points": info.points_count if info else 0,
            "sparse_ready": bool(self.manifest),
            "dense_ready": self.manifest.get("dense_ready", False),
            "dense_search_ready": self.manifest.get("dense_ready", False)
            or self.manifest.get("dense_search_ready", False),
            "dense_count": self.manifest.get("dense_count", 0),
            "dense_mode": self.manifest.get("dense_mode", "none"),
            "embedding_model": self.s.embedding_model,
            "generation": self.manifest.get("generation"),
            "exclusion_groups": self.manifest.get("excluded_groups", 0),
        }

    def build(self, source, exclusions, embed=None, valid_codes=None, limit=0):
        if limit < 0:
            raise ValueError("limit must be zero (all) or positive")
        source = Path(source)
        excluded = {group_identity(row["description"]) for path in exclusions for row in rows(path)}
        with source.open("rb") as stream:
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        source_sha = digest.hexdigest()
        generation = hashlib.sha256(
            json.dumps(
                [
                    source_sha,
                    sorted(excluded),
                    self.s.embedding_model,
                    self.s.embedding_dimensions,
                    sorted(valid_codes or []),
                    limit,
                    "jieba-bm25-v1",
                    self.kind,
                ]
            ).encode()
        ).hexdigest()[:24]
        name = ("tariff_rules_" if self.kind == "tariff_rule" else "tariff_history_") + generation
        cache = self.directory / (generation + ".sqlite")
        vocab_path = self.directory / (generation + ".vocabulary.json")
        if cache.exists() and not vocab_path.exists():
            cache.rename(cache.with_suffix(".incomplete-" + uuid.uuid4().hex))
        if not cache.exists():
            db = sqlite3.connect(cache)
            db.execute("CREATE TABLE documents (id TEXT PRIMARY KEY, data TEXT, counts TEXT, length INTEGER)")
            vocabulary = {}
            total_length = 0
            count = 0
            try:
                for row in rows(source):
                    if (
                        row.get("synthetic")
                        or row.get("split", "history") != "history"
                        or group_identity(row["description"]) in excluded
                    ):
                        continue
                    if valid_codes is not None and row["gold_code"] not in valid_codes:
                        continue
                    words = tokenize(row["description"])
                    if not words:
                        continue
                    counts = Counter(vocabulary.setdefault(word, len(vocabulary)) for word in words)
                    key = str(uuid.UUID(text_identity(row["description"])[:32]))
                    item = row | {
                        "normalized_fingerprint": text_identity(row["description"]),
                        "group_id": group_identity(row["description"]),
                    }
                    changed = db.execute(
                        "INSERT OR IGNORE INTO documents VALUES (?,?,?,?)",
                        (key, json.dumps(item, ensure_ascii=False), json.dumps(counts), len(words)),
                    ).rowcount
                    count += changed
                    total_length += len(words) * changed
                    if count and count % 1000 == 0:
                        db.commit()
                        print(json.dumps({"phase": "tokenize_history", "processed": count}), flush=True)
                    if limit and count >= limit:
                        break
                db.execute("CREATE TABLE metadata (data TEXT)")
                db.execute(
                    "INSERT INTO metadata VALUES (?)",
                    (json.dumps({"count": count, "average_length": total_length / max(1, count)}),),
                )
                db.commit()
                vocab_path.write_text(json.dumps(vocabulary, ensure_ascii=False), encoding="utf-8")
            except BaseException:
                db.close()
                cache.unlink(missing_ok=True)
                raise
            finally:
                db.close()
        db = sqlite3.connect(cache)
        meta = json.loads(db.execute("SELECT data FROM metadata").fetchone()[0])
        if not self.client.collection_exists(name):
            self.client.create_collection(
                name,
                vectors_config={
                    "qwen3_8b": models.VectorParams(
                        size=self.s.embedding_dimensions, distance=models.Distance.COSINE, on_disk=True
                    )
                },
                sparse_vectors_config={"bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)},
                on_disk_payload=True,
            )
        batch = []
        count = 0
        for key, data, counts, length in db.execute("SELECT * FROM documents ORDER BY id"):
            row = json.loads(data)
            counts = json.loads(counts)
            indices = sorted(map(int, counts))
            weights = [
                counts[str(i)]
                * 2.2
                / (counts[str(i)] + 1.2 * (0.25 + 0.75 * length / max(1, meta["average_length"])))
                for i in indices
            ]
            payload = {
                "description": row["description"],
                "code": row["gold_code"],
                "source": row["source"],
                "normalized_fingerprint": row["normalized_fingerprint"],
                "group_id": row["group_id"],
                "split": "history",
                "chapter": row["gold_code"][:2],
                "synthetic": False,
                "label_status": "versioned_tariff_catalog"
                if self.kind == "tariff_rule"
                else "historical_reference_unverified",
                "authority": self.kind,
                "generation": generation,
            }
            batch.append(
                models.PointStruct(
                    id=key,
                    payload=payload,
                    vector={"bm25": models.SparseVector(indices=indices, values=weights)},
                )
            )
            if len(batch) == 256:
                self.client.upsert(name, points=batch, wait=True)
                count += len(batch)
                batch = []
                if count % 4096 == 0:
                    print(json.dumps({"phase": "qdrant_upsert", "processed": count}), flush=True)
        if batch:
            self.client.upsert(name, points=batch, wait=True)
            count += len(batch)
        db.close()
        if self.client.count(name, exact=True).count != meta["count"]:
            raise ValueError("Incomplete Qdrant import")
        for field in ("split", "chapter", "group_id", "normalized_fingerprint"):
            self.client.create_payload_index(name, field, models.PayloadSchemaType.KEYWORD, wait=True)
        aliases = self.client.get_aliases().aliases
        operations = (
            [models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=self.s.history_alias))]
            if any(a.alias_name == self.s.history_alias for a in aliases)
            else []
        )
        operations.append(
            models.CreateAliasOperation(
                create_alias=models.CreateAlias(collection_name=name, alias_name=self.s.history_alias)
            )
        )
        self.client.update_collection_aliases(operations)
        manifest = {
            "generation": generation,
            "collection": name,
            "indexed": count,
            "source_sha256": source_sha,
            "excluded_groups": len(excluded),
            "embedding_model": self.s.embedding_model,
            "dense_ready": False,
            "token_cache": str(cache.resolve()),
            "vocabulary": str(vocab_path.resolve()),
            "backend": "qdrant",
        }
        temporary = self.manifest_path.with_suffix(".next")
        temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        temporary.replace(self.manifest_path)
        self.manifest = manifest
        if embed is not None:
            self.attach_dense(embed)
        return self.manifest

    def attach_dense(self, embed, limit=0):
        if not self.manifest:
            raise ValueError("Build the isolated history index first")
        if self.manifest["embedding_model"] != self.s.embedding_model:
            raise ValueError("Embedding model changed; rebuild a new collection")
        db = sqlite3.connect(self.manifest["token_cache"])
        batch = []
        if limit < 0:
            raise ValueError("Dense representative limit cannot be negative")
        partial = bool(limit and limit < self.manifest["indexed"])
        if partial and self.manifest.get("dense_ready"):
            db.close()
            return
        if partial and self.manifest.get("dense_count", 0) > limit:
            db.close()
            raise ValueError("Cannot shrink dense coverage without building a new collection")
        suffix = f".dense-representative-{limit}-v1" if partial else ".dense"
        checkpoint = self.directory / (self.manifest["generation"] + suffix + "-checkpoint.json")
        progress = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
        if progress and progress.get("embedding_model") != self.s.embedding_model:
            raise ValueError("Dense checkpoint belongs to a different model")
        count = progress.get("count", 0)

        def flush():
            if not batch:
                return
            vectors = embed([row[1] for row in batch])
            if vectors.shape != (len(batch), self.s.embedding_dimensions):
                raise ValueError("Embedding dimension mismatch")
            self.client.update_vectors(
                self.manifest["collection"],
                [
                    models.PointVectors(id=row[0], vector={"qwen3_8b": vector.tolist()})
                    for row, vector in zip(batch, vectors)
                ],
                wait=True,
            )
            batch.clear()
            temporary = checkpoint.with_suffix(".next")
            temporary.write_text(
                json.dumps({"count": count, "embedding_model": self.s.embedding_model}), encoding="utf-8"
            )
            temporary.replace(checkpoint)

        try:
            if partial:
                query = """SELECT id,data FROM (
                    SELECT id,data,ROW_NUMBER() OVER (
                        PARTITION BY substr(json_extract(data,'$.gold_code'),1,4)
                        ORDER BY COALESCE(json_extract(data,'$.frequency'),1) DESC,id
                    ) AS heading_rank FROM documents
                ) ORDER BY heading_rank,id LIMIT ? OFFSET ?"""
                selected = db.execute(query, (limit - count, count))
            else:
                selected = db.execute("SELECT id,data FROM documents ORDER BY id LIMIT -1 OFFSET ?", (count,))
            for key, data in selected:
                batch.append((key, json.loads(data)["description"]))
                count += 1
                if len(batch) == 16:
                    flush()
            flush()
        finally:
            db.close()
        if count != (limit if partial else self.manifest["indexed"]):
            raise ValueError("Incomplete dense index")
        if json.loads(self.manifest_path.read_text())["generation"] != self.manifest["generation"]:
            raise ValueError("The active index changed while dense vectors were being built")
        self.manifest.update(
            dense_ready=not partial,
            dense_search_ready=True,
            dense_count=count,
            dense_mode="heading_stratified_representatives" if partial else "complete",
        )
        temporary = self.manifest_path.with_suffix(".next")
        temporary.write_text(json.dumps(self.manifest, indent=2), encoding="utf-8")
        temporary.replace(self.manifest_path)

    def search(self, query, embed=None, limit=3, rerank=None, exclude_description=None):
        with span(
            "tool.qdrant." + self.kind, {"retrieval.limit": limit, "retrieval.dense": embed is not None}
        ):
            return self._search(query, embed, limit, rerank, exclude_description)

    def _search(self, query, embed=None, limit=3, rerank=None, exclude_description=None):
        if not self.manifest:
            return []
        if self.vocabulary is None:
            self.vocabulary = load_vocabulary(self.manifest["vocabulary"])
        indices = sorted({self.vocabulary[w] for w in tokenize(query) if w in self.vocabulary})
        if not indices and embed is None:
            return []
        sparse = models.SparseVector(indices=indices, values=[1.0] * len(indices))
        excluded = {text_identity(query)}
        if exclude_description:
            excluded.add(text_identity(exclude_description))
        filters = models.Filter(
            must=[models.FieldCondition(key="split", match=models.MatchValue(value="history"))],
            must_not=[
                models.FieldCondition(key="normalized_fingerprint", match=models.MatchValue(value=value))
                for value in excluded
            ],
        )
        arguments = {"query": sparse, "using": "bm25"}
        mode = "qdrant_bm25_sparse"
        fetch_limit = max(limit, min(100, limit * 2)) if rerank else limit
        if embed is not None:
            if not (self.manifest.get("dense_ready") or self.manifest.get("dense_search_ready")):
                raise ValueError("Qwen dense history vectors have not been built")
            dense = embed([query])[0].tolist()
            arguments = {
                "prefetch": [
                    models.Prefetch(query=sparse, using="bm25", limit=max(80, fetch_limit)),
                    models.Prefetch(query=dense, using="qwen3_8b", limit=max(80, fetch_limit)),
                ],
                "query": models.FusionQuery(fusion=models.Fusion.RRF),
            }
            mode = "qdrant_sparse_dense_rrf"
        points = self.client.query_points(
            self.manifest["collection"],
            **arguments,
            query_filter=filters,
            limit=fetch_limit,
            with_payload=True,
        ).points
        if rerank and points:
            order = rerank(query, [p.payload["description"] for p in points])
            points = [points[i] for i in order]
            mode += "_reranked"
        return [
            {
                "source_id": "history:" + str(p.id),
                "description": p.payload["description"],
                "reference_code": p.payload["code"],
                "source": p.payload["source"],
                "score": p.score,
                "group_id": p.payload["group_id"],
                "generation": self.manifest["generation"],
                "retrieval_mode": mode,
                "label_status": p.payload["label_status"],
                "authority": "tariff_rule" if self.kind == "tariff_rule" else "historical_reference_only",
            }
            for p in points[:limit]
        ]

    def snapshot(self):
        if not self.manifest:
            raise ValueError("No active history index")
        return self.client.create_snapshot(self.manifest["collection"]).name

    def close(self):
        self.client.close()


def catalog_settings(settings):
    return settings.model_copy(
        update={"history_directory": settings.history_directory / "rules", "history_alias": "tariff_catalog"}
    )


def retrieval_ready(settings, dense=False):
    indexes = []
    if settings.history_enabled:
        indexes.append((settings, "historical_case"))
    if settings.qdrant_catalog_enabled:
        indexes.append((catalog_settings(settings), "tariff_rule"))
    for config, kind in indexes:
        index = History(config, kind=kind)
        try:
            state = index.status()
            needs_dense = dense and (kind == "tariff_rule" or settings.history_dense_enabled)
            if (
                not state["active"]
                or not state["points"]
                or (needs_dense and not state["dense_search_ready"])
            ):
                return False
        except Exception:
            return False
        finally:
            index.close()
    return True
