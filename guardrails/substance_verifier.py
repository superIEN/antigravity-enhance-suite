#!/usr/bin/env python3
"""Guardrail 3: Normal-Entry Substance Verifier (v1.1).

Blocks hallucinated completion ("duty reduction + evidence expansion"):
1. Implementation substance & hollow/constant/logging-only stub detection (`verify_implementation_substance`):
   - Rejects `.json`/`.md` summaries (`F06`) or `.sh` wrappers masquerading as core implementation.
   - Rejects functions whose bodies are only `pass` / `...` / `raise NotImplementedError` OR public
     functions without control-flow/business calls that only log/print and return constants (`return {}`).
2. Registered Entrypoint Call-Graph & Live Registry Consumption (`verify_entrypoint_reachability`):
   - Locks to `<project_root>/guardrails/entrypoint_registry.json` (forbidding self-referential entrypoint
     overrides on the CLI).
   - Traces AST `Call` edges and single-hop local aliases (`reg = SUBCOMMAND_HANDLERS; reg[cmd]()`) from
     registered entry symbols (`main`), distinguishing live-consumed dispatch registries from dead
     unconsumed containers like `boundSpecialistServices` in `r34–r36` (across both Python and JS/MJS).
3. Attested Runner Empirical Evidence Verification (`verify_empirical_evidence`):
   - Verifies `runner == "harness_guardrails.run_and_attest"`, `run_nonce`, `argv` matching registered
     normal entrypoints with non-empty subcommands, `impl_sha256_map` matching current disk files,
     recomputed `attestation_digest`, and cross-checks `ATTESTED_EVIDENCE_NONCE=<nonce> DIGEST=<digest>`
     in the session's `transcript_full.jsonl` (including spilled `steps/*/output.txt`).
4. Outbound Envelope & Prompt Anti-Induction Hygiene (`verify_declaration_hygiene`):
   - Scans `summary`, `rebuttal_text`, `inputs/<op>_input.json`, and `<op>_prompt.md` (excluding lines
     explicitly tagged `[FORBIDDEN_EXAMPLE_QUOTE]`) for reviewer-induction phrases.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from guardrails.citation_verifier import (
    resolve_local_python_module,
    strip_js_comments_and_strings,
)
from guardrails.read_auditor import (
    RE_SPILLED_OUTPUT,
    locate_session_logs,
    normalize_target_path,
    sha256_file,
)

CODE_EXTENSIONS = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx"}
AUX_SCRIPT_EXTENSIONS = {".sh", ".zsh", ".bash", ".ps1"}
DOC_OR_DATA_EXTENSIONS = {".md", ".json", ".jsonl", ".txt", ".yaml", ".yml", ".toml", ".ini"}

TRIVIAL_BUILTIN_CALLS = {
    "print",
    "len",
    "str",
    "repr",
    "bool",
    "int",
    "float",
    "dict",
    "list",
    "set",
    "tuple",
    "isinstance",
    "issubclass",
    "getattr",
    "hasattr",
    "min",
    "max",
    "sum",
    "sorted",
    "enumerate",
    "zip",
    "range",
    "any",
    "all",
}

ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")
INDUCTION_PATTERNS = [
    re.compile(r"请\s*(?:直接\s*)?(?:返回|给|填|输出|签)\s*`?\"?'?acceptable", re.IGNORECASE),
    re.compile(r"务必\s*(?:返回|给|判定为)\s*`?\"?'?acceptable", re.IGNORECASE),
    re.compile(r"return\s+`?\"?'?acceptable`?\"?'?\s+directly", re.IGNORECASE),
    re.compile(r"must\s+return\s+`?\"?'?acceptable", re.IGNORECASE),
    re.compile(r"set\s+verdict\s+to\s+`?\"?'?acceptable", re.IGNORECASE),
]


def is_test_or_fixture_path(path: Path) -> bool:
    parts = {p.lower() for p in path.parts}
    if "tests" in parts or "test" in parts or "fixtures" in parts or "fixture" in parts:
        return True
    name = path.name.lower()
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
    )


def _is_constant_or_literal_expr(node: Optional[ast.AST]) -> bool:
    if node is None:
        return True
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_constant_or_literal_expr(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            _is_constant_or_literal_expr(k) and _is_constant_or_literal_expr(v)
            for k, v in zip(node.keys, node.values)
        )
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd, ast.Not)):
        return _is_constant_or_literal_expr(node.operand)
    return False


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name):
            return f"{func.value.id}.{func.attr}"
        return func.attr
    return ""


def _analyze_python_function_substance(node: ast.AST) -> Dict[str, Any]:
    body = list(getattr(node, "body", []))
    # Strip docstring
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]

    if not body:
        return {"is_hollow": True, "reason": "empty_body_after_docstring"}

    if len(body) == 1:
        stmt = body[0]
        if isinstance(stmt, ast.Pass):
            return {"is_hollow": True, "reason": "pass_only_stub"}
        if (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and stmt.value.value is Ellipsis
        ):
            return {"is_hollow": True, "reason": "ellipsis_only_stub"}
        if isinstance(stmt, ast.Raise):
            exc = stmt.exc
            if isinstance(exc, ast.Name) and exc.id == "NotImplementedError":
                return {"is_hollow": True, "reason": "not_implemented_error_stub"}
            if (
                isinstance(exc, ast.Call)
                and isinstance(exc.func, ast.Name)
                and exc.func.id == "NotImplementedError"
            ):
                return {"is_hollow": True, "reason": "not_implemented_error_stub"}

    # Check if function has any control-flow, attribute/subscript mutation, or non-trivial calls
    has_control_flow = False
    has_nontrivial_call = False
    has_nonconstant_return = False
    has_side_effect_assign = False
    return_count = 0

    for sub in ast.walk(node):
        if sub is node:
            continue
        if isinstance(
            sub,
            (
                ast.If,
                ast.For,
                ast.AsyncFor,
                ast.While,
                ast.Try,
                ast.With,
                ast.AsyncWith,
                ast.ListComp,
                ast.SetComp,
                ast.DictComp,
                ast.GeneratorExp,
                ast.Yield,
                ast.YieldFrom,
                ast.Raise,
            ),
        ):
            has_control_flow = True
        elif isinstance(sub, ast.Call):
            cname = _call_name(sub)
            base_cname = cname.split(".")[-1]
            is_logging = cname.startswith(("logging.", "logger.", "log."))
            if base_cname not in TRIVIAL_BUILTIN_CALLS and not is_logging:
                has_nontrivial_call = True
        elif isinstance(sub, ast.Return):
            return_count += 1
            if not _is_constant_or_literal_expr(sub.value):
                has_nonconstant_return = True
        elif isinstance(sub, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = (
                sub.targets
                if isinstance(sub, ast.Assign)
                else ([sub.target] if hasattr(sub, "target") else [])
            )
            for t in targets:
                if isinstance(t, (ast.Attribute, ast.Subscript)):
                    has_side_effect_assign = True

    if (
        not has_control_flow
        and not has_nontrivial_call
        and not has_nonconstant_return
        and not has_side_effect_assign
    ):
        return {
            "is_hollow": True,
            "reason": "constant_or_logging_only_stub_without_business_logic",
        }
    return {"is_hollow": False, "reason": "ok"}


def verify_implementation_substance(
    implementation_files: Iterable[str],
    project_root: Optional[Path] = None,
    require_code: bool = True,
    allowed_script_entrypoints: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Verify `implementation_files` contain real non-hollow production source code."""
    code_files: List[str] = []
    test_files: List[str] = []
    auxiliary_scripts: List[str] = []
    doc_or_data_files: List[str] = []
    missing_files: List[str] = []
    hollow_stubs: List[Dict[str, Any]] = []
    function_counts: Dict[str, int] = {}

    allowed_scripts = allowed_script_entrypoints or set()

    for raw in implementation_files:
        norm = normalize_target_path(raw, cwd=project_root)
        if not norm:
            continue
        p = Path(norm)
        if not p.is_file():
            missing_files.append(norm)
            continue
        suffix = p.suffix.lower()
        if is_test_or_fixture_path(p):
            test_files.append(norm)
        elif suffix in CODE_EXTENSIONS:
            code_files.append(norm)
        elif suffix in AUX_SCRIPT_EXTENSIONS:
            if norm in allowed_scripts:
                code_files.append(norm)
            else:
                auxiliary_scripts.append(norm)
        else:
            doc_or_data_files.append(norm)

    failures: List[str] = []
    if missing_files:
        failures.append("IMPLEMENTATION_FILE_MISSING")
    if require_code and len(code_files) == 0:
        failures.append("SUMMARY_OR_DATA_ONLY_NOT_CODE_IMPLEMENTATION")

    for cf in code_files:
        p = Path(cf)
        if p.suffix.lower() == ".py":
            text = p.read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(text)
            except SyntaxError as exc:
                failures.append("PYTHON_SYNTAX_ERROR")
                hollow_stubs.append(
                    {"file": cf, "symbol": "<syntax_error>", "line": exc.lineno or 1, "reason": str(exc)}
                )
                continue
            fn_nodes = [
                n
                for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            function_counts[cf] = len(fn_nodes)
            if p.name != "__init__.py" and len(fn_nodes) == 0:
                failures.append("NO_EXECUTABLE_FUNCTIONS_IN_CODE_FILE")
            for fn in fn_nodes:
                # Skip private helper getters or dunder methods except __call__
                if fn.name.startswith("__") and fn.name != "__call__":
                    continue
                check_res = _analyze_python_function_substance(fn)
                if check_res["is_hollow"]:
                    hollow_stubs.append(
                        {
                            "file": cf,
                            "symbol": fn.name,
                            "line": fn.lineno,
                            "reason": check_res["reason"],
                        }
                    )
        elif p.suffix.lower() in {".js", ".mjs", ".cjs", ".ts", ".tsx"}:
            cleaned = strip_js_comments_and_strings(p.read_text(encoding="utf-8", errors="replace"))
            fn_matches = list(
                re.finditer(
                    r"(?:function\s+([A-Za-z_$][\w$]*)|(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>)\s*\{([^}]*)\}",
                    cleaned,
                    re.DOTALL,
                )
            )
            function_counts[cf] = len(fn_matches)
            for m in fn_matches:
                fname = m.group(1) or m.group(2) or "<anon>"
                fbody = (m.group(3) or "").strip()
                if not fbody or re.fullmatch(r"return\s*(?:\{\s*\}|null|undefined|true|false)?\s*;?", fbody):
                    line_no = cleaned[: m.start()].count("\n") + 1
                    hollow_stubs.append(
                        {
                            "file": cf,
                            "symbol": fname,
                            "line": line_no,
                            "reason": "js_empty_or_constant_return_stub",
                        }
                    )

    if hollow_stubs and "HOLLOW_OR_CONSTANT_STUB" not in failures:
        failures.append("HOLLOW_OR_CONSTANT_STUB")

    return {
        "passed": len(failures) == 0,
        "failures": failures,
        "code_files": code_files,
        "test_files": test_files,
        "auxiliary_scripts": auxiliary_scripts,
        "doc_or_data_files": doc_or_data_files,
        "missing_files": missing_files,
        "function_counts": function_counts,
        "hollow_stubs": hollow_stubs,
    }


def _extract_dict_or_list_func_refs(node: ast.AST, known_funcs: Set[str]) -> Set[str]:
    refs: Set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in known_funcs:
            refs.add(sub.id)
        elif isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
            q = f"{sub.value.id}.{sub.attr}"
            if q in known_funcs:
                refs.add(q)
    return refs


def _analyze_python_reachability(
    entry_path: Path,
    entry_symbols: List[str],
    code_files: List[str],
    search_roots: List[Path],
) -> Dict[str, Any]:
    """Build static AST Call-Graph + single-hop local alias live-registry consumption graph."""
    parsed_modules: Dict[str, ast.Module] = {}
    module_funcs: Dict[str, Set[str]] = {}
    # Map (mod_path, func_name) -> ast.AST
    func_nodes: Dict[Tuple[str, str], ast.AST] = {}
    # Map (mod_path, local_name) -> (target_mod_path, target_symbol_name)
    import_map: Dict[Tuple[str, str], Tuple[str, str]] = {}
    # Map (mod_path, mod_alias) -> target_mod_path
    mod_alias_map: Dict[Tuple[str, str], str] = {}
    # Map (mod_path, container_name) -> Set[(target_mod_path, target_func_name)]
    registry_containers: Dict[Tuple[str, str], Set[Tuple[str, str]]] = {}

    queue_files: List[Path] = [entry_path.resolve()]
    for cf in code_files:
        cp = Path(cf).resolve()
        if cp.suffix.lower() == ".py" and cp not in queue_files:
            queue_files.append(cp)

    visited_files: Set[str] = set()
    while queue_files:
        curr = queue_files.pop(0)
        curr_s = str(curr)
        if curr_s in visited_files or not curr.is_file() or curr.suffix.lower() != ".py":
            continue
        visited_files.add(curr_s)
        try:
            tree = ast.parse(curr.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        parsed_modules[curr_s] = tree
        funcs_in_mod: Set[str] = set()

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                funcs_in_mod.add(node.name)
                func_nodes[(curr_s, node.name)] = node
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    resolved = resolve_local_python_module(alias.name, search_roots)
                    if resolved:
                        res_s = str(resolved)
                        bound = alias.asname or alias.name.split(".")[-1]
                        mod_alias_map[(curr_s, bound)] = res_s
                        if res_s not in visited_files:
                            queue_files.append(resolved)
            elif isinstance(node, ast.ImportFrom):
                mod_name = node.module or ""
                resolved = resolve_local_python_module(mod_name, search_roots)
                if resolved:
                    res_s = str(resolved)
                    if res_s not in visited_files:
                        queue_files.append(resolved)
                    for alias in node.names:
                        sub_mod = resolve_local_python_module(
                            f"{mod_name}.{alias.name}" if mod_name else alias.name,
                            search_roots,
                        )
                        bound = alias.asname or alias.name
                        if sub_mod:
                            sub_s = str(sub_mod)
                            mod_alias_map[(curr_s, bound)] = sub_s
                            if sub_s not in visited_files:
                                queue_files.append(sub_mod)
                        else:
                            import_map[(curr_s, bound)] = (res_s, alias.name)
        module_funcs[curr_s] = funcs_in_mod

    # Second pass: identify module-level registry containers (dicts/lists/tuples/sets holding func refs)
    for mod_s, tree in parsed_modules.items():
        local_callables = set(module_funcs.get(mod_s, set()))
        imported_callables = {
            local_name
            for (m, local_name) in import_map
            if m == mod_s
        }
        mod_aliases = {
            alias_name
            for (m, alias_name) in mod_alias_map
            if m == mod_s
        }
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = (
                    node.targets
                    if isinstance(node, ast.Assign)
                    else ([node.target] if hasattr(node, "target") else [])
                )
                val = getattr(node, "value", None)
                if val is None or not isinstance(val, (ast.Dict, ast.List, ast.Tuple, ast.Set, ast.Call)):
                    continue
                for t in targets:
                    if not isinstance(t, ast.Name):
                        continue
                    cname = t.id
                    refs: Set[Tuple[str, str]] = set()
                    for sub in ast.walk(val):
                        if isinstance(sub, ast.Name):
                            if sub.id in local_callables:
                                refs.add((mod_s, sub.id))
                            elif sub.id in imported_callables:
                                refs.add(import_map[(mod_s, sub.id)])
                        elif isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
                            if sub.value.id in mod_aliases:
                                target_mod = mod_alias_map[(mod_s, sub.value.id)]
                                refs.add((target_mod, sub.attr))
                    if refs:
                        registry_containers[(mod_s, cname)] = refs

    # Worklist call-graph traversal from entry_path
    entry_s = str(entry_path.resolve())
    live_functions: Set[Tuple[str, str]] = set()
    live_consumed_registries: Set[Tuple[str, str]] = set()
    worklist: List[Tuple[str, str]] = []

    for sym in entry_symbols:
        if (entry_s, sym) in func_nodes:
            live_functions.add((entry_s, sym))
            worklist.append((entry_s, sym))

    def _resolve_symbol_in_mod(mod_s: str, name: str) -> Optional[Tuple[str, str]]:
        if (mod_s, name) in func_nodes:
            return (mod_s, name)
        if (mod_s, name) in import_map:
            return import_map[(mod_s, name)]
        return None

    def _resolve_registry_in_mod(mod_s: str, name: str) -> Optional[Tuple[str, str]]:
        if (mod_s, name) in registry_containers:
            return (mod_s, name)
        if (mod_s, name) in import_map:
            target = import_map[(mod_s, name)]
            if target in registry_containers:
                return target
        return None

    while worklist:
        curr_mod, curr_fn = worklist.pop(0)
        fnode = func_nodes.get((curr_mod, curr_fn))
        if fnode is None:
            continue

        # Track single-hop local aliases inside `fnode`:
        # e.g. `reg = SUBCOMMAND_HANDLERS` or `handler = reg[cmd]` or `handler = SUBCOMMAND_HANDLERS.get(cmd)`
        local_reg_aliases: Dict[str, Tuple[str, str]] = {}
        derived_callable_vars: Dict[str, Tuple[str, str]] = {}

        for sub in ast.walk(fnode):
            if isinstance(sub, ast.Assign) and len(sub.targets) == 1 and isinstance(sub.targets[0], ast.Name):
                lhs = sub.targets[0].id
                rhs = sub.value
                if isinstance(rhs, ast.Name):
                    reg_key = _resolve_registry_in_mod(curr_mod, rhs.id)
                    if reg_key:
                        local_reg_aliases[lhs] = reg_key
                elif isinstance(rhs, ast.Subscript) and isinstance(rhs.value, ast.Name):
                    base_id = rhs.value.id
                    reg_key = local_reg_aliases.get(base_id) or _resolve_registry_in_mod(curr_mod, base_id)
                    if reg_key:
                        derived_callable_vars[lhs] = reg_key
                elif (
                    isinstance(rhs, ast.Call)
                    and isinstance(rhs.func, ast.Attribute)
                    and rhs.func.attr in {"get", "pop", "values", "items"}
                    and isinstance(rhs.func.value, ast.Name)
                ):
                    base_id = rhs.func.value.id
                    reg_key = local_reg_aliases.get(base_id) or _resolve_registry_in_mod(curr_mod, base_id)
                    if reg_key:
                        derived_callable_vars[lhs] = reg_key
            elif isinstance(sub, ast.For) and isinstance(sub.target, (ast.Name, ast.Tuple)):
                iter_expr = sub.iter
                iter_base = None
                if isinstance(iter_expr, ast.Name):
                    iter_base = iter_expr.id
                elif (
                    isinstance(iter_expr, ast.Call)
                    and isinstance(iter_expr.func, ast.Attribute)
                    and iter_expr.func.attr in {"values", "items"}
                    and isinstance(iter_expr.func.value, ast.Name)
                ):
                    iter_base = iter_expr.func.value.id
                if iter_base:
                    reg_key = local_reg_aliases.get(iter_base) or _resolve_registry_in_mod(curr_mod, iter_base)
                    if reg_key:
                        if isinstance(sub.target, ast.Name):
                            derived_callable_vars[sub.target.id] = reg_key
                        elif isinstance(sub.target, ast.Tuple):
                            for elt in sub.target.elts:
                                if isinstance(elt, ast.Name):
                                    derived_callable_vars[elt.id] = reg_key

        # Scan all ast.Call nodes in `fnode`
        for sub in ast.walk(fnode):
            if not isinstance(sub, ast.Call):
                continue
            func_expr = sub.func
            # Case A: direct call `foo(...)` or `handler(...)` where `handler` comes from a registry
            if isinstance(func_expr, ast.Name):
                fname = func_expr.id
                if fname in derived_callable_vars:
                    reg_key = derived_callable_vars[fname]
                    live_consumed_registries.add(reg_key)
                    for target in registry_containers.get(reg_key, set()):
                        if target not in live_functions and target in func_nodes:
                            live_functions.add(target)
                            worklist.append(target)
                else:
                    target = _resolve_symbol_in_mod(curr_mod, fname)
                    if target and target not in live_functions and target in func_nodes:
                        live_functions.add(target)
                        worklist.append(target)

            # Case B: `mod_alias.foo(...)`
            elif isinstance(func_expr, ast.Attribute) and isinstance(func_expr.value, ast.Name):
                base_id = func_expr.value.id
                attr_id = func_expr.attr
                if (curr_mod, base_id) in mod_alias_map:
                    target_mod = mod_alias_map[(curr_mod, base_id)]
                    target = (target_mod, attr_id)
                    if target in func_nodes and target not in live_functions:
                        live_functions.add(target)
                        worklist.append(target)

            # Case C: inline registry subscript call `REG[k](...)` or `REG.get(k)(...)`
            elif isinstance(func_expr, ast.Subscript) and isinstance(func_expr.value, ast.Name):
                base_id = func_expr.value.id
                reg_key = local_reg_aliases.get(base_id) or _resolve_registry_in_mod(curr_mod, base_id)
                if reg_key:
                    live_consumed_registries.add(reg_key)
                    for target in registry_containers.get(reg_key, set()):
                        if target not in live_functions and target in func_nodes:
                            live_functions.add(target)
                            worklist.append(target)
            elif (
                isinstance(func_expr, ast.Call)
                and isinstance(func_expr.func, ast.Attribute)
                and func_expr.func.attr in {"get", "pop"}
                and isinstance(func_expr.func.value, ast.Name)
            ):
                base_id = func_expr.func.value.id
                reg_key = local_reg_aliases.get(base_id) or _resolve_registry_in_mod(curr_mod, base_id)
                if reg_key:
                    live_consumed_registries.add(reg_key)
                    for target in registry_containers.get(reg_key, set()):
                        if target not in live_functions and target in func_nodes:
                            live_functions.add(target)
                            worklist.append(target)

    dead_registries = [
        {"module": mod_s, "container": cname, "dead_targets": [list(x) for x in sorted(refs)]}
        for (mod_s, cname), refs in sorted(registry_containers.items())
        if (mod_s, cname) not in live_consumed_registries
        and not all(t in live_functions for t in refs)
    ]

    live_modules = {mod_s for (mod_s, _) in live_functions}
    return {
        "live_functions": [list(x) for x in sorted(live_functions)],
        "live_modules": sorted(live_modules),
        "live_consumed_registries": [list(x) for x in sorted(live_consumed_registries)],
        "dead_unconsumed_registries": dead_registries,
    }


def _analyze_js_reachability(
    entry_path: Path,
    live_containers: List[str],
    code_files: List[str],
) -> Dict[str, Any]:
    """Analyze JS/MJS reachability and detect dead unconsumed Map/Object registries (`r34–r36`)."""
    all_js_files = [entry_path.resolve()] + [
        Path(cf).resolve()
        for cf in code_files
        if Path(cf).suffix.lower() in {".js", ".mjs", ".cjs", ".ts", ".tsx"}
    ]
    cleaned_map: Dict[str, str] = {}
    for jp in all_js_files:
        if jp.is_file():
            cleaned_map[str(jp)] = strip_js_comments_and_strings(
                jp.read_text(encoding="utf-8", errors="replace")
            )

    entry_s = str(entry_path.resolve())
    entry_text = cleaned_map.get(entry_s, "")

    dead_registries: List[Dict[str, Any]] = []
    live_modules: Set[str] = {entry_s}

    # Find all Map / Object containers across `code_files`
    re_container = re.compile(
        r"(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:new\s+Map\s*\(([^;]+)\)|\{([^;]+)\})",
        re.DOTALL,
    )
    for mod_s, text in cleaned_map.items():
        for m in re_container.finditer(text):
            cname = m.group(1)
            body = m.group(2) or m.group(3) or ""
            # Check if `cname` is a live container or is `.get()` / indexed / iterated and called in entry_text
            is_consumed = (
                cname in live_containers
                or bool(re.search(rf"\b{re.escape(cname)}\s*(?:\.\s*get\s*\([^)]*\)\s*\(|\[[^\]]+\]\s*\()", text))
                or bool(re.search(rf"\b{re.escape(cname)}\s*(?:\.\s*get\s*\([^)]*\)\s*\(|\[[^\]]+\]\s*\()", entry_text))
            )
            if not is_consumed:
                dead_registries.append(
                    {
                        "module": mod_s,
                        "container": cname,
                        "snippet": body.strip()[:120],
                    }
                )

    # Check which code_files have symbols actually called in entry_text (beyond dead containers)
    for cf in code_files:
        cp_s = str(Path(cf).resolve())
        if cp_s == entry_s:
            continue
        mod_text = cleaned_map.get(cp_s, "")
        exported_fns = re.findall(
            r"export\s+(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", mod_text
        )
        called_in_entry = any(
            re.search(rf"\b{re.escape(fn)}\s*\(", entry_text) for fn in exported_fns
        )
        if called_in_entry:
            live_modules.add(cp_s)

    return {
        "live_modules": sorted(live_modules),
        "dead_unconsumed_registries": dead_registries,
    }


def verify_entrypoint_reachability(
    implementation_files: Iterable[str],
    project_root: Path,
    registry_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Verify every production code file in `implementation_files` is live-wired from the
    registered normal entrypoint (`guardrails/entrypoint_registry.json`), and block dead
    bypass containers (`boundSpecialistServices`).
    """
    project_root = project_root.resolve()
    if registry_override is not None:
        reg = registry_override
    else:
        reg_path = project_root / "guardrails" / "entrypoint_registry.json"
        if not reg_path.is_file():
            return {
                "passed": False,
                "failures": ["ENTRYPOINT_REGISTRY_MISSING"],
                "message": f"Missing immutable entrypoint registry at {reg_path}",
            }
        reg = json.loads(reg_path.read_text(encoding="utf-8"))

    code_files: List[str] = []
    for raw in implementation_files:
        norm = normalize_target_path(raw, cwd=project_root)
        if not norm:
            continue
        p = Path(norm)
        if p.is_file() and p.suffix.lower() in CODE_EXTENSIONS and not is_test_or_fixture_path(p):
            if p.name != "__init__.py":
                code_files.append(str(p.resolve()))

    entrypoints = reg.get("primary_entrypoints") or []
    if not entrypoints:
        return {
            "passed": False,
            "failures": ["NO_PRIMARY_ENTRYPOINTS_REGISTERED"],
        }

    all_live_modules: Set[str] = set()
    all_dead_registries: List[Dict[str, Any]] = []
    per_entrypoint_reports: List[Dict[str, Any]] = []
    failures: List[str] = []

    for ep in entrypoints:
        ep_path = Path(normalize_target_path(ep["path"], cwd=project_root))
        if not ep_path.is_file():
            failures.append("REGISTERED_ENTRYPOINT_FILE_MISSING")
            continue
        # Self-referential check: if `forbid_self_referential_entrypoint` is True and a non-CLI module
        # claims itself as the only entrypoint without calling anything else
        lang = str(ep.get("language", "python")).lower()
        if lang == "python":
            rep = _analyze_python_reachability(
                entry_path=ep_path,
                entry_symbols=ep.get("entry_symbols", ["main"]),
                code_files=code_files,
                search_roots=[project_root, ep_path.parent],
            )
        else:
            rep = _analyze_js_reachability(
                entry_path=ep_path,
                live_containers=ep.get("live_dispatch_containers", ["TOOLS"]),
                code_files=code_files,
            )
        all_live_modules.update(rep.get("live_modules", []))
        all_dead_registries.extend(rep.get("dead_unconsumed_registries", []))
        per_entrypoint_reports.append({"entrypoint": str(ep_path), **rep})

    unreachable_code_files = [
        cf for cf in code_files if cf not in all_live_modules
    ]
    if unreachable_code_files or all_dead_registries:
        failures.append("DEAD_BYPASS_OR_UNCONSUMED_REGISTRY")

    return {
        "passed": len(failures) == 0,
        "failures": failures,
        "code_files_checked": code_files,
        "live_reached_modules": sorted(all_live_modules),
        "unreachable_code_files": unreachable_code_files,
        "dead_unconsumed_registries": all_dead_registries,
        "entrypoint_reports": per_entrypoint_reports,
    }


def compute_attestation_digest(payload: Dict[str, Any]) -> str:
    canonical = {
        "runner": payload.get("runner"),
        "run_nonce": payload.get("run_nonce"),
        "argv": payload.get("argv"),
        "cwd": payload.get("cwd"),
        "git_head": payload.get("git_head"),
        "started_at_utc": payload.get("started_at_utc"),
        "finished_at_utc": payload.get("finished_at_utc"),
        "exit_code": payload.get("exit_code"),
        "stdout_sha256": payload.get("stdout_sha256"),
        "impl_sha256_map": payload.get("impl_sha256_map"),
    }
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def verify_empirical_evidence(
    empirical_evidence_files: Iterable[str],
    implementation_files: Iterable[str],
    project_root: Path,
    conversation_id: Optional[str] = None,
    brain_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Verify attested normal-entry empirical evidence files, including SHA256 map freshness
    and cross-checking `ATTESTED_EVIDENCE_NONCE=<nonce> DIGEST=<digest>` in `transcript_full.jsonl`.
    """
    project_root = project_root.resolve()
    ev_files = [
        normalize_target_path(e, cwd=project_root)
        for e in empirical_evidence_files
        if normalize_target_path(e, cwd=project_root)
    ]
    impl_files = [
        normalize_target_path(f, cwd=project_root)
        for f in implementation_files
        if normalize_target_path(f, cwd=project_root)
    ]

    failures: List[str] = []
    evidence_reports: List[Dict[str, Any]] = []

    if not ev_files:
        return {
            "passed": False,
            "failures": ["EMPIRICAL_EVIDENCE_FILES_EMPTY"],
            "evidence_reports": [],
        }

    # Load transcript steps if conversation_id is provided for cross-checking run_nonce
    transcript_steps: List[Dict[str, Any]] = []
    if conversation_id:
        loc = locate_session_logs(conversation_id=conversation_id, brain_dir=brain_dir)
        if loc.get("ok"):
            full_path = Path(loc["transcript_full_path"])
            transcript_steps = [
                json.loads(line)
                for line in full_path.read_text(encoding="utf-8", errors="replace").splitlines()
                if line.strip()
            ]

    current_impl_shas: Dict[str, str] = {}
    for imp in impl_files:
        ip = Path(imp)
        if ip.is_file():
            current_impl_shas[str(ip.resolve())] = sha256_file(ip)

    covered_impl_paths: Set[str] = set()
    for ev_s in ev_files:
        ep = Path(ev_s)
        item_failures: List[str] = []
        if not ep.is_file() or ep.stat().st_size == 0:
            item_failures.append("EVIDENCE_FILE_MISSING_OR_EMPTY")
            failures.extend(item_failures)
            evidence_reports.append({"path": ev_s, "passed": False, "failures": item_failures})
            continue

        try:
            data = json.loads(ep.read_text(encoding="utf-8"))
        except Exception:
            item_failures.append("EVIDENCE_NOT_VALID_JSON")
            failures.extend(item_failures)
            evidence_reports.append({"path": ev_s, "passed": False, "failures": item_failures})
            continue

        if data.get("fixture_only") or data.get("mock_only"):
            item_failures.append("FIXTURE_OR_MOCK_ONLY_EVIDENCE_REJECTED")

        if data.get("runner") != "harness_guardrails.run_and_attest":
            item_failures.append("UNATTESTED_OR_TAMPERED_EMPIRICAL_EVIDENCE")

        nonce = str(data.get("run_nonce", "") or "")
        claimed_digest = str(data.get("attestation_digest", "") or "")
        expected_digest = compute_attestation_digest(data)
        if not nonce or not claimed_digest or claimed_digest != expected_digest:
            if "UNATTESTED_OR_TAMPERED_EMPIRICAL_EVIDENCE" not in item_failures:
                item_failures.append("UNATTESTED_OR_TAMPERED_EMPIRICAL_EVIDENCE")

        if data.get("exit_code") != 0:
            item_failures.append("EVIDENCE_COMMAND_NONZERO_EXIT")

        argv = data.get("argv") or []
        if not isinstance(argv, list) or len(argv) < 2:
            item_failures.append("EVIDENCE_ARGV_MISSING_NORMAL_SUBCOMMAND")
        else:
            argv_joined = " ".join(str(x) for x in argv)
            if "harness_guardrails.py" not in argv_joined and "unittest" not in argv_joined:
                item_failures.append("EVIDENCE_ARGV_NOT_REGISTERED_ENTRYPOINT")
            if any(x in {"--help", "-h", "--version"} for x in argv):
                item_failures.append("EVIDENCE_ARGV_TRIVIAL_HELP_OR_VERSION")

        recorded_shas = data.get("impl_sha256_map") or {}
        if not isinstance(recorded_shas, dict) or not recorded_shas:
            item_failures.append("EVIDENCE_IMPL_SHA_MAP_MISSING")
        else:
            for rec_path, rec_sha in recorded_shas.items():
                rp = Path(rec_path)
                if not rp.is_file() or sha256_file(rp) != rec_sha:
                    item_failures.append("STALE_EMPIRICAL_EVIDENCE_AFTER_CODE_EDIT")
                    break
                covered_impl_paths.add(str(rp.resolve()))

        # Cross-check against session `transcript_full.jsonl` `run_command` steps
        if transcript_steps and nonce and claimed_digest:
            anchor_token = f"ATTESTED_EVIDENCE_NONCE={nonce} DIGEST={claimed_digest}"
            found_in_transcript = False
            saw_run_and_attest_cmd = False
            for row in transcript_steps:
                for tc in row.get("tool_calls") or []:
                    if isinstance(tc, dict) and tc.get("name") in {"run_command", "run_terminal_command"}:
                        targs = tc.get("args") or tc.get("arguments") or {}
                        cmd_line = str(targs.get("CommandLine") or targs.get("command") or "")
                        if "harness_guardrails.py" in cmd_line and "run-and-attest" in cmd_line:
                            saw_run_and_attest_cmd = True
                c = str(row.get("content", "") or "")
                if saw_run_and_attest_cmd and anchor_token in c:
                    found_in_transcript = True
                    break
                # Also check spilled `output.txt` if this step spilled output
                if saw_run_and_attest_cmd:
                    for m_spill in RE_SPILLED_OUTPUT.finditer(c[:600]):
                        sp_path = Path(normalize_target_path(m_spill.group(1)))
                        if sp_path.is_file():
                            sp_text = sp_path.read_text(encoding="utf-8", errors="replace")
                            if anchor_token in sp_text:
                                found_in_transcript = True
                                break
                if found_in_transcript:
                    break
            if not found_in_transcript:
                item_failures.append("EVIDENCE_NOT_FOUND_IN_SESSION_TRANSCRIPT_COMMAND_LOG")

        for f in item_failures:
            if f not in failures:
                failures.append(f)
        evidence_reports.append(
            {
                "path": ev_s,
                "passed": len(item_failures) == 0,
                "run_nonce": nonce,
                "attestation_digest": claimed_digest,
                "failures": item_failures,
            }
        )

    uncovered_impl = [p for p in current_impl_shas if p not in covered_impl_paths]
    if uncovered_impl and "STALE_EMPIRICAL_EVIDENCE_AFTER_CODE_EDIT" not in failures:
        failures.append("STALE_EMPIRICAL_EVIDENCE_AFTER_CODE_EDIT")

    return {
        "passed": len(failures) == 0,
        "failures": failures,
        "uncovered_implementation_files": uncovered_impl,
        "evidence_reports": evidence_reports,
    }


def _scan_text_for_induction(text: str, source_label: str) -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = []
    for idx, raw_line in enumerate(text.splitlines()):
        if "[FORBIDDEN_EXAMPLE_QUOTE]" in raw_line:
            continue
        cleaned_line = ZERO_WIDTH_RE.sub("", raw_line)
        for pat in INDUCTION_PATTERNS:
            m = pat.search(cleaned_line)
            if m:
                hits.append(
                    {
                        "source": source_label,
                        "line": idx + 1,
                        "matched": m.group(0),
                        "excerpt": cleaned_line.strip()[:160],
                    }
                )
                break
    return hits


def verify_declaration_hygiene(
    summary: str = "",
    rebuttal_text: str = "",
    input_envelope_path: Optional[Path] = None,
    prompt_file_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Scan declaration summary, rebuttal text, on-disk `input.json`, and dispatch prompt for
    reviewer-induction phrases (`请直接返回 acceptable`, etc.).
    """
    violations: List[Dict[str, Any]] = []
    if summary:
        violations.extend(_scan_text_for_induction(summary, "declaration.summary"))
    if rebuttal_text:
        violations.extend(_scan_text_for_induction(rebuttal_text, "declaration.rebuttal_text"))
    if input_envelope_path and input_envelope_path.is_file():
        violations.extend(
            _scan_text_for_induction(
                input_envelope_path.read_text(encoding="utf-8", errors="replace"),
                str(input_envelope_path),
            )
        )
    if prompt_file_path and prompt_file_path.is_file():
        violations.extend(
            _scan_text_for_induction(
                prompt_file_path.read_text(encoding="utf-8", errors="replace"),
                str(prompt_file_path),
            )
        )
    return {
        "passed": len(violations) == 0,
        "failures": ["REVIEWER_INDUCTION_PHRASE_DETECTED"] if violations else [],
        "violations": violations,
    }


def verify_full_substance(
    implementation_files: List[str],
    empirical_evidence_files: List[str],
    project_root: Path,
    summary: str = "",
    rebuttal_text: str = "",
    input_envelope_path: Optional[Path] = None,
    prompt_file_path: Optional[Path] = None,
    conversation_id: Optional[str] = None,
    brain_dir: Optional[str] = None,
    registry_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Unified Guardrail 3 check combining code classification, AST call-graph reachability,
    attested runner evidence verification, and anti-induction hygiene.
    """
    code_audit = verify_implementation_substance(
        implementation_files=implementation_files,
        project_root=project_root,
        require_code=True,
    )
    reach_audit = verify_entrypoint_reachability(
        implementation_files=implementation_files,
        project_root=project_root,
        registry_override=registry_override,
    )
    evidence_audit = verify_empirical_evidence(
        empirical_evidence_files=empirical_evidence_files,
        implementation_files=code_audit.get("code_files", implementation_files),
        project_root=project_root,
        conversation_id=conversation_id,
        brain_dir=brain_dir,
    )
    hygiene_audit = verify_declaration_hygiene(
        summary=summary,
        rebuttal_text=rebuttal_text,
        input_envelope_path=input_envelope_path,
        prompt_file_path=prompt_file_path,
    )

    passed = (
        code_audit["passed"]
        and reach_audit["passed"]
        and evidence_audit["passed"]
        and hygiene_audit["passed"]
    )
    all_failures: List[str] = []
    for sub in (code_audit, reach_audit, evidence_audit, hygiene_audit):
        for f in sub.get("failures", []):
            if f not in all_failures:
                all_failures.append(f)

    return {
        "passed": passed,
        "failures": all_failures,
        "code_classification": code_audit,
        "reachability_audit": reach_audit,
        "evidence_freshness_audit": evidence_audit,
        "hygiene_audit": hygiene_audit,
    }
