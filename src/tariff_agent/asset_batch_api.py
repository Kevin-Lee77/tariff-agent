"""Business file batches and immutable dictionary operations; reuse session/CSRF/role checks."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import Field

from .asset_api import Entry, Review
from .asset_batches import MAX_FILE, AssetBatches, csv_bytes
from .asset_operations import DictionaryEditor
from .business_rules import KINDS
from .domain import Strict
from .persistence import database_target


class Operation(Strict):
    action: Literal["add", "delete", "confirm", "unconfirm", "rename", "promote", "move", "merge", "split"]
    entry_ids: list[str] = Field(default_factory=list, max_length=1000)
    value: str = Field(default="", max_length=1000)
    entry: Entry | None = None


class Edit(Review):
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    operations: list[Operation] = Field(min_length=1, max_length=100)


class VersionAction(Review):
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    target_id: str = ""


class Export(Strict):
    asset_kind: Literal["bill", "negative", "entries", "dictionaries", "reports"]
    dictionary_id: str | None = None
    synthetic: bool | None = None


def router(settings, user, reviewer, idem):
    api = APIRouter(prefix="/v1/business")

    def batches():
        return AssetBatches(database_target(settings), auto_create=not settings.production)

    def editor():
        return DictionaryEditor(database_target(settings), auto_create=not settings.production)

    @api.get("/templates/{kind}", dependencies=[Depends(user)])
    def template(kind: Literal["dictionary", "bill", "negative"]):
        headers = {
            "dictionary": [
                "term",
                "canonical",
                "element_code",
                "element_name",
                "scope",
                "frequency",
                "payload",
            ],
            "bill": ["code_ts", "description", "elements"],
            "negative": ["knowledge_id", "fit_area", "element_code", "vocabulary", "threshold"],
        }[kind]
        return Response(
            csv_bytes(headers, []),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="' + kind + '-template.csv"'},
        )

    @api.post("/imports", status_code=202, dependencies=[Depends(user)])
    async def upload(
        request: Request,
        filename: str = Query(max_length=128),
        asset_kind: Literal["dictionary", "bill", "negative"] = "dictionary",
        dictionary_kind: str = "synonym",
        name: str = Query(default="导入词典", min_length=1, max_length=100),
        synthetic: bool = True,
        key=Depends(idem),
    ):
        if dictionary_kind not in KINDS:
            raise HTTPException(422, "Unsupported dictionary type")
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > MAX_FILE:
                raise HTTPException(413, "File exceeds 4 MiB")
        return batches().create(
            bytes(content),
            filename,
            {
                "operation": "import",
                "asset_kind": asset_kind,
                "dictionary_kind": dictionary_kind,
                "name": name,
                "synthetic": synthetic,
            },
            request.state.actor["username"],
            key,
        )

    @api.post("/exports", status_code=202, dependencies=[Depends(user)])
    def export(body: Export, request: Request, key=Depends(idem)):
        return batches().create_export(
            body.asset_kind, body.dictionary_id, body.synthetic, request.state.actor["username"], key
        )

    @api.get("/batches", dependencies=[Depends(user)])
    def listing(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
        return batches().list_jobs(kind="asset_batch", offset=offset, limit=limit)

    @api.get("/batches/{key}", dependencies=[Depends(user)])
    def detail(
        key: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=100),
        errors_only: bool = False,
    ):
        return batches().detail(key, offset, limit, errors_only)

    @api.get("/batches/{key}/report", dependencies=[Depends(user)])
    def report(key: str, errors_only: bool = False):
        name, content = batches().report(key, errors_only)
        return Response(
            content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="' + name + '"'},
        )

    @api.get("/dictionaries/{key}", dependencies=[Depends(user)])
    def dictionary(key: str):
        return editor().detail(key)

    @api.post("/dictionaries/{key}/operations", dependencies=[Depends(reviewer)])
    def edit(key: str, body: Edit, request: Request):
        for operation in body.operations:
            if operation.action == "add" and operation.entry is None:
                raise HTTPException(422, "An add operation requires an entry")
        return editor().edit(
            key,
            body.expected_revision,
            [o.model_dump() for o in body.operations],
            request.state.actor["username"],
            body.reason,
        )

    @api.post("/dictionaries/{key}/restore", dependencies=[Depends(reviewer)])
    def restore(key: str, body: VersionAction, request: Request):
        return editor().restore(
            key, body.target_id, body.expected_revision, request.state.actor["username"], body.reason
        )

    @api.post("/dictionaries/{key}/withdraw", dependencies=[Depends(reviewer)])
    def withdraw(key: str, body: VersionAction, request: Request):
        return editor().withdraw(key, body.expected_revision, request.state.actor["username"], body.reason)

    return api
