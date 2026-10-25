#!/usr/bin/env python3
"""Antigravity Enhance Suite — Unified CLI Installer, Inspector, Rollback & Live Hot-Injector.

Usage:
    python agy_suite.py inspect [--agy-dir PATH]
    python agy_suite.py install [--agy-dir PATH] [--runtime-dir PATH] [--hot-inject]
    python agy_suite.py rollback [--agy-dir PATH] [--runtime-dir PATH]
    python agy_suite.py hot-inject [--runtime-dir PATH]
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import struct
import sys
from pathlib import Path

from src import build_asar, build_ls, hot_inject, patch_sites


def default_agy_dir() -> Path:
    local_app = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
    return Path(local_app) / "Programs" / "antigravity"


def default_runtime_dir() -> Path:
    return Path(os.environ.get("AGY_ENHANCE_DIR", str(Path.home() / ".gemini" / "agy-enhance")))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_asar_version(asar_path: Path) -> str:
    raw = asar_path.read_bytes()
    _, hdr_sz, _, json_sz = struct.unpack_from("<IIII", raw, 0)
    hdr = json.loads(raw[16 : 16 + json_sz].decode("utf-8"))
    data_start = 8 + hdr_sz
    pinfo = hdr["files"]["package.json"]
    off, sz = int(pinfo["offset"]), int(pinfo["size"])
    pkg = json.loads(raw[data_start + off : data_start + off + sz].decode("utf-8"))
    return pkg.get("version", "unknown")


def cmd_inspect(args):
    agy_dir = Path(args.agy_dir)
    asar_path = agy_dir / "resources" / "app.asar"
    ls_path = agy_dir / "resources" / "bin" / "language_server.exe"
    if not asar_path.exists() or not ls_path.exists():
        raise FileNotFoundError(f"Antigravity binaries not found under {agy_dir}")

    ver = read_asar_version(asar_path)
    raw_ls = ls_path.read_bytes()
    funcs = patch_sites.parse_gopclntab(raw_ls)
    sites = (
        patch_sites.locate_h2ping_sites(raw_ls, funcs)
        + patch_sites.locate_1m_context_sites(raw_ls, funcs)
        + patch_sites.locate_media_compression_sites(raw_ls, funcs)
        + patch_sites.locate_utf8_and_uri_sites(raw_ls, funcs)
        + patch_sites.locate_stream_retry_sites(raw_ls, funcs)
        + patch_sites.locate_bg_task_timeout_sites(raw_ls, funcs)
    )
    report = {
        "antigravity_version": ver,
        "app_asar": {"path": str(asar_path), "size": asar_path.stat().st_size, "sha256": sha256_file(asar_path)},
        "language_server": {"path": str(ls_path), "size": len(raw_ls), "sha256": hashlib.sha256(raw_ls).hexdigest()},
        "gopclntab_functions": len(funcs),
        "locatable_base_sites": len(sites),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_install(args):
    agy_dir = Path(args.agy_dir)
    runtime_dir = Path(args.runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)

    prod_asar = agy_dir / "resources" / "app.asar"
    prod_ls = agy_dir / "resources" / "bin" / "language_server.exe"
    if not prod_asar.exists() or not prod_ls.exists():
        raise FileNotFoundError(f"Antigravity installation not found at {agy_dir}")

    ver = read_asar_version(prod_asar)
    orig_asar_bak = runtime_dir / f"app.asar.orig_{ver}.bak"
    orig_ls_bak = runtime_dir / f"language_server.exe.orig_{ver}.bak"

    if not orig_asar_bak.exists():
        shutil.copy2(prod_asar, orig_asar_bak)
    if not orig_ls_bak.exists():
        shutil.copy2(prod_ls, orig_ls_bak)

    repo_loader = Path(__file__).resolve().parent / "src" / "agy-enhance-loader.js"
    target_loader = runtime_dir / "agy-enhance-loader.js"
    shutil.copy2(repo_loader, target_loader)

    cand_ls = runtime_dir / "language_server.exe.v3.candidate"
    cand_asar = runtime_dir / "app.asar.v3.candidate"
    staging_js = runtime_dir / "staged_dist_js"

    ls_receipt = build_ls.build_and_verify_ls(orig_ls_bak, cand_ls)
    asar_receipt = build_asar.build_candidate_asar(orig_asar_bak, target_loader, cand_asar, staging_js)

    tmp_prod_asar = prod_asar.parent / f"app.asar.tmp.{os.getpid()}"
    shutil.copy2(cand_asar, tmp_prod_asar)
    os.replace(tmp_prod_asar, prod_asar)

    ls_deployed_live = False
    tmp_prod_ls = prod_ls.parent / f"language_server.exe.tmp.{os.getpid()}"
    shutil.copy2(cand_ls, tmp_prod_ls)
    try:
        os.replace(tmp_prod_ls, prod_ls)
        ls_deployed_live = True
    except PermissionError:
        old_ls = prod_ls.parent / f"language_server.exe.old.{os.getpid()}"
        if old_ls.exists():
            old_ls.unlink()
        os.rename(prod_ls, old_ls)
        os.replace(tmp_prod_ls, prod_ls)
        ls_deployed_live = True

    tx_receipt = {
        "timestamp": datetime.datetime.now().isoformat(),
        "version": ver,
        "runtime_dir": str(runtime_dir),
        "ls_deployed_live": ls_deployed_live,
        "ls_receipt": ls_receipt,
        "asar_receipt": asar_receipt,
    }
    if args.hot_inject:
        tx_receipt["hot_inject"] = hot_inject.hot_inject_running_windows(target_loader)

    (runtime_dir / "last_install_receipt.json").write_text(
        json.dumps(tx_receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(tx_receipt, ensure_ascii=False, indent=2))


def cmd_rollback(args):
    agy_dir = Path(args.agy_dir)
    runtime_dir = Path(args.runtime_dir)
    prod_asar = agy_dir / "resources" / "app.asar"
    prod_ls = agy_dir / "resources" / "bin" / "language_server.exe"
    ver = read_asar_version(prod_asar)

    orig_asar_bak = runtime_dir / f"app.asar.orig_{ver}.bak"
    orig_ls_bak = runtime_dir / f"language_server.exe.orig_{ver}.bak"
    if not orig_asar_bak.exists() or not orig_ls_bak.exists():
        raise FileNotFoundError(f"Baseline backups for v{ver} not found in {runtime_dir}")

    shutil.copy2(orig_asar_bak, prod_asar)
    try:
        shutil.copy2(orig_ls_bak, prod_ls)
    except PermissionError:
        old_ls = prod_ls.parent / f"language_server.exe.old.{os.getpid()}"
        os.rename(prod_ls, old_ls)
        shutil.copy2(orig_ls_bak, prod_ls)

    print(json.dumps({
        "status": "rolled_back",
        "version": ver,
        "app_asar_sha256": sha256_file(prod_asar),
        "language_server_sha256": sha256_file(prod_ls),
    }, indent=2))


def cmd_hot_inject(args):
    runtime_dir = Path(args.runtime_dir)
    target_loader = runtime_dir / "agy-enhance-loader.js"
    if not target_loader.exists():
        target_loader = Path(__file__).resolve().parent / "src" / "agy-enhance-loader.js"
    res = hot_inject.hot_inject_running_windows(target_loader)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Antigravity Enhance Suite CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ins = sub.add_parser("inspect", help="Inspect current Antigravity installation & patch sites")
    p_ins.add_argument("--agy-dir", default=str(default_agy_dir()))

    p_inst = sub.add_parser("install", help="Build, verify, and atomically install all enhancements")
    p_inst.add_argument("--agy-dir", default=str(default_agy_dir()))
    p_inst.add_argument("--runtime-dir", default=str(default_runtime_dir()))
    p_inst.add_argument("--hot-inject", action="store_true", help="Also hot-inject into running AGY windows via CDP")

    p_rb = sub.add_parser("rollback", help="Restore clean official baseline backups")
    p_rb.add_argument("--agy-dir", default=str(default_agy_dir()))
    p_rb.add_argument("--runtime-dir", default=str(default_runtime_dir()))

    p_hi = sub.add_parser("hot-inject", help="Hot-inject loader into running AGY windows via CDP")
    p_hi.add_argument("--runtime-dir", default=str(default_runtime_dir()))

    args = parser.parse_args()
    if args.cmd == "inspect":
        cmd_inspect(args)
    elif args.cmd == "install":
        cmd_install(args)
    elif args.cmd == "rollback":
        cmd_rollback(args)
    elif args.cmd == "hot-inject":
        cmd_hot_inject(args)


if __name__ == "__main__":
    main()
