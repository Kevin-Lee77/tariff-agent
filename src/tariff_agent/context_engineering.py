"""Lossless fact protection, whole-evidence selection and recoverable context offloading."""

import copy
import json
from functools import lru_cache

from .clients import ModelError
from .runtime_context import digest


class ContextBudgetExceeded(ModelError):
    pass


def serialize(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@lru_cache(maxsize=2)
def load_counter(path, modified):
    from tokenizers import Tokenizer

    return Tokenizer.from_file(path)


class ContextPacker:
    # Everything else is protected. Lists are selected as whole records, never substring-truncated.
    OPTIONAL = (
        "evidence",
        "tool_observations",
        "reviewed_expert_references_not_legal_authority",
        "historical_references_not_legal_authority",
    )

    def __init__(self, settings):
        self.s = settings
        path = settings.context_tokenizer
        self.tokenizer = (
            load_counter(str(path.resolve()), path.stat().st_mtime_ns) if path.is_file() else None
        )
        self.counter_kind = (
            "qwen_tokenizer_proxy_not_provider_billing" if self.tokenizer else "utf8_byte_upper_bound_proxy"
        )

    def count(self, value):
        text = value if isinstance(value, str) else serialize(value)
        return (
            len(self.tokenizer.encode(text, add_special_tokens=False).ids)
            if self.tokenizer
            else len(text.encode())
        )

    def pack(self, stage, payload, schema):
        original = copy.deepcopy(payload)
        # Reserve space for system instructions, schema and the transport envelope.
        reserve = self.count(schema.model_json_schema()) + 768
        budget = self.s.context_max_input_tokens - reserve
        before = self.count(original)
        artifact = digest([stage, original])
        if not self.s.context_compression_enabled:
            return original, self.report(before, before, artifact, False, 0)
        packed = copy.deepcopy({key: value for key, value in original.items() if key not in self.OPTIONAL})
        candidate_rows = [row for row in packed.get("candidates", []) if isinstance(row, dict)]
        note_ids = {row["code"]: row.get("note_ids", []) for row in candidate_rows}
        for row in candidate_rows:
            row["note_ids"] = []
        packed["context_policy"] = (
            "Selected whole records; omitted evidence is not evidence of absence. Original records are recoverable by artifact ID."
        )
        if self.count(packed) > budget:
            raise ContextBudgetExceeded(
                "Protected case facts/candidates exceed context budget; cannot truncate safely"
            )
        omitted = 0
        for field in self.OPTIONAL:
            if field not in original:
                continue
            packed[field] = []
            seen = set()
            for item in original[field]:
                identity = digest(item)
                if identity in seen:
                    omitted += 1
                    continue
                seen.add(identity)
                trial = copy.deepcopy(packed)
                trial[field].append(item)
                if field == "evidence":
                    kept = {r["id"] for r in trial[field]}
                    for row in trial.get("candidates", []):
                        if not isinstance(row, dict):
                            continue
                        row["note_ids"] = [key for key in note_ids.get(row["code"], []) if key in kept]
                if self.count(trial) <= budget:
                    packed = trial
                else:
                    omitted += 1
        after = self.count(packed)
        return packed, self.report(before, after, artifact, packed != original, omitted)

    def report(self, before, after, artifact, changed, omitted):
        return {
            "artifact_id": artifact,
            "counter_kind": self.counter_kind,
            "input_before": before,
            "input_after": after,
            "input_budget": self.s.context_max_input_tokens,
            "changed": changed,
            "omitted_records": omitted,
            "protected_facts_unchanged": True,
        }
