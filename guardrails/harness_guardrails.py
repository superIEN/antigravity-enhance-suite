#!/usr/bin/env python3
"""Unified Normal Entrypoint CLI: `guardrails/harness_guardrails.py` (v1.1).

Provides the single authoritative normal CLI entrypoint for all four AGY anti-degradation
Harness Guardrails and the `ArchUnit 0.3` macOS Strong-Contract Bridge:
- `audit-reads`        (Guardrail 1: Read-Coverage & Truncation Auditor)
- `verify-citations`   (Guardrail 2: Compaction & AST Citation Verifier)
- `verify-substance`   (Guardrail 3: Normal-Entry Substance & Call-Graph Verifier)
- `run-and-attest`     (Guardrail 3: Attested Normal-Entry Runner with Transcript Nonce Anchor)
- `verify-macos-host`  (Guardrail 4: macOS Mach-O / `codesign` & `sop-master` SHA256 Verifier)
- `preflight-declare`  (Guardrails 1+2+3+4 Joint Declaration Gate & Readback Verifier)
- `archunit-op`        (Guardrail 4: Strong-Contract `archunit_record.py` Bridge)
- `verify-completion`  (Guardrails 1+2+3+4 Completion Gate splitting `technical_gate_passed` vs `triad_complete`)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

THIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT_DEFAULT = THIS_DIR.parent
if str(PROJECT_ROOT_DEFAULT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_DEFAULT))

from guardrails.archunit_macos_bridge import (
    DEFAULT_AGY_BIN,
    DEFAULT_SOP_ROOT,
    execute_archunit_op,
    preflight_declare,
    verify_completion,
    verify_macos_environment,
)
from guardrails.citation_verifier import (
    extract_citations_from_markdown,
    verify_citations_and_compaction,
)
from guardrails.read_auditor import (
    audit_session_reads,
    normalize_target_path,
    sha256_file,
)
from guardrails.substance_verifier import (
    compute_attestation_digest,
    verify_full_substance,
)


def _write_or_print_report(report: Dict[str, Any], out_path: Optional[str]) -> None:
    formatted = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if out_path:
        op = Path(out_path).expanduser().resolve()
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(formatted, encoding="utf-8")
    sys.stdout.write(formatted)


def cmd_audit_reads(args: argparse.Namespace) -> Dict[str, Any]:
    proj = Path(args.project_root).expanduser().resolve()
    task_order = Path(args.task_order).expanduser().resolve() if args.task_order else None
    decl_file = Path(args.declaration_file).expanduser().resolve() if args.declaration_file else None
    return audit_session_reads(
        conversation_id=args.conversation_id,
        brain_dir=args.brain_dir,
        task_order_path=task_order,
        declaration_path=decl_file,
        extra_required_files=args.require_file or [],
        project_root=proj,
    )


def cmd_verify_citations(args: argparse.Namespace) -> Dict[str, Any]:
    proj = Path(args.project_root).expanduser().resolve()
    citations: List[Dict[str, Any]] = []
    if args.citations_file:
        cf = Path(args.citations_file).expanduser().resolve()
        raw = json.loads(cf.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            citations.extend(raw.get("citations", []))
        elif isinstance(raw, list):
            citations.extend(raw)
    if args.scan_markdown:
        for md in args.scan_markdown:
            mp = Path(normalize_target_path(md, cwd=proj))
            if mp.is_file():
                citations.extend(extract_citations_from_markdown(mp))
    return verify_citations_and_compaction(
        citations=citations,
        project_root=proj,
        conversation_id=args.conversation_id,
        brain_dir=args.brain_dir,
        implementation_files=args.implementation_files or [],
    )


def cmd_verify_substance(args: argparse.Namespace) -> Dict[str, Any]:
    proj = Path(args.project_root).expanduser().resolve()
    env_path = (
        Path(normalize_target_path(args.input_envelope, cwd=proj))
        if args.input_envelope
        else None
    )
    prompt_path = (
        Path(normalize_target_path(args.prompt_file, cwd=proj))
        if args.prompt_file
        else None
    )
    return verify_full_substance(
        implementation_files=args.implementation_files or [],
        empirical_evidence_files=args.empirical_evidence_files or [],
        project_root=proj,
        summary=args.summary or "",
        input_envelope_path=env_path,
        prompt_file_path=prompt_path,
        conversation_id=args.conversation_id,
        brain_dir=args.brain_dir,
    )


def cmd_run_and_attest(args: argparse.Namespace) -> Dict[str, Any]:
    proj = Path(args.project_root).expanduser().resolve()
    cmd_argv = list(args.command or [])
    if cmd_argv and cmd_argv[0] == "--":
        cmd_argv = cmd_argv[1:]

    if len(cmd_argv) < 2:
        return {
            "passed": False,
            "failures": ["ATTEST_COMMAND_ARGV_TOO_SHORT"],
            "message": "Provide a registered normal-entry command after --",
        }
    joined = " ".join(cmd_argv)
    if "harness_guardrails.py" not in joined and "unittest" not in joined:
        return {
            "passed": False,
            "failures": ["ATTEST_COMMAND_NOT_REGISTERED_NORMAL_ENTRY"],
            "argv": cmd_argv,
        }
    if any(flag in {"--help", "-h", "--version"} for flag in cmd_argv):
        return {
            "passed": False,
            "failures": ["ATTEST_COMMAND_TRIVIAL_HELP_FORBIDDEN"],
            "argv": cmd_argv,
        }

    impl_map: Dict[str, str] = {}
    for raw_f in args.implementation_files or []:
        norm_f = normalize_target_path(raw_f, cwd=proj)
        if norm_f and Path(norm_f).is_file():
            impl_map[str(Path(norm_f).resolve())] = sha256_file(Path(norm_f))

    git_proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(proj),
        capture_output=True,
        text=True,
        timeout=5,
    )
    git_head = (git_proc.stdout or "").strip() if git_proc.returncode == 0 else "no_git_head"

    run_nonce = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    proc = subprocess.run(
        cmd_argv,
        cwd=str(proj),
        capture_output=True,
        text=True,
        timeout=120,
    )
    finished_at = datetime.now(timezone.utc).isoformat()
    stdout_str = proc.stdout or ""
    stderr_str = proc.stderr or ""
    stdout_sha = hashlib.sha256(stdout_str.encode("utf-8")).hexdigest()

    evidence_payload: Dict[str, Any] = {
        "schema_version": "harness_guardrails.attested_evidence.v1.1",
        "runner": "harness_guardrails.run_and_attest",
        "normal_entry": True,
        "fixture_only": False,
        "mock_only": False,
        "run_nonce": run_nonce,
        "argv": cmd_argv,
        "cwd": str(proj),
        "git_head": git_head,
        "started_at_utc": started_at,
        "finished_at_utc": finished_at,
        "exit_code": proc.returncode,
        "stdout_sha256": stdout_sha,
        "impl_sha256_map": impl_map,
        "stdout_preview": stdout_str[:4000],
        "stderr_preview": stderr_str[:2000],
    }
    digest = compute_attestation_digest(evidence_payload)
    evidence_payload["attestation_digest"] = digest
    evidence_payload["passed"] = proc.returncode == 0

    out_file = Path(normalize_target_path(args.out, cwd=proj))
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(
        json.dumps(evidence_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # Print immutable anchor line to stdout so the AGY host logs it in `transcript_full.jsonl`!
    sys.stdout.write(f"ATTESTED_EVIDENCE_NONCE={run_nonce} DIGEST={digest}\n")
    return evidence_payload


def cmd_verify_macos_host(args: argparse.Namespace) -> Dict[str, Any]:
    agy_bin = Path(args.agy_bin) if args.agy_bin else DEFAULT_AGY_BIN
    sop_root = Path(args.sop_root) if args.sop_root else DEFAULT_SOP_ROOT
    return verify_macos_environment(agy_bin=agy_bin, sop_root=sop_root)


def cmd_preflight_declare(args: argparse.Namespace) -> Dict[str, Any]:
    proj = Path(args.project_root).expanduser().resolve()
    decl_file = Path(normalize_target_path(args.declaration_file, cwd=proj))
    decl_data = json.loads(decl_file.read_text(encoding="utf-8"))
    return preflight_declare(
        project_root=proj,
        task_id=args.task_id,
        declaration_data=decl_data,
        conversation_id=args.conversation_id,
        brain_dir=args.brain_dir,
    )


def cmd_archunit_op(args: argparse.Namespace) -> Dict[str, Any]:
    proj = Path(args.project_root).expanduser().resolve()
    payload_file = Path(normalize_target_path(args.payload_file, cwd=proj))
    payload_data = json.loads(payload_file.read_text(encoding="utf-8"))
    return execute_archunit_op(
        project_root=proj,
        task_id=args.task_id,
        action=args.action,
        payload_data=payload_data,
        conversation_id=args.conversation_id,
        brain_dir=args.brain_dir,
    )


def cmd_verify_completion(args: argparse.Namespace) -> Dict[str, Any]:
    proj = Path(args.project_root).expanduser().resolve()
    return verify_completion(
        project_root=proj,
        task_id=args.task_id,
        declaration_id=args.declaration_id,
        stage_id=args.stage_id,
        conversation_id=args.conversation_id,
        brain_dir=args.brain_dir,
    )


SUBCOMMAND_HANDLERS: Dict[str, Callable[[argparse.Namespace], Dict[str, Any]]] = {
    "audit-reads": cmd_audit_reads,
    "verify-citations": cmd_verify_citations,
    "verify-substance": cmd_verify_substance,
    "run-and-attest": cmd_run_and_attest,
    "verify-macos-host": cmd_verify_macos_host,
    "preflight-declare": cmd_preflight_declare,
    "archunit-op": cmd_archunit_op,
    "verify-completion": cmd_verify_completion,
}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AGY Anti-Degradation Harness Guardrails & ArchUnit 0.3 macOS Strong-Contract CLI"
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # 1. audit-reads
    p_reads = sub.add_parser("audit-reads", help="Guardrail 1: Audit session transcript_full.jsonl")
    p_reads.add_argument("--conversation-id", required=True)
    p_reads.add_argument("--brain-dir", default=None)
    p_reads.add_argument("--project-root", default=str(PROJECT_ROOT_DEFAULT))
    p_reads.add_argument("--task-order", default=None)
    p_reads.add_argument("--declaration-file", default=None)
    p_reads.add_argument("--require-file", nargs="*", default=[])
    p_reads.add_argument("--out", default=None)

    # 2. verify-citations
    p_cit = sub.add_parser("verify-citations", help="Guardrail 2: Verify AST citations & post-compaction rereads")
    p_cit.add_argument("--citations-file", default=None)
    p_cit.add_argument("--scan-markdown", nargs="*", default=[])
    p_cit.add_argument("--implementation-files", nargs="*", default=[])
    p_cit.add_argument("--conversation-id", default=None)
    p_cit.add_argument("--brain-dir", default=None)
    p_cit.add_argument("--project-root", default=str(PROJECT_ROOT_DEFAULT))
    p_cit.add_argument("--out", default=None)

    # 3. verify-substance
    p_sub = sub.add_parser("verify-substance", help="Guardrail 3: Verify call-graph reachability & attested evidence")
    p_sub.add_argument("--implementation-files", nargs="+", required=True)
    p_sub.add_argument("--empirical-evidence-files", nargs="+", required=True)
    p_sub.add_argument("--project-root", default=str(PROJECT_ROOT_DEFAULT))
    p_sub.add_argument("--summary", default="")
    p_sub.add_argument("--input-envelope", default=None)
    p_sub.add_argument("--prompt-file", default=None)
    p_sub.add_argument("--conversation-id", default=None)
    p_sub.add_argument("--brain-dir", default=None)
    p_sub.add_argument("--out", default=None)

    # 4. run-and-attest
    p_att = sub.add_parser("run-and-attest", help="Guardrail 3: Execute normal entrypoint and generate signed evidence")
    p_att.add_argument("--out", required=True)
    p_att.add_argument("--project-root", default=str(PROJECT_ROOT_DEFAULT))
    p_att.add_argument("--implementation-files", nargs="*", default=[])
    p_att.add_argument("command", nargs=argparse.REMAINDER)

    # 5. verify-macos-host
    p_mac = sub.add_parser("verify-macos-host", help="Guardrail 4: Verify macOS codesign & sop-master SHA256")
    p_mac.add_argument("--agy-bin", default=None)
    p_mac.add_argument("--sop-root", default=None)
    p_mac.add_argument("--out", default=None)

    # 6. preflight-declare
    p_pre = sub.add_parser("preflight-declare", help="Guardrails 1+2+3+4 Joint Declaration Gate")
    p_pre.add_argument("--project-root", default=str(PROJECT_ROOT_DEFAULT))
    p_pre.add_argument("--task-id", default="session3-harness-guardrails")
    p_pre.add_argument("--declaration-file", required=True)
    p_pre.add_argument("--conversation-id", required=True)
    p_pre.add_argument("--brain-dir", default=None)
    p_pre.add_argument("--out", default=None)

    # 7. archunit-op
    p_op = sub.add_parser("archunit-op", help="Guardrail 4: Execute strong-contract ArchUnit 0.3 operation")
    p_op.add_argument("--project-root", default=str(PROJECT_ROOT_DEFAULT))
    p_op.add_argument("--task-id", default="session3-harness-guardrails")
    p_op.add_argument("--action", required=True)
    p_op.add_argument("--payload-file", required=True)
    p_op.add_argument("--conversation-id", default=None)
    p_op.add_argument("--brain-dir", default=None)
    p_op.add_argument("--out", default=None)

    # 8. verify-completion
    p_comp = sub.add_parser("verify-completion", help="Guardrails 1+2+3+4 Completion Gate")
    p_comp.add_argument("--project-root", default=str(PROJECT_ROOT_DEFAULT))
    p_comp.add_argument("--task-id", default="session3-harness-guardrails")
    p_comp.add_argument("--declaration-id", default=None)
    p_comp.add_argument("--stage-id", default=None)
    p_comp.add_argument("--conversation-id", default=None)
    p_comp.add_argument("--brain-dir", default=None)
    p_comp.add_argument("--out", default=None)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    handler = SUBCOMMAND_HANDLERS[args.subcommand]
    report = handler(args)
    out_arg = getattr(args, "out", None)
    if args.subcommand != "run-and-attest":
        _write_or_print_report(report, out_arg)
    else:
        compact = {k: v for k, v in report.items() if k not in {"stdout_preview", "stderr_preview"}}
        sys.stdout.write(json.dumps(compact, ensure_ascii=False, indent=2) + "\n")
        if report.get("run_nonce") and report.get("attestation_digest"):
            sys.stdout.write(
                f"ATTESTED_EVIDENCE_NONCE={report['run_nonce']} DIGEST={report['attestation_digest']}\n"
            )
    return 0 if report.get("passed", False) else 2


if __name__ == "__main__":
    raise SystemExit(main())
