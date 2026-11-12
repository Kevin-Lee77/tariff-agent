"""Bounded file import/export with durable row progress and lease-fenced atomic application."""

import csv
import hashlib
import io
import itertools
import json
import re
import time
import uuid
import zipfile
from pathlib import PurePosixPath

from pydantic import ValidationError
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError

from .assets import Assets
from .business_rules import validate_entries
from .domain import CaseInput
from .persistence import asset_batch_files, asset_batch_rows, audit, database_target, events, jobs
from .store import Conflict, dump

MAX_FILE = 4 * 1024 * 1024
MAX_ROWS = 5000
MAX_EXPANDED = 32 * 1024 * 1024


def safe_cell(value):
    value = str(value) if value is not None else ""
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def csv_bytes(headers, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows([[safe_cell(value) for value in row] for row in rows])
    return b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8")


def read_rows(filename, content):
    if filename.lower().endswith(".csv"):
        try:
            values = list(
                itertools.islice(
                    csv.reader(io.StringIO(content.decode("utf-8-sig"), newline=""), strict=True),
                    MAX_ROWS + 2,
                )
            )
        except (UnicodeError, csv.Error):
            raise ValueError("CSV must use UTF-8 encoding and valid quoting") from None
    elif filename.lower().endswith(".xlsx"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if (
                    len(archive.infolist()) > 2000
                    or sum(info.file_size for info in archive.infolist()) > MAX_EXPANDED
                ):
                    raise ValueError("Workbook expanded size exceeds limit")
                if any("vbaProject" in info.filename for info in archive.infolist()):
                    raise ValueError("Macro workbooks are not supported")
            from openpyxl import load_workbook

            book = load_workbook(io.BytesIO(content), read_only=True, data_only=False, keep_links=False)
            try:
                if len(book.worksheets) != 1:
                    raise ValueError("Workbook must have exactly one sheet")
                if book.active.max_column > 30 or book.active.max_row > MAX_ROWS + 1:
                    raise ValueError("Workbook exceeds row/column limit")
                values = []
                for row in book.active.iter_rows():
                    if len(values) > MAX_ROWS or len(row) > 30:
                        raise ValueError("Workbook exceeds row/column limit")
                    if any(cell.data_type == "f" for cell in row):
                        raise ValueError("Formula cells are not accepted")
                    values.append(["" if cell.value is None else str(cell.value) for cell in row])
            finally:
                book.close()
        except (zipfile.BadZipFile, KeyError, OSError):
            raise ValueError("Invalid XLSX workbook") from None
    else:
        raise ValueError("Only CSV and XLSX files are supported")
    if not values or not 1 <= len(values) - 1 <= MAX_ROWS or len(values[0]) > 30:
        raise ValueError("Provide a header and 1..5000 data rows")
    headers = [value.strip() for value in values[0]]
    if not all(headers) or len(set(headers)) != len(headers):
        raise ValueError("Headers must be nonempty and unique")
    records = []
    for number, row in enumerate(values[1:], 2):
        if len(row) != len(headers):
            records.append((number, {}, "Column count does not match header"))
        elif any(len(str(value)) > 20000 for value in row):
            records.append((number, {}, "Cell exceeds size limit"))
        else:
            records.append((number, dict(zip(headers, row, strict=True)), ""))
    return records


def normalize_row(kind, row, dictionary_kind):
    row = {key: value.strip() if isinstance(value, str) else value for key, value in row.items()}
    if kind == "dictionary":
        if set(row) - {"term", "canonical", "element_code", "element_name", "scope", "frequency", "payload"}:
            raise ValueError("Unknown dictionary column")
        entry = {
            "term": row.get("term", "").strip(),
            "canonical": row.get("canonical", "").strip() or row.get("term", "").strip(),
            "element_code": row.get("element_code", "").strip(),
            "element_name": row.get("element_name", "").strip(),
            "scope": row.get("scope", "").strip(),
            "frequency": int(row.get("frequency") or 0),
            "payload": json.loads(row.get("payload") or "{}"),
            "status": "unreviewed",
        }
        if not 0 <= entry["frequency"] <= 2**31 - 1:
            raise ValueError("Frequency out of range")
        validate_entries(dictionary_kind, [entry])
        return entry
    if kind == "bill":
        if set(row) - {"code_ts", "description", "elements"}:
            raise ValueError("Unknown bill column")
        case = CaseInput(
            description=row.get("description", ""),
            declared_code=row.get("code_ts", ""),
            elements=json.loads(row.get("elements") or "[]"),
        )
        if not re.fullmatch(r"\d{6}(?:\d{2}){0,2}", case.declared_code or ""):
            raise ValueError("Bill requires a six/eight/ten-digit code")
        if (
            not case.elements
            or any(not e.code for e in case.elements)
            or len({e.code for e in case.elements}) != len(case.elements)
        ):
            raise ValueError("Bill requires explicit, distinct element codes")
        return {
            "codeTs": case.declared_code,
            "description": case.description,
            "elements": [e.model_dump() for e in case.elements],
            "alignment_status": "aligned",
        }
    if kind == "negative":
        if set(row) - {"knowledge_id", "fit_area", "element_code", "vocabulary", "threshold"}:
            raise ValueError("Unknown risk column")
        if (
            not re.fullmatch(r"\d{2,10}", row.get("fit_area", ""))
            or not row.get("element_code")
            or not row.get("vocabulary")
        ):
            raise ValueError("Risk requires a code prefix, element code and vocabulary")
        threshold = float(row.get("threshold") or 0.8)
        if not 0 <= threshold <= 1:
            raise ValueError("Threshold must be within 0..1")
        return row | {
            "threshold": threshold,
            "knowledge_id": row.get("knowledge_id") or "imported",
            "status": "unreviewed",
        }
    raise ValueError("Unknown import type")


class AssetBatches(Assets):
    def create(self, content, filename, params, actor, idem):
        if len(content) > MAX_FILE or not content:
            raise Conflict("File must contain 1 byte..4 MiB")
        filename = PurePosixPath(filename.replace("\\", "/")).name
        if not re.fullmatch(r"[^\x00-\x1f/\\]{1,120}\.(?:csv|xlsx)", filename, re.I):
            raise Conflict("Use a CSV/XLSX filename of at most 125 characters")
        payload = params | {
            "actor": actor,
            "filename": filename,
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        key = uuid.uuid4().hex
        try:
            with self.connect(write=True) as db:
                old = db.execute(select(jobs).where(jobs.c.idem == idem)).first()
                if old:
                    return self._replay(old, "asset_batch", payload)
                db.execute(
                    insert(jobs).values(
                        id=key,
                        kind="asset_batch",
                        payload=dump(payload),
                        original_payload=dump(payload),
                        status="queued",
                        idem=idem,
                        attempts=0,
                        created=time.time(),
                        updated=time.time(),
                    )
                )
                db.execute(insert(asset_batch_files).values(job_id=key, filename=filename, content=content))
        except IntegrityError:
            return self.replay("asset_batch", payload, idem)
        return self.get(key)

    def create_export(self, kind, dictionary_id, synthetic, actor, idem):
        payload = {
            "operation": "export",
            "asset_kind": kind,
            "dictionary_id": dictionary_id,
            "synthetic": synthetic,
            "actor": actor,
        }
        return self.submit("asset_batch", payload, idem)

    def detail(self, key, offset=0, limit=50, errors_only=False):
        job = self.get(key)
        if job["kind"] != "asset_batch":
            raise KeyError("Not a business batch")
        with self.connect() as db:
            counts = dict(
                db.execute(
                    select(asset_batch_rows.c.status, func.count())
                    .where(asset_batch_rows.c.job_id == key)
                    .group_by(asset_batch_rows.c.status)
                ).all()
            )
            query = select(asset_batch_rows).where(asset_batch_rows.c.job_id == key)
            if errors_only:
                query = query.where(asset_batch_rows.c.status == "invalid")
            rows = [
                dict(r._mapping)
                for r in db.execute(query.order_by(asset_batch_rows.c.row_number).offset(offset).limit(limit))
            ]
        for row in rows:
            row["data"] = json.loads(row["data"])
        return {"job": job, "counts": counts, "processed": sum(counts.values()), "rows": rows}

    def report(self, key, errors_only=False):
        job = self.get(key)
        if job["kind"] != "asset_batch":
            raise KeyError("Not a business batch")
        with self.connect() as db:
            if not errors_only:
                file = db.execute(select(asset_batch_files).where(asset_batch_files.c.job_id == key)).first()
                if file and file.output is not None and job["status"] == "completed":
                    return file.output_name, bytes(file.output)
            query = (
                select(asset_batch_rows)
                .where(asset_batch_rows.c.job_id == key)
                .order_by(asset_batch_rows.c.row_number)
            )
            if errors_only:
                query = query.where(asset_batch_rows.c.status == "invalid")
            rows = [(r.row_number, r.status, r.error, r.data) for r in db.execute(query)]
        return "batch-" + key[:12] + ".csv", csv_bytes(["row_number", "status", "error", "data"], rows)


def process_batch(settings, store, job):
    batch = AssetBatches(database_target(settings), auto_create=False)
    key, token, params = job["id"], job["token"], job["payload"]
    if params["operation"] == "export":
        with store.lease_guard(key, token) as db:
            kind = params["asset_kind"]
            from .persistence import (
                bill,
                bill_element,
                dictionary_entry,
                dictionary_version,
                governance_report,
                negative_rules,
            )

            table = {
                "bill": bill,
                "negative": negative_rules,
                "entries": dictionary_entry,
                "dictionaries": dictionary_version,
                "reports": governance_report,
            }[kind]
            query = select(table)
            if kind == "entries":
                query = select(dictionary_entry).join(
                    dictionary_version, dictionary_entry.c.dictionary_id == dictionary_version.c.id
                )
                if params.get("dictionary_id"):
                    query = query.where(dictionary_entry.c.dictionary_id == params["dictionary_id"])
                synthetic_column = dictionary_version.c.synthetic
            else:
                synthetic_column = table.c.synthetic
            if params.get("synthetic") is not None:
                query = query.where(synthetic_column == int(params["synthetic"]))
            records = [dict(r._mapping) for r in db.execute(query.order_by(table.c.id).limit(MAX_ROWS + 1))]
            if len(records) > MAX_ROWS:
                raise Conflict("Export exceeds 5000 rows; narrow the environment/dictionary selection")
            headers = {
                "entries": [
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
            }.get(kind, list(table.c.keys()))
            if kind == "bill":
                grouped = {r["id"]: [] for r in records}
                if grouped:
                    for element in db.execute(
                        select(bill_element)
                        .where(bill_element.c.bill_id.in_(grouped))
                        .order_by(bill_element.c.bill_id, bill_element.c.position)
                    ):
                        grouped[element.bill_id].append(
                            {"code": element.code, "name": element.name, "value": element.value}
                        )
                for row in records:
                    row["elements"] = dump(grouped[row["id"]])
            output = csv_bytes(headers, [[row[h] for h in headers] for row in records])
            if len(output) > MAX_EXPANDED:
                raise Conflict("Export exceeds 32 MiB; narrow the selection")
            db.execute(
                insert(asset_batch_files).values(
                    job_id=key, filename="", content=b"", output=output, output_name=kind + "-export.csv"
                )
            )
            finish(
                db,
                job,
                {"operation": "export", "total": len(records), "applied": False, "download_ready": True},
            )
        return
    with batch.connect() as db:
        file = db.execute(select(asset_batch_files).where(asset_batch_files.c.job_id == key)).one()
    try:
        source = read_rows(file.filename, bytes(file.content))
    except ValueError as exc:
        source = [(1, {}, str(exc))]
    with store.lease_guard(key, token) as db:
        db.execute(
            update(jobs).where(jobs.c.id == key).values(result=dump({"total": len(source), "applied": False}))
        )
        db.execute(
            insert(events).values(
                job_id=key,
                stage="batch_validation_started",
                data=dump({"total": len(source)}),
                created=time.time(),
            )
        )
    valid, invalid, seen = [], 0, set()
    for number, raw, initial_error in source:
        error, row = initial_error, {}
        try:
            if error:
                raise ValueError(error)
            row = normalize_row(params["asset_kind"], raw, params.get("dictionary_kind"))
            identity = (
                dump([row.get("term"), row.get("element_code"), row.get("element_name"), row.get("scope")])
                if params["asset_kind"] == "dictionary"
                else dump(row)
            )
            if identity in seen:
                raise ValueError("Duplicate row or term in the same element/scope")
            seen.add(identity)
        except Conflict as exc:
            error = str(exc)
        except ValidationError as exc:
            error = "; ".join(
                ".".join(map(str, e["loc"])) + ": " + e["type"] for e in exc.errors(include_input=False)
            )
        except json.JSONDecodeError:
            error = "elements or payload must contain valid JSON"
        except (ValueError, TypeError):
            if not error:
                error = "Invalid fields, rule payload, duplicate row or schema; check the template"
        if error:
            invalid += 1
        else:
            valid.append(row)
        with store.lease_guard(key, token) as db:
            exists = db.execute(
                select(asset_batch_rows.c.row_number).where(
                    asset_batch_rows.c.job_id == key, asset_batch_rows.c.row_number == number
                )
            ).first()
            values = {
                "status": "invalid" if error else "valid",
                "data": dump(raw if error else row),
                "error": error,
            }
            if exists:
                db.execute(
                    update(asset_batch_rows)
                    .where(asset_batch_rows.c.job_id == key, asset_batch_rows.c.row_number == number)
                    .values(**values)
                )
            else:
                db.execute(insert(asset_batch_rows).values(job_id=key, row_number=number, **values))
    with store.lease_guard(key, token) as db:
        result = {
            "operation": "import",
            "total": len(source),
            "valid": len(valid),
            "invalid": invalid,
            "applied": False,
        }
        if not invalid:
            bundle = {
                "kind": "file-import",
                "source": "file-sha256:" + params["sha256"],
                "synthetic": params["synthetic"],
            }
            if params["asset_kind"] == "dictionary":
                bundle["dictionaries"] = [
                    {"kind": params["dictionary_kind"], "name": params["name"], "entries": valid}
                ]
            else:
                bundle["bills" if params["asset_kind"] == "bill" else "negative"] = valid
            imported = batch.import_bundle(bundle, connection=db)
            result.update(applied=True, import_id=imported["id"], replayed=imported["replayed"])
            if params["asset_kind"] == "dictionary":
                from .persistence import dictionary_version

                result["dictionary_id"] = db.scalar(
                    select(dictionary_version.c.id)
                    .where(dictionary_version.c.import_id == imported["id"])
                    .order_by(dictionary_version.c.active.desc(), dictionary_version.c.created.desc())
                )
        finish(db, job, result)


def finish(db, job, result):
    """Commit final business effects and the task terminal state in one fenced transaction."""
    db.execute(
        update(jobs)
        .where(jobs.c.id == job["id"])
        .values(
            status="completed", result=dump(result), error=None, token=None, lease=None, updated=time.time()
        )
    )
    db.execute(
        insert(events).values(job_id=job["id"], stage="finished", data=dump(result), created=time.time())
    )
    db.execute(
        insert(audit).values(
            actor=job["payload"]["actor"],
            action="business_batch",
            resource=job["id"],
            detail=dump(result),
            created=time.time(),
        )
    )
