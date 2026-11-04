#!/usr/bin/env python3
"""Antigravity Enhance Suite v3.6.0 — Unified CLI Installer, Inspector, Rollback, Live Hot-Injector, DB Upgrader & Media Guard (Windows & macOS).

Usage:
    python3 agy_suite.py inspect [--agy-dir PATH]
    python3 agy_suite.py install [--agy-dir PATH] [--runtime-dir PATH] [--hot-inject] [--restart-app] [--strict-asar]
    python3 agy_suite.py rollback [--agy-dir PATH] [--runtime-dir PATH]
    python3 agy_suite.py hot-inject [--runtime-dir PATH]
    python3 agy_suite.py upgrade-db [--all] [--upgrade-to 983040]
    python3 agy_suite.py compress-media [--check]
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from src import build_asar, build_ls, hot_inject, patch_sites


def default_agy_dir() -> Path:
    if sys.platform == "darwin":
        user_app = Path.home() / "Applications" / "Antigravity.app"
        if user_app.exists():
            return user_app
        return Path("/Applications/Antigravity.app")
    local_app = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
    return Path(local_app) / "Programs" / "antigravity"


def default_runtime_dir() -> Path:
    return Path(os.environ.get("AGY_ENHANCE_DIR", str(Path.home() / ".gemini" / "agy-enhance")))


def resolve_ls_binaries(agy_dir: Path) -> list[Path]:
    bin_dir = agy_dir / "resources" / "bin"
    bins: list[Path] = []
    for name in ("language_server_windows_x64.exe", "language_server.exe"):
        p = bin_dir / name
        if p.exists():
            bins.append(p)
    return bins


def resolve_agy_paths(agy_dir: Path) -> dict:
    p = agy_dir.resolve()
    if sys.platform == "darwin" or p.name.endswith(".app") or (p / "Contents" / "Resources" / "app.asar").exists():
        if p.name == "Resources" and p.parent.name == "Contents":
            app_bundle = p.parent.parent
        elif p.name == "Contents":
            app_bundle = p.parent
        else:
            app_bundle = p
        ls_path = app_bundle / "Contents" / "Resources" / "bin" / "language_server"
        return {
            "is_mac_bundle": True,
            "app_bundle": app_bundle,
            "asar_path": app_bundle / "Contents" / "Resources" / "app.asar",
            "ls_path": ls_path,
            "ls_bins": [ls_path] if ls_path.exists() else [],
            "info_plist_path": app_bundle / "Contents" / "Info.plist",
            "main_exec_path": app_bundle / "Contents" / "MacOS" / "Antigravity",
            "code_resources_path": app_bundle / "Contents" / "_CodeSignature" / "CodeResources",
        }
    ls_bins = resolve_ls_binaries(p)
    return {
        "is_mac_bundle": False,
        "app_bundle": None,
        "asar_path": p / "resources" / "app.asar",
        "ls_path": ls_bins[0] if ls_bins else (p / "resources" / "bin" / "language_server.exe"),
        "ls_bins": ls_bins,
    }


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_replace_from_file(src_path: Path, dst_path: Path):
    """Copy src_path to a new temporary inode in dst_path.parent and atomically rename over dst_path."""
    tmp_path = dst_path.parent / f".{dst_path.name}.tmp.{os.getpid()}"
    if tmp_path.exists():
        tmp_path.unlink()
    shutil.copy2(src_path, tmp_path)
    with tmp_path.open("rb") as f:
        os.fsync(f.fileno())
    os.replace(tmp_path, dst_path)


def read_asar_version(asar_path: Path) -> str:
    raw = asar_path.read_bytes()
    _, hdr_sz, _, json_sz = struct.unpack_from("<IIII", raw, 0)
    hdr = json.loads(raw[16 : 16 + json_sz].decode("utf-8"))
    data_start = 8 + hdr_sz
    pinfo = hdr["files"]["package.json"]
    off, sz = int(pinfo["offset"]), int(pinfo["size"])
    pkg = json.loads(raw[data_start + off : data_start + off + sz].decode("utf-8"))
    return pkg.get("version", "unknown")


def find_clean_mac_factory_bundle(app_bundle: Path, ver: str) -> Path:
    """If an earlier patch preserved the Google-signed factory original .app bundle, prefer it for clean baselines."""
    candidates = sorted(app_bundle.parent.glob(".Antigravity-original-*.app"))
    for cand in candidates:
        cand_asar = cand / "Contents" / "Resources" / "app.asar"
        cand_ls = cand / "Contents" / "Resources" / "bin" / "language_server"
        if cand_asar.is_file() and cand_ls.is_file():
            try:
                if read_asar_version(cand_asar) == ver:
                    return cand
            except Exception:
                pass
    return app_bundle


def stage_and_sign_mac_bundle(
    source_bundle: Path,
    cand_asar: Path,
    cand_ls: Path,
    asar_header_sha256: str,
    orig_main_bak: Path,
    orig_info_plist_bak: Path,
    runtime_dir: Path,
) -> Path:
    """Clone Antigravity.app into staging dir, apply cand_asar + cand_ls + Info.plist hash, and sign offline."""
    staging_root = runtime_dir / "staged_bundle"
    staged_app = staging_root / "Antigravity.app"
    if staged_app.exists():
        shutil.rmtree(staged_app)
    staging_root.mkdir(parents=True, exist_ok=True)

    subprocess.run(["/bin/cp", "-cRp", str(source_bundle), str(staged_app)], check=True)

    atomic_replace_from_file(orig_main_bak, staged_app / "Contents" / "MacOS" / "Antigravity")
    plist_data = plistlib.loads(orig_info_plist_bak.read_bytes())
    if "ElectronAsarIntegrity" in plist_data and "Resources/app.asar" in plist_data["ElectronAsarIntegrity"]:
        plist_data["ElectronAsarIntegrity"]["Resources/app.asar"]["hash"] = asar_header_sha256
    staged_info_plist = staged_app / "Contents" / "Info.plist"
    staged_info_plist.write_bytes(plistlib.dumps(plist_data))

    atomic_replace_from_file(cand_asar, staged_app / "Contents" / "Resources" / "app.asar")
    atomic_replace_from_file(cand_ls, staged_app / "Contents" / "Resources" / "bin" / "language_server")
    (staged_app / "Contents" / "Resources" / "bin" / "language_server").chmod(0o755)

    with tempfile.NamedTemporaryFile("w", suffix=".entitlements.plist", delete=False) as tf:
        tf.write(build_ls.ENTITLEMENTS_XML)
        ls_ent_path = Path(tf.name)
    try:
        subprocess.run(
            [
                "/usr/bin/codesign",
                "--force",
                "--sign",
                "-",
                "--identifier",
                "language_server",
                "--options",
                "runtime",
                "--entitlements",
                str(ls_ent_path),
                "--timestamp=none",
                str(staged_app / "Contents" / "Resources" / "bin" / "language_server"),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        ls_ent_path.unlink(missing_ok=True)

    orig_ent_raw = subprocess.run(
        ["/usr/bin/codesign", "-d", "--entitlements", ":-", str(orig_main_bak)],
        capture_output=True,
        check=True,
    ).stdout
    ent = plistlib.loads(orig_ent_raw) if orig_ent_raw else {}
    ent["com.apple.security.cs.disable-library-validation"] = True
    ent_path = staging_root / "root-entitlements.plist"
    ent_path.write_bytes(plistlib.dumps(ent))

    constraint = {"team-identifier": "EQHXZ8M8AV"}
    constraint_path = staging_root / "google-library-constraint.plist"
    constraint_path.write_bytes(plistlib.dumps(constraint))

    subprocess.run(
        [
            "/usr/bin/codesign",
            "--force",
            "--sign",
            "-",
            "--preserve-metadata=identifier,flags",
            "--entitlements",
            str(ent_path),
            "--library-constraint",
            str(constraint_path),
            "--timestamp=none",
            str(staged_app),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(staged_app)],
        check=True,
        capture_output=True,
        text=True,
    )
    return staged_app


DEFERRED_SWAP_SCRIPT = r"""import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

