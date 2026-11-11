"""OpenTelemetry task/node/tool/model spans; SQL is local, OTLP export is explicitly opt-in."""

import contextlib
import json
from contextvars import ContextVar

from opentelemetry import baggage, trace
from opentelemetry import context as otel_context
from opentelemetry.propagate import inject
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import Status, StatusCode
from sqlalchemy import insert, update
from sqlalchemy.exc import IntegrityError

from .persistence import agent_spans

ACTIVE_TRACER = ContextVar("tariff_agent_tracer", default=None)
TRACE_SCOPE = ContextVar("tariff_trace_scope", default=None)
EXPERIMENT = ContextVar("tariff_experiment", default=None)


@contextlib.contextmanager
def experiment_item(experiment_id, name, dataset_id, item_id, expected_code):
    values = {
        "langfuse.experiment.id": experiment_id,
        "langfuse.experiment.name": name,
        "langfuse.experiment.dataset.id": dataset_id,
        "langfuse.experiment.item.id": item_id,
        "langfuse.environment": "experiment",
    }
    token = EXPERIMENT.set(values | {"expected_code": expected_code})
    current = otel_context.Context()
    for key, value in values.items():
        current = baggage.set_baggage(key, value, context=current)
    attached = otel_context.attach(current)
    try:
        yield
    finally:
        otel_context.detach(attached)
        EXPERIMENT.reset(token)


class LocalExporter(SpanExporter):
    def __init__(self, store, job_id, run_id):
        self.store, self.job_id, self.run_id = store, job_id, run_id

    def export(self, spans):
        try:
            with self.store.connect() as db:
                for value in spans:
                    db.execute(
                        insert(agent_spans).values(
                            span_id=f"{value.context.span_id:016x}",
                            trace_id=f"{value.context.trace_id:032x}",
                            parent_id=f"{value.parent.span_id:016x}" if value.parent else None,
                            job_id=self.job_id,
                            run_id=self.run_id,
                            name=value.name,
                            status=value.status.status_code.name,
                            started=value.start_time / 1e9,
                            ended=value.end_time / 1e9,
                            attributes=json.dumps(dict(value.attributes or {}), ensure_ascii=False),
                        )
                    )
            return SpanExportResult.SUCCESS
        except IntegrityError:
            return SpanExportResult.FAILURE

    def shutdown(self):
        pass


class TraceSession:
    def __init__(self, exporter, root):
        self.exporter, self.root = exporter, root
        self.trace_id = f"{root.get_span_context().trace_id:032x}"

    def bind_run(self, run_id):
        self.exporter.run_id = run_id
        self.root.set_attribute("run.id", run_id)
        self.root.set_attribute("langfuse.trace.metadata.run_id", run_id)
        scope = TRACE_SCOPE.get()
        if scope is not None:
            scope["run_id"] = run_id
        with self.exporter.store.connect() as db:
            db.execute(
                update(agent_spans).where(agent_spans.c.trace_id == self.trace_id).values(run_id=run_id)
            )

    def complete(self, result):
        if EXPERIMENT.get():
            self.root.set_attribute(
                "langfuse.observation.output",
                json.dumps({"status": result["status"], "predicted_codes": result.get("resolved", [])}),
            )


@contextlib.contextmanager
def task_trace(store, settings, job_id, run_id, name="agent.run", parent_context=None):
    provider = TracerProvider(
        resource=Resource.create({"service.name": "tariff-agent"}), shutdown_on_exit=False
    )
    exporter = LocalExporter(store, job_id, run_id)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    if settings.otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=settings.otlp_endpoint,
                    headers=json.loads(settings.otlp_headers.get_secret_value() or "{}"),
                    timeout=5,
                )
            )
        )
    token = ACTIVE_TRACER.set(provider.get_tracer("tariff.agent"))
    scope_token = TRACE_SCOPE.set({"job_id": job_id, "run_id": run_id})
    try:
        with span(name, {"job.id": job_id, "run.id": run_id}, context=parent_context) as current:
            if EXPERIMENT.get() and name == "agent.run":
                root_id = f"{current.get_span_context().span_id:016x}"
                TRACE_SCOPE.get()["experiment_root_id"] = root_id
                current.set_attribute("langfuse.experiment.item.root_observation_id", root_id)
                current.set_attribute("langfuse.observation.input", json.dumps({"case_id": job_id}))
                current.set_attribute(
                    "langfuse.experiment.item.expected_output",
                    json.dumps({"code": EXPERIMENT.get()["expected_code"]}),
                )
            yield TraceSession(exporter, current)
    finally:
        ACTIVE_TRACER.reset(token)
        TRACE_SCOPE.reset(scope_token)
        provider.shutdown()


@contextlib.contextmanager
def span(name, attributes=None, context=None):
    tracer = ACTIVE_TRACER.get()
    if tracer is None:
        yield trace.get_current_span()
        return
    attributes = dict(attributes or {})
    experiment = EXPERIMENT.get() or {
        key: value
        for key, value in baggage.get_all(context=context).items()
        if key.startswith("langfuse.experiment.") or key == "langfuse.environment"
    }
    attributes.update({key: value for key, value in experiment.items() if key != "expected_code"})
    scope = TRACE_SCOPE.get()
    if scope:
        attributes.update(
            {
                "langfuse.session.id": scope["job_id"],
                "langfuse.trace.name": "customs-classification",
                "langfuse.trace.metadata.run_id": scope["run_id"],
            }
        )
        if scope.get("experiment_root_id"):
            attributes["langfuse.experiment.item.root_observation_id"] = scope["experiment_root_id"]
    if name.startswith("model.") and name not in ("model.embedding", "model.rerank"):
        attributes["langfuse.observation.type"] = "generation"
    with tracer.start_as_current_span(
        name,
        context=context,
        attributes=attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as current:
        try:
            yield current
        except BaseException as exc:
            # Do not export raw exception messages, prompts, product text or provider response bodies.
            current.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            current.set_attribute("error.type", type(exc).__name__)
            raise


def trace_headers(headers=None, internal=False):
    carrier = dict(headers or {})
    if ACTIVE_TRACER.get() is not None:
        outbound = otel_context.get_current()
        scope = TRACE_SCOPE.get()
        if scope and scope.get("experiment_root_id"):
            outbound = baggage.set_baggage(
                "langfuse.experiment.item.root_observation_id", scope["experiment_root_id"], context=outbound
            )
        inject(carrier, context=outbound)
        if internal and TRACE_SCOPE.get():
            carrier["X-Agent-Job-Id"] = TRACE_SCOPE.get()["job_id"]
            carrier["X-Agent-Run-Id"] = TRACE_SCOPE.get()["run_id"]
    return carrier


def trace_id():
    context = trace.get_current_span().get_span_context()
    return f"{context.trace_id:032x}" if context.is_valid else None
