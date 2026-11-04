#!/usr/bin/env python3
"""Guardrail 1: Read-Coverage & Truncation Auditor (v1.1).

Directly audits AGY session `transcript_full.jsonl` (sole authority for tool outputs)
and `.system_generated/steps/*/output.txt` across all three real AGY header dialects:
1. macOS/CLI `read_file`:
   - `Showing lines L1-L2 of Total`
   - `Partial read (lines L1-L2 of Total). Showing bytes B1-B2 of B_total in this line range`
2. Windows/Desktop `view_file` with `ContentOffset`:
   - `Total Lines: Total` + `Total Bytes: M` + `Showing lines L1 to L2`
   - `Content truncated: showing bytes B1-B2 of B_win_total.(?: To see more... ContentOffset=B2.)?`
3. macOS `view_file` with tail `<truncated N bytes>` vs line-subset notice:
   - `<truncated N bytes>` shrinks effective intact upper line to `L_last_intact` before the
     truncated line and marks `[L_last_intact + 1, L2]` as an unclosed byte-truncation gap.
   - `The above content does NOT show the entire file contents.` without `<truncated N bytes>`
     or `Content truncated:` is treated as a normal line-range subset `[L1, L2]`.

Enforces:
- Per-window `(L1, L2)` byte coordinate closure (never merges byte slices across different windows)
- Read-Before-Edit / Post-Edit invalidation + append-only `read_snapshots.jsonl` for equal-length edits
- Exclusion of `run_command` (`cat`/`head`/`tail`/`sed`/`python`) and `grep`/`rg` from `file_coverage`
- Fail-closed `output.txt` audit (missing/deleted spilled files fail closed)
- Automatic mandatory file set derivation from task order Markdown and declaration JSON
- 213KB hard truncation & 160KB on-disk envelope enforcement
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

HARD_TRUNCATION_BYTES = 213000
WARN_INPUT_BYTES = 160000

READ_TOOL_NAMES = {"read_file", "view_file"}
WRITE_TOOL_NAMES = {
    "write_file",
    "edit_file",
    "replace_file_content",
    "multi_replace_file_content",
}

# Dialect 1 regexes
RE_DIALECT1_PARTIAL = re.compile(
    r"Partial read \(lines (\d+)-(\d+) of (\d+)\)\.\s*"
    r"Showing bytes (\d+)-(\d+) of (\d+) in this line range"
)
RE_DIALECT1_COMPLETE = re.compile(r"(?m)^Showing lines (\d+)-(\d+) of (\d+)\b")
RE_EMPTY_FILE = re.compile(r"(?m)^File is empty\s*$")

# Dialect 2 & 3 regexes
RE_FILE_PATH_HEADER = re.compile(r"(?m)^File Path:\s*`?(?:file://)?([^`\n\r]+)`?")
RE_TOTAL_LINES = re.compile(r"(?m)^Total Lines:\s*(\d+)\b")
RE_TOTAL_BYTES = re.compile(r"(?m)^Total Bytes:\s*(\d+)\b")
RE_SHOWING_LINES_TO = re.compile(r"(?m)^Showing lines (\d+) to (\d+)\b")
RE_CONTENT_TRUNCATED_BYTES = re.compile(
    r"(?m)^Content truncated:\s*showing bytes (\d+)-(\d+) of (\d+)\."
    r"(?:\s*To see more, call this tool again with the same line range and ContentOffset=(\d+)\.)?"
)
RE_TAIL_TRUNCATED_BYTES = re.compile(
    r"(?m)^<truncated (\d+) bytes>\s*(?:\n+NOTE: The output was truncated because it was too long\.)?"
)
RE_DOES_NOT_SHOW_ENTIRE = re.compile(
    r"(?m)^The above content does NOT show the entire file contents\."
)
RE_NUMBERED_LINE = re.compile(r"(?m)^(\d+): ")

# Spilled command output regex (matches host truncation notice in command output preamble)
RE_SPILLED_OUTPUT = re.compile(
    r"The tool's output was truncated due to being too long\.\s*"
    r"You can find the full output in a file at (?:file:///?)?((?:/|[A-Za-z]:[\\/])[^\n\r\"`]+?[/\\]\.system_generated[/\\]steps[/\\](\d+)[/\\]output\.txt)"
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def count_file_lines_and_bytes(path: Path) -> Tuple[int, int]:
    raw = path.read_bytes()
    if not raw:
        return 0, 0
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    # Note: view_file sometimes counts a trailing newline as an extra empty line;
    # we return standard line count and raw byte count, plus trailing-newline line count.
    return len(lines), len(raw)


def normalize_target_path(raw_path: str, cwd: Optional[Path] = None) -> str:
    if not raw_path:
        return ""
    s = raw_path.strip().strip("`").strip("'").strip('"')
    if s.startswith("file:///") and len(s) >= 10 and s[9] == ":":
        s = s[len("file:///") :]
    elif s.startswith("file://"):
        s = s[len("file://") :]
    s = urllib.parse.unquote(s)
    if len(s) >= 3 and s[0] == "/" and s[2] == ":":
        s = s[1:]
    p = Path(s)
    if not p.is_absolute() and cwd is not None:
        p = cwd / p
    try:
        return str(p.resolve())
    except OSError:
        return str(p)


def merge_intervals(intervals: Iterable[Tuple[int, int]], closed_upper: bool = True) -> List[Tuple[int, int]]:
    """Merge 1-based closed line intervals [a, b] (closed_upper=True)
    or 0-based half-open byte intervals [a, b) (closed_upper=False).
    """
    items = sorted((int(a), int(b)) for a, b in intervals if (b >= a if closed_upper else b > a))
    if not items:
        return []
    merged: List[Tuple[int, int]] = [items[0]]
    adjacency = 1 if closed_upper else 0
    for start, end in items[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + adjacency:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def compute_line_gaps(closed_intervals: List[Tuple[int, int]], total_lines: int) -> List[Tuple[int, int]]:
    if total_lines <= 0:
        return []
    merged = merge_intervals(closed_intervals, closed_upper=True)
    gaps: List[Tuple[int, int]] = []
    cursor = 1
    for a, b in merged:
        if b < 1 or a > total_lines:
            continue
        a_clamped = max(1, a)
        b_clamped = min(total_lines, b)
        if a_clamped > cursor:
            gaps.append((cursor, a_clamped - 1))
        cursor = max(cursor, b_clamped + 1)
    if cursor <= total_lines:
        gaps.append((cursor, total_lines))
    return gaps


def parse_read_tool_output(content: str, fallback_path: str = "") -> Dict[str, Any]:
    """Parse a single `read_file` or `view_file` output string across all 3 real AGY dialects.
    Strictly parses header metadata from the preamble before the first numbered line (`^\\d+: `)
    so file contents mentioning `Partial read` or `Showing lines` never spoof the header parser.
    """
    first_num_match = RE_NUMBERED_LINE.search(content)
    header_preamble = content[: first_num_match.start()] if first_num_match else content

    header_path = ""
    m_path = RE_FILE_PATH_HEADER.search(header_preamble)
    if m_path:
        header_path = normalize_target_path(m_path.group(1))
    target_path = header_path or normalize_target_path(fallback_path)

    # Check empty file in header preamble
    if RE_EMPTY_FILE.search(header_preamble):
        return {
            "recognized": True,
            "dialect": "empty_file",
            "target_path": target_path,
            "total_lines": 0,
            "total_bytes": 0,
            "window": (0, 0),
            "intact_lines": (0, 0),
            "byte_slice": None,
            "truncated_line_gap": None,
            "is_byte_truncated": False,
        }

    # Check Dialect 1 Partial read (bytes X-Y of Z in line range L1-L2 of Total) in header preamble
    m_d1_partial = RE_DIALECT1_PARTIAL.search(header_preamble)
    if m_d1_partial:
        l1, l2, total_lines, b1, b2, b_total = (int(x) for x in m_d1_partial.groups())
        return {
            "recognized": True,
            "dialect": "macos_read_file_partial_bytes",
            "target_path": target_path,
            "total_lines": total_lines,
            "total_bytes": None,
            "window": (l1, l2),
            "intact_lines": None,
            "byte_slice": (b1, b2, b_total),
            "truncated_line_gap": None,
            "is_byte_truncated": True,
        }

    # Check Dialect 2 / Dialect 3 (`Showing lines L1 to L2` with `Total Lines: N`) in header preamble
    m_to = RE_SHOWING_LINES_TO.search(header_preamble)
    m_total_lines = RE_TOTAL_LINES.search(header_preamble)
    m_total_bytes = RE_TOTAL_BYTES.search(header_preamble)
    if m_to and m_total_lines:
        l1, l2 = int(m_to.group(1)), int(m_to.group(2))
        total_lines = int(m_total_lines.group(1))
        total_bytes = int(m_total_bytes.group(1)) if m_total_bytes else None

        # Dialect 2: `Content truncated: showing bytes B1-B2 of B_win_total.`
        m_ct = RE_CONTENT_TRUNCATED_BYTES.search(header_preamble)
        if m_ct:
            b1, b2, b_win_total = int(m_ct.group(1)), int(m_ct.group(2)), int(m_ct.group(3))
            return {
                "recognized": True,
                "dialect": "windows_view_file_content_offset",
                "target_path": target_path,
                "total_lines": total_lines,
                "total_bytes": total_bytes,
                "window": (l1, l2),
                "intact_lines": None,
                "byte_slice": (b1, b2, b_win_total),
                "truncated_line_gap": None,
                "is_byte_truncated": True,
            }

        # Dialect 3a: `<truncated N bytes>` on its own un-numbered tail line
        m_tail_trunc = RE_TAIL_TRUNCATED_BYTES.search(content)
        if m_tail_trunc:
            trunc_bytes = int(m_tail_trunc.group(1))
            prefix_before_trunc = content[: m_tail_trunc.start()]
            numbered = [int(m.group(1)) for m in RE_NUMBERED_LINE.finditer(prefix_before_trunc)]
            if len(numbered) >= 2:
                # The last numbered line before `<truncated N bytes>` was cut off mid-line
                last_intact = numbered[-2]
            elif len(numbered) == 1:
                last_intact = max(l1, numbered[0] - 1)
            else:
                last_intact = l1 - 1
            intact_range = (l1, last_intact) if last_intact >= l1 else None
            gap_start = max(l1, last_intact + 1)
            return {
                "recognized": True,
                "dialect": "macos_view_file_tail_truncated",
                "target_path": target_path,
                "total_lines": total_lines,
                "total_bytes": total_bytes,
                "window": (l1, l2),
                "intact_lines": intact_range,
                "byte_slice": None,
                "truncated_line_gap": (gap_start, l2),
                "truncated_bytes": trunc_bytes,
                "is_byte_truncated": True,
            }

        # Dialect 3b: normal `view_file` line window (with or without `does NOT show the entire file contents`)
        dialect_name = (
            "macos_view_file_line_subset"
            if RE_DOES_NOT_SHOW_ENTIRE.search(content)
            else "view_file_complete_window"
        )
        return {
            "recognized": True,
            "dialect": dialect_name,
            "target_path": target_path,
            "total_lines": total_lines,
            "total_bytes": total_bytes,
            "window": (l1, l2),
            "intact_lines": (l1, l2),
            "byte_slice": None,
            "truncated_line_gap": None,
            "is_byte_truncated": False,
        }

    # Check Dialect 1 complete window (`Showing lines L1-L2 of Total`) in header preamble
    m_d1_complete = RE_DIALECT1_COMPLETE.search(header_preamble)
    if m_d1_complete:
        l1, l2, total_lines = (int(x) for x in m_d1_complete.groups())
        m_tail_trunc = RE_TAIL_TRUNCATED_BYTES.search(content)
        if m_tail_trunc:
            trunc_bytes = int(m_tail_trunc.group(1))
            prefix_before_trunc = content[: m_tail_trunc.start()]
            numbered = [int(m.group(1)) for m in RE_NUMBERED_LINE.finditer(prefix_before_trunc)]
            last_intact = numbered[-2] if len(numbered) >= 2 else max(l1 - 1, l1)
            intact_range = (l1, last_intact) if last_intact >= l1 else None
            return {
                "recognized": True,
                "dialect": "macos_read_file_tail_truncated",
                "target_path": target_path,
                "total_lines": total_lines,
                "total_bytes": None,
                "window": (l1, l2),
                "intact_lines": intact_range,
                "byte_slice": None,
                "truncated_line_gap": (max(l1, last_intact + 1), l2),
                "truncated_bytes": trunc_bytes,
                "is_byte_truncated": True,
            }
        return {
            "recognized": True,
            "dialect": "macos_read_file_complete_window",
            "target_path": target_path,
            "total_lines": total_lines,
            "total_bytes": None,
            "window": (l1, l2),
            "intact_lines": (l1, l2),
            "byte_slice": None,
            "truncated_line_gap": None,
            "is_byte_truncated": False,
        }

    return {
        "recognized": False,
        "dialect": "unknown",
        "target_path": target_path,
    }


def locate_session_logs(
    conversation_id: str,
    brain_dir: Optional[str] = None,
    search_brain_roots: Optional[List[Path]] = None,
) -> Dict[str, Any]:
    """Locate `transcript_full.jsonl` for a conversation ID.
    Fails closed (`AMBIGUOUS_BRAIN_DIRS_REQUIRE_EXPLICIT_FLAG`) if multiple brain roots match.
    """
    cid = (conversation_id or "").strip()
    if not cid:
        return {
            "ok": False,
            "error_code": "CONVERSATION_ID_REQUIRED",
            "message": "conversation_id must be non-empty",
        }

    if brain_dir:
        bpath = Path(brain_dir).expanduser().resolve()
        # Allow passing either the brain root or the specific <brain_root>/<cid> directory
        candidate = bpath if bpath.name == cid else (bpath / cid)
        full_log = candidate / ".system_generated" / "logs" / "transcript_full.jsonl"
        compact_log = candidate / ".system_generated" / "logs" / "transcript.jsonl"
        steps_dir = candidate / ".system_generated" / "steps"
        if not full_log.is_file():
            return {
                "ok": False,
                "error_code": "TRANSCRIPT_FULL_NOT_FOUND",
                "message": f"transcript_full.jsonl not found at {full_log}",
            }
        return {
            "ok": True,
            "conversation_dir": str(candidate),
            "transcript_full_path": str(full_log),
            "transcript_compact_path": str(compact_log) if compact_log.is_file() else None,
            "steps_dir": str(steps_dir),
        }

    home = Path.home()
    roots = search_brain_roots or [
        home / ".gemini" / "antigravity" / "brain",
        home / ".gemini" / "antigravity-cli" / "brain",
        home / ".gemini" / "antigravity-ide" / "brain",
    ]
    matches: List[Path] = []
    for r in roots:
        cand = r / cid
        if (cand / ".system_generated" / "logs" / "transcript_full.jsonl").is_file():
            matches.append(cand.resolve())

    # Deduplicate resolved paths
    unique_matches: List[Path] = []
    seen: Set[str] = set()
    for m in matches:
        s = str(m)
        if s not in seen:
            seen.add(s)
            unique_matches.append(m)

    if len(unique_matches) > 1:
        return {
            "ok": False,
            "error_code": "AMBIGUOUS_BRAIN_DIRS_REQUIRE_EXPLICIT_FLAG",
            "matching_dirs": [str(x) for x in unique_matches],
            "message": f"Conversation {cid} exists in multiple brain roots; pass --brain-dir explicitly.",
        }
    if not unique_matches:
        return {
            "ok": False,
            "error_code": "TRANSCRIPT_FULL_NOT_FOUND",
            "message": f"No transcript_full.jsonl found for conversation {cid}",
        }

    chosen = unique_matches[0]
    full_log = chosen / ".system_generated" / "logs" / "transcript_full.jsonl"
    compact_log = chosen / ".system_generated" / "logs" / "transcript.jsonl"
    steps_dir = chosen / ".system_generated" / "steps"
    return {
        "ok": True,
        "conversation_dir": str(chosen),
        "transcript_full_path": str(full_log),
        "transcript_compact_path": str(compact_log) if compact_log.is_file() else None,
        "steps_dir": str(steps_dir),
    }


def extract_mandatory_files_from_task_order(task_order_path: Path) -> List[str]:
    """Automatically extract mandatory read-only input paths from a task order Markdown file."""
    if not task_order_path.is_file():
        return []
    text = task_order_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    in_readonly_section = False
    raw_paths: List[str] = []
    for line in lines:
        if re.match(r"^##\s+.*(只读输入原件|必读|Mandatory|Read-Only)", line, re.IGNORECASE):
            in_readonly_section = True
            continue
        if in_readonly_section and re.match(r"^##\s+", line):
            break
        if in_readonly_section:
            for m in re.finditer(r"`(/Users/[^`\n]+)`", line):
                raw_paths.append(m.group(1).strip())
    return expand_file_or_dir_list(raw_paths)


def extract_mandatory_files_from_declaration(
    decl_data: Dict[str, Any], project_root: Optional[Path] = None
) -> List[str]:
    """Automatically extract all basis, object, and envelope files referenced in a declaration."""
    candidates: List[str] = []
    for key in (
        "basis",
        "basis_files",
        "objects",
        "object_files",
        "object_refs",
        "empirical_evidence",
        "empirical_evidence_files",
        "empirical_evidence_refs",
    ):
        val = decl_data.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    candidates.append(item)
                elif isinstance(item, dict) and item.get("path"):
                    candidates.append(str(item["path"]))
    for single_key in ("input_envelope", "on_disk_input_envelope"):
        val = decl_data.get(single_key)
        if isinstance(val, str):
            candidates.append(val)
        elif isinstance(val, dict) and val.get("path"):
            candidates.append(str(val["path"]))
    return expand_file_or_dir_list(candidates, cwd=project_root)


SKIP_ARCHIVE_SUBDIRS = {"checkpoints", "operations", "receipts", "work", ".git", "__pycache__", "图稿", "教学评定"}
SKIP_BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".sqlite", ".db", ".pyc", ".zip"}


def expand_file_or_dir_list(paths: Iterable[str], cwd: Optional[Path] = None) -> List[str]:
    expanded: List[str] = []
    seen: Set[str] = set()
    for raw in paths:
        norm = normalize_target_path(raw, cwd=cwd)
        if not norm:
            continue
        p = Path(norm)
        if p.is_dir():
            for child in sorted(p.rglob("*")):
                if not child.is_file() or child.name.startswith("."):
                    continue
                rel_parts = set(child.relative_to(p).parts[:-1])
                if rel_parts & SKIP_ARCHIVE_SUBDIRS:
                    continue
                if child.suffix.lower() in SKIP_BINARY_SUFFIXES:
                    continue
                cs = str(child.resolve())
                if cs not in seen:
                    seen.add(cs)
                    expanded.append(cs)
        else:
            if norm not in seen:
                seen.add(norm)
                expanded.append(norm)
    return expanded


def _extract_tool_target_path(tc_args: Dict[str, Any], cwd: Optional[Path] = None) -> str:
    for key in ("Path", "AbsolutePath", "TargetFile", "FilePath", "path", "file_path"):
        if tc_args.get(key):
            return normalize_target_path(str(tc_args[key]), cwd=cwd)
    return ""


class FileReadTracker:
    """Tracks per-file read coverage, per-window byte slices, and post-edit invalidation."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.reported_total_lines: Optional[int] = None
        self.reported_total_bytes: Optional[int] = None
        self.is_empty_file: bool = False
        self.intact_line_intervals: List[Tuple[int, int]] = []
        # Map window (L1, L2) -> {"total_bytes": Z, "slices": [(B1, B2), ...]}
        self.window_byte_slices: Dict[Tuple[int, int], Dict[str, Any]] = {}
        # Gaps from Dialect 3 `<truncated N bytes>` [(gap_start, gap_end), ...]
        self.tail_truncated_gaps: List[Tuple[int, int]] = []
        self.dialects_seen: List[str] = []
        self.read_steps: List[int] = []
        self.last_read_step: Optional[int] = None
        self.last_edit_step: Optional[int] = None
        self.invalidated_reads_before_edit: List[Dict[str, Any]] = []

    def invalidate_on_edit(self, edit_step: int, tool_name: str) -> None:
        if self.read_steps or self.intact_line_intervals or self.window_byte_slices:
            self.invalidated_reads_before_edit.append(
                {
                    "invalidated_at_step": edit_step,
                    "edit_tool": tool_name,
                    "prior_read_steps": list(self.read_steps),
                    "prior_intact_intervals": list(self.intact_line_intervals),
                }
            )
        self.reported_total_lines = None
        self.reported_total_bytes = None
        self.is_empty_file = False
        self.intact_line_intervals.clear()
        self.window_byte_slices.clear()
        self.tail_truncated_gaps.clear()
        self.read_steps.clear()
        self.last_read_step = None
        self.last_edit_step = edit_step

    def record_read(self, step_index: int, parsed: Dict[str, Any]) -> None:
        self.read_steps.append(step_index)
        self.last_read_step = step_index
        dialect = parsed.get("dialect", "unknown")
        if dialect not in self.dialects_seen:
            self.dialects_seen.append(dialect)

        if parsed.get("total_lines") is not None:
            self.reported_total_lines = int(parsed["total_lines"])
        if parsed.get("total_bytes") is not None:
            self.reported_total_bytes = int(parsed["total_bytes"])

        if dialect == "empty_file" or self.reported_total_lines == 0:
            self.is_empty_file = True
            return

        if parsed.get("intact_lines") is not None:
            l1, l2 = parsed["intact_lines"]
            if l2 >= l1 and l1 >= 1:
                self.intact_line_intervals.append((int(l1), int(l2)))

        if parsed.get("byte_slice") is not None and parsed.get("window") is not None:
            win = (int(parsed["window"][0]), int(parsed["window"][1]))
            b1, b2, b_total = (int(x) for x in parsed["byte_slice"])
            bucket = self.window_byte_slices.setdefault(
                win, {"total_bytes": b_total, "slices": []}
            )
            bucket["total_bytes"] = b_total
            bucket["slices"].append((b1, b2))
            # Check if this window's byte slices now close [0, b_total)
            merged_bytes = merge_intervals(bucket["slices"], closed_upper=False)
            if len(merged_bytes) == 1 and merged_bytes[0][0] <= 0 and merged_bytes[0][1] >= b_total:
                self.intact_line_intervals.append(win)

        if parsed.get("truncated_line_gap") is not None:
            g1, g2 = parsed["truncated_line_gap"]
            if g2 >= g1:
                self.tail_truncated_gaps.append((int(g1), int(g2)))

    def evaluate(
        self,
        check_disk: bool = True,
        snapshot_map: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        failures: List[str] = []
        unclosed_windows: List[Dict[str, Any]] = []
        merged_intact = merge_intervals(self.intact_line_intervals, closed_upper=True)

        # 1. Check any window_byte_slices that never closed [0, b_total)
        for win, bucket in sorted(self.window_byte_slices.items()):
            b_total = bucket["total_bytes"]
            merged_bytes = merge_intervals(bucket["slices"], closed_upper=False)
            window_closed = (
                len(merged_bytes) == 1
                and merged_bytes[0][0] <= 0
                and merged_bytes[0][1] >= b_total
            )
            if not window_closed:
                # Check if the line range `win` was nevertheless covered by other intact line reads
                win_gaps = compute_line_gaps(merged_intact, win[1])
                uncovered_in_win = [
                    ( max(win[0], ga), min(win[1], gb) )
                    for ga, gb in win_gaps
                    if max(win[0], ga) <= min(win[1], gb)
                ]
                next_offset = merged_bytes[0][1] if merged_bytes and merged_bytes[0][0] == 0 else 0
                unclosed_windows.append(
                    {
                        "window": list(win),
                        "total_window_bytes": b_total,
                        "covered_byte_intervals": [list(x) for x in merged_bytes],
                        "next_required_byte_offset": next_offset,
                        "uncovered_lines_in_window": [list(x) for x in uncovered_in_win],
                    }
                )
                failures.append("byte_truncated_unresumed")

        # 2. Check any tail_truncated_gaps from Dialect 3 (`<truncated N bytes>`)
        unclosed_tail_gaps: List[List[int]] = []
        for g1, g2 in self.tail_truncated_gaps:
            sub_gaps = compute_line_gaps(merged_intact, g2)
            for ga, gb in sub_gaps:
                ov_a, ov_b = max(g1, ga), min(g2, gb)
                if ov_a <= ov_b:
                    unclosed_tail_gaps.append([ov_a, ov_b])
        if unclosed_tail_gaps and "byte_truncated_unresumed" not in failures:
            failures.append("byte_truncated_unresumed")

        # 3. Determine target total_lines and check post-edit / disk consistency
        target_total_lines = self.reported_total_lines or 0
        disk_lines: Optional[int] = None
        disk_bytes: Optional[int] = None
        disk_sha256: Optional[str] = None
        p = Path(self.path)
        is_mutable_ledger_projection = p.name.startswith("read_snapshots")
        if check_disk and p.is_file():
            disk_lines, disk_bytes = count_file_lines_and_bytes(p)
            disk_sha256 = sha256_file(p)
            # Note: view_file reports `Total Lines` counting trailing empty line if file ends with \n,
            # while Go's read_file splits strictly on \n (ignoring bare \r) after stripping trailing \n.
            raw_text = p.read_bytes().decode("utf-8", errors="replace")
            view_file_line_count = len(raw_text.split("\n")) if raw_text else 0
            read_file_line_count = len(raw_text.rstrip("\n").split("\n")) if raw_text else 0
            if not is_mutable_ledger_projection:
                if self.reported_total_lines is not None:
                    if self.reported_total_lines not in {
                        disk_lines,
                        view_file_line_count,
                        read_file_line_count,
                    }:
                        failures.append("stale_read_after_edit")
                if self.reported_total_bytes is not None and disk_bytes is not None:
                    if self.reported_total_bytes != disk_bytes:
                        if "stale_read_after_edit" not in failures:
                            failures.append("stale_read_after_edit")
                # Check snapshot ledger for equal-length in-place edits
                if snapshot_map and self.path in snapshot_map:
                    snap = snapshot_map[self.path]
                    snap_step = snap.get("last_read_step")
                    snap_sha = snap.get("disk_sha256_at_read")
                    if (
                        snap_step is not None
                        and self.last_read_step is not None
                        and int(snap_step) >= int(self.last_read_step)
                        and snap_sha
                        and disk_sha256
                        and snap_sha != disk_sha256
                    ):
                        if "stale_read_after_edit" not in failures:
                            failures.append("stale_read_after_edit")
            if target_total_lines == 0 and not self.is_empty_file:
                target_total_lines = disk_lines

        # 4. If edited after a prior read and never re-read
        if (
            self.last_edit_step is not None
            and self.last_read_step is None
            and len(self.invalidated_reads_before_edit) > 0
        ):
            if "stale_read_after_edit" not in failures:
                failures.append("stale_read_after_edit")

        # 5. Compute line gaps against target_total_lines
        if self.is_empty_file and (disk_lines in (None, 0)):
            line_gaps: List[Tuple[int, int]] = []
        elif target_total_lines > 0:
            line_gaps = compute_line_gaps(merged_intact, target_total_lines)
        else:
            line_gaps = [(1, max(1, disk_lines or 1))]

        if line_gaps:
            failures.append("line_range_incomplete")

        fully_read = len(failures) == 0 and (
            self.is_empty_file or (target_total_lines > 0 and len(merged_intact) > 0)
        )
        return {
            "path": self.path,
            "fully_read": fully_read,
            "failures": failures,
            "reported_total_lines": self.reported_total_lines,
            "reported_total_bytes": self.reported_total_bytes,
            "disk_lines": disk_lines,
            "disk_bytes": disk_bytes,
            "disk_sha256": disk_sha256,
            "covered_line_intervals": [list(x) for x in merged_intact],
            "missing_line_intervals": [list(x) for x in line_gaps],
            "unclosed_byte_windows": unclosed_windows,
            "unclosed_tail_truncated_gaps": unclosed_tail_gaps,
            "dialects_seen": self.dialects_seen,
            "read_steps": self.read_steps,
            "last_read_step": self.last_read_step,
            "last_edit_step": self.last_edit_step,
            "invalidated_reads_before_edit": self.invalidated_reads_before_edit,
        }


def load_read_snapshots(snapshot_ledger_path: Optional[Path]) -> Dict[str, Dict[str, Any]]:
    snapshots: Dict[str, Dict[str, Any]] = {}
    if not snapshot_ledger_path or not snapshot_ledger_path.is_file():
        return snapshots
    for line in snapshot_ledger_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            if isinstance(row, dict) and row.get("path"):
                snapshots[str(row["path"])] = row
        except json.JSONDecodeError:
            continue
    return snapshots


def append_read_snapshots(
    snapshot_ledger_path: Path, file_results: Dict[str, Dict[str, Any]]
) -> int:
    existing = load_read_snapshots(snapshot_ledger_path)
    snapshot_ledger_path.parent.mkdir(parents=True, exist_ok=True)
    appended = 0
    with snapshot_ledger_path.open("a", encoding="utf-8") as f:
        for path_str, info in sorted(file_results.items()):
            if not info.get("fully_read") or not info.get("disk_sha256"):
                continue
            last_step = info.get("last_read_step")
            prev = existing.get(path_str)
            if (
                prev
                and prev.get("last_read_step") == last_step
                and prev.get("disk_sha256_at_read") == info["disk_sha256"]
            ):
                continue
            entry = {
                "path": path_str,
                "last_read_step": last_step,
                "closed_line_intervals": info.get("covered_line_intervals", []),
                "disk_sha256_at_read": info["disk_sha256"],
                "total_lines": info.get("disk_lines"),
                "total_bytes": info.get("disk_bytes"),
            }
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            appended += 1
    return appended


def audit_transcript_steps(
    steps: List[Dict[str, Any]],
    steps_dir: Optional[Path] = None,
    required_files: Optional[List[str]] = None,
    check_disk: bool = True,
    snapshot_ledger_path: Optional[Path] = None,
    record_new_snapshots: bool = True,
    on_disk_input_envelope: Optional[Path] = None,
) -> Dict[str, Any]:
    """Core auditor over parsed `transcript_full.jsonl` rows."""
    trackers: Dict[str, FileReadTracker] = {}
    pending_tool_calls: List[Tuple[int, str, str]] = []  # (step_idx, tool_name, target_path)
    non_read_tool_bypasses: List[Dict[str, Any]] = []
    spilled_outputs_referenced: Dict[str, int] = {}  # normalized_path -> step_index
    user_input_truncations: List[Dict[str, Any]] = []
    user_input_warnings: List[Dict[str, Any]] = []

    for idx, row in enumerate(steps):
        step_index = int(row.get("step_index", idx))
        step_type = str(row.get("type", ""))
        content = str(row.get("content", "") or "")

        # 1. Audit USER_INPUT for 213KB hard truncation & 160KB envelope threshold
        if step_type == "USER_INPUT":
            utf8_len = len(content.encode("utf-8"))
            trunc_fields = row.get("truncated_fields") or []
            unclosed_tag = content.startswith("<USER_REQUEST>") and "</USER_REQUEST>" not in content
            if "content" in trunc_fields or unclosed_tag or utf8_len >= HARD_TRUNCATION_BYTES:
                user_input_truncations.append(
                    {
                        "step_index": step_index,
                        "utf8_bytes": utf8_len,
                        "truncated_fields": trunc_fields,
                        "unclosed_user_request_tag": unclosed_tag,
                        "reason": "hard_truncated_213kb_or_unclosed_tag",
                    }
                )
            elif utf8_len >= WARN_INPUT_BYTES:
                envelope_verified = bool(
                    on_disk_input_envelope and on_disk_input_envelope.is_file()
                )
                entry = {
                    "step_index": step_index,
                    "utf8_bytes": utf8_len,
                    "on_disk_envelope_verified": envelope_verified,
                    "reason": "approaching_213kb_limit_requires_on_disk_envelope",
                }
                if not envelope_verified:
                    user_input_truncations.append(entry)
                else:
                    user_input_warnings.append(entry)

        # 2. Scan tool_calls in PLANNER_RESPONSE (or inline tool call steps)
        tcs = row.get("tool_calls") or []
        if isinstance(tcs, list) and tcs:
            for tc in tcs:
                if not isinstance(tc, dict):
                    continue
                tname = str(tc.get("name") or tc.get("tool_name") or "")
                targs = tc.get("args") or tc.get("arguments") or {}
                if not isinstance(targs, dict):
                    targs = {}
                target_path = _extract_tool_target_path(targs)

                if tname in READ_TOOL_NAMES:
                    # Check if output is embedded directly on the tool_call object
                    inline_out = tc.get("output") or tc.get("result")
                    if isinstance(inline_out, str) and inline_out.strip():
                        parsed = parse_read_tool_output(inline_out, fallback_path=target_path)
                        final_path = parsed.get("target_path") or target_path
                        if final_path and parsed.get("recognized"):
                            tr = trackers.setdefault(final_path, FileReadTracker(final_path))
                            tr.record_read(step_index, parsed)
                    else:
                        pending_tool_calls.append((step_index, tname, target_path))
                elif tname in WRITE_TOOL_NAMES:
                    if target_path and target_path in trackers:
                        trackers[target_path].invalidate_on_edit(step_index, tname)
                elif tname in {"run_command", "run_terminal_command", "grep", "grep_search", "view_file_outline"}:
                    cmd_str = str(targs.get("CommandLine") or targs.get("command") or "")
                    if tname in {"grep", "grep_search", "view_file_outline"} or re.search(
                        r"\b(cat|head|tail|sed|awk|rg|grep)\b", cmd_str
                    ):
                        non_read_tool_bypasses.append(
                            {
                                "step_index": step_index,
                                "tool_name": tname,
                                "command_or_target": cmd_str[:160] or target_path,
                                "counted_toward_read_coverage": False,
                            }
                        )

        # 3. Scan GENERIC / tool output content
        if content:
            first_num = RE_NUMBERED_LINE.search(content)
            preamble_only = content[: first_num.start()] if first_num else content[:600]
            for m_spill in RE_SPILLED_OUTPUT.finditer(preamble_only):
                spilled_path = normalize_target_path(m_spill.group(1))
                spilled_outputs_referenced[spilled_path] = step_index

            if step_type in {"GENERIC", "TOOL_RESPONSE", "TOOL_RESULT"} or not tcs:
                parsed_probe = parse_read_tool_output(content)
                if parsed_probe.get("recognized"):
                    caller_step = step_index
                    caller_path = ""
                    if pending_tool_calls:
                        caller_step, _, caller_path = pending_tool_calls.pop(0)
                    parsed = parse_read_tool_output(content, fallback_path=caller_path)
                    final_path = parsed.get("target_path") or caller_path
                    if final_path:
                        tr = trackers.setdefault(final_path, FileReadTracker(final_path))
                        tr.record_read(caller_step, parsed)

    snapshot_map = load_read_snapshots(snapshot_ledger_path)
    file_coverage: Dict[str, Dict[str, Any]] = {}
    partial_reads: List[Dict[str, Any]] = []
    stale_reads_after_edit: List[Dict[str, Any]] = []

    for path_str, tracker in sorted(trackers.items()):
        res = tracker.evaluate(check_disk=check_disk, snapshot_map=snapshot_map)
        file_coverage[path_str] = res
        if not res["fully_read"]:
            if "stale_read_after_edit" in res["failures"]:
                stale_reads_after_edit.append(res)
            else:
                partial_reads.append(res)

    if record_new_snapshots and snapshot_ledger_path is not None:
        append_read_snapshots(snapshot_ledger_path, file_coverage)

    # 4. Fail-closed audit of spilled `output.txt` files
    unread_spilled_outputs: List[Dict[str, Any]] = []
    missing_or_deleted_spilled_outputs: List[Dict[str, Any]] = []
    partially_read_spilled_outputs: List[Dict[str, Any]] = []

    for spilled_path, ref_step in sorted(spilled_outputs_referenced.items()):
        sp = Path(spilled_path)
        if check_disk and not sp.is_file():
            missing_or_deleted_spilled_outputs.append(
                {
                    "path": spilled_path,
                    "referenced_at_step": ref_step,
                    "reason": "missing_or_deleted_spilled_output_fails_closed",
                }
            )
            continue
        cov = file_coverage.get(spilled_path)
        if cov is None:
            unread_spilled_outputs.append(
                {
                    "path": spilled_path,
                    "referenced_at_step": ref_step,
                    "reason": "spilled_output_never_read_via_read_file",
                }
            )
        elif not cov.get("fully_read"):
            partially_read_spilled_outputs.append(
                {
                    "path": spilled_path,
                    "referenced_at_step": ref_step,
                    "failures": cov.get("failures", []),
                    "missing_line_intervals": cov.get("missing_line_intervals", []),
                    "unclosed_byte_windows": cov.get("unclosed_byte_windows", []),
                }
            )

    # 5. Mandatory required files verification
    req_list = expand_file_or_dir_list(required_files or [])
    unread_required_files: List[str] = []
    partially_read_required_files: List[Dict[str, Any]] = []
    for req_path in req_list:
        cov = file_coverage.get(req_path)
        if cov is None:
            unread_required_files.append(req_path)
        elif not cov.get("fully_read"):
            partially_read_required_files.append(cov)

    passed = (
        len(partial_reads) == 0
        and len(stale_reads_after_edit) == 0
        and len(unread_spilled_outputs) == 0
        and len(missing_or_deleted_spilled_outputs) == 0
        and len(partially_read_spilled_outputs) == 0
        and len(user_input_truncations) == 0
        and len(unread_required_files) == 0
        and len(partially_read_required_files) == 0
    )

    return {
        "passed": passed,
        "authority_log_source": "transcript_full.jsonl",
        "total_steps_scanned": len(steps),
        "total_files_read": len(file_coverage),
        "fully_read_file_count": sum(1 for v in file_coverage.values() if v["fully_read"]),
        "partial_reads": partial_reads,
        "stale_reads_after_edit": stale_reads_after_edit,
        "unread_spilled_outputs": unread_spilled_outputs,
        "missing_or_deleted_spilled_outputs": missing_or_deleted_spilled_outputs,
        "partially_read_spilled_outputs": partially_read_spilled_outputs,
        "user_input_truncations": user_input_truncations,
        "user_input_warnings": user_input_warnings,
        "required_files_count": len(req_list),
        "unread_required_files": unread_required_files,
        "partially_read_required_files": partially_read_required_files,
        "non_read_tool_bypasses": non_read_tool_bypasses,
        "unprovable_silent_tail_drop_note": (
            "Host silent tail drops without truncated_fields or unclosed tags cannot be disproven "
            "by transcript length alone; inputs >= 160KB require SHA256-locked on-disk envelopes."
        ),
        "file_coverage": file_coverage,
    }


def audit_session_reads(
    conversation_id: str,
    brain_dir: Optional[str] = None,
    task_order_path: Optional[Path] = None,
    declaration_path: Optional[Path] = None,
    extra_required_files: Optional[List[str]] = None,
    project_root: Optional[Path] = None,
    snapshot_ledger_path: Optional[Path] = None,
    search_brain_roots: Optional[List[Path]] = None,
) -> Dict[str, Any]:
    """High-level entry to audit an AGY session's `transcript_full.jsonl`."""
    loc = locate_session_logs(
        conversation_id=conversation_id,
        brain_dir=brain_dir,
        search_brain_roots=search_brain_roots,
    )
    if not loc.get("ok"):
        return {
            "passed": False,
            "error": loc,
        }

    full_path = Path(loc["transcript_full_path"])
    steps_dir = Path(loc["steps_dir"])
    steps: List[Dict[str, Any]] = []
    for line in full_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            steps.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    required: List[str] = []
    if task_order_path and task_order_path.is_file():
        required.extend(extract_mandatory_files_from_task_order(task_order_path))
    if declaration_path and declaration_path.is_file():
        decl_data = json.loads(declaration_path.read_text(encoding="utf-8"))
        required.extend(extract_mandatory_files_from_declaration(decl_data, project_root=project_root))
    if extra_required_files:
        required.extend(expand_file_or_dir_list(extra_required_files, cwd=project_root))

    if snapshot_ledger_path is None and project_root is not None:
        snapshot_ledger_path = (
            project_root
            / ".biluohuangquan"
            / "harness-guardrails"
            / f"read_snapshots_{conversation_id[:8]}.jsonl"
        )

    res = audit_transcript_steps(
        steps=steps,
        steps_dir=steps_dir,
        required_files=required,
        check_disk=True,
        snapshot_ledger_path=snapshot_ledger_path,
        record_new_snapshots=True,
    )
    res["conversation_id"] = conversation_id
    res["transcript_full_path"] = str(full_path)
    return res
