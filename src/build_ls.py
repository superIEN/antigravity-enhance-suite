"""Portable Language Server Binary Patcher & Verifier for Antigravity (Windows PE x86_64 & macOS Mach-O ARM64, v2.24.0–v2.32.1+)."""
from __future__ import annotations

import hashlib
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from . import patch_sites as p

NS_5_MIN = struct.pack("<Q", 300_000_000_000)
NS_10_SEC = struct.pack("<Q", 10_000_000_000)
NS_20_MIN = struct.pack("<Q", 1_200_000_000_000)

ENTITLEMENTS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>com.apple.security.cs.allow-jit</key>
\t<true/>
</dict>
</plist>
"""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_valid_site_state(name: str, cur: bytes, before: bytes, after: bytes) -> bool:
    if cur in (before, after):
        return True
    if "truncateIfTooLarge" not in name or len(cur) != len(before) or len(before) != len(after):
        return False
    i = 0
    n = len(cur)
    while i < n:
        if before[i] == after[i]:
            if cur[i] != before[i]:
                return False
            i += 1
        else:
            j = i + 1
            while j < n and before[j] != after[j]:
                j += 1
            if cur[i:j] not in (before[i:j], after[i:j]):
                return False
            i = j
    return True


def build_and_verify_ls(orig_ls_path: Path, candidate_ls_path: Path) -> dict:
    raw_base = orig_ls_path.read_bytes()
    base_sha = sha256_bytes(raw_base)
    raw = bytearray(raw_base)
    funcs = p.parse_gopclntab(raw)
    patched_sites = []

    if p.is_macho_arm64(raw):
        media_sites = p.locate_media_compression_sites(raw, funcs)
        all_sites = (
            p.locate_h2ping_sites(raw, funcs)
            + p.locate_1m_context_sites(raw, funcs)
            + media_sites
            + p.locate_utf8_and_uri_sites(raw, funcs)
            + p.locate_stream_retry_sites(raw, funcs)
            + p.locate_bg_task_timeout_sites(raw, funcs)
            + p.locate_oauth_token_sites(raw, funcs)
        )
        if not any("Site 23" in s[0] for s in all_sites):
            raise RuntimeError("Site 23 (TruncationDecorator.truncateIfTooLarge) must be located on macOS ARM64")
        min_arm64_sites = 29 if media_sites else 27
        assert len(all_sites) >= min_arm64_sites, f"Expected >={min_arm64_sites} Mach-O ARM64 sites, got {len(all_sites)}"
        for name, off, before, after in all_sites:
            cur = bytes(raw[off : off + len(before)])
            if "retryableSubstrings" in name or "ClampCheckpointConfigForModelInfo" in name:
                assert len(cur) == len(after)
            else:
                assert _is_valid_site_state(name, cur, before, after), f"Unexpected bytes at {name} (@0x{off:x})"
            raw[off : off + len(after)] = after
            patched_sites.append({"name": name, "offset": f"0x{off:x}"})

        oa_key = next((k for k in funcs if k.endswith("/oauth2.(*Token).expired")), None)
        if oa_key:
            oa_off, oa_sz = funcs[oa_key]
            oa_bytes = raw[oa_off : oa_off + oa_sz]
            arm_10s = bytes.fromhex("05809cd26581aaf24500c0f2")
            idx_oa = oa_bytes.find(arm_10s)
            if idx_oa != -1:
                site18_off = oa_off + idx_oa
                raw[site18_off : site18_off + len(arm_10s)] = arm_10s
                patched_sites.append(
                    {"name": "Site 18 oauth2.(*Token).expired 10s default verified", "offset": f"0x{site18_off:x}"}
                )

        candidate_ls_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_ls_path.write_bytes(raw)
        candidate_ls_path.chmod(0o755)
        unsigned_sha = sha256_bytes(raw)

        if sys.platform == "darwin" and os.path.exists("/usr/bin/codesign"):
            with tempfile.NamedTemporaryFile("w", suffix=".entitlements.plist", delete=False) as tf:
                tf.write(ENTITLEMENTS_XML)
                ent_path = Path(tf.name)
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
                        str(ent_path),
                        "--timestamp=none",
                        str(candidate_ls_path),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            finally:
                ent_path.unlink(missing_ok=True)
            subprocess.run(
                ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(candidate_ls_path)],
                check=True,
                capture_output=True,
                text=True,
            )

        signed_raw = candidate_ls_path.read_bytes()
        cand_sha = sha256_bytes(signed_raw)
        return {
            "platform": "darwin-arm64",
            "orig_sha256": base_sha,
            "unsigned_candidate_sha256": unsigned_sha,
            "candidate_sha256": cand_sha,
            "size_bytes": len(signed_raw),
            "patched_sites_count": len(patched_sites),
            "patched_sites": patched_sites,
        }

    image_base, sections = p.parse_pe_sections(raw)

    media_sites = p.locate_media_compression_sites(raw, funcs)
    base_sites = (
        p.locate_h2ping_sites(raw, funcs)
        + p.locate_1m_context_sites(raw, funcs)
        + media_sites
        + p.locate_utf8_and_uri_sites(raw, funcs)
        + [s for s in p.locate_stream_retry_sites(raw, funcs) if "unexpected EOF" in s[0]]
    )
    expected_base_counts = (15, 16) if media_sites else (13, 14)
    if len(base_sites) not in expected_base_counts:
        raise RuntimeError(f"Expected {expected_base_counts} base sites, got {len(base_sites)}")
    if not any("Site 22" in s[0] for s in base_sites):
        raise RuntimeError("Site 22 (ClampCheckpointConfigForModelInfo) must be located on Windows PE")
    if not any("Site 23" in s[0] for s in base_sites):
        raise RuntimeError("Site 23 (TruncationDecorator.truncateIfTooLarge) must be located on Windows PE")
    for name, off, before, after in base_sites:
        cur = bytes(raw[off : off + len(before)])
        if not _is_valid_site_state(name, cur, before, after):
            raise RuntimeError(f"Unexpected bytes at {name}")
        raw[off : off + len(after)] = after
        patched_sites.append({"name": name, "offset": f"0x{off:x}"})

    ir_off, ir_sz = funcs["google3/third_party/jetski/cortex/utils/utils.IsRetryableAPIError"]
    ir_bytes = raw[ir_off : ir_off + ir_sz]
    s14_ctx = bytes.fromhex("48898424d000000048895c2468488b0d")
    idx14 = ir_bytes.find(s14_ctx)
    assert idx14 != -1, "Site 14 context not found"
    lea_pos = ir_off + idx14 + 13
    disp14 = struct.unpack_from("<i", raw, lea_pos + 3)[0]
    hdr_rva = p.raw_to_rva(sections, lea_pos) + 7 + disp14
    hdr_raw = p.rva_to_raw(sections, hdr_rva)
    arr_va, arr_len, _ = struct.unpack_from("<QQQ", raw, hdr_raw)
    arr_raw = p.rva_to_raw(sections, arr_va - image_base)
    assert arr_len in (6, 7, 15, 16), f"Unexpected retryableSubstrings length: {arr_len}"

    entries = [list(struct.unpack_from("<QQ", raw, arr_raw + i * 16)) for i in range(arr_len)]
    pos_sre = raw.find(b"stream reading error: %w")
    pos_unauth_lower = raw.find(b"unauthenticated")
    pos_oneof = raw.find(b"oneof_index")
    pos_proxyconn = raw.find(b"proxyconnect")
    pos_ctx_deadline = raw.find(b"context deadline exceeded")
    assert pos_sre != -1 and pos_unauth_lower != -1
    va_sre = image_base + p.raw_to_rva(sections, pos_sre)
    va_unauth_lower = image_base + p.raw_to_rva(sections, pos_unauth_lower)

    if arr_len in (15, 16):
        assert pos_oneof != -1 and pos_proxyconn != -1 and pos_ctx_deadline != -1
        va_eof = image_base + p.raw_to_rva(sections, pos_oneof + 2)
        va_proxyconn = image_base + p.raw_to_rva(sections, pos_proxyconn)
        va_deadline = image_base + p.raw_to_rva(sections, pos_ctx_deadline + 8)

        va0, _ = entries[0]
        va4, len4 = entries[4]
        orig_va4 = va4 if len4 == 32 else (va4 - 22)
        va12, len12 = entries[12]
        orig_va12 = va12 if len12 == 19 else (va12 - 10)
        va13, _ = entries[13]
        va_last, len_last = entries[arr_len - 1]
        orig_va_last = va_last if len_last == 11 else (va_last - 4)

        entries[0] = [va0, 3]                   # "wsa"
        entries[1] = [va_sre, 7]                # "stream "
        entries[4] = [orig_va4 + 22, 10]        # "connection"
        entries[5] = [va_unauth_lower, 15]      # "unauthenticated"
        entries[7] = [va_eof, 3]                # "eof"
        entries[11] = [va_proxyconn, 12]        # "proxyconnect"
        entries[12] = [orig_va12 + 10, 9]       # "timed out"
        entries[13] = [va13, 6]                 # "http2:"
        if arr_len == 16:
            entries[14] = [va_deadline, 17]     # "deadline exceeded"
        entries[arr_len - 1] = [orig_va_last + 4, 7]  # "timeout" (TLS handshake timeout, i/o timeout)
    elif arr_len == 7:
        va0, _ = entries[0]
        va4, len4 = entries[4]
        orig_va4 = va4 if len4 == 32 else (va4 - 22)
        entries[0] = [va0, 3]
        entries[1] = [va_sre, 21]
        entries[4] = [orig_va4 + 22, 10]
        entries[5] = [va_unauth_lower, 15]
    else:
        va0, _ = entries[0]
        va2, len2 = entries[2]
        orig_va2 = va2 if len2 == 32 else (va2 - 22)
        va5, _ = entries[5]
        entries[0] = [va0, 3]
        entries[1] = [va_sre, 7]
        entries[2] = [orig_va2 + 22, 10]
        entries[3] = [va_unauth_lower, 15]
        entries[5] = [va5, 6]

    s14_patch = b"".join(struct.pack("<QQ", v, l) for v, l in entries)
    raw[arr_raw : arr_raw + len(s14_patch)] = s14_patch
    patched_sites.append({"name": f"Site 14 retryableSubstrings ({arr_len} slots)", "offset": f"0x{arr_raw:x}"})

    # Sites 15, 16, 19, 20, 21: 60s non-daemon background task timeout notification & AgentExecutor wakeup FSM fixes
    bg_sites = p.locate_bg_task_timeout_sites(raw, funcs)
    assert len(bg_sites) in (2, 5), f"Expected 2 or 5 bg task timeout & wakeup FSM sites (Sites 15, 16, 19, 20, 21), found {len(bg_sites)}"
    for name, off, before, after in bg_sites:
        cur = bytes(raw[off : off + len(before)])
        assert cur in (before, after), f"Unexpected bytes at {name}"
        raw[off : off + len(after)] = after
        patched_sites.append({"name": name, "offset": f"0x{off:x}"})

    oauth_sites = p.locate_oauth_token_sites(raw, funcs)
    assert len(oauth_sites) == 1, f"Expected 1 OAuth token refresh site (Site 17), found {len(oauth_sites)}"
    for name, off, before, after in oauth_sites:
        cur = bytes(raw[off : off + len(before)])
        assert cur in (before, after), f"Unexpected bytes at {name}"
        raw[off : off + len(after)] = after
        patched_sites.append({"name": f"Site 17 {name}", "offset": f"0x{off:x}"})

    exp_fn = next((k for k in funcs if k.endswith("/oauth2.(*Token).expired")), None)
    if exp_fn:
        exp_off, exp_sz = funcs[exp_fn]
        assert raw[exp_off : exp_off + exp_sz].count(NS_10_SEC) == 1, "oauth2.(*Token).expired 10s default must remain untouched"
        patched_sites.append({"name": "Site 18 oauth2.(*Token).expired 10s default verified", "offset": f"0x{exp_off:x}"})

    candidate_ls_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_ls_path.write_bytes(raw)
    cand_sha = sha256_bytes(raw)
    return {
        "platform": "win32-x64",
        "orig_sha256": base_sha,
        "candidate_sha256": cand_sha,
        "size_bytes": len(raw),
        "patched_sites_count": len(patched_sites),
        "patched_sites": patched_sites,
    }
