"""Extract page-cited PDF notes without inventing typed legal exclusion rules."""

import argparse
import hashlib
import json
import re
from pathlib import Path

from pypdf import PdfReader


def chinese_number(value):
    if value.isdigit():
        return int(value)
    digits = {x: i for i, x in enumerate("零一二三四五六七八九")}
    if "十" in value:
        left, right = value.split("十")
        return (digits.get(left, 1) * 10) + digits.get(right, 0)
    return digits.get(value, 0)


def ingest(pdf, corpus, output):
    if output.exists():
        raise FileExistsError("Choose a new versioned corpus output")
    data = json.loads(corpus.read_text(encoding="utf-8"))
    entries = {e["code"]: e for e in data["entries"]}
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    reader = PdfReader(pdf)
    chapter, heading, section = None, None, None
    section_notes, general, count, unmatched = {}, [], 0, []
    chapter_sections = {}
    for page_no, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        buffers = []
        current = []
        target = heading or chapter or (f"section:{section}" if section else None)

        def flush():
            if current:
                buffers.append((target, "\n".join(current)))
                current.clear()

        for line in text.splitlines():
            compact = re.sub(r"\s+", "", line)
            sec = re.match(r"^第([一二三四五六七八九十零\d]+)类", compact)
            chap = re.match(r"^第([一二三四五六七八九十零\d]+)章", compact)
            head = re.match(r"^\s*(\d{2})\.(\d{2})\s", line)
            if sec:
                flush()
                section = chinese_number(sec.group(1))
                target, heading, chapter = f"section:{section}", None, None
            elif chap:
                flush()
                chapter = f"{chinese_number(chap.group(1)):02}"
                chapter_sections[chapter] = section
                heading, target = None, chapter
            elif head:
                flush()
                candidate = head.group(1) + head.group(2)
                if candidate in entries:
                    heading, target = candidate, candidate
            if compact and not re.fullmatch(r"-?\d+-?", compact):
                current.append(line)
        flush()
        for segment, (scope, content) in enumerate(buffers):
            for chunk_no, start in enumerate(range(0, len(content), 1600)):
                note = {
                    "id": f"notes2022:{sha[:12]}:p{page_no}:s{segment}:c{chunk_no}",
                    "kind": "context",
                    "text": content[start : start + 1600],
                }
                if scope is None:
                    general.append(note)
                elif str(scope).startswith("section:"):
                    section_notes.setdefault(int(scope.split(":")[1]), []).append(note)
                elif scope in entries:
                    entries[scope].setdefault("notes", []).append(note)
                else:
                    unmatched.append({"page": page_no, "scope": scope})
                count += 1
    # Section material is inherited per chapter, with distinct source IDs for each attachment.
    for code, sec in chapter_sections.items():
        if code in entries:
            for note in section_notes.get(sec, []):
                entries[code].setdefault("notes", []).append(note | {"id": note["id"] + ":chapter" + code})
    data["general_notes"] = general
    data["version"] += "+notes2022"
    data["source"] += "; explanatory notes 2022, mixed versions require expert verification"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    manifest = {
        "pdf": str(pdf.resolve()),
        "sha256": sha,
        "pages": len(reader.pages),
        "extracted_chunks": count,
        "general_chunks": len(general),
        "chapter_count": len(chapter_sections),
        "unmatched": unmatched,
        "note_version": "2022",
        "catalog_version": "2026",
        "typed_exclusion_rules_created": 0,
        "manual_version_review_required": True,
    }
    output.with_suffix(".notes-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps({k: v for k, v in manifest.items() if k not in ("pdf", "unmatched")}, ensure_ascii=False)
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--pdf", required=True, type=Path)
    p.add_argument("--corpus", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    a = p.parse_args()
    ingest(a.pdf, a.corpus, a.output)
