#!/usr/bin/env python3
"""Guardrail 2: Compaction & Citation Verifier (v1.1).

Prevents long-horizon compaction amnesia and symbol hallucination (`note_compaction_citation_drift_001`):
1. Detects `/compact` events (`CHECKPOINT: Resuming from a compaction` / `<CONTEXT_SUMMARY>`) in
   `transcript_full.jsonl` and verifies that anchor files AND any pre-existing target source files
   modified or cited after compaction were 100% re-read after the compaction step.
2. Performs AST definition-level symbol verification (forbidding substring-in-line-range fallback):
   - Extracts top-level & nested functions (`outer.inner`), classes (`ClassName`), methods
     (`ClassName.method` with strict per-class line range overlap), and module/class assignments
     (`Assign`, `AnnAssign`, `TypeAlias`), while separating `import_bindings` so imported aliases
     cannot impersonate native definitions.
   - Strips comments and string literals before scanning JS/TS definitions.
3. Three-dimensional symbol drift diagnostics:
   - Dimension 1 (Line-range actual definition lookup): catches non-neighbor rewrites like `invoke -> pre`
     (`line_range_defines_different_symbol`).
   - Dimension 2 (Token-reordering & stem Jaccard): catches `TestArchUnit030 -> ArchUnit030Tests`.
   - Dimension 3 (Edit distance & token overlap): catches `evaluate_completion -> cmd_check_completion`.
4. Static cross-module symbol scanner (`verify_code_external_symbols`):
   - Verifies `from <mod> import <real_name> [as <alias>]` (checking `<real_name>` in `<mod>`'s AST
     definitions) and `<mod_alias>.<attr>` attribute references in newly written Python code.
"""

from __future__ import annotations

import ast
import difflib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from guardrails.read_auditor import (
    READ_TOOL_NAMES,
    WRITE_TOOL_NAMES,
    _extract_tool_target_path,
    audit_transcript_steps,
    locate_session_logs,
    normalize_target_path,
    parse_read_tool_output,
)

COMPACTION_MARKERS = (
    "CHECKPOINT: Resuming from a compaction",
    "<CONTEXT_SUMMARY>",
)

RE_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])")
RE_MARKDOWN_FILE_LINK = re.compile(
    r"\[`?([^\]`]+)`?\]\(file://([^)#\s]+)(?:#L(\d+)(?:-L?(\d+))?)?\)"
)


def tokenize_symbol(sym: str) -> List[str]:
    base = sym.split(".")[-1]
    raw_parts = re.split(r"[_\-\s]+", base)
    tokens: List[str] = []
    for part in raw_parts:
        if not part:
            continue
        for sub in RE_CAMEL_BOUNDARY.split(part):
            s = sub.lower().strip()
            if not s:
                continue
            if len(s) > 3 and s.endswith("s"):
                s = s[:-1]
            tokens.append(s)
    return tokens


def symbol_similarity(query: str, candidate: str) -> Tuple[float, str]:
    """Compute composite similarity and match kind between `query` and `candidate`."""
    q_base = query.split(".")[-1]
    c_base = candidate.split(".")[-1]
    q_low = q_base.lower()
    c_low = c_base.lower()

    seq_ratio = difflib.SequenceMatcher(None, q_low, c_low).ratio()
    q_tokens = tokenize_symbol(q_base)
    c_tokens = tokenize_symbol(c_base)
    q_set = set(q_tokens)
    c_set = set(c_tokens)

    jaccard = (len(q_set & c_set) / len(q_set | c_set)) if (q_set and c_set) else 0.0
    if q_set and q_set == c_set:
        return max(0.95, seq_ratio), "token_reordering_exact"
    if q_set and c_set and (q_set & c_set):
        combined = max(seq_ratio, 0.55 * jaccard + 0.45 * seq_ratio + 0.15)
        return min(0.99, combined), "token_overlap_and_edit_distance"
    return seq_ratio, "edit_distance"


