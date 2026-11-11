"""Deterministic grouped splits. Labels stay outside model inputs and retrieval holdouts."""

import gzip
import hashlib
import json
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path


def canonical(text):
    value = unicodedata.normalize("NFKC", text).casefold()
    value = re.sub(r"(?:商品名称|规格型号|申报品名|品名)\s*[:：]", "", value)
    return "".join(c for c in value if c.isalnum())


def text_identity(text):
    return hashlib.sha256(canonical(text).encode()).hexdigest()


def group_identity(text):
    # Conservative family grouping: formatting and numeric/model/quantity variants cannot cross splits.
    template = re.sub(r"\d+(?:[.,]\d+)*", "<num>", canonical(text))
    return hashlib.sha256(template.encode()).hexdigest()


def rows(path):
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as source:
        for line in source:
            if line.strip():
                yield json.loads(line)


def stratified(records, size):
    by_chapter = defaultdict(list)
    for row in records:
        by_chapter[row["gold_code"][:2]].append(row)
    if sum(map(len, by_chapter.values())) < size:
        raise ValueError("Not enough independent groups for requested evaluation size")
    for values in by_chapter.values():
        values.sort(key=lambda row: row["group_id"])
    selected = []
    # Round-robin chapter coverage, then proportional fill; deterministic and without duplicate groups.
    if size >= len(by_chapter):
        for chapter in sorted(by_chapter):
            selected.append(by_chapter[chapter].pop(0))
    remaining = size - len(selected)
    total = sum(map(len, by_chapter.values()))
    exact = {c: remaining * len(v) / max(1, total) for c, v in by_chapter.items()}
    quotas = {c: min(len(by_chapter[c]), int(value)) for c, value in exact.items()}
    while sum(quotas.values()) < remaining:
        chapter = max(
            (c for c in by_chapter if quotas[c] < len(by_chapter[c])), key=lambda c: (exact[c] - quotas[c], c)
        )
        quotas[chapter] += 1
    for chapter in sorted(by_chapter):
        selected.extend(by_chapter[chapter][: quotas[chapter]])
    return sorted(selected, key=lambda row: row["group_id"])


def prepare(source, corpus, output, dev_size=3000, heldout_size=10000, seed="tariff-grouped-v1"):
    source, corpus, output = Path(source), Path(corpus), Path(output)
    if output.exists():
        raise FileExistsError("Choose a new immutable split directory")
    output.mkdir(parents=True)
    valid = {
        r["code"] for r in json.loads(corpus.read_text(encoding="utf-8"))["entries"] if len(r["code"]) == 10
    }
    db = sqlite3.connect(output / "split-audit.sqlite")
    db.execute(
        "CREATE TABLE cases (id INTEGER PRIMARY KEY, norm TEXT, family TEXT, code TEXT, split TEXT, data TEXT)"
    )
    counts = Counter()
    batch = []
    try:
        for number, row in enumerate(rows(source)):
            counts["source_rows"] += 1
            if (
                row.get("synthetic")
                or not row.get("description")
                or row.get("gold_code") not in valid
                or not row.get("catalog_2026_covered", True)
            ):
                counts["quarantined_unmatched_or_invalid"] += 1
                continue
            norm = text_identity(row["description"])
            family = group_identity(row["description"])
            bucket = int(hashlib.sha256((seed + family).encode()).hexdigest()[:8], 16) % 100
            split = "history" if bucket < 80 else "dev_pool" if bucket < 90 else "heldout_pool"
            clean = {k: row[k] for k in ("description", "gold_code", "source")}
            clean.update(
                id=hashlib.sha256((norm + row["gold_code"]).encode()).hexdigest(),
                normalized_fingerprint=norm,
                group_id=family,
                label_status="historical_reference_unverified",
                split=split,
            )
            batch.append(
                (number, norm, family, row["gold_code"], split, json.dumps(clean, ensure_ascii=False))
            )
            if len(batch) == 1000:
                db.executemany("INSERT INTO cases VALUES (?,?,?,?,?,?)", batch)
                batch = []
        if batch:
            db.executemany("INSERT INTO cases VALUES (?,?,?,?,?,?)", batch)
        db.execute("CREATE INDEX case_norm ON cases(norm)")
        db.execute("CREATE INDEX case_group ON cases(family)")
        db.execute(
            "CREATE TABLE conflicts AS SELECT DISTINCT family FROM cases WHERE norm IN (SELECT norm FROM cases GROUP BY norm HAVING COUNT(DISTINCT code)>1)"
        )
        counts["conflicting_families"] = db.execute("SELECT COUNT(*) FROM conflicts").fetchone()[0]
        db.execute(
            "CREATE TABLE kept AS SELECT * FROM cases WHERE family NOT IN (SELECT family FROM conflicts) AND id IN (SELECT MIN(id) FROM cases GROUP BY norm)"
        )
        counts["normalized_duplicates_or_conflicts"] = (
            db.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
            - db.execute("SELECT COUNT(*) FROM kept").fetchone()[0]
        )
        pools = {}
        for split in ("history", "dev_pool", "heldout_pool"):
            with (output / (split + ".jsonl")).open("w", encoding="utf-8") as target:
                for (value,) in db.execute(
                    "SELECT data FROM kept WHERE split=? ORDER BY family,id", (split,)
                ):
                    target.write(value + "\n")
                    counts[split] += 1
            pools[split] = (
                [
                    json.loads(r[0])
                    for r in db.execute(
                        "SELECT data FROM kept WHERE split=? AND id IN (SELECT MIN(id) FROM kept GROUP BY family) ORDER BY family",
                        (split,),
                    )
                ]
                if split != "history"
                else []
            )
        for name, pool, size in [("dev", "dev_pool", dev_size), ("heldout", "heldout_pool", heldout_size)]:
            chosen = stratified(pools[pool], size)
            (output / (name + ".jsonl")).write_text(
                "".join(json.dumps(r | {"split": name}, ensure_ascii=False) + "\n" for r in chosen),
                encoding="utf-8",
            )
            counts[name] = len(chosen)
        db.commit()
        overlap = db.execute(
            "SELECT COUNT(*) FROM (SELECT family FROM kept GROUP BY family HAVING COUNT(DISTINCT split)>1)"
        ).fetchone()[0]
        if overlap:
            raise ValueError("Split group leakage detected")
        manifest = {
            "version": seed,
            "counts": dict(counts),
            "group_overlap": overlap,
            "grouping": "NFKC/case/label/punctuation normalization; numeric-template family; conflicting normalized labels quarantine whole family",
            "near_duplicate_limit": "Grouping is conservative template deduplication, not a proof against all semantic paraphrases",
            "label_status": "Historical declared labels, not expert-adjudicated ground truth",
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "corpus_sha256": hashlib.sha256(corpus.read_bytes()).hexdigest(),
            "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob("*.jsonl")},
        }
        (output / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return manifest
    finally:
        db.close()
