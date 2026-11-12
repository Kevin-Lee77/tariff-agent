import logging
import os
import signal
import socket
import threading

from .clients import Models
from .config import Settings
from .domain import CaseInput
from .governance import RiskRequest, TermRequest, load_w2v, normalize_terms, risk_check
from .governance_tools import GovernanceTools
from .knowledge import cached_knowledge
from .store import Conflict, configured_store
from .workflow import Workflow

log = logging.getLogger(__name__)


def process_one(settings, models=None):
    store = configured_store(settings)
    identifier = f"{socket.gethostname()}:{os.getpid()}"
    store.ping_worker(identifier)
    job = store.claim(settings.lease_seconds, settings.max_attempts)
    if job is None:
        return False
    is_batch = job["kind"] == "asset_batch"
    owned = models is None and not is_batch
    models = models if is_batch else models or Models(settings)
    if hasattr(models, "bind"):
        models.bind(store, job["id"], job["token"])
    done = threading.Event()

    def heartbeat():
        while not done.wait(min(10, max(1, settings.lease_seconds / 3))):
            store.ping_worker(identifier)
            if not store.renew(job["id"], job["token"], settings.lease_seconds):
                return

    lease_thread = threading.Thread(target=heartbeat, daemon=True)
    lease_thread.start()
    try:
        if is_batch:
            from .asset_batches import process_batch

            process_batch(settings, store, job)
            return True
        if job["kind"] == "terms":
            result = normalize_terms(TermRequest.model_validate(job["payload"]), models.embed)
            # Persist per-task proposal, not a global overwrite: operator controls publishing.
            status = "completed"
        elif job["kind"] == "risk":
            result = risk_check(RiskRequest.model_validate(job["payload"]), load_w2v(str(settings.word2vec)))
            status = "completed"
        elif job["kind"] in ("declaration_risk", "declaration_language"):
            case = CaseInput.model_validate(job["payload"])
            tools = GovernanceTools(settings)
            result = (
                tools.tax_source_risk_check(case)
                if job["kind"] == "declaration_risk"
                else tools.tax_language_normalize(case, store.terms())
            )
            status = (
                {
                    "clear": "completed",
                    "blocked": "risk_blocked",
                    "information_required": "awaiting_information",
                }.get(result.get("decision"), "risk_review_required")
                if job["kind"] == "declaration_risk"
                else "completed"
                if result.get("status") == "processed"
                else "language_review_required"
            )
        else:
            raw = {k: v for k, v in job["payload"].items() if not k.startswith("_")}
            case = CaseInput.model_validate(raw)
            execution = (
                settings.model_copy(update={"dense_enabled": False, "rerank_enabled": False})
                if case.retrieval_profile == "bm25_diagnostic"
                else settings
            )
            k = cached_knowledge(settings.corpus, settings.allow_test_corpus)
            result = Workflow(k, models, store, execution).run(job)
            result["retrieval_profile"] = case.retrieval_profile
            status = result["status"]
        store.finish(job["id"], job["token"], status, result)
        store.event(job["id"], "finished", {"status": status})
    except Conflict:
        log.warning("task_lease_lost id=%s", job["id"])
    except Exception as exc:
        # No data, tokens or provider bodies in error response/logs.
        try:
            store.finish(job["id"], job["token"], "failed", error=type(exc).__name__)
            store.event(job["id"], "failed", {"type": type(exc).__name__})
        except Conflict:
            log.warning("task_lease_lost id=%s", job["id"])
        log.error("task_failed id=%s type=%s", job["id"], type(exc).__name__)
    finally:
        done.set()
        lease_thread.join(timeout=2)
        if owned:
            models.close()
    return True


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()
    stopping = threading.Event()
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda *_: stopping.set())
    with_models = Models(settings)
    try:
        while not stopping.is_set():
            if not process_one(settings, with_models):
                stopping.wait(1)
    finally:
        with_models.close()


if __name__ == "__main__":
    main()
