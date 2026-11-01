#!/usr/bin/env python3
"""
Antigravity (AGY) 多图会话 30MB 请求体超限 (FAILED_PRECONDITION 400) 自动诊断与免重启热修复工具 (macOS & Windows 双兼容版)

功能：
1. 自动扫描本机语言服务日志（macOS: ~/Library/Logs/Antigravity/language_server.log[.prev]；Windows: %APPDATA%\\Antigravity\\logs\\language_server.log[.prev]）中最近的 `exceeds 30.000MB limit` 报错；
2. 扫描 ~/.gemini/antigravity/brain/*/.tempmediaStorage 以及 brain/<cascade_id>/ 下的大图（默认会话媒体总和 >10MB Raw / >13.3MB Base64，或单张大图 >=2MB，或指定会话 ID）；
3. 幂等增量备份：对每张未备份的新图自动备份原图至 `.tempmediaStorage_bak_orig`（或 `.brain_media_bak_orig`），已压缩过的旧图自动跳过，绝不重复有损压缩；
4. 严格保持原始 MIME 格式（PNG 保持 24-bit RGB PNG、JPEG 保持 JPEG、WebP 保持 WebP，防 Claude / Gemini Vision 报 MIME 不匹配），缩放到长边 <=1280px 并做调色板/质量优化，将请求体积压缩 80%~90%；支持 Pillow (PIL) 与 macOS 原生 `/usr/bin/sips` 双引擎兜底；
5. 无需重启 Antigravity，执行后直接在报错窗口点击 Retry 或继续对话即可。

用法：
  python3 compress_tempmedia.py                  # 自动检测报错会话 + 瘦身所有超阈值会话与大图
  python3 compress_tempmedia.py --check          # 仅巡检各会话 .tempmediaStorage 与 brain/ 大图体积及日志报错，不修改文件
  python3 compress_tempmedia.py <cascade_id>     # 指定会话 ID 强制增量瘦身
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from contextlib import contextmanager

try:
    import fcntl  # type: ignore
except ImportError:
    fcntl = None  # type: ignore

try:
    from PIL import Image, ImageOps  # type: ignore

    HAS_PIL = True
except ImportError:
    Image = None  # type: ignore
    ImageOps = None  # type: ignore
    HAS_PIL = False
    # If running under macOS system python3 without Pillow, transparently re-exec into local Python with Pillow if present
    _FALLBACK_PY = os.path.expanduser(
        "~/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
    )
    if (
        os.environ.get("AGY_TEMP_MEDIA_NO_REEXEC") != "1"
        and os.path.isfile(_FALLBACK_PY)
        and os.access(_FALLBACK_PY, os.X_OK)
        and os.path.realpath(sys.executable) != os.path.realpath(_FALLBACK_PY)
    ):
        os.environ["AGY_TEMP_MEDIA_NO_REEXEC"] = "1"
        os.execv(_FALLBACK_PY, [_FALLBACK_PY] + sys.argv)

HOME_DIR = os.path.expanduser("~")
ANTIGRAVITY_DATA_ROOT = os.environ.get(
    "AGY_DATA_DIR", os.path.join(HOME_DIR, ".gemini", "antigravity")
)
BRAIN_ROOT = os.path.join(ANTIGRAVITY_DATA_ROOT, "brain")


def get_default_log_dirs() -> list[str]:
    """Return candidate Antigravity log directories across macOS, Windows, and Linux."""
    dirs: list[str] = []
    if sys.platform == "darwin":
        dirs.append(os.path.join(HOME_DIR, "Library", "Logs", "Antigravity"))
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA", os.path.join(HOME_DIR, "AppData", "Roaming"))
        dirs.append(os.path.join(appdata, "Antigravity", "logs"))
    else:
        dirs.append(os.path.join(HOME_DIR, ".config", "Antigravity", "logs"))
        dirs.append(os.path.join(HOME_DIR, "Library", "Logs", "Antigravity"))

    win_appdata = os.environ.get("APPDATA")
    if win_appdata:
        cand = os.path.join(win_appdata, "Antigravity", "logs")
        if cand not in dirs:
            dirs.append(cand)
    return dirs


LOG_DIRS = get_default_log_dirs()
LOG_DIR = LOG_DIRS[0]
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
SAFE_B64_BYTES = 18_000_000  # 18MB Base64 safe ceiling (12MB headroom below 30MB limit)


@contextmanager
def _directory_flock(target_dir: str):
    """Acquire an exclusive per-directory file lock on POSIX to prevent concurrent writer races."""
    os.makedirs(target_dir, exist_ok=True)
    lock_path = os.path.join(target_dir, ".agy_compress.lock")
    lock_fd = None
    try:
        lock_fd = open(lock_path, "a+", encoding="utf-8")
        if fcntl is not None:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if lock_fd is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                lock_fd.close()
            except Exception:
                pass


def detect_magic_mime_format(file_path: str) -> str | None:
    """Detect PNG / JPEG / WEBP / GIF container format directly from file header bytes."""
    try:
        with open(file_path, "rb") as f:
            head = f.read(16)
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            return "PNG"
        if head.startswith(b"\xff\xd8\xff"):
            return "JPEG"
        if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            return "WEBP"
        if head.startswith((b"GIF87a", b"GIF89a")):
            return "GIF"
    except Exception:
        pass
    return None


def find_30mb_errors_in_logs() -> list[str]:
    """Return recent `exceeds 30.000MB limit` error lines from language_server.log(.prev)."""
    hits: list[str] = []
    for ldir in LOG_DIRS:
        for fname in ("language_server.log.prev", "language_server.log"):
            log_path = os.path.join(ldir, fname)
            if not os.path.exists(log_path):
                continue
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if "exceeds 30.000MB limit" in line:
                            hits.append(line.strip())
            except Exception:
                pass
    return hits[-5:]


def _file_sha256(file_path: str) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_manifest(bak_dir: str) -> dict[str, dict]:
    mpath = os.path.join(bak_dir, ".agy_compressed_manifest.json")
    if not os.path.isfile(mpath):
        return {}
    try:
        with open(mpath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_manifest(bak_dir: str, manifest: dict[str, dict]) -> None:
    os.makedirs(bak_dir, exist_ok=True)
    mpath = os.path.join(bak_dir, ".agy_compressed_manifest.json")
    tmp_path = f"{mpath}.tmp.{os.getpid()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, mpath)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _query_sips_dimensions(file_path: str) -> tuple[int, int] | None:
    if not os.path.exists("/usr/bin/sips"):
        return None
    res = subprocess.run(
        ["/usr/bin/sips", "-g", "pixelWidth", "-g", "pixelHeight", file_path],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return None
    m_w = re.search(r"pixelWidth:\s*(\d+)", res.stdout)
    m_h = re.search(r"pixelHeight:\s*(\d+)", res.stdout)
    if not m_w or not m_h:
        return None
    return int(m_w.group(1)), int(m_h.group(1))


def _compress_with_sips(
    file_path: str, orig_fmt: str, tmp_out: str, max_dim: int, quality: int = 80
) -> bool:
    """macOS zero-dependency fallback using `/usr/bin/sips` while preserving original container format and never upscaling."""
    if not os.path.exists("/usr/bin/sips"):
        return False
    sips_fmt = "png" if orig_fmt == "PNG" else ("jpeg" if orig_fmt == "JPEG" else None)
    if not sips_fmt:
        return False
    dims = _query_sips_dimensions(file_path)
    if not dims:
        return False
    orig_w, orig_h = dims
    shutil.copy2(file_path, tmp_out)
    cmd = ["/usr/bin/sips"]
    # Only pass -Z when longest side exceeds max_dim so sips never upscales small images!
    if max(orig_w, orig_h) > max_dim:
        cmd.extend(["-Z", str(max_dim)])
    cmd.extend(["-s", "format", sips_fmt])
    if sips_fmt == "jpeg":
        cmd.extend(["-s", "formatOptions", str(max(40, min(95, quality)))])
    cmd.extend([tmp_out, "--out", tmp_out])
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not os.path.exists(tmp_out):
        return False
    out_dims = _query_sips_dimensions(tmp_out)
    if not out_dims:
        return False
    out_w, out_h = out_dims
    if max(out_w, out_h) > max(orig_w, orig_h) or (
        max(orig_w, orig_h) > max_dim and max(out_w, out_h) > max_dim
    ):
        return False
    return True


def _composite_to_rgb(im: "Image.Image") -> "Image.Image":
    """Composite transparent RGBA/LA/P images onto a clean white background before converting to RGB."""
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        white_bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(white_bg, rgba).convert("RGB")
    return im.convert("RGB")


def plan_image_budget(
    files: list[str], default_dim: int = 1280, default_quality: int = 80
) -> tuple[int, int]:
    """Choose conversation-wide (max_dim, quality) before encoding so total Base64 stays under SAFE_B64_BYTES (18MB)."""
    if not files:
        return default_dim, default_quality
    raw = sum(os.path.getsize(f) for f in files if os.path.isfile(f))
    b64 = raw * 4 // 3
    if b64 <= SAFE_B64_BYTES:
        return default_dim, default_quality
    scale = ((SAFE_B64_BYTES / float(b64)) ** 0.5) * 0.92
    dim = max(640, min(default_dim, int(default_dim * scale)))
    quality = 80 if dim >= 1100 else (70 if dim >= 800 else 58)
    return dim, min(default_quality, quality)


def status_for_session(
    image_raw: int, other_raw: int, has_uncompressed_or_over_ceiling: bool
) -> str:
    """Compute honest session status accounting for both image Base64 and non-image temp media."""
    img_b64 = image_raw * 4 // 3
    total_est = img_b64 + other_raw
    if img_b64 >= 29_000_000 or total_est >= 29_000_000:
        return "🔴 超限死锁 (>30MB B64)"
    if img_b64 >= SAFE_B64_BYTES or has_uncompressed_or_over_ceiling:
        return "🟡 建议瘦身"
    if other_raw >= 8_000_000:
        return "🟡 含大体积非图媒体 (>8MB)"
    return "🟢 安全"


def _compress_single_image(
    file_path: str,
    bak_file: str,
    legacy_file: str | None,
    max_dim: int,
    quality: int = 80,
) -> tuple[bool, int, int]:
    """Idempotently back up and compress a single image file while preserving MIME format.

    If a tighter (max_dim, quality) budget is requested than previously applied, re-encodes
    cleanly from the lossless backup original (`bak_file`) so lossy generations never stack.
    Returns (was_newly_compressed, old_size_bytes, new_size_bytes).
    """
    if os.path.islink(file_path):
        return False, 0, 0

    cur_sz = os.path.getsize(file_path)
    bak_dir = os.path.dirname(bak_file)
    bname = os.path.basename(bak_file)
    manifest = _load_manifest(bak_dir)
    entry = manifest.get(bname, {})

    cur_sha = _file_sha256(file_path)
    prev_dim = int(entry.get("max_dim", 1280))
    prev_quality = int(entry.get("quality", 80))
    budget_tightened = (max_dim < prev_dim) or (quality < prev_quality)

    # 1. Manifest SHA256 fast-path: if current file matches recorded compressed_sha256 and budget did not tighten, skip
    if entry.get("compressed_sha256") == cur_sha and not budget_tightened:
        return False, cur_sz, cur_sz

    # 2. Legacy/size check with backup integrity verification
    if os.path.exists(bak_file):
        bak_sz = os.path.getsize(bak_file)
        bak_sha = entry.get("orig_sha256") or _file_sha256(bak_file)
        if cur_sz < bak_sz and entry.get("orig_sha256") in (None, bak_sha) and not budget_tightened:
            entry["orig_sha256"] = bak_sha
            entry["compressed_sha256"] = cur_sha
            entry["max_dim"] = prev_dim
            entry["quality"] = prev_quality
            manifest[bname] = entry
            _save_manifest(bak_dir, manifest)
            return False, cur_sz, cur_sz
        if cur_sha != bak_sha and cur_sz >= bak_sz and entry.get("compressed_sha256") != cur_sha:
            # A new uncompressed image reused the same filename; preserve previous backup!
            archived_bak = f"{bak_file}.prev_{int(time.time())}"
            if not os.path.exists(archived_bak):
                shutil.copy2(bak_file, archived_bak)
            shutil.copy2(file_path, bak_file)
            entry = {"orig_sha256": cur_sha}
            manifest[bname] = entry
            _save_manifest(bak_dir, manifest)

    if (
        legacy_file
        and os.path.exists(legacy_file)
        and cur_sz < os.path.getsize(legacy_file)
        and not budget_tightened
    ):
        if not os.path.exists(bak_file):
            os.makedirs(bak_dir, exist_ok=True)
            shutil.copy2(legacy_file, bak_file)
        return False, cur_sz, cur_sz

    magic_fmt = detect_magic_mime_format(file_path)
    ext = os.path.splitext(file_path)[1].lower()
    if not magic_fmt and ext not in IMAGE_EXTENSIONS:
        return False, cur_sz, cur_sz

    os.makedirs(bak_dir, exist_ok=True)
    if not os.path.exists(bak_file):
        shutil.copy2(file_path, bak_file)
        entry["orig_sha256"] = cur_sha

    # Always read pixels from the lossless original backup when available so tighter budgets never stack lossy passes!
    src_image_path = bak_file if os.path.exists(bak_file) else file_path

    tmp_out = file_path + f".agy_tmp_{os.getpid()}"
    try:
        if HAS_PIL and Image is not None:
            with Image.open(src_image_path) as im:
                # Skip multi-frame animated images (APNG / animated WebP / GIF)
                if getattr(im, "is_animated", False) or getattr(im, "n_frames", 1) > 1:
                    return False, cur_sz, cur_sz
                orig_fmt = (im.format or magic_fmt or "").upper()
                if orig_fmt == "GIF" or ext == ".gif":
                    return False, cur_sz, cur_sz
                if ImageOps is not None:
                    im = ImageOps.exif_transpose(im)
                w, h = im.size
                if max(w, h) > max_dim:
                    scale = max_dim / float(max(w, h))
                    im = im.resize(
                        (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
                        Image.Resampling.LANCZOS,
                    )
                rgb_im = _composite_to_rgb(im)
                if orig_fmt == "PNG" or ext == ".png":
                    palette_colors = 256 if quality >= 80 else (160 if quality >= 70 else 96)
                    im_q = rgb_im.quantize(
                        colors=palette_colors, method=Image.Quantize.FASTOCTREE
                    ).convert("RGB")
                    im_q.save(tmp_out, format="PNG", optimize=True, compress_level=9)
                elif orig_fmt == "JPEG" or ext in (".jpg", ".jpeg"):
                    rgb_im.save(tmp_out, format="JPEG", quality=quality, optimize=True)
                elif orig_fmt == "WEBP" or ext == ".webp":
                    rgb_im.save(tmp_out, format="WEBP", quality=quality, method=6)
                else:
                    return False, cur_sz, cur_sz
        else:
            orig_fmt = magic_fmt or ("PNG" if ext == ".png" else "JPEG")
            if not _compress_with_sips(src_image_path, orig_fmt, tmp_out, max_dim, quality):
                if os.path.exists(tmp_out):
                    os.remove(tmp_out)
                return False, cur_sz, cur_sz

        if os.path.exists(tmp_out):
            new_sz = os.path.getsize(tmp_out)
            if new_sz < cur_sz:
                os.replace(tmp_out, file_path)
                entry["orig_sha256"] = entry.get("orig_sha256") or _file_sha256(bak_file)
                entry["compressed_sha256"] = _file_sha256(file_path)
                entry["old_bytes"] = os.path.getsize(bak_file) if os.path.exists(bak_file) else cur_sz
                entry["new_bytes"] = new_sz
                entry["max_dim"] = max_dim
                entry["quality"] = quality
                manifest[bname] = entry
                _save_manifest(bak_dir, manifest)
                return True, cur_sz, new_sz
            else:
                os.remove(tmp_out)
                entry["orig_sha256"] = entry.get("orig_sha256") or cur_sha
                entry["compressed_sha256"] = cur_sha
                entry["max_dim"] = min(prev_dim, max_dim)
                entry["quality"] = min(prev_quality, quality)
                manifest[bname] = entry
                _save_manifest(bak_dir, manifest)
                return False, cur_sz, cur_sz
    except Exception as e:
        if os.path.exists(tmp_out):
            try:
                os.remove(tmp_out)
            except OSError:
                pass
        print(f"  [跳过] {os.path.basename(file_path)}: {e}")

    return False, cur_sz, os.path.getsize(file_path)


def is_supported_image_file(file_path: str) -> bool:
    """Return True if file_path is a non-symlink supported image by extension or magic header."""
    if os.path.islink(file_path) or not os.path.isfile(file_path):
        return False
    ext = os.path.splitext(file_path)[1].lower()
    if ext in IMAGE_EXTENSIONS:
        return True
    return detect_magic_mime_format(file_path) is not None


def compress_media_dir(media_dir: str, max_dim: int = 1280) -> tuple[int, int, int, int]:
    """Idempotently backup and compress new/uncompressed images in media_dir (.tempmediaStorage)."""
    files = sorted(
        f
        for f in glob.glob(os.path.join(media_dir, "*"))
        if not os.path.islink(f) and os.path.isfile(f) and is_supported_image_file(f)
    )
    if not files:
        return 0, 0, 0, 0
    bak_dir = media_dir + "_bak_orig"
    os.makedirs(bak_dir, exist_ok=True)
    legacy_bak = media_dir + "_bak_20261014"

    with _directory_flock(bak_dir):
        planned_dim, planned_q = plan_image_budget(files, default_dim=max_dim, default_quality=80)
        old_total = sum(os.path.getsize(f) for f in files)
        new_total = 0
        newly_compressed_files: set[str] = set()

        for f in files:
            bname = os.path.basename(f)
            bak_file = os.path.join(bak_dir, bname)
            legacy_file = os.path.join(legacy_bak, bname)
            did_comp, _old_sz, new_sz = _compress_single_image(
                f, bak_file, legacy_file, planned_dim, planned_q
            )
            new_total += new_sz
            if did_comp:
                newly_compressed_files.add(f)

        # Second adaptive pass from lossless backup if still above 18MB Base64 ceiling
        if new_total * 4 // 3 > SAFE_B64_BYTES and planned_dim > 640:
            pass2_dim = max(640, int(planned_dim * 0.78))
            pass2_q = max(58, planned_q - 12)
            new_total = 0
            for f in files:
                bname = os.path.basename(f)
                bak_file = os.path.join(bak_dir, bname)
                legacy_file = os.path.join(legacy_bak, bname)
                did_comp, _old_sz, new_sz = _compress_single_image(
                    f, bak_file, legacy_file, pass2_dim, pass2_q
                )
                new_total += new_sz
                if did_comp:
                    newly_compressed_files.add(f)

        return len(files), len(newly_compressed_files), old_total, new_total


def collect_brain_session_images(session_dir: str) -> list[str]:
    """Collect direct/nested image files under brain/<cascade_id>/ outside .tempmediaStorage* and .system_generated."""
    results: list[str] = []
    if not os.path.isdir(session_dir):
        return results
    for root, dirs, files in os.walk(session_dir, followlinks=False):
        dirs[:] = [
            d
            for d in dirs
            if not d.startswith(".tempmediaStorage")
            and d not in (".system_generated", ".brain_media_bak_orig")
            and not d.endswith("_bak_orig")
            and not os.path.islink(os.path.join(root, d))
        ]
        for fn in sorted(files):
            ext = os.path.splitext(fn)[1].lower()
            if ext in IMAGE_EXTENSIONS and ".agy_tmp_" not in fn:
                full_p = os.path.join(root, fn)
                if not os.path.islink(full_p) and os.path.isfile(full_p):
                    results.append(full_p)
    return results


def compress_brain_session_images(
    session_dir: str, files: list[str], max_dim: int = 1280
) -> tuple[int, int, int, int]:
    """Idempotently backup and compress large images under brain/<cascade_id>/."""
    if not files:
        return 0, 0, 0, 0
    bak_root = os.path.join(session_dir, ".brain_media_bak_orig")
    with _directory_flock(bak_root):
        planned_dim, planned_q = plan_image_budget(files, default_dim=max_dim, default_quality=80)
        old_total = sum(os.path.getsize(f) for f in files)
        new_total = 0
        newly_compressed_files: set[str] = set()

        for f in files:
            rel_p = os.path.relpath(f, session_dir)
            bak_file = os.path.join(bak_root, rel_p)
            did_comp, _old_sz, new_sz = _compress_single_image(
                f, bak_file, None, planned_dim, planned_q
            )
            new_total += new_sz
            if did_comp:
                newly_compressed_files.add(f)

        # Second adaptive pass from lossless backup if still above 18MB Base64 ceiling
        if new_total * 4 // 3 > SAFE_B64_BYTES and planned_dim > 640:
            pass2_dim = max(640, int(planned_dim * 0.78))
            pass2_q = max(58, planned_q - 12)
            new_total = 0
            for f in files:
                rel_p = os.path.relpath(f, session_dir)
                bak_file = os.path.join(bak_root, rel_p)
                did_comp, _old_sz, new_sz = _compress_single_image(
                    f, bak_file, None, pass2_dim, pass2_q
                )
                new_total += new_sz
                if did_comp:
                    newly_compressed_files.add(f)

        return len(files), len(newly_compressed_files), old_total, new_total


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Antigravity 30MB 多图会话体积巡检与免重启热修复 (macOS & Windows)"
    )
    parser.add_argument("cascade_id", nargs="?", help="指定要瘦身的会话 ID (cascade_id)")
    parser.add_argument("--check", action="store_true", help="仅巡检状态，不修改任何文件")
    parser.add_argument(
        "--brain-root",
        default=BRAIN_ROOT,
        help=f"指定 brain 根目录 (默认: {BRAIN_ROOT})",
    )
    parser.add_argument(
        "--threshold-mb",
        type=float,
        default=10.0,
        help="自动瘦身的会话图片总体积阈值 (MB，默认 10.0，对应 Base64 约 13.3MB)",
    )
    parser.add_argument(
        "--single-image-mb",
        type=float,
        default=2.0,
        help="brain/ 下单张大图触发瘦身阈值 (MB，默认 2.0)",
    )
    parser.add_argument(
        "--max-dim",
        type=int,
        default=1280,
        help="图片最长边缩放像素上限 (默认 1280)",
    )
    args = parser.parse_args()

    brain_root = os.path.expanduser(args.brain_root)
    log_hits = find_30mb_errors_in_logs()
    if log_hits:
        print("=== [日志检测] language_server.log 中近期 30.000MB 超限报错记录 ===")
        for line in log_hits[-3:]:
            print(f"  {line[:140]}")
        print()

    threshold_bytes = int(args.threshold_mb * 1_000_000)
    single_img_bytes = int(args.single_image_mb * 1_000_000)

    session_pattern = os.path.join(
        brain_root, args.cascade_id if args.cascade_id else "*"
    )
    session_dirs = sorted(d for d in glob.glob(session_pattern) if os.path.isdir(d))

    engine_name = "Pillow (PIL)" if HAS_PIL else "macOS native sips"
    print(
        f"=== [会话媒体库巡检 | 引擎: {engine_name}] ({brain_root}/*/{{.tempmediaStorage,brain大图}}) ==="
    )
    found_any = False

    for sdir in session_dirs:
        cid = os.path.basename(sdir)
        temp_dir = os.path.join(sdir, ".tempmediaStorage")
        all_temp_files = (
            sorted(
                f
                for f in glob.glob(os.path.join(temp_dir, "*"))
                if not os.path.islink(f) and os.path.isfile(f)
            )
            if os.path.isdir(temp_dir)
            else []
        )
        temp_files = [f for f in all_temp_files if is_supported_image_file(f)]
        non_img_temp_files = [f for f in all_temp_files if not is_supported_image_file(f)]
        brain_img_files = collect_brain_session_images(sdir)

        if not temp_files and not brain_img_files and not non_img_temp_files:
            continue

        temp_total = sum(os.path.getsize(f) for f in temp_files)
        non_img_total = sum(os.path.getsize(f) for f in non_img_temp_files)
        brain_total = sum(os.path.getsize(f) for f in brain_img_files)
        oversized_brain_imgs = [
            f for f in brain_img_files if os.path.getsize(f) >= single_img_bytes
        ]
        combined_total = temp_total + brain_total
        b64_mb = combined_total * 4 / 3 / 1e6

        temp_manifest = _load_manifest(temp_dir + "_bak_orig") if temp_files else {}
        brain_manifest = (
            _load_manifest(os.path.join(sdir, ".brain_media_bak_orig"))
            if brain_img_files
            else {}
        )
        all_already_compressed = (
            all(os.path.basename(f) in temp_manifest for f in temp_files)
            and all(os.path.basename(f) in brain_manifest for f in brain_img_files)
        )
        needs_compression = (
            combined_total * 4 // 3 >= SAFE_B64_BYTES
            or (
                not all_already_compressed
                and (combined_total >= threshold_bytes or bool(oversized_brain_imgs))
            )
        )
        should_act = bool(args.cascade_id) or needs_compression

        if combined_total >= 2_000_000 or non_img_total >= 2_000_000 or should_act:
            found_any = True
            status_tag = status_for_session(combined_total, non_img_total, needs_compression)
            extra_note = (
                f" [另含视频/非图媒体 {len(non_img_temp_files)} 个 {non_img_total/1e6:.2f} MB]"
                if non_img_temp_files
                else ""
            )
            print(
                f"  {status_tag} | 会话 {cid} | tempmedia图: {len(temp_files):2d} 张 ({temp_total/1e6:5.2f} MB) "
                f"+ brain图: {len(brain_img_files):2d} 张 ({brain_total/1e6:5.2f} MB) | "
                f"图片 Raw 合计: {combined_total/1e6:6.2f} MB | Base64 预估: {b64_mb:6.2f} MB / 30.00 MB{extra_note}"
            )
            if should_act and not args.check:
                if temp_files and (
                    bool(args.cascade_id) or temp_total >= threshold_bytes or combined_total >= threshold_bytes
                ):
                    cnt, new_cnt, old_b, new_b = compress_media_dir(
                        temp_dir, max_dim=args.max_dim
                    )
                    print(
                        f"     => [.tempmediaStorage 热修复] 共 {cnt} 张图 (新压缩 {new_cnt} 张，原图备份于 .tempmediaStorage_bak_orig): "
                        f"Raw {old_b/1e6:.2f} MB -> {new_b/1e6:.2f} MB | "
                        f"Base64 {old_b*4/3/1e6:.2f} MB -> {new_b*4/3/1e6:.2f} MB"
                    )
                target_brain_imgs = (
                    brain_img_files
                    if (bool(args.cascade_id) or combined_total >= threshold_bytes)
                    else oversized_brain_imgs
                )
                if target_brain_imgs:
                    bcnt, bnew_cnt, bold_b, bnew_b = compress_brain_session_images(
                        sdir, target_brain_imgs, max_dim=args.max_dim
                    )
                    print(
                        f"     => [brain/ 大图热修复] 共 {bcnt} 张图 (新压缩 {bnew_cnt} 张，原图备份于 .brain_media_bak_orig): "
                        f"Raw {bold_b/1e6:.2f} MB -> {bnew_b/1e6:.2f} MB | "
                        f"Base64 {bold_b*4/3/1e6:.2f} MB -> {bnew_b*4/3/1e6:.2f} MB"
                    )

    if not found_any:
        print("  所有会话 .tempmediaStorage 与 brain/ 媒体体积均小于 2MB，处于安全水位。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
