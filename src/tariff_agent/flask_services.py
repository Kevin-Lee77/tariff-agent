"""Standalone tax-source and tax-language WSGI applications; never run Flask debug server."""

import re
import secrets
from contextlib import nullcontext

from flask import Flask, jsonify, request
from opentelemetry.propagate import extract
from pydantic import ValidationError
from werkzeug.exceptions import HTTPException

from .agent_telemetry import task_trace
from .config import Settings
from .declaration import language_check, source_check
from .domain import CaseInput
from .store import configured_store


def create_service(kind, settings=None):
    settings = settings or Settings()
    key = settings.api_key.get_secret_value()
    if len(key) < 24:
        raise RuntimeError("A service API key of at least 24 characters is required")
    app = Flask("tax-" + kind)
    app.config["MAX_CONTENT_LENGTH"] = 256 * 1024

    @app.before_request
    def authenticate():
        if request.path != "/health/live" and not secrets.compare_digest(
            request.headers.get("X-API-Key", ""), key
        ):
            return jsonify(error="Unauthorized"), 401

    @app.get("/health/live")
    def health():
        return {"status": "alive", "service": "tax-" + kind}

    path = "/v1/declarations/check" if kind == "source" else "/v1/declarations/normalize"

    @app.post(path)
    def process():
        try:
            case = CaseInput.model_validate(request.get_json())
        except ValidationError:
            return jsonify(error="Invalid declaration payload"), 422
        context = nullcontext()
        job_id, run_id = request.headers.get("X-Agent-Job-Id", ""), request.headers.get("X-Agent-Run-Id", "")
        if re.fullmatch(r"[a-f0-9]{32}", job_id) and re.fullmatch(r"[a-f0-9]{32}", run_id):
            store = configured_store(settings)
            try:
                if store.get(job_id)["status"] == "running":
                    context = task_trace(
                        store,
                        settings,
                        job_id,
                        run_id,
                        name="service.tax-" + kind,
                        parent_context=extract({k.lower(): v for k, v in request.headers.items()}),
                    )
            except KeyError:
                pass
        with context:
            if kind == "source":
                result = source_check(case, settings)
            else:
                result = language_check(case, settings, configured_store(settings).terms())
        if settings.business_assets_enabled:
            from .assets import Assets
            from .persistence import database_target

            result["report_id"] = Assets(
                database_target(settings), auto_create=not settings.production
            ).save_report(kind, result, case.bill_id)
        return result

    @app.errorhandler(Exception)
    def failure(exc):
        if isinstance(exc, HTTPException):
            return jsonify(error=exc.name), exc.code
        app.logger.error("declaration_processing_failed type=%s", type(exc).__name__)
        return jsonify(error="Processing unavailable; do not continue automatically"), 503

    return app


def create_source_app():
    return create_service("source")


def create_language_app():
    return create_service("language")
