"""Bridge business records from recovered Java SynonymResult, never accept SQL."""

from typing import Literal

from pydantic import Field

from .domain import Strict
from .governance import TermRequest, negative


class SynonymRow(Strict):
    uuid: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    parentUuid: str | None = Field(default=None, max_length=100)
    cnt: int = Field(default=1, ge=0)
    status: Literal["UNTRAINED", "CONFIRMED", "UNCONFIRMED"] = "UNCONFIRMED"
    manualIntervention: bool = False
    elementName: str = Field(default="", max_length=100)
    elementCode: str = Field(default="", max_length=100)


class JavaSynonymRequest(Strict):
    taskId: str = Field(min_length=1, max_length=100)
    rows: list[SynonymRow] = Field(min_length=1, max_length=2000)
    synthetic: bool = False


def convert_synonyms(request):
    rows = {row.uuid: row for row in request.rows}
    if len(rows) != len(request.rows):
        raise ValueError("Duplicate Java row UUID")
    known, pending, frequencies = {}, [], {}
    for row in request.rows:
        frequencies[row.name] = frequencies.get(row.name, 0) + row.cnt
        if row.status != "CONFIRMED":
            pending.append(row.name)
            continue
        root, visited = row, set()
        while root.parentUuid:
            if root.uuid in visited or root.parentUuid not in rows:
                raise ValueError("Cyclic or orphan Java synonym tree")
            visited.add(root.uuid)
            parent = rows[root.parentUuid]
            if parent.status != "CONFIRMED" or parent.elementCode != row.elementCode:
                raise ValueError("Confirmed aliases require a confirmed parent in the same element")
            root = parent
        if negative(root.name) != negative(row.name):
            raise ValueError("Positive and negative terms must not share a group")
        aliases = known.setdefault(root.name, [])
        if row.name != root.name and row.name not in aliases:
            aliases.append(row.name)
    return TermRequest(known=known, pending=pending, frequencies=frequencies, synthetic=request.synthetic)
