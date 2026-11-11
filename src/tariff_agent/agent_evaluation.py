"""Local outcome + trajectory evaluators. No cloud trace uploads or judge model by default."""

import json


def expected_stages(result, scope="end_to_end"):
    prefix = [] if scope == "classification_component" else ["risk_precheck"]
    if scope != "classification_component" and result.get("risk_check", {}).get("decision") != "clear":
        return prefix
    if scope != "classification_component":
        prefix += ["normalize"]
        if scope == "governance_component":
            return prefix
        if (
            result.get("normalization", {}).get("status") != "processed"
            or result.get("status") == "awaiting_models"
        ):
            return prefix
    prefix += ["attributes"]
    if result.get("attributes", {}).get("missing"):
        return prefix
    prefix += ["retrieve", "l1", "l2"]
    if result.get("status") == "awaiting_information":
        return prefix
    if not result.get("ranked"):
        return prefix + ["teacher"]
    return prefix + ["subheading", "verify", "teacher"]


def trajectory_score(events, result, scope="end_to_end"):
    from agentevals.trajectory.match import create_trajectory_match_evaluator

    actual = [
        e["stage"]
        for e in events
        if e["stage"]
        in (
            "risk_precheck",
            "normalize",
            "attributes",
            "retrieve",
            "l1",
            "l2",
            "subheading",
            "verify",
            "teacher",
        )
    ]
    expected = expected_stages(result, scope)

    def messages(stages):
        return [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": stage, "arguments": "{}"}}],
            }
            for stage in stages
        ]

    evaluate = create_trajectory_match_evaluator(trajectory_match_mode="strict", tool_args_match_mode="exact")
    value = evaluate(outputs=messages(actual), reference_outputs=messages(expected))
    return {
        "framework": "agentevals",
        "score": bool(value["score"]),
        "actual": actual,
        "expected": expected,
        "scope": "Graph stage order; not a claim of semantic argument correctness",
    }


def inspect_run(result, events, knowledge, scope="end_to_end"):
    run_id = result.get("provenance", {}).get("run_id")
    if run_id:
        events = [e for e in events if e["data"].get("run_id") == run_id]
    top = result.get("votes", {}).get("top_code")
    predictions = list(dict.fromkeys(([top] if top else []) + result.get("resolved", [])))
    citations = result.get("citations", [])
    valid_quotes = sum(
        bool(c.get("quote"))
        and c.get("source_id") in knowledge.sources
        and c["quote"] in knowledge.sources[c["source_id"]]
        for c in citations
    )
    path_valid = None
    if predictions:
        try:
            for code in predictions:
                knowledge.path(code)
            path_valid = True
        except ValueError:
            path_valid = False
    violations = []
    for decision in result.get("decisions", []):
        if decision.get("parent"):
            allowed = set(knowledge.children(decision["parent"]))
            if not set(decision.get("reasons", {})) <= allowed:
                violations.append(decision["parent"])
    trace = trajectory_score(events, result, scope)
    request_count = sum(e["stage"] == "model_request" for e in events)
    usage = [e["data"].get("usage", {}) for e in events if e["stage"] == "model_usage"]
    from .runtime_context import digest

    provenance = result.get("provenance", {})
    expert_references = result.get("expert_memories", [])
    fingerprinted = result.get("evidence_manifest", {})
    evidence_ok = all(
        c["source_id"] in fingerprinted
        and fingerprinted[c["source_id"]]["corpus_version"] == knowledge.fingerprint
        and fingerprinted[c["source_id"]]["content_hash"] == digest(knowledge.sources.get(c["source_id"], ""))
        for c in citations
    )
    contexts = [e["data"] for e in events if e["stage"] == "context_prepared"]
    actions = [e["data"] for e in events if e["stage"] == "agentic_action"]
    return {
        "predicted_codes": predictions,
        "path_valid": path_valid,
        "illegal_parent_decisions": violations,
        "citation_count": len(citations),
        "verified_citations": valid_quotes,
        "citation_presence": bool(citations),
        "trace": trace,
        "model_requests": request_count,
        "tokens": sum(r.get("total_tokens", 0) for r in usage),
        "run_id": provenance.get("run_id"),
        "trace_id": provenance.get("trace_id"),
        "runtime_versioned": all(
            provenance.get(k) for k in ("run_id", "domain_version", "execution_version", "input_revision")
        ),
        "memory_reference_count": len(expert_references),
        "memory_scope_valid": bool(provenance)
        and all(
            r.get("domain_version") == provenance.get("domain_version")
            and r.get("source_job") != provenance.get("case_id")
            and r.get("authority") == "reviewed_expert_reference_only_not_law"
            and r.get("reference_code") in knowledge.formal_codes
            for r in expert_references
        ),
        "evidence_fingerprints_valid": evidence_ok if citations else None,
        "target_resolved": all(
            code in knowledge.formal_codes and len(code) == result.get("case", {}).get("target_digits", 10)
            for code in predictions
        )
        if predictions
        else None,
        "evaluation_scope": scope,
        "risk_gate_respected": None
        if scope == "classification_component"
        else not (
            result.get("risk_check", {}).get("decision") != "clear"
            and scope != "classification_component"
            and any(s in trace["actual"] for s in ("normalize", "attributes", "retrieve"))
        ),
        "status": result.get("status", "failed"),
        "context_preparations": len(contexts),
        "context_before_proxy_tokens": sum(e["input_before"] for e in contexts),
        "context_after_proxy_tokens": sum(e["input_after"] for e in contexts),
        "context_omitted_records": sum(e["omitted_records"] for e in contexts),
        "context_protected_facts_valid": all(e.get("protected_facts_unchanged") for e in contexts),
        "exploration_actions": len(actions),
        "exploration_scope_valid": len(actions) <= 2
        and all(
            set(e["codes"]) <= set(result.get("shortlist", []))
            and e["action"] in ("lookup_notes", "search_cases", "compare_candidates", "ask_clarification")
            for e in actions
        ),
        "native_human_review_waiting": result.get("human_review", {}).get("status") == "waiting_for_expert",
    }


def strict_json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)
