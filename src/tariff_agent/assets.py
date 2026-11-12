"""Versioned business assets; no legacy database connection or arbitrary SQL inputs."""

import hashlib
import json
import time
import uuid
from contextlib import nullcontext

from sqlalchemy import func, insert, select, update

from .persistence import (
    bill,
    bill_element,
    data_imports,
    dictionary_entry,
    dictionary_version,
    governance_annotation,
    governance_report,
    negative_rules,
)
from .store import Conflict, Store, dump


def identifier(*parts):
    return hashlib.sha256(dump(parts).encode()).hexdigest()[:32]


class Assets(Store):
    def import_bundle(self, bundle, connection=None):
        """One immutable dataset per transaction, deterministic identities and replay."""
        fingerprint = hashlib.sha256(dump(bundle).encode()).hexdigest()
        batch = identifier(fingerprint)
        with self.connect(write=True) if connection is None else nullcontext(connection) as db:
            previous = db.execute(
                select(data_imports).where(data_imports.c.fingerprint == fingerprint)
            ).first()
            if previous:
                return dict(previous._mapping) | {"summary": json.loads(previous.summary), "replayed": True}
            synthetic = int(bundle["synthetic"])
            counts = {key: len(bundle.get(key, [])) for key in ("bills", "negative", "dictionaries")}
            db.execute(
                insert(data_imports).values(
                    id=batch,
                    kind=bundle["kind"],
                    source=bundle["source"],
                    fingerprint=fingerprint,
                    synthetic=synthetic,
                    summary=dump(counts | bundle.get("summary", {})),
                    created=time.time(),
                )
            )
            bills, elements = [], []
            for index, row in enumerate(bundle.get("bills", [])):
                key = identifier(batch, "bill", index)
                bills.append(
                    {
                        "id": key,
                        "import_id": batch,
                        "source_key": str(row.get("source_key", index)),
                        "task_id": row.get("taskId", ""),
                        "bill_code": row.get("billCode", ""),
                        "entry_id": row.get("entryId", ""),
                        "org_code": row.get("orgCode", ""),
                        "code_ts": row["codeTs"],
                        "description": row["description"],
                        "alignment_status": row["alignment_status"],
                        "raw": dump(row),
                        "synthetic": synthetic,
                    }
                )
                elements.extend(
                    {
                        "bill_id": key,
                        "position": i,
                        "code": e["code"],
                        "name": e.get("name", ""),
                        "value": e["value"],
                        "alignment_status": row["alignment_status"],
                    }
                    for i, e in enumerate(row["elements"])
                )
            for table, rows in ((bill, bills), (bill_element, elements)):
                for start in range(0, len(rows), 500):
                    db.execute(insert(table), rows[start : start + 500])
            negatives = []
            for index, row in enumerate(bundle.get("negative", [])):
                negatives.append(
                    {
                        "id": identifier(batch, "negative", index),
                        "import_id": batch,
                        "knowledge_id": row["knowledge_id"],
                        "fit_area": row["fit_area"],
                        "element_code": row["element_code"],
                        "vocabulary": row["vocabulary"],
                        "threshold": row["threshold"],
                        "review_comment": row.get("review_comment", ""),
                        "status": row.get("status", "unreviewed"),
                        "raw": dump(row),
                        "synthetic": synthetic,
                    }
                )
            for start in range(0, len(negatives), 500):
                db.execute(insert(negative_rules), negatives[start : start + 500])
            for index, version in enumerate(bundle.get("dictionaries", [])):
                key = identifier(batch, "dictionary", index)
                config = dict(version.get("config", {}))
                if version["kind"] == "risk":
                    config["negative_import_id"] = batch
                # Imported or generated data never implicitly approve a business dictionary.
                status = "sandbox" if synthetic else "unreviewed"
                db.execute(
                    insert(dictionary_version).values(
                        id=key,
                        import_id=batch,
                        name=version["name"],
                        kind=version["kind"],
                        status=status,
                        synthetic=synthetic,
                        active=int(synthetic),
                        config=dump(config),
                        created=time.time(),
                    )
                )
                entries = []
                for position, row in enumerate(version.get("entries", [])):
                    entries.append(
                        {
                            "id": identifier(key, position),
                            "dictionary_id": key,
                            "term": row["term"],
                            "canonical": row.get("canonical", row["term"]),
                            "element_code": row.get("element_code", ""),
                            "element_name": row.get("element_name", ""),
                            "scope": row.get("scope", ""),
                            "frequency": int(row.get("frequency", 0)),
                            "status": row.get("status", "unreviewed"),
                            "payload": dump(row.get("payload", {})),
                        }
                    )
                for start in range(0, len(entries), 500):
                    db.execute(insert(dictionary_entry), entries[start : start + 500])
        return {"id": batch, "summary": counts, "replayed": False}

    def stats(self):
        tables = {
            "bill": bill,
            "negative": negative_rules,
            "dictionary_version": dictionary_version,
            "dictionary_entry": dictionary_entry,
            "bill_element": bill_element,
            "governance_report": governance_report,
        }
        with self.connect() as db:
            result = {
                name: db.scalar(select(func.count()).select_from(table)) for name, table in tables.items()
            }
            result["bill_alignment"] = {
                r[0]: r[1]
                for r in db.execute(
                    select(bill.c.alignment_status, func.count()).group_by(bill.c.alignment_status)
                )
            }
            result["dictionary_kinds"] = {
                r[0]: r[1]
                for r in db.execute(
                    select(dictionary_version.c.kind, func.count()).group_by(dictionary_version.c.kind)
                )
            }
            result["imports"] = [
                dict(r._mapping) | {"summary": json.loads(r.summary)}
                for r in db.execute(select(data_imports).order_by(data_imports.c.created.desc()))
            ]
            result["database_backend"] = self.engine.dialect.name
        return result

    def list_assets(self, kind, query="", synthetic=None, offset=0, limit=30, dictionary_id=None):
        tables = {
            "bill": bill,
            "negative": negative_rules,
            "dictionaries": dictionary_version,
            "entries": dictionary_entry,
        }
        table = tables[kind]
        statement = (
            select(table, dictionary_version.c.synthetic).join(
                dictionary_version, table.c.dictionary_id == dictionary_version.c.id
            )
            if kind == "entries"
            else select(table)
        )
        if synthetic is not None and (kind == "entries" or "synthetic" in table.c):
            column = dictionary_version.c.synthetic if kind == "entries" else table.c.synthetic
            statement = statement.where(column == int(synthetic))
        if dictionary_id and kind == "entries":
            statement = statement.where(dictionary_entry.c.dictionary_id == dictionary_id)
        if query:
            field = {
                "bill": "description",
                "negative": "vocabulary",
                "dictionaries": "name",
                "entries": "term",
            }[kind]
            statement = statement.where(table.c[field].contains(query, autoescape=True))
        with self.connect() as db:
            total = db.scalar(select(func.count()).select_from(statement.subquery()))
            rows = [
                dict(r._mapping)
                for r in db.execute(statement.order_by(table.c.id).offset(offset).limit(limit))
            ]
        for row in rows:
            for field in ("raw", "config", "payload"):
                if field in row:
                    row[field] = json.loads(row[field])
        return {"total": total, "items": rows}

    def get_bill(self, key):
        with self.connect() as db:
            row = db.execute(select(bill).where(bill.c.id == key)).first()
            if row is None:
                raise KeyError("Bill not found")
            result = dict(row._mapping)
            result["raw"] = json.loads(result["raw"])
            result["elements"] = [
                dict(r._mapping)
                for r in db.execute(
                    select(bill_element)
                    .where(bill_element.c.bill_id == key)
                    .order_by(bill_element.c.position)
                )
            ]
            return result

    def revise_bill(self, key, elements, code, actor, reason):
        original = self.get_bill(key)
        rows = [e.model_dump() for e in elements]
        if not rows or any(not e["code"] for e in rows) or not any(e["code"] == "0000" for e in rows):
            raise Conflict("修订映射须包含明确要素编码和0000商品名称")
        description = "；".join((e["name"] or e["code"]) + "：" + e["value"] for e in rows)
        if len(description) > 6000:
            raise Conflict("修订申报超过6000字")
        payload = {
            "kind": "bill_revision",
            "source": "manual-mapping-revision",
            "synthetic": bool(original["synthetic"]),
            "bills": [
                {
                    "codeTs": code,
                    "description": description,
                    "elements": rows,
                    "alignment_status": "aligned",
                    "source_key": "revision:" + key,
                    "parent_bill_id": key,
                    "reason": reason,
                    "actor": actor,
                }
            ],
        }
        imported = self.import_bundle(payload)
        with self.connect() as db:
            new = db.scalar(select(bill.c.id).where(bill.c.import_id == imported["id"]))
        self.audit(actor, "bill_mapping_revise", new, {"parent_bill_id": key, "reason": reason})
        return {"id": new, "parent_bill_id": key}

    def risk_policy(self, synthetic):
        with self.connect() as db:
            versions = [
                dict(r._mapping)
                for r in db.execute(
                    select(dictionary_version).where(
                        dictionary_version.c.kind == "risk", dictionary_version.c.synthetic == int(synthetic)
                    )
                )
            ]
            if not versions:
                return {}
            versions.sort(
                key=lambda v: (v["active"], json.loads(v["config"]).get("priority", 0), v["created"]),
                reverse=True,
            )
            version = versions[0]
            config = json.loads(version["config"])
            rows = db.execute(
                select(negative_rules).where(
                    negative_rules.c.import_id == config["negative_import_id"],
                    negative_rules.c.status != "unsupported_scope",
                )
            )
            rules = [
                {
                    name: r._mapping[name]
                    for name in (
                        "knowledge_id",
                        "fit_area",
                        "element_code",
                        "vocabulary",
                        "threshold",
                        "review_comment",
                    )
                }
                for r in rows
            ]
        return config | {
            "version": version["id"],
            "synthetic": bool(synthetic),
            "approved": version["active"] == 1 and version["status"] in ("approved", "sandbox"),
            "rules": rules,
        }

    def language_policy(self, synthetic, code=""):
        with self.connect() as db:
            versions = [
                dict(r._mapping)
                for r in db.execute(
                    select(dictionary_version).where(
                        dictionary_version.c.kind != "risk",
                        dictionary_version.c.synthetic == int(synthetic),
                        dictionary_version.c.active == 1,
                    )
                )
            ]
            ids = [v["id"] for v in versions]
            kinds = {v["id"]: v["kind"] for v in versions}
            entries = (
                [
                    dict(r._mapping) | {"kind": kinds[r.dictionary_id]}
                    for r in db.execute(
                        select(dictionary_entry).where(dictionary_entry.c.dictionary_id.in_(ids))
                    )
                ]
                if ids
                else []
            )
        entries = [
            r | {"payload": json.loads(r["payload"])}
            for r in entries
            if not r["scope"] or (code and code.startswith(r["scope"]))
        ]
        return {"version": identifier(ids), "synthetic": synthetic, "entries": entries} if ids else {}

    def publish(self, key, actor, reason):
        with self.connect(write=True) as db:
            row = db.execute(
                select(dictionary_version).where(dictionary_version.c.id == key).with_for_update()
            ).first()
            if not row:
                raise KeyError("Dictionary not found")
            if row.synthetic:
                raise Conflict("Generated/sandbox assets cannot be published as business dictionaries")
            # Serialize one import family so concurrent drafts cannot both become active.
            db.execute(
                select(data_imports.c.id).where(data_imports.c.id == row.import_id).with_for_update()
            ).first()
            family = {
                r.id: r
                for r in db.execute(
                    select(dictionary_version).where(dictionary_version.c.import_id == row.import_id)
                )
            }

            def root_of(item):
                visited = set()
                while item.parent_id:
                    if item.id in visited or item.parent_id not in family:
                        raise Conflict("Invalid dictionary lineage")
                    visited.add(item.id)
                    item = family[item.parent_id]
                return item.id

            root = root_of(row)
            active = [r.id for r in family.values() if r.active and not r.synthetic and root_of(r) == root]
            if (row.status in ("approved", "withdrawn") and not row.active) or any(
                i not in (row.id, row.parent_id) for i in active
            ):
                raise Conflict("Base version was superseded; create a revision from the current version")
            config = json.loads(row.config)
            if row.kind == "risk" and not config.get("coverage"):
                raise Conflict("Risk coverage must be explicitly reviewed before publication")
            if row.parent_id:
                db.execute(
                    update(dictionary_version)
                    .where(dictionary_version.c.id == row.parent_id)
                    .values(active=0)
                )
            if row.kind == "risk":
                db.execute(
                    update(dictionary_version)
                    .where(dictionary_version.c.kind == "risk", dictionary_version.c.synthetic == 0)
                    .values(active=0)
                )
            db.execute(
                update(dictionary_version)
                .where(dictionary_version.c.id == key)
                .values(status="approved", active=1)
            )
        self.audit(actor, "dictionary_publish", key, {"reason": reason})
        return {"id": key, "status": "approved"}

    def revise(self, key, name, entries, config, actor, reason):
        revision = uuid.uuid4().hex
        with self.connect(write=True) as db:
            old = db.execute(
                select(dictionary_version).where(dictionary_version.c.id == key).with_for_update()
            ).first()
            if not old:
                raise KeyError("Dictionary not found")
            if old.synthetic and not old.active:
                raise Conflict("Sandbox base version was superseded; reload the current version")
            if entries is not None and old.kind != "risk":
                from .business_rules import validate_entries

                validate_entries(old.kind, entries)
            values = dict(old._mapping) | {
                "id": revision,
                "name": name or old.name,
                "parent_id": key,
                "active": int(bool(old.synthetic)),
                "status": "sandbox" if old.synthetic else "unreviewed",
                "created": time.time(),
                "config": dump(config if config is not None else json.loads(old.config)),
            }
            if old.kind == "risk":
                original = json.loads(old.config)
                revised = json.loads(values["config"])
                revised["negative_import_id"] = original["negative_import_id"]
                values["config"] = dump(revised)
                if entries is not None:
                    raise Conflict(
                        "Risk words are managed in the negative import; revise only reviewed coverage here"
                    )
            db.execute(insert(dictionary_version).values(**values))
            rows = (
                [
                    dict(row._mapping)
                    for row in db.execute(
                        select(dictionary_entry).where(dictionary_entry.c.dictionary_id == key)
                    )
                ]
                if entries is None
                else entries
            )
            for i, row in enumerate(rows):
                item = {
                    field: row.get(field, default)
                    for field, default in [
                        ("term", ""),
                        ("canonical", row.get("term", "")),
                        ("element_code", ""),
                        ("element_name", ""),
                        ("scope", ""),
                        ("frequency", 0),
                        ("status", "unreviewed"),
                        ("payload", {}),
                    ]
                }
                if not isinstance(item["payload"], str):
                    item["payload"] = dump(item["payload"])
                db.execute(
                    insert(dictionary_entry).values(
                        id=identifier(revision, i), dictionary_id=revision, **item
                    )
                )
            if old.synthetic:
                db.execute(update(dictionary_version).where(dictionary_version.c.id == key).values(active=0))
        self.audit(actor, "dictionary_revise", revision, {"parent": key, "reason": reason})
        return {"id": revision, "parent_id": key, "status": "sandbox" if old.synthetic else "unreviewed"}

    def save_report(self, kind, result, bill_id=None):
        key = uuid.uuid4().hex
        with self.connect(write=True) as db:
            db.execute(
                insert(governance_report).values(
                    id=key,
                    bill_id=bill_id,
                    kind=kind,
                    version=result.get("dictionary_version", "unavailable"),
                    decision=result.get("decision", result.get("status", "unknown")),
                    synthetic=int(bool(result.get("synthetic"))),
                    data=dump(result),
                    created=time.time(),
                )
            )
            for index, row in enumerate(result.get("annotations", [])):
                db.execute(
                    insert(governance_annotation).values(
                        id=identifier(key, index),
                        report_id=key,
                        knowledge_id=row["knowledge_id"],
                        data=dump(row),
                    )
                )
        return key
