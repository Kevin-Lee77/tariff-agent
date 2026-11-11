from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Element(Strict):
    name: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=6000)
    code: str = Field(default="", max_length=80)


class CaseInput(Strict):
    bill_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    governance_profile: Literal["standard", "sandbox"] = "standard"
    target_digits: Literal[6, 8, 10] = 10
    retrieval_profile: Literal["hybrid", "bm25_diagnostic"] = "hybrid"
    description: str = Field(min_length=2, max_length=6000)
    elements: list[Element] = Field(default_factory=list, max_length=50)
    declared_code: str | None = Field(default=None, pattern=r"^\d{2,10}$")


class Attributes(Strict):
    material: str = ""
    function: str = ""
    use: str = ""
    form: str = ""
    processing: str = ""
    negations: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class Citation(Strict):
    source_id: str
    quote: str = Field(min_length=1)


class Ranking(Strict):
    codes: list[str] = Field(min_length=1, max_length=10)
    reasons: dict[str, str] = Field(default_factory=dict)
    citations: list[Citation] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)


class Report(Strict):
    summary: str
    alternatives: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    basis_to_verify: list[str] = Field(default_factory=list)


class Feedback(Strict):
    approved: bool
    final_code: str | None = Field(default=None, pattern=r"^\d{6}(?:\d{2}){0,2}$")
    reason: str = Field(min_length=3, max_length=3000)
    reviewer: str = Field(min_length=1, max_length=100)
    expected_revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def require_code(self):
        if self.approved and not self.final_code:
            raise ValueError("Approval requires final_code")
        return self


class Note(Strict):
    id: str
    text: str = Field(min_length=1)
    kind: Literal["context", "exclude", "require", "priority"] = "context"
    attribute: str = ""
    value: str = ""


class Entry(Strict):
    code: str = Field(pattern=r"^\d{2}(?:\d{2}){0,4}$")
    parent: str | None = None
    text: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    notes: list[Note] = Field(default_factory=list)


class Corpus(Strict):
    jurisdiction: str = "CN"
    version: str = Field(min_length=1)
    source: str = Field(min_length=1)
    test_only: bool = False
    general_notes: list[Note] = Field(default_factory=list)
    groups: list["SubheadingGroup"] = Field(default_factory=list)
    entries: list[Entry] = Field(min_length=1)

    @model_validator(mode="after")
    def hierarchy(self):
        by_code = {e.code: e for e in self.entries}
        if len(by_code) != len(self.entries):
            raise ValueError("Duplicate codes")
        sources = [e.source_id for e in self.entries]
        sources += [g.source_id for g in self.groups]
        sources += [n.id for e in self.entries for n in e.notes] + [n.id for n in self.general_notes]
        if len(sources) != len(set(sources)):
            raise ValueError("Source IDs must be unique")
        for e in self.entries:
            if len(e.code) == 2:
                if e.parent is not None:
                    raise ValueError("Chapter cannot have parent")
            elif e.parent != e.code[:-2] or e.parent not in by_code:
                raise ValueError(f"Missing or illegal parent: {e.code}")
        prefixes = set()
        for group in self.groups:
            if group.code in prefixes or group.parent != group.code[:-1] or group.parent not in by_code:
                raise ValueError("Invalid structural group")
            prefixes.add(group.code)
        return self


class SubheadingGroup(Strict):
    code: str = Field(pattern=r"^\d{5}(?:\d{2}){0,2}$")
    parent: str
    text: str
    source_id: str
    notes: list[Note] = Field(default_factory=list)


Corpus.model_rebuild()