class _PythonDefinitionExtractor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.scope_stack: List[Tuple[str, str]] = []  # (name, kind: "class" | "func")
        self.definitions: List[Dict[str, Any]] = []
        self.import_bindings: Dict[str, Dict[str, Any]] = {}

    def _node_start_line(self, node: ast.AST) -> int:
        lineno = getattr(node, "lineno", 1)
        decos = getattr(node, "decorator_list", None) or []
        deco_lines = [getattr(d, "lineno", lineno) for d in decos]
        return min([lineno] + deco_lines)

    def _add_def(self, bare_name: str, kind: str, line_start: int, line_end: int) -> None:
        if not bare_name:
            return
        prefix = ".".join(name for name, _ in self.scope_stack)
        qualified = f"{prefix}.{bare_name}" if prefix else bare_name
        enclosing_class = next(
            (name for name, skind in reversed(self.scope_stack) if skind == "class"),
            None,
        )
        self.definitions.append(
            {
                "bare_name": bare_name,
                "qualified_name": qualified,
                "enclosing_class": enclosing_class,
                "kind": kind,
                "line_start": int(line_start),
                "line_end": int(max(line_start, line_end)),
            }
        )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        start = self._node_start_line(node)
        end = getattr(node, "end_lineno", start) or start
        self._add_def(node.name, "class", start, end)
        self.scope_stack.append((node.name, "class"))
        self.generic_visit(node)
        self.scope_stack.pop()

    def _visit_func(self, node: ast.AST, is_async: bool = False) -> None:
        name = getattr(node, "name", "")
        start = self._node_start_line(node)
        end = getattr(node, "end_lineno", start) or start
        if self.scope_stack and self.scope_stack[-1][1] == "class":
            kind = "async_method" if is_async else "method"
        elif self.scope_stack:
            kind = "nested_async_function" if is_async else "nested_function"
        else:
            kind = "async_function" if is_async else "function"
        self._add_def(name, kind, start, end)
        self.scope_stack.append((name, "func"))
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_func(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_func(node, is_async=True)

    def _extract_assign_targets(self, target: ast.AST, start: int, end: int) -> None:
        if isinstance(target, ast.Name):
            self._add_def(target.id, "assignment", start, end)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._extract_assign_targets(elt, start, end)

    def visit_Assign(self, node: ast.Assign) -> None:
        # Record module-level and class-body assignments
        if not self.scope_stack or self.scope_stack[-1][1] == "class":
            start = getattr(node, "lineno", 1)
            end = getattr(node, "end_lineno", start) or start
            for t in node.targets:
                self._extract_assign_targets(t, start, end)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if not self.scope_stack or self.scope_stack[-1][1] == "class":
            start = getattr(node, "lineno", 1)
            end = getattr(node, "end_lineno", start) or start
            self._extract_assign_targets(node.target, start, end)
        self.generic_visit(node)

    def visit_TypeAlias(self, node: Any) -> None:
        start = getattr(node, "lineno", 1)
        end = getattr(node, "end_lineno", start) or start
        name_node = getattr(node, "name", None)
        if isinstance(name_node, ast.Name):
            self._add_def(name_node.id, "type_alias", start, end)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        start = getattr(node, "lineno", 1)
        end = getattr(node, "end_lineno", start) or start
        for alias in node.names:
            bound = alias.asname or alias.name.split(".")[0]
            self.import_bindings[bound] = {
                "bound_name": bound,
                "module": alias.name,
                "real_name": None,
                "line_start": start,
                "line_end": end,
            }

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        start = getattr(node, "lineno", 1)
        end = getattr(node, "end_lineno", start) or start
        mod = node.module or ""
        for alias in node.names:
            bound = alias.asname or alias.name
            self.import_bindings[bound] = {
                "bound_name": bound,
                "module": mod,
                "real_name": alias.name,
                "line_start": start,
                "line_end": end,
            }


def extract_python_definitions(source_text: str) -> Dict[str, Any]:
    try:
        tree = ast.parse(source_text)
    except SyntaxError as exc:
        return {
            "ok": False,
            "syntax_error": str(exc),
            "definitions": [],
            "import_bindings": {},
        }
    extractor = _PythonDefinitionExtractor()
    extractor.visit(tree)
    return {
        "ok": True,
        "definitions": extractor.definitions,
        "import_bindings": extractor.import_bindings,
    }


def strip_js_comments_and_strings(source_text: str) -> str:
    """Replace comments and string literals with spaces while preserving all newlines."""
    out: List[str] = []
    i = 0
    n = len(source_text)
    while i < n:
        ch = source_text[i]
        nxt = source_text[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            while i < n and source_text[i] != "\n":
                out.append(" ")
                i += 1
        elif ch == "/" and nxt == "*":
            out.append("  ")
            i += 2
            while i < n and not (source_text[i] == "*" and (i + 1 < n and source_text[i + 1] == "/")):
                out.append("\n" if source_text[i] == "\n" else " ")
                i += 1
            if i < n:
                out.append("  ")
                i += 2
        elif ch in ("'", '"', "`"):
            quote = ch
            out.append(" ")
            i += 1
            while i < n:
                c = source_text[i]
                if c == "\\" and i + 1 < n:
                    out.append("\n" if source_text[i + 1] == "\n" else "  ")
                    i += 2
                    continue
                if c == quote:
                    out.append(" ")
                    i += 1
                    break
                out.append("\n" if c == "\n" else " ")
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _find_brace_end_line(lines: List[str], start_line_idx: int) -> int:
    depth = 0
    opened = False
    for idx in range(start_line_idx, len(lines)):
        for ch in lines[idx]:
            if ch == "{":
                depth += 1
                opened = True
            elif ch == "}":
                depth -= 1
                if opened and depth <= 0:
                    return idx + 1
    return start_line_idx + 1


def extract_js_definitions(source_text: str) -> Dict[str, Any]:
    cleaned = strip_js_comments_and_strings(source_text)
    lines = cleaned.splitlines()
    definitions: List[Dict[str, Any]] = []
    import_bindings: Dict[str, Dict[str, Any]] = {}

    re_func = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\b")
    re_class = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)\b")
    re_var = re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=")
    re_import = re.compile(r"^\s*import\s+(.+?)\s+from\s+")

    for idx, line in enumerate(lines):
        lineno = idx + 1
        m_imp = re_import.search(line)
        if m_imp:
            spec = m_imp.group(1)
            for ident in re.findall(r"\b([A-Za-z_$][\w$]*)\b", spec):
                if ident not in {"as", "from", "type"}:
                    import_bindings[ident] = {
                        "bound_name": ident,
                        "line_start": lineno,
                        "line_end": lineno,
                    }
            continue
        for kind, regex in (("function", re_func), ("class", re_class), ("assignment", re_var)):
            m = regex.search(line)
            if m:
                name = m.group(1)
                end_line = _find_brace_end_line(lines, idx) if "{" in line else lineno
                definitions.append(
                    {
                        "bare_name": name,
                        "qualified_name": name,
                        "enclosing_class": None,
                        "kind": kind,
                        "line_start": lineno,
                        "line_end": max(lineno, end_line),
                    }
                )
                break
    return {
        "ok": True,
        "definitions": definitions,
        "import_bindings": import_bindings,
    }


def extract_file_definitions(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    suffix = path.suffix.lower()
    if suffix == ".py":
        return extract_python_definitions(text)
    if suffix in {".js", ".mjs", ".cjs", ".ts", ".tsx"}:
        return extract_js_definitions(text)
    return {"ok": True, "definitions": [], "import_bindings": {}}


def _intervals_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return max(a_start, b_start) <= min(a_end, b_end)


def verify_single_citation(
    item: Dict[str, Any],
    allowed_roots: List[Path],
    voices_map: Dict[str, Dict[str, Any]],
    project_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Verify a single citation object against disk AST/lines or voice_ledger."""
    failures: List[str] = []
    diagnostics: Dict[str, Any] = {}

    voice_id = item.get("voice_id")
    if voice_id:
        voice = voices_map.get(str(voice_id))
        quote = str(item.get("quote", "") or "")
        if not voice:
            failures.append("voice_id_not_found")
        elif quote and quote not in str(voice.get("text", "")):
            failures.append("voice_quote_mismatch")
        return {
            "citation": item,
            "verified": len(failures) == 0,
            "failures": failures,
            "diagnostics": diagnostics,
        }

    raw_path = str(item.get("path", "") or "")
    norm_path = normalize_target_path(raw_path, cwd=project_root)
    if not norm_path:
        return {
            "citation": item,
            "verified": False,
            "failures": ["citation_path_missing"],
            "diagnostics": diagnostics,
        }
    p = Path(norm_path)
    if allowed_roots and not any(
        p == root or root in p.parents for root in allowed_roots
    ):
        failures.append("path_outside_allowed_roots")
    if not p.is_file():
        failures.append("cited_file_missing")
        return {
            "citation": item,
            "verified": False,
            "failures": failures,
            "diagnostics": diagnostics,
        }

    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    total_lines = len(lines)

    line_start = item.get("line_start")
    line_end = item.get("line_end", line_start)
    has_line_range = isinstance(line_start, int) and isinstance(line_end, int)
    if has_line_range:
        if line_start < 1 or line_end < line_start or line_end > max(1, total_lines):
            failures.append("invalid_line_range")
            has_line_range = False

    # 1. Verify quote in line range (and check for line drift)
    quote = str(item.get("quote", "") or "")
    if quote:
        if has_line_range:
            segment = "\n".join(lines[line_start - 1 : line_end])
            if quote not in segment:
                failures.append("quote_not_found_in_line_range")
                # Check if quote exists elsewhere in the file
                if quote in text and len(quote.strip()) >= 6:
                    for idx, l_str in enumerate(lines):
                        if quote in l_str:
                            diagnostics["quote_line_drift"] = {
                                "cited_range": [line_start, line_end],
                                "actual_line": idx + 1,
                            }
                            failures.append("quote_line_drift")
                            break
        else:
            if quote not in text:
                failures.append("quote_not_found")

    # 2. Definition-level AST / lexical symbol verification (NO substring fallback!)
    symbol = str(item.get("symbol", "") or "").strip()
    if symbol:
        def_info = extract_file_definitions(p)
        defs = def_info.get("definitions", [])
        imp_bindings = def_info.get("import_bindings", {})

        # Find exact name matches in definitions (qualified or bare)
        name_matches = [
            d
            for d in defs
            if d["qualified_name"] == symbol or d["bare_name"] == symbol
        ]

        if not name_matches:
            if symbol in imp_bindings:
                failures.append("cited_symbol_is_import_not_definition")
                diagnostics["import_binding"] = imp_bindings[symbol]
            else:
                failures.append("symbol_not_found_possible_compaction_drift")
                # Dimension 1: check what AST symbols are actually defined inside [line_start, line_end]
                if has_line_range:
                    in_range_defs = [
                        d
                        for d in defs
                        if _intervals_overlap(line_start, line_end, d["line_start"], d["line_end"])
                    ]
                    if in_range_defs:
                        failures.append("line_range_defines_different_symbol")
                        diagnostics["actual_symbols_in_line_range"] = [
                            {
                                "qualified_name": d["qualified_name"],
                                "bare_name": d["bare_name"],
                                "kind": d["kind"],
                                "line_start": d["line_start"],
                                "line_end": d["line_end"],
                            }
                            for d in in_range_defs
                        ]

                # Dimension 2 & 3: Token reordering Jaccard + Edit distance closest real symbols
                scored: List[Tuple[float, str, Dict[str, Any]]] = []
                for d in defs:
                    for cand_name in {d["bare_name"], d["qualified_name"]}:
                        score, match_kind = symbol_similarity(symbol, cand_name)
                        if score >= 0.42:
                            scored.append((score, match_kind, d))
                scored.sort(key=lambda x: (-x[0], x[2]["line_start"]))
                closest: List[Dict[str, Any]] = []
                seen_q: Set[str] = set()
                for sc, mkind, d in scored:
                    qname = f"{d['qualified_name']}@{d['line_start']}"
                    if qname in seen_q:
                        continue
                    seen_q.add(qname)
                    closest.append(
                        {
                            "symbol": d["qualified_name"],
                            "bare_name": d["bare_name"],
                            "kind": d["kind"],
                            "line_start": d["line_start"],
                            "line_end": d["line_end"],
                            "similarity": round(sc, 3),
                            "match_kind": mkind,
                        }
                    )
                    if len(closest) >= 5:
                        break
                diagnostics["closest_real_symbols"] = closest
        else:
            # Symbol name exists in file definitions; if line_start..line_end is given, verify overlap!
            if has_line_range:
                overlapping = [
                    d
                    for d in name_matches
                    if _intervals_overlap(line_start, line_end, d["line_start"], d["line_end"])
                ]
                if not overlapping:
                    failures.append("symbol_line_range_mismatch")
                    diagnostics["matching_symbol_definitions_at_other_lines"] = [
                        {
                            "qualified_name": d["qualified_name"],
                            "enclosing_class": d["enclosing_class"],
                            "line_start": d["line_start"],
                            "line_end": d["line_end"],
                        }
                        for d in name_matches
                    ]

    return {
        "citation": item,
        "resolved_path": str(p),
        "verified": len(failures) == 0,
        "failures": failures,
        "diagnostics": diagnostics,
    }


def extract_citations_from_markdown(md_path: Path) -> List[Dict[str, Any]]:
    text = md_path.read_text(encoding="utf-8", errors="replace")
    citations: List[Dict[str, Any]] = []
    for m in RE_MARKDOWN_FILE_LINK.finditer(text):
        label, raw_path, l1, l2 = m.groups()
        entry: Dict[str, Any] = {"path": raw_path}
        if l1:
            entry["line_start"] = int(l1)
            entry["line_end"] = int(l2) if l2 else int(l1)
        # If label looks like a Python/JS identifier (and not a filename), treat as symbol
        clean_label = label.strip()
        if (
            re.match(r"^[A-Za-z_][\w.]*$", clean_label)
            and not clean_label.endswith((".py", ".md", ".json", ".js", ".mjs", ".ts", ".txt", ".sh"))
            and Path(raw_path).suffix.lower() in {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx"}
        ):
            entry["symbol"] = clean_label
        citations.append(entry)
    return citations


def resolve_local_python_module(
    module_name: str, search_roots: List[Path]
) -> Optional[Path]:
    if not module_name:
        return None
    parts = module_name.split(".")
    for root in search_roots:
        cand_file = root.joinpath(*parts).with_suffix(".py")
        if cand_file.is_file():
            return cand_file.resolve()
        cand_init = root.joinpath(*parts, "__init__.py")
        if cand_init.is_file():
            return cand_init.resolve()
    return None


def verify_code_external_symbols(
    implementation_files: Iterable[str],
    search_roots: List[Path],
    project_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Scan Python implementation files for `from <mod> import <real_name> [as <alias>]`
    and `<mod_alias>.<attr>` against local modules (including `archunit_record.py`).
    Catches hallucinated symbols written directly into code before citations are even created.
    """
    roots = list(search_roots)
    archunit_scripts_dir = Path(
        "/Users/ien/.gemini/config/plugins/biluohuangquan-agy/skills/archunit/scripts"
    )
    if archunit_scripts_dir.is_dir() and archunit_scripts_dir not in roots:
        roots.append(archunit_scripts_dir)

    violations: List[Dict[str, Any]] = []
    files_scanned: List[str] = []

    for raw in implementation_files:
        norm = normalize_target_path(raw, cwd=project_root)
        if not norm:
            continue
        p = Path(norm)
        if not p.is_file() or p.suffix.lower() != ".py":
            continue
        files_scanned.append(str(p))
        text = p.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            violations.append(
                {
                    "file": str(p),
                    "line": exc.lineno or 1,
                    "error_code": "PYTHON_SYNTAX_ERROR",
                    "detail": str(exc),
                }
            )
            continue

        # Map local module alias -> resolved Path
        module_aliases: Dict[str, Path] = {}
        module_defs_cache: Dict[str, Tuple[Set[str], List[Dict[str, Any]]]] = {}

        def _get_mod_defs(mod_path: Path) -> Tuple[Set[str], List[Dict[str, Any]]]:
            key = str(mod_path)
            if key not in module_defs_cache:
                info = extract_python_definitions(
                    mod_path.read_text(encoding="utf-8", errors="replace")
                )
                defs = info.get("definitions", [])
                imp = info.get("import_bindings", {})
                top_names = {
                    d["bare_name"]
                    for d in defs
                    if "." not in d["qualified_name"]
                } | set(imp.keys())
                module_defs_cache[key] = (top_names, defs)
            return module_defs_cache[key]

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    resolved = resolve_local_python_module(alias.name, roots)
                    if resolved:
                        bound = alias.asname or alias.name.split(".")[-1]
                        module_aliases[bound] = resolved
            elif isinstance(node, ast.ImportFrom):
                mod_name = node.module or ""
                resolved = resolve_local_python_module(mod_name, roots)
                if resolved:
                    top_names, all_defs = _get_mod_defs(resolved)
                    for alias in node.names:
                        # Crucial: check `alias.name` (real_name), NOT `alias.asname`!
                        real_name = alias.name
                        if real_name == "*":
                            continue
                        if real_name not in top_names and not resolve_local_python_module(
                            f"{mod_name}.{real_name}", roots
                        ):
                            scored = sorted(
                                (
                                    (symbol_similarity(real_name, d["bare_name"])[0], d)
                                    for d in all_defs
                                    if "." not in d["qualified_name"]
                                ),
                                key=lambda x: -x[0],
                            )
                            violations.append(
                                {
                                    "file": str(p),
                                    "line": getattr(node, "lineno", 1),
                                    "error_code": "IMPORTED_SYMBOL_NOT_FOUND_IN_TARGET_MODULE",
                                    "module": mod_name,
                                    "target_module_path": str(resolved),
                                    "missing_symbol": real_name,
                                    "alias": alias.asname,
                                    "closest_real_symbols": [
                                        {
                                            "symbol": d["bare_name"],
                                            "line_start": d["line_start"],
                                            "line_end": d["line_end"],
                                            "similarity": round(sc, 3),
                                        }
                                        for sc, d in scored[:3]
                                        if sc >= 0.42
                                    ],
                                }
                            )

        # Second pass: check `<mod_alias>.<attr>` attribute references
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                base_name = node.value.id
                attr_name = node.attr
                if base_name in module_aliases:
                    resolved = module_aliases[base_name]
                    top_names, all_defs = _get_mod_defs(resolved)
                    if attr_name not in top_names:
                        scored = sorted(
                            (
                                (symbol_similarity(attr_name, d["bare_name"])[0], d)
                                for d in all_defs
                                if "." not in d["qualified_name"]
                            ),
                            key=lambda x: -x[0],
                        )
                        violations.append(
                            {
                                "file": str(p),
                                "line": getattr(node, "lineno", 1),
                                "error_code": "MODULE_ATTRIBUTE_NOT_FOUND_POSSIBLE_DRIFT",
                                "module_alias": base_name,
                                "target_module_path": str(resolved),
                                "missing_attribute": attr_name,
                                "closest_real_symbols": [
                                    {
                                        "symbol": d["bare_name"],
                                        "line_start": d["line_start"],
                                        "line_end": d["line_end"],
                                        "similarity": round(sc, 3),
                                    }
                                    for sc, d in scored[:3]
                                    if sc >= 0.42
                                ],
                            }
                        )

    return {
        "passed": len(violations) == 0,
        "files_scanned": files_scanned,
        "violations": violations,
    }


def detect_compaction_and_rereads(
    steps: List[Dict[str, Any]],
    anchor_files: List[str],
    target_source_files: Optional[List[str]] = None,
    project_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Detect compaction events in `transcript_full.jsonl` steps and verify that:
    1. All `anchor_files` (`TASK_ORDER`, `00_PRINCIPLES.md`, `02_IMPLEMENTATION_HANDBOOK.md`)
       were 100% read after `last_compaction_step`;
    2. Any pre-existing target source file modified or cited after `last_compaction_step`
       was 100% read after `last_compaction_step` (and before being edited after compaction).
    """
    compaction_steps: List[int] = []
    for idx, row in enumerate(steps):
        step_index = int(row.get("step_index", idx))
        step_type = str(row.get("type", ""))
        if step_type == "CHECKPOINT":
            compaction_steps.append(step_index)
            continue
        if step_type not in {"USER_INPUT", "SYSTEM", "SYSTEM_MESSAGE"}:
            continue
        content = str(row.get("content", "") or "").lstrip()
        if (
            content.startswith("<CONTEXT_SUMMARY>")
            or content.startswith("# Resuming from a compaction")
            or content.startswith("CHECKPOINT: Resuming from a compaction")
            or "\n<CONTEXT_SUMMARY>\n" in content[:400]
            or "Resuming from a compaction" in content[:400]
        ):
            compaction_steps.append(step_index)

    norm_anchors = [
        normalize_target_path(a, cwd=project_root)
        for a in anchor_files
        if normalize_target_path(a, cwd=project_root)
    ]
    norm_targets = [
        normalize_target_path(t, cwd=project_root)
        for t in (target_source_files or [])
        if normalize_target_path(t, cwd=project_root)
    ]

    if not compaction_steps:
        return {
            "compaction_detected": False,
            "compaction_steps": [],
            "last_compaction_step": None,
            "post_compaction_reread_passed": True,
            "missing_anchor_rereads": [],
            "missing_target_source_rereads": [],
        }

    last_compaction = compaction_steps[-1]
    # Slice steps strictly at or after the last compaction step
    post_steps = [
        row
        for idx, row in enumerate(steps)
        if int(row.get("step_index", idx)) >= last_compaction
    ]
    # Also identify which files already existed / were read or written BEFORE `last_compaction`
    pre_steps = [
        row
        for idx, row in enumerate(steps)
        if int(row.get("step_index", idx)) < last_compaction
    ]
    pre_existing_files: Set[str] = set()
    for row in pre_steps:
        for tc in row.get("tool_calls") or []:
            if isinstance(tc, dict):
                targs = tc.get("args") or tc.get("arguments") or {}
                if isinstance(targs, dict):
                    tp = _extract_tool_target_path(targs, cwd=project_root)
                    if tp:
                        pre_existing_files.add(tp)
        c = str(row.get("content", "") or "")
        parsed = parse_read_tool_output(c)
        if parsed.get("recognized") and parsed.get("target_path"):
            pre_existing_files.add(str(parsed["target_path"]))

    post_audit = audit_transcript_steps(
        steps=post_steps,
        check_disk=True,
        record_new_snapshots=False,
    )
    post_cov = post_audit.get("file_coverage", {})

    missing_anchors: List[str] = []
    for anc in norm_anchors:
        if Path(anc).is_file():
            cov = post_cov.get(anc)
            if not cov or not cov.get("fully_read"):
                missing_anchors.append(anc)

    missing_targets: List[str] = []
    for tgt in norm_targets:
        # If target file existed before compaction (or is an outside existing source file)
        if Path(tgt).is_file() and (
            tgt in pre_existing_files
            or (project_root and not Path(tgt).is_relative_to(project_root))
        ):
            cov = post_cov.get(tgt)
            if not cov or not cov.get("fully_read"):
                if tgt not in missing_targets:
                    missing_targets.append(tgt)

    passed = len(missing_anchors) == 0 and len(missing_targets) == 0
    return {
        "compaction_detected": True,
        "compaction_steps": compaction_steps,
        "last_compaction_step": last_compaction,
        "post_compaction_reread_passed": passed,
        "missing_anchor_rereads": missing_anchors,
        "missing_target_source_rereads": missing_targets,
    }


def verify_citations_and_compaction(
    citations: List[Dict[str, Any]],
    project_root: Path,
    conversation_id: Optional[str] = None,
    brain_dir: Optional[str] = None,
    implementation_files: Optional[List[str]] = None,
    extra_allowed_roots: Optional[List[Path]] = None,
    voices_Override: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Full Guardrail 2 entry combining AST citation verification, code symbol scan, and compaction reread audit."""
    project_root = project_root.resolve()
    record_json_path = (
        project_root
        / ".biluohuangquan"
        / "archunit"
        / "session3-harness-guardrails"
        / "record.json"
    )
    allowed_roots: List[Path] = [project_root]
    voices_map: Dict[str, Dict[str, Any]] = {}

    if record_json_path.is_file():
        rec = json.loads(record_json_path.read_text(encoding="utf-8"))
        for r in rec.get("allowed_read_roots", []):
            rp = Path(r).resolve()
            if rp not in allowed_roots:
                allowed_roots.append(rp)
        for v in rec.get("voice_ledger", []):
            if isinstance(v, dict) and v.get("voice_id"):
                voices_map[str(v["voice_id"])] = v

    if extra_allowed_roots:
        for r in extra_allowed_roots:
            rp = Path(r).resolve()
            if rp not in allowed_roots:
                allowed_roots.append(rp)
    if voices_Override:
        for v in voices_Override:
            if isinstance(v, dict) and v.get("voice_id"):
                voices_map[str(v["voice_id"])] = v

    citation_results: List[Dict[str, Any]] = []
    cited_paths: List[str] = []
    for item in citations:
        res = verify_single_citation(
            item=item,
            allowed_roots=allowed_roots,
            voices_map=voices_map,
            project_root=project_root,
        )
        citation_results.append(res)
        if res.get("resolved_path"):
            cited_paths.append(str(res["resolved_path"]))

    citations_passed = all(r["verified"] for r in citation_results)

    code_scan = verify_code_external_symbols(
        implementation_files=implementation_files or [],
        search_roots=[project_root],
        project_root=project_root,
    )

    compaction_audit: Dict[str, Any] = {
        "compaction_detected": False,
        "post_compaction_reread_passed": True,
    }
    if conversation_id:
        loc = locate_session_logs(conversation_id=conversation_id, brain_dir=brain_dir)
        if not loc.get("ok"):
            compaction_audit = {
                "compaction_detected": False,
                "post_compaction_reread_passed": False,
                "error": loc,
            }
        else:
            full_path = Path(loc["transcript_full_path"])
            steps = [
                json.loads(line)
                for line in full_path.read_text(encoding="utf-8", errors="replace").splitlines()
                if line.strip()
            ]
            default_anchors = [
                str(project_root / "TASK_ORDER_SESSION3_HARNESS_GUARDRAILS.md"),
                str(project_root / "docs" / "00_PRINCIPLES.md"),
                str(project_root / "docs" / "02_IMPLEMENTATION_HANDBOOK.md"),
            ]
            target_sources = list(cited_paths) + [
                normalize_target_path(f, cwd=project_root) for f in (implementation_files or [])
            ]
            compaction_audit = detect_compaction_and_rereads(
                steps=steps,
                anchor_files=default_anchors,
                target_source_files=target_sources,
                project_root=project_root,
            )

    passed = (
        citations_passed
        and code_scan["passed"]
        and compaction_audit.get("post_compaction_reread_passed", False)
    )
    return {
        "passed": passed,
        "citations_passed": citations_passed,
        "code_external_symbols_passed": code_scan["passed"],
        "post_compaction_reread_passed": compaction_audit.get("post_compaction_reread_passed", False),
        "citation_results": citation_results,
        "code_external_symbol_scan": code_scan,
        "compaction_audit": compaction_audit,
    }
