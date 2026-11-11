"""Content-addressed run versions; credentials and model guesses never become policy."""

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select

from .persistence import dictionary_version


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def input_revision(payload):
    return digest({k: v for k, v in payload.items() if not k.startswith("_")})


@lru_cache(maxsize=64)
def _file_hash(path, size, modified):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def file_hash(path):
    path = Path(path)
    if not path.is_file():
        return "absent"
    stat = path.stat()
    return _file_hash(str(path.resolve()), stat.st_size, stat.st_mtime_ns)


def versions(settings, store):
    paths = {
        "corpus": settings.corpus,
        "risk": settings.risk_rules,
        "language": settings.language_rules,
        "standard": settings.rules,
        "word2vec": settings.word2vec,
    }
    domain = {name: file_hash(path) for name, path in paths.items()}
    if settings.business_assets_enabled:
        with store.connect() as db:
            domain["dictionaries"] = digest(
                [
                    dict(row._mapping)
                    for row in db.execute(
                        select(
                            dictionary_version.c.id,
                            dictionary_version.c.config,
                            dictionary_version.c.active,
                            dictionary_version.c.status,
                        ).order_by(dictionary_version.c.id)
                    )
                ]
            )
    domain["published_terms"] = digest(store.terms())
    domain_version = digest(domain)
    module = Path(__file__).parent
    execution = {
        "files": {
            name: file_hash(module / name)
            for name in (
                "workflow.py",
                "prompts.py",
                "retrieval.py",
                "clients.py",
                "runtime_context.py",
                "agent_memory.py",
                "knowledge.py",
                "history.py",
                "declaration.py",
                "governance.py",
                "durable.py",
                "context_engineering.py",
                "agent_skills.py",
                "agentic_retrieval.py",
                "human_review.py",
                "governance_tools.py",
                "assets.py",
                "domain.py",
            )
        },
        "models": [settings.deepseek_model, settings.embedding_model, settings.small_model_name],
        "artifacts": file_hash(Path("models/manifest.json")),
        "context_tokenizer": file_hash(settings.context_tokenizer),
        "indexes": {
            name: file_hash(settings.history_directory / path)
            for name, path in (("history", "active.json"), ("catalog", "rules/active.json"))
        },
        "options": [
            settings.small_model_enabled,
            settings.dense_enabled,
            settings.rerank_enabled,
            settings.history_enabled,
            settings.history_dense_enabled,
            settings.qdrant_catalog_enabled,
            settings.evidence_budget_chars,
            settings.memory_context_budget_chars,
            settings.max_model_calls,
            settings.expert_memory_enabled,
            settings.expert_memory_limit,
            settings.durable_execution_enabled,
            settings.context_compression_enabled,
            settings.context_max_input_tokens,
            settings.agentic_retrieval_enabled,
            settings.agentic_retrieval_max_rounds,
            settings.native_human_review_enabled,
        ],
    }
    return {"domain_version": domain_version, "execution_version": digest(execution), "assets": domain}


class RunContextChanged(RuntimeError):
    """A previously pinned policy/memory no longer applies; stop before the next tool/model."""
