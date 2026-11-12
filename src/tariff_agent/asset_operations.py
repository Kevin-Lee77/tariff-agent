"""Transactional, immutable dictionary editing, publication rollback and optimistic snapshots."""

import hashlib
import json
import time
import uuid

from sqlalchemy import insert, select, update

from .assets import Assets, identifier
from .business_rules import validate_entries
from .governance import negative
from .persistence import audit, dictionary_entry, dictionary_version
from .store import Conflict, dump


def snapshot(db, key):
    version = db.execute(
        select(dictionary_version).where(dictionary_version.c.id == key).with_for_update()
    ).first()
    if not version:
        raise KeyError("Dictionary not found")
    entries = [
        dict(row._mapping)
        for row in db.execute(
            select(dictionary_entry)
            .where(dictionary_entry.c.dictionary_id == key)
            .order_by(dictionary_entry.c.id)
        )
    ]
    for entry in entries:
        entry["payload"] = json.loads(entry["payload"])
    value = dict(version._mapping) | {"config": json.loads(version.config), "entries": entries}
    value["revision"] = hashlib.sha256(dump(value).encode()).hexdigest()
    return value


class DictionaryEditor(Assets):
    def detail(self, key):
        with self.connect() as db:
            value = snapshot(db, key)
            versions = {
                r.id: r
                for r in db.execute(
                    select(dictionary_version).where(dictionary_version.c.import_id == value["import_id"])
                )
            }
            ancestors, current, visited = [], value["parent_id"], set()
            while current:
                if current in visited or current not in versions:
                    raise Conflict("Invalid dictionary lineage")
                visited.add(current)
                row = versions[current]
                ancestors.append(
                    {"id": row.id, "name": row.name, "status": row.status, "created": row.created}
                )
                current = row.parent_id
            return value | {"ancestors": ancestors}

    def edit(self, key, revision, operations, actor, reason):
        if not 1 <= len(operations) <= 100:
            raise Conflict("Provide 1..100 operations")
        with self.connect(write=True) as db:
            old = snapshot(db, key)
            if old["revision"] != revision:
                raise Conflict("Dictionary changed; reload before editing")
            if old["kind"] == "risk":
                raise Conflict("Risk policy uses coverage review rather than synonym editing")
            rows = {r["id"]: dict(r) for r in old["entries"]}
            for operation in operations:
                action = operation["action"]
                selected = operation.get("entry_ids", [])
                if action == "add":
                    row = operation["entry"]
                    rows[uuid.uuid4().hex] = row
                    continue
                if (
                    not selected
                    or len(set(selected)) != len(selected)
                    or any(i not in rows for i in selected)
                ):
                    raise Conflict("Select existing, distinct entries")
                if action in ("confirm", "unconfirm"):
                    for i in selected:
                        rows[i]["status"] = "confirmed" if action == "confirm" else "unreviewed"
                elif action == "rename":
                    value = operation.get("value", "").strip()
                    if len(selected) != 1 or not value:
                        raise Conflict("Rename requires one entry and a new word")
                    row = rows[selected[0]]
                    previous = row["term"]
                    if row.get("canonical", previous) == previous:
                        for member in rows.values():
                            if member.get("canonical", member["term"]) == previous and self.context(
                                member
                            ) == self.context(row):
                                member["canonical"] = value
                    row["term"] = value
                    row["status"] = "unreviewed"
                elif action == "delete":
                    for i in selected:
                        del rows[i]
                elif action in ("move", "merge", "split", "promote"):
                    if old["kind"] not in ("synonym", "standard"):
                        raise Conflict("Group editing applies only to synonym/standard dictionaries")
                    chosen = [rows[i] for i in selected]
                    value = operation.get("value", "").strip()
                    if action == "promote":
                        if len(selected) != 1:
                            raise Conflict("Promote one alias at a time")
                        pivot = chosen[0]
                        value = pivot["term"]
                        chosen = [
                            r
                            for r in rows.values()
                            if r.get("canonical", r["term"]) == pivot.get("canonical", pivot["term"])
                            and self.context(r) == self.context(pivot)
                        ]
                    if not value or len(value) > 1000 or len({self.context(r) for r in chosen}) != 1:
                        raise Conflict(
                            "Group operations require a canonical word and identical element/scope"
                        )
                    if any(negative(r["term"]) != negative(value) for r in chosen):
                        raise Conflict("Positive and negative words cannot share a group")
                    for row in chosen:
                        row["canonical"] = value
                        row["status"] = "unreviewed"
                else:
                    raise Conflict("Unsupported dictionary operation")
            clean = list(rows.values())
            validate_entries(old["kind"], clean)
            return self.write_revision(db, old, clean, actor, reason, {"operations": operations})

    @staticmethod
    def context(row):
        return row.get("element_code", ""), row.get("element_name", ""), row.get("scope", "")

    @staticmethod
    def write_revision(db, old, entries, actor, reason, detail):
        if old["synthetic"] and not old["active"]:
            raise Conflict("Sandbox base version was superseded; reload the current version")
        key = uuid.uuid4().hex
        db.execute(
            insert(dictionary_version).values(
                id=key,
                import_id=old["import_id"],
                name=old["name"],
                kind=old["kind"],
                status="sandbox" if old["synthetic"] else "unreviewed",
                synthetic=old["synthetic"],
                active=int(bool(old["synthetic"])),
                parent_id=old["id"],
                config=dump(old["config"]),
                created=time.time(),
            )
        )
        if old["synthetic"]:
            db.execute(
                update(dictionary_version).where(dictionary_version.c.id == old["id"]).values(active=0)
            )
        for i, row in enumerate(entries):
            db.execute(
                insert(dictionary_entry).values(
                    id=identifier(key, i),
                    dictionary_id=key,
                    term=row["term"],
                    canonical=row.get("canonical") or row["term"],
                    element_code=row.get("element_code", ""),
                    element_name=row.get("element_name", ""),
                    scope=row.get("scope", ""),
                    frequency=row.get("frequency", 0),
                    status=row.get("status", "unreviewed"),
                    payload=dump(row.get("payload", {})),
                )
            )
        db.execute(
            insert(audit).values(
                actor=actor,
                action="dictionary_batch_edit",
                resource=key,
                detail=dump(detail | {"parent": old["id"], "reason": reason}),
                created=time.time(),
            )
        )
        return {"id": key, "parent_id": old["id"], "status": "sandbox" if old["synthetic"] else "unreviewed"}

    def restore(self, key, target, revision, actor, reason):
        with self.connect(write=True) as db:
            current, previous = snapshot(db, key), snapshot(db, target)
            if current["revision"] != revision:
                raise Conflict("Dictionary changed; reload before restoring")
            ancestor, visited = current, set()
            while ancestor["id"] != target:
                if not ancestor["parent_id"] or ancestor["id"] in visited:
                    raise Conflict("Restore target must be an ancestor in the same dictionary lineage")
                visited.add(ancestor["id"])
                ancestor = snapshot(db, ancestor["parent_id"])
            if current["kind"] == "risk":
                raise Conflict("Risk coverage restoration requires explicit coverage revision")
            return self.write_revision(
                db,
                current | {"config": previous["config"]},
                previous["entries"],
                actor,
                reason,
                {"restored_from": target},
            )

    def withdraw(self, key, revision, actor, reason):
        with self.connect(write=True) as db:
            value = snapshot(db, key)
            if value["revision"] != revision or value["status"] != "approved" or not value["active"]:
                raise Conflict("Only the current approved version can be withdrawn")
            db.execute(
                update(dictionary_version)
                .where(dictionary_version.c.id == key)
                .values(status="withdrawn", active=0)
            )
            db.execute(
                insert(audit).values(
                    actor=actor,
                    action="dictionary_withdraw",
                    resource=key,
                    detail=dump({"reason": reason}),
                    created=time.time(),
                )
            )
        return {"id": key, "status": "withdrawn", "active": False}