task_file = Path(sys.argv[1])
my_task_id = sys.argv[2]

def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()

try:
    task = json.loads(task_file.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)

if task.get("task_id") != my_task_id:
    raise SystemExit(0)

cand_asar = Path(task["cand_asar"])
prod_asar = Path(task["prod_asar"])
receipt_path = Path(task["receipt_path"])
expected_orig_sha = task["expected_orig_sha256"]
expected_cand_sha = task["candidate_sha256"]
restart_exe = Path(task["restart_exe"]) if task.get("restart_exe") else None

if not cand_asar.exists() or sha256_file(cand_asar) != expected_cand_sha:
    raise SystemExit(1)

swap_tmp = prod_asar.parent / f".app.asar.swap.{os.getpid()}.tmp"
shutil.copy2(cand_asar, swap_tmp)
with swap_tmp.open("rb") as f:
    os.fsync(f.fileno())

deadline = time.time() + 86400
swapped = False
try:
    while time.time() < deadline:
        try:
            cur_task = json.loads(task_file.read_text(encoding="utf-8"))
            if cur_task.get("task_id") != my_task_id:
                break
        except Exception:
            break

        if prod_asar.exists():
            try:
                cur_sha = sha256_file(prod_asar)
                if cur_sha == expected_cand_sha:
                    swapped = True
                    break
                if cur_sha != expected_orig_sha:
                    # Target app.asar changed to a different version; abort safely
                    break
            except OSError:
                pass

        try:
            os.replace(swap_tmp, prod_asar)
            if sha256_file(prod_asar) == expected_cand_sha:
                swapped = True
                break
        except PermissionError:
            time.sleep(0.12)
        except Exception:
            time.sleep(0.25)
