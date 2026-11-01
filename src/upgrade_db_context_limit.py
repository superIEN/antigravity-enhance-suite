#!/usr/bin/env python3
"""
AGY Trajectory SQLite (.db) Context Window Inspector & Structural Protobuf Upgrader
===================================================================================
Inspects or upgrades `checkpoint_config.max_token_limit` and `ContextWindowMetadata.max_context_tokens`
inside `/Users/ien/.gemini/antigravity/conversations/*.db` protobuf blobs using an exact
structural protobuf wire-tree parser (zero blind byte-slice replacement).

Verified Protobuf Field Paths & 3-Byte Varints:
- `executor_metadata.data`: field path `(10, 2, 5)` (`tag = 0x28`) = `checkpoint_config.max_token_limit`
- `gen_metadata.data`:      field path `(1, 9, 10, 4)` (`tag = 0x20`) = `ContextWindowMetadata.max_context_tokens`
- `256,000`   (`0x03e800`) -> varint `80 d0 0f` (3 bytes)
- `983,040`   (`0x0f0000`) -> varint `80 80 3c` (3 bytes, default target matching Site 22: 1,048,576 - 65,536)
- `1,008,576` (`0x0f63c0`) -> varint `c0 c7 3d` (3 bytes)
- `1,048,576` (`0x100000`) -> varint `80 80 40` (3 bytes)

Because all of these values encode as exact 3-byte varints, replacing the varint at the verified
protobuf tree path `(10, 2, 5)` or `(1, 9, 10, 4)` is 100% byte-length-preserving and never
invalidates any enclosing length-delimited submessage header or `gen_metadata.size` column.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

TARGET_PATHS: dict[str, tuple[int, ...]] = {
    "executor_metadata": (10, 2, 5),
    "gen_metadata": (1, 9, 10, 4),
}


def encode_varint(value: int) -> bytes:
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value & 0x7F)
    return bytes(out)


def decode_varint(buf: bytes | bytearray, pos: int, end: int) -> tuple[int, int]:
    shift = 0
    result = 0
    while pos < end:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
        if shift >= 64:
            raise ValueError("Varint too long")
    raise ValueError("Truncated varint")


@dataclass(frozen=True)
class VarintSpan:
    path: tuple[int, ...]
    val_start: int
    val_end: int
    value: int


def find_varints_at_path(
    buf: bytes | bytearray,
    target_path: tuple[int, ...],
    start: int = 0,
    end: int | None = None,
    prefix: tuple[int, ...] = (),
) -> list[VarintSpan]:
    """Walks a protobuf wire-format message and returns all varint spans matching `target_path`."""
    if end is None:
        end = len(buf)
    spans: list[VarintSpan] = []
    pos = start
    target_depth = len(target_path)
    cur_depth = len(prefix)

    while pos < end:
        tag_val, pos = decode_varint(buf, pos, end)
        field_num = tag_val >> 3
        wire_type = tag_val & 0x07
        cur_path = prefix + (field_num,)

        if wire_type == 0:  # Varint
            val_start = pos
            val, pos = decode_varint(buf, pos, end)
            if cur_path == target_path:
                spans.append(VarintSpan(path=cur_path, val_start=val_start, val_end=pos, value=val))
        elif wire_type == 1:  # 64-bit
            pos += 8
            if pos > end:
                raise ValueError("Truncated fixed64")
        elif wire_type == 2:  # Length-delimited
            length, pos = decode_varint(buf, pos, end)
            sub_start = pos
            sub_end = pos + length
            if sub_end > end:
                raise ValueError("Truncated length-delimited field")
            if cur_depth < target_depth - 1 and cur_path == target_path[: cur_depth + 1]:
                spans.extend(find_varints_at_path(buf, target_path, sub_start, sub_end, cur_path))
            pos = sub_end
        elif wire_type == 5:  # 32-bit
            pos += 4
            if pos > end:
                raise ValueError("Truncated fixed32")
        else:
            raise ValueError(f"Unsupported wire type {wire_type}")

    return spans


def backup_sqlite_with_sidecars(db_path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak_db = db_path.with_suffix(f".db.bak_{ts}")
    try:
        src_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
        dst_conn = sqlite3.connect(str(bak_db))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()
    except Exception:
        shutil.copy2(db_path, bak_db)
    for sidecar in ("-wal", "-shm"):
        sc_path = Path(str(db_path) + sidecar)
        if sc_path.exists():
            shutil.copy2(sc_path, Path(str(bak_db) + sidecar))
    return bak_db


def inspect_or_upgrade_db(db_path: Path, upgrade_to: int | None = None) -> dict[str, object]:
    bak_path: Path | None = None
    if upgrade_to is not None:
        new_varint = encode_varint(upgrade_to)
        if len(new_varint) != 3:
            raise ValueError(
                f"upgrade_to={upgrade_to} encodes to {len(new_varint)} bytes; must be 3 bytes (16384..2097151)"
            )
        bak_path = backup_sqlite_with_sidecars(db_path)
        conn = sqlite3.connect(str(db_path), timeout=10.0, isolation_level=None)
    else:
        new_varint = None
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0, isolation_level=None)

    cur = None
    try:
        cur = conn.cursor()
        if upgrade_to is not None:
            cur.execute("BEGIN IMMEDIATE")
        else:
            cur.execute("PRAGMA query_only = ON")

        tables = [
            r[0]
            for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]
        summary: list[dict[str, object]] = []

        for tbl, target_path in TARGET_PATHS.items():
            if tbl not in tables:
                continue
            rows = cur.execute(f"SELECT rowid, data FROM {tbl}").fetchall()
            hits_256k = 0
            hits_983k = 0
            hits_1008k = 0
            hits_other = 0
            tbl_replaced = 0

            for rowid, val in rows:
                if isinstance(val, bytes):
                    blob = bytearray(val)
                    is_hex_str = False
                elif isinstance(val, str):
                    blob = bytearray.fromhex(val)
                    is_hex_str = True
                else:
                    continue

                spans = find_varints_at_path(blob, target_path)
                row_modified = False
                for sp in spans:
                    if sp.value == 256_000:
                        if upgrade_to is not None and new_varint is not None:
                            assert sp.val_end - sp.val_start == 3, (
                                f"Expected 3-byte varint at {sp.path}, got {sp.val_end - sp.val_start}"
                            )
                            blob[sp.val_start : sp.val_end] = new_varint
                            tbl_replaced += 1
                            row_modified = True
                            if upgrade_to == 983_040:
                                hits_983k += 1
                            elif upgrade_to == 1_008_576:
                                hits_1008k += 1
                            else:
                                hits_other += 1
                        else:
                            hits_256k += 1
                    elif sp.value == 983_040:
                        hits_983k += 1
                    elif sp.value == 1_008_576:
                        hits_1008k += 1
                    else:
                        hits_other += 1

                if row_modified:
                    new_val = bytes(blob).hex() if is_hex_str else bytes(blob)
                    cur.execute(f"UPDATE {tbl} SET data = ? WHERE rowid = ?", (new_val, rowid))

            summary.append(
                {
                    "table": tbl,
                    "rows": len(rows),
                    "path": "." + ".".join(str(x) for x in target_path),
                    "hits_256k": hits_256k,
                    "hits_983k": hits_983k,
                    "hits_1008k": hits_1008k,
                    "hits_other": hits_other,
                    "replaced": tbl_replaced,
                }
            )

        if upgrade_to is not None:
            cur.execute("COMMIT")
        return {
            "db": str(db_path),
            "backup": str(bak_path) if bak_path else None,
            "entries": summary,
        }
    except Exception:
        if upgrade_to is not None and cur is not None:
            try:
                cur.execute("ROLLBACK")
            except Exception:
                pass
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Structurally inspect or upgrade AGY conversation .db context window token limits."
    )
    parser.add_argument(
        "dbs",
        nargs="*",
        type=Path,
        default=[],
        help="Paths to .db files to inspect (default: b6ce5010 and e93889f8, or all with --all)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Scan all *.db files under /Users/ien/.gemini/antigravity/conversations/",
    )
    parser.add_argument(
        "--upgrade-to",
        type=int,
        default=None,
        help="Optional 3-byte varint token limit (recommended: 983040) to replace 256,000 caps with",
    )
    args = parser.parse_args()

    conv_dir = Path("/Users/ien/.gemini/antigravity/conversations")
    if args.all:
        db_list = sorted(p for p in conv_dir.glob("*.db") if ".bak_" not in p.name)
    elif args.dbs:
        db_list = args.dbs
    else:
        db_list = [
            conv_dir / "b6ce5010-8b99-45c4-a3d8-cb7bbb1f531b.db",
            conv_dir / "e93889f8-4672-4741-994b-fcb135fd507d.db",
        ]

    for db_path in db_list:
        if not db_path.exists():
            print(f"[SKIP] {db_path} (not found)")
            continue
        info = inspect_or_upgrade_db(db_path, upgrade_to=args.upgrade_to)
        print(f"=== {info['db']} ===")
        if info["backup"]:
            print(f"  [BACKUP] {info['backup']}")
        for b in info["entries"]:
            print(
                f"  {b['table']:18s} (rows={b['rows']:3d}, path=${b['path']}) | "
                f"256k={b['hits_256k']:3d} | 983k={b['hits_983k']:3d} | "
                f"1008k={b['hits_1008k']:3d} | other={b['hits_other']:3d} | replaced={b['replaced']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
