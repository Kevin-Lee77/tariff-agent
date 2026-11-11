"""Authenticated data workbench endpoints; write operations retain revision history."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field

from .assets import Assets
from .domain import CaseInput, Element, Strict
from .persistence import database_target


class Review(Strict):
    reason: str = Field(min_length=3, max_length=1000)


class Entry(Strict):
    term: str = Field(min_length=1, max_length=1000)
    canonical: str = Field(default="", max_length=1000)
    element_code: str = Field(default="", max_length=80)
    element_name: str = Field(default="", max_length=100)
    scope: str = Field(default="", pattern=r"^\d{0,10}$")
    frequency: int = Field(default=0, ge=0)
    status: str = Field(default="unreviewed", max_length=40)
    payload: dict = Field(default_factory=dict)


class Revision(Review):
    name: str = Field(default="", max_length=200)
    entries: list[Entry] | None = Field(default=None, max_length=10000)
    config: dict | None = None


class Execute(Strict):
    classify: bool = False
    retrieval_profile: str = "hybrid"


class BillRevision(Review):
    declared_code: str = Field(pattern=r"^\d{6}(?:\d{2}){0,2}$")
    elements: list[Element] = Field(min_length=1, max_length=50)


def router(settings, user, reviewer, idem):
    route = APIRouter(prefix="/v1/assets")

    def assets():
        return Assets(database_target(settings), auto_create=not settings.production)

    @route.get("/summary", dependencies=[Depends(user)])
    def summary():
        return assets().stats()

    @route.get("/{kind}", dependencies=[Depends(user)])
    def listing(
        kind: str,
        query: str = Query("", max_length=200),
        synthetic: bool | None = None,
        offset: int = Query(0, ge=0),
        limit: int = Query(30, ge=1, le=100),
        dictionary_id: str | None = None,
    ):
        if kind not in ("bill", "negative", "dictionaries", "entries"):
            raise HTTPException(404, "Unknown asset kind")
        return assets().list_assets(kind, query, synthetic, offset, limit, dictionary_id)

    @route.get("/bill/{key}", dependencies=[Depends(user)])
    def detail(key: str):
        return assets().get_bill(key)

    @route.post("/bill/{key}/execute", status_code=202, dependencies=[Depends(user)])
    def execute(key: str, body: Execute, request: Request, token=Depends(idem)):
        row = assets().get_bill(key)
        case = CaseInput(
            bill_id=key,
            description=row["description"],
            declared_code=row["code_ts"],
            governance_profile="sandbox" if row["synthetic"] else "standard",
            retrieval_profile=body.retrieval_profile,
            elements=[
                Element(name=e["name"] or "要素" + e["code"], value=e["value"], code=e["code"])
                for e in row["elements"]
                if e["value"].strip()
            ],
        )
        return request.app.state.store.submit(
            "case" if body.classify else "declaration_risk", case.model_dump(), token
        )

    @route.post("/bill/{key}/revisions", status_code=201, dependencies=[Depends(user)])
    def bill_revision(key: str, body: BillRevision, request: Request):
        return assets().revise_bill(
            key, body.elements, body.declared_code, request.state.actor["username"], body.reason
        )

    @route.post("/dictionaries/{key}/publish", dependencies=[Depends(reviewer)])
    def publish(key: str, body: Review, request: Request):
        return assets().publish(key, request.state.actor["username"], body.reason)

    @route.post("/dictionaries/{key}/revisions", status_code=201, dependencies=[Depends(reviewer)])
    def revise(key: str, body: Revision, request: Request):
        config = body.config
        if config is not None:
            if not isinstance(config.get("coverage", []), list):
                raise HTTPException(422, "Invalid coverage")
            for scope in config.get("coverage", []):
                import re

                if (
                    not isinstance(scope, dict)
                    or not re.fullmatch(r"\d{2,10}", str(scope.get("prefix", "")))
                    or not isinstance(scope.get("elements"), list)
                ):
                    raise HTTPException(422, "Invalid coverage prefix/elements")
        entries = (
            [e.model_dump() | {"canonical": e.canonical or e.term} for e in body.entries]
            if body.entries is not None
            else None
        )
        return assets().revise(key, body.name, entries, config, request.state.actor["username"], body.reason)

    return route
