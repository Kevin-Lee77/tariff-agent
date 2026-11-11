"""Read damaged ZIP local records, recover only CRC-verified textual source files.

Never modifies the input, executes contents, or restores settings/credentials.
"""

import argparse
import binascii
import hashlib
import json
import re
import struct
import zlib
from pathlib import Path, PurePosixPath


def recover(source, output):
    raw = source.read_bytes()
    result = {
        "archive": source.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "central_directory_present": b"PK\x05\x06" in raw,
        "recovered": [],
        "failed": [],
    }
    for match in re.finditer(b"PK\x03\x04", raw):
        pos = match.start()
        if pos + 30 > len(raw):
            continue
        fields = struct.unpack_from("<4s5H3I2H", raw, pos)
        _, version, flags, method, _, _, crc, packed, unpacked, name_len, extra_len = fields
        if version > 63 or method not in (0, 8) or flags & 1 or name_len > 2000:
            continue
        name = raw[pos + 30 : pos + 30 + name_len].decode("utf-8" if flags & 2048 else "gbk", "replace")
        parts = PurePosixPath(name.replace("\\", "/")).parts
        if not parts or ".." in parts or any(":" in p for p in parts) or name.startswith("/"):
            continue
        if ".svn" in parts or not (
            name.endswith((".java", ".vue", ".html")) or parts[-1] in ("pom.xml", "package.json")
        ):
            continue
        try:
            if unpacked > 2_000_000:
                raise ValueError("source record too large")
            start = pos + 30 + name_len + extra_len
            if flags & 8 and method == 8:
                decoder = zlib.decompressobj(-15)
                content = decoder.decompress(raw[start:], 2_000_001)
                if not decoder.eof or len(content) > 2_000_000:
                    raise ValueError("truncated or oversized deflate record")
                consumed = len(raw) - start - len(decoder.unused_data)
                tail = start + consumed
                if raw[tail : tail + 4] == b"PK\x07\x08":
                    tail += 4
                crc, packed, unpacked = struct.unpack_from("<III", raw, tail)
                if packed != consumed:
                    raise ValueError("descriptor compressed length mismatch")
            else:
                content = raw[start : start + packed]
                if method == 8:
                    content = zlib.decompress(content, -15)
            if len(content) != unpacked or binascii.crc32(content) & 0xFFFFFFFF != crc:
                raise ValueError("length/CRC mismatch")
            destination = output.joinpath(*parts)
            if not destination.resolve().is_relative_to(output.resolve()):
                raise ValueError("unsafe destination")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            result["recovered"].append(name)
        except (ValueError, zlib.error, struct.error) as exc:
            result["failed"].append({"path": name, "reason": str(exc)})
    output.mkdir(parents=True, exist_ok=True)
    (output / (source.stem + ".manifest.json")).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {"archive": source.name, "recovered": len(result["recovered"]), "failed": len(result["failed"])},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("source", type=Path)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    recover(a.source, a.output)