finally:
    if swap_tmp.exists():
        try:
            swap_tmp.unlink()
        except OSError:
            pass

if swapped:
    for old_f in list(prod_asar.parent.glob("*.old.*")) + list((prod_asar.parent / "bin").glob("*.old.*")):
        try:
            old_f.unlink()
        except OSError:
            pass
    if receipt_path.exists():
        try:
            rec = json.loads(receipt_path.read_text(encoding="utf-8"))
            rec["asar_deployed_live"] = True
            rec["asar_deferred_swapped_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            rec.pop("asar_warning", None)
            tmp_rec = receipt_path.with_suffix(f".json.tmp.{os.getpid()}")
            tmp_rec.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp_rec, receipt_path)
        except Exception:
            pass
    if restart_exe and restart_exe.exists():
        subprocess.Popen(
            [str(restart_exe)],
            creationflags=0x00000008 | 0x00000200,
            close_fds=True,
        )
"""


def cleanup_stale_old_binaries(prod_asar: Path):
    for old_f in list(prod_asar.parent.glob("*.old.*")) + list((prod_asar.parent / "bin").glob("*.old.*")):
        try:
            old_f.unlink()
        except OSError:
            pass


def spawn_windows_deferred_asar_swapper(
    cand_asar: Path,
    prod_asar: Path,
    receipt_path: Path,
    expected_orig_sha256: str,
    candidate_sha256: str,
    restart_app: bool = False,
) -> int | None:
    runtime_dir = receipt_path.parent
    swapper_py = runtime_dir / "deferred_asar_swapper.py"
    swapper_py.write_text(DEFERRED_SWAP_SCRIPT, encoding="utf-8")

    task_id = f"{int(datetime.datetime.now().timestamp() * 1000)}-{os.getpid()}"
    agy_exe = prod_asar.parent.parent / "Antigravity.exe"
    task_file = runtime_dir / "deferred_asar_swap_task.json"
    task_payload = {
        "task_id": task_id,
        "cand_asar": str(cand_asar),
        "prod_asar": str(prod_asar),
        "receipt_path": str(receipt_path),
        "expected_orig_sha256": expected_orig_sha256,
        "candidate_sha256": candidate_sha256,
        "restart_exe": str(agy_exe) if (restart_app and agy_exe.exists()) else "",
    }
    task_file.write_text(json.dumps(task_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    py_exe = sys.executable
    pyw_exe = Path(py_exe).with_name("pythonw.exe")
    runner = str(pyw_exe if pyw_exe.exists() else py_exe)
    CREATE_NO_WINDOW = 0x08000000

    pid = None
    # Prefer WMI Win32_Process.Create so the swapper is parented to WmiPrvSE.exe and escapes Job Object termination
    try:
        cmd_str = f'"{runner}" "{swapper_py}" "{task_file}" "{task_id}"'
        ps_cmd = (
            f"$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
            f"-Arguments @{{CommandLine='{cmd_str}'}}; "
            f"Write-Output $r.ProcessId"
        )
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_cmd],
            creationflags=CREATE_NO_WINDOW,
            text=True,
            errors="ignore",
            timeout=8,
        ).strip()
        for line in reversed(out.splitlines()):
            if line.strip().isdigit():
                pid = int(line.strip())
                break
    except Exception:
        pid = None

    if not pid:
        DETACHED = 0x00000008
        NEW_GROUP = 0x00000200
        BREAKAWAY = 0x01000000
        for flags in (BREAKAWAY | DETACHED | NEW_GROUP | CREATE_NO_WINDOW, DETACHED | NEW_GROUP | CREATE_NO_WINDOW):
            try:
                proc = subprocess.Popen(
                    [runner, str(swapper_py), str(task_file), task_id],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    creationflags=flags,
                )
                pid = proc.pid
                break
            except OSError:
                continue

    if pid and restart_app and agy_exe.exists():
        subprocess.Popen(
            ["taskkill", "/IM", "Antigravity.exe"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
    return pid


def cmd_inspect(args):
    paths = resolve_agy_paths(Path(args.agy_dir))
    asar_path = paths["asar_path"]
    ls_path = paths["ls_path"]
    if not asar_path.exists() or not ls_path.exists():
        raise FileNotFoundError(f"Antigravity binaries not found under {args.agy_dir}")

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
        + patch_sites.locate_oauth_token_sites(raw_ls, funcs)
    )
    report = {
        "platform": "darwin-arm64" if patch_sites.is_macho_arm64(raw_ls) else "win32-x64",
        "antigravity_version": ver,
        "app_asar": {"path": str(asar_path), "size": asar_path.stat().st_size, "sha256": sha256_file(asar_path)},
        "language_server": {"path": str(ls_path), "size": len(raw_ls), "sha256": hashlib.sha256(raw_ls).hexdigest()},
        "gopclntab_functions": len(funcs),
        "locatable_sites": len(sites),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_install(args):
    paths = resolve_agy_paths(Path(args.agy_dir))
    runtime_dir = Path(args.runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)

    prod_asar = paths["asar_path"]
    prod_ls = paths["ls_path"]
    ls_bins = paths["ls_bins"]
    if not prod_asar.exists() or not ls_bins:
        raise FileNotFoundError(f"Antigravity installation not found at {args.agy_dir}")

    ver = read_asar_version(prod_asar)
    ls_bak_name = f"language_server.orig_{ver}.bak" if paths["is_mac_bundle"] else f"language_server.exe.orig_{ver}.bak"
    orig_asar_bak = runtime_dir / f"app.asar.orig_{ver}.bak"
    orig_ls_bak = runtime_dir / ls_bak_name

    if paths["is_mac_bundle"]:
        factory_bundle = find_clean_mac_factory_bundle(paths["app_bundle"], ver)
        orig_info_plist_bak = runtime_dir / f"Info.plist.orig_{ver}.bak"
        orig_main_bak = runtime_dir / f"Antigravity.orig_{ver}.bak"
        orig_code_res_bak = runtime_dir / f"CodeResources.orig_{ver}.bak"
        if not orig_asar_bak.exists():
            shutil.copy2(factory_bundle / "Contents" / "Resources" / "app.asar", orig_asar_bak)
        if not orig_ls_bak.exists():
            shutil.copy2(factory_bundle / "Contents" / "Resources" / "bin" / "language_server", orig_ls_bak)
        if not orig_info_plist_bak.exists():
            shutil.copy2(factory_bundle / "Contents" / "Info.plist", orig_info_plist_bak)
        if not orig_main_bak.exists():
            shutil.copy2(factory_bundle / "Contents" / "MacOS" / "Antigravity", orig_main_bak)
        if not orig_code_res_bak.exists():
            shutil.copy2(factory_bundle / "Contents" / "_CodeSignature" / "CodeResources", orig_code_res_bak)
    else:
        if not orig_asar_bak.exists():
            shutil.copy2(prod_asar, orig_asar_bak)
        if not orig_ls_bak.exists():
            shutil.copy2(prod_ls, orig_ls_bak)

    repo_loader = Path(__file__).resolve().parent / "src" / "agy-enhance-loader.js"
    target_loader = runtime_dir / "agy-enhance-loader.js"
    if repo_loader.resolve() != target_loader.resolve():
        shutil.copy2(repo_loader, target_loader)

    cand_ls_name = "language_server.v3.candidate" if paths["is_mac_bundle"] else "language_server.exe.v3.candidate"
    cand_ls = runtime_dir / cand_ls_name
    cand_asar = runtime_dir / "app.asar.v3.candidate"
    staging_js = runtime_dir / "staged_dist_js"

    ls_receipt = build_ls.build_and_verify_ls(orig_ls_bak, cand_ls)
    asar_receipt = build_asar.build_candidate_asar(orig_asar_bak, target_loader, cand_asar, staging_js)

    asar_deployed_live = False
    asar_warning = None
    ls_deployed_live = False

    if paths["is_mac_bundle"]:
        staged_app = stage_and_sign_mac_bundle(
            paths["app_bundle"],
            cand_asar,
            cand_ls,
            asar_receipt["header_sha256"],
            orig_main_bak,
            orig_info_plist_bak,
            runtime_dir,
        )
        for rel in [
            "Contents/Resources/app.asar",
            "Contents/Info.plist",
            "Contents/Resources/bin/language_server",
            "Contents/MacOS/Antigravity",
            "Contents/_CodeSignature/CodeResources",
        ]:
            atomic_replace_from_file(staged_app / rel, paths["app_bundle"] / rel)
        subprocess.run(
            ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(paths["app_bundle"])],
            check=True,
            capture_output=True,
            text=True,
        )
        shutil.rmtree(staged_app.parent, ignore_errors=True)
        shutil.rmtree(staging_js, ignore_errors=True)
        asar_deployed_live = True
        ls_deployed_live = True
    else:
        tmp_prod_asar = prod_asar.parent / f"app.asar.tmp.{os.getpid()}"
        shutil.copy2(cand_asar, tmp_prod_asar)
        try:
            os.replace(tmp_prod_asar, prod_asar)
            asar_deployed_live = True
        except PermissionError:
            old_asar = prod_asar.parent / f"app.asar.old.{os.getpid()}"
            try:
                if old_asar.exists():
                    old_asar.unlink()
                os.rename(prod_asar, old_asar)
                os.replace(tmp_prod_asar, prod_asar)
                asar_deployed_live = True
            except PermissionError as exc:
                if old_asar.exists() and not prod_asar.exists():
                    try:
                        os.rename(old_asar, prod_asar)
                    except OSError:
                        pass
                if tmp_prod_asar.exists():
                    tmp_prod_asar.unlink()
                asar_warning = (
                    f"app.asar is locked by a running Antigravity process ({exc}); "
                    f"verified candidate staged at {cand_asar}. Close Antigravity and re-run install, "
                    f"or pass --hot-inject for immediate live UI injection."
                )
                print(f"[WARN] {asar_warning}", file=sys.stderr)
                if getattr(args, "strict_asar", False):
                    raise RuntimeError(asar_warning) from exc

        cleanup_stale_old_binaries(prod_asar)
        for target_ls in ls_bins:
            tmp_prod_ls = target_ls.parent / f"{target_ls.name}.tmp.{os.getpid()}"
            shutil.copy2(cand_ls, tmp_prod_ls)
            try:
                os.replace(tmp_prod_ls, target_ls)
                ls_deployed_live = True
            except PermissionError:
                old_ls = target_ls.parent / f"{target_ls.name}.old.{os.getpid()}"
                if old_ls.exists():
                    try:
                        old_ls.unlink()
                    except OSError:
                        pass
                os.rename(target_ls, old_ls)
                os.replace(tmp_prod_ls, target_ls)
                ls_deployed_live = True

    deferred_swapper_pid = None
    if not paths["is_mac_bundle"] and not asar_deployed_live:
        deferred_swapper_pid = spawn_windows_deferred_asar_swapper(
            cand_asar=cand_asar,
            prod_asar=prod_asar,
            receipt_path=runtime_dir / "last_install_receipt.json",
            expected_orig_sha256=asar_receipt["orig_sha256"],
            candidate_sha256=asar_receipt["candidate_sha256"],
            restart_app=getattr(args, "restart_app", False),
        )

    tx_receipt = {
        "timestamp": datetime.datetime.now().isoformat(),
        "platform": "darwin-arm64" if paths["is_mac_bundle"] else "win32-x64",
        "version": ver,
        "runtime_dir": str(runtime_dir),
        "asar_deployed_live": asar_deployed_live,
        "ls_deployed_live": ls_deployed_live,
        "loader_sha256": asar_receipt["loader_sha256"],
        "loader_bytes": asar_receipt["loader_bytes"],
        "utils_js_sha256": asar_receipt.get("utils_js_sha256", ""),
        "ls_receipt": ls_receipt,
        "asar_receipt": asar_receipt,
        "desktop_enhance_version": "3.6.0-macos-merged",
    }
    if deferred_swapper_pid:
        tx_receipt["deferred_asar_swapper_pid"] = deferred_swapper_pid
    if asar_warning:
        tx_receipt["asar_warning"] = asar_warning
    if args.hot_inject:
        tx_receipt["hot_inject"] = hot_inject.hot_inject_running_windows(target_loader)

    receipt_file = runtime_dir / "last_install_receipt.json"
    tmp_receipt_file = runtime_dir / f"last_install_receipt.json.tmp.{os.getpid()}"
    tmp_receipt_file.write_text(json.dumps(tx_receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_receipt_file, receipt_file)
    print(json.dumps(tx_receipt, ensure_ascii=False, indent=2))


def cmd_rollback(args):
    paths = resolve_agy_paths(Path(args.agy_dir))
    runtime_dir = Path(args.runtime_dir)
    prod_asar = paths["asar_path"]
    prod_ls = paths["ls_path"]
    ls_bins = paths["ls_bins"]
    ver = read_asar_version(prod_asar)

    ls_bak_name = f"language_server.orig_{ver}.bak" if paths["is_mac_bundle"] else f"language_server.exe.orig_{ver}.bak"
    orig_asar_bak = runtime_dir / f"app.asar.orig_{ver}.bak"
    orig_ls_bak = runtime_dir / ls_bak_name
    if not orig_asar_bak.exists() or not orig_ls_bak.exists():
        raise FileNotFoundError(f"Baseline backups for v{ver} not found in {runtime_dir}")

    if paths["is_mac_bundle"]:
        orig_info_plist_bak = runtime_dir / f"Info.plist.orig_{ver}.bak"
        orig_main_bak = runtime_dir / f"Antigravity.orig_{ver}.bak"
        orig_code_res_bak = runtime_dir / f"CodeResources.orig_{ver}.bak"
        for src_bak, dst_file in [
            (orig_asar_bak, prod_asar),
            (orig_ls_bak, prod_ls),
            (orig_info_plist_bak, paths["info_plist_path"]),
            (orig_main_bak, paths["main_exec_path"]),
            (orig_code_res_bak, paths["code_resources_path"]),
        ]:
            if src_bak.exists():
                atomic_replace_from_file(src_bak, dst_file)
        subprocess.run(
            ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(paths["app_bundle"])],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        shutil.copy2(orig_asar_bak, prod_asar)
        for target_ls in ls_bins:
            try:
                shutil.copy2(orig_ls_bak, target_ls)
            except PermissionError:
                old_ls = target_ls.parent / f"{target_ls.name}.old.{os.getpid()}"
                os.rename(target_ls, old_ls)
                shutil.copy2(orig_ls_bak, target_ls)

    print(json.dumps({
        "status": "rolled_back",
        "version": ver,
        "app_asar_sha256": sha256_file(prod_asar),
        "language_server_sha256": sha256_file(prod_ls),
    }, indent=2))


def cmd_hot_inject(args):
    runtime_dir = Path(args.runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    repo_loader = Path(__file__).resolve().parent / "src" / "agy-enhance-loader.js"
    target_loader = runtime_dir / "agy-enhance-loader.js"
    if repo_loader.exists() and repo_loader.resolve() != target_loader.resolve():
        shutil.copy2(repo_loader, target_loader)
    if not target_loader.exists():
        target_loader = repo_loader
    receipt_file = runtime_dir / "last_install_receipt.json"
    if receipt_file.exists() and target_loader.exists():
        try:
            raw_loader = target_loader.read_bytes()
            l_sha = hashlib.sha256(raw_loader).hexdigest()
            l_len = len(raw_loader)
            rec = json.loads(receipt_file.read_text(encoding="utf-8"))
            rec["loader_sha256"] = l_sha
            rec["loader_bytes"] = l_len
            if isinstance(rec.get("asar_receipt"), dict):
                rec["asar_receipt"]["loader_sha256"] = l_sha
                rec["asar_receipt"]["loader_bytes"] = l_len
            receipt_file.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    res = hot_inject.hot_inject_running_windows(target_loader)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Antigravity Enhance Suite CLI (v3.6.0)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ins = sub.add_parser("inspect", help="Inspect current Antigravity installation & patch sites")
    p_ins.add_argument("--agy-dir", default=str(default_agy_dir()))

    p_inst = sub.add_parser("install", help="Build, verify, and atomically install all enhancements")
    p_inst.add_argument("--agy-dir", default=str(default_agy_dir()))
    p_inst.add_argument("--runtime-dir", default=str(default_runtime_dir()))
    p_inst.add_argument("--hot-inject", action="store_true", help="Also hot-inject into running AGY windows via CDP")
    p_inst.add_argument("--restart-app", action="store_true", help="Automatically close, swap app.asar, and relaunch Antigravity on Windows")
    p_inst.add_argument("--strict-asar", action="store_true", help="Abort immediately if app.asar is locked by a running process")

    p_rb = sub.add_parser("rollback", help="Restore clean official baseline backups")
    p_rb.add_argument("--agy-dir", default=str(default_agy_dir()))
    p_rb.add_argument("--runtime-dir", default=str(default_runtime_dir()))

    p_hi = sub.add_parser("hot-inject", help="Hot-inject loader into running AGY windows via CDP")
    p_hi.add_argument("--runtime-dir", default=str(default_runtime_dir()))

    p_db = sub.add_parser("upgrade-db", help="Structurally inspect or upgrade conversation .db context window limits")
    p_db.add_argument("dbs", nargs="*", default=[])
    p_db.add_argument("--all", action="store_true", help="Scan all *.db files under ~/.gemini/antigravity/conversations/")
    p_db.add_argument("--upgrade-to", type=int, default=None, help="Target 3-byte varint token limit (recommended: 983040)")

    p_cm = sub.add_parser("compress-media", help="Inspect or compress oversized session media under brain/")
    p_cm.add_argument("cascade_id", nargs="?", default=None)
    p_cm.add_argument("--check", action="store_true", help="Only inspect session media without modifying files")
    p_cm.add_argument("--threshold-mb", type=float, default=10.0)
    p_cm.add_argument("--single-image-mb", type=float, default=2.0)
    p_cm.add_argument("--max-dim", type=int, default=1280)

    args = parser.parse_args()
    if args.cmd == "inspect":
        cmd_inspect(args)
    elif args.cmd == "install":
        cmd_install(args)
    elif args.cmd == "rollback":
        cmd_rollback(args)
    elif args.cmd == "hot-inject":
        cmd_hot_inject(args)
    elif args.cmd == "upgrade-db":
        cmd_argv = [sys.executable, str(Path(__file__).resolve().parent / "src" / "upgrade_db_context_limit.py")]
        if args.all:
            cmd_argv.append("--all")
        if args.upgrade_to is not None:
            cmd_argv.extend(["--upgrade-to", str(args.upgrade_to)])
        cmd_argv.extend(args.dbs)
        raise SystemExit(subprocess.run(cmd_argv).returncode)
    elif args.cmd == "compress-media":
        cmd_argv = [sys.executable, str(Path(__file__).resolve().parent / "src" / "compress_tempmedia.py")]
        if args.check:
            cmd_argv.append("--check")
        cmd_argv.extend(["--threshold-mb", str(args.threshold_mb), "--single-image-mb", str(args.single_image_mb), "--max-dim", str(args.max_dim)])
        if args.cascade_id:
            cmd_argv.append(args.cascade_id)
        raise SystemExit(subprocess.run(cmd_argv).returncode)


if __name__ == "__main__":
    main()
