"""Allowlisted, bounded clients used by LangGraph and exposed through MCP."""

import httpx

from .agent_telemetry import span, trace_headers
from .declaration import language_check, source_check


class GovernanceTools:
    def __init__(self, settings):
        self.s = settings

    def _call(self, base, path, payload):
        try:
            with span("tool." + ("tax_source" if "check" in path else "tax_language")):
                response = httpx.post(
                    base + path,
                    json=payload,
                    timeout=120,
                    headers=trace_headers({"X-API-Key": self.s.api_key.get_secret_value()}, internal=True),
                )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError):
            return {
                "decision": "review_required",
                "status": "unavailable",
                "reason": "税智服务暂不可用，停止后续自动处理",
                "hits": [],
                "annotations": [],
            }

    def tax_source_risk_check(self, case):
        if self.s.governance_transport == "embedded":
            return self._record("source", source_check(case, self.s), case)
        return self._call(self.s.tax_source_url, "/v1/declarations/check", case.model_dump())

    def tax_language_normalize(self, case, published=None):
        if self.s.governance_transport == "embedded":
            return self._record("language", language_check(case, self.s, published), case)
        return self._call(self.s.tax_language_url, "/v1/declarations/normalize", case.model_dump())

    def _record(self, kind, result, case):
        if self.s.business_assets_enabled:
            from .assets import Assets
            from .persistence import database_target

            result["report_id"] = Assets(
                database_target(self.s), auto_create=not self.s.production
            ).save_report(kind, result, case.bill_id)
        return result
