"""Portable Language Server Binary Patcher & Verifier for Antigravity (v2.24.0 / v2.25.0 / v2.26.0+)."""
import hashlib
import struct
from pathlib import Path
from . import patch_sites as p

NS_5_MIN = struct.pack("<Q", 300_000_000_000)
NS_10_SEC = struct.pack("<Q", 10_000_000_000)
NS_20_MIN = struct.pack("<Q", 1_200_000_000_000)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_and_verify_ls(orig_ls_path: Path, candidate_ls_path: Path) -> dict:
    raw_base = orig_ls_path.read_bytes()
    base_sha = sha256_bytes(raw_base)
    raw = bytearray(raw_base)
    funcs = p.parse_gopclntab(raw)
    image_base, sections = p.parse_pe_sections(raw)

    patched_sites = []

    base_sites = (
        p.locate_h2ping_sites(raw, funcs)
        + p.locate_1m_context_sites(raw, funcs)
        + p.locate_media_compression_sites(raw, funcs)
        + p.locate_utf8_and_uri_sites(raw, funcs)
        + [s for s in p.locate_stream_retry_sites(raw, funcs) if "unexpected EOF" in s[0]]
    )
    assert len(base_sites) == 14, f"Expected 14 base sites (Sites 1..13), got {len(base_sites)}"
    for name, off, before, after in base_sites:
        cur = bytes(raw[off : off + len(before)])
        assert cur in (before, after), f"Unexpected bytes at {name}"
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
    assert arr_len in (6, 15), f"Unexpected retryableSubstrings length: {arr_len}"

    entries = [list(struct.unpack_from("<QQ", raw, arr_raw + i * 16)) for i in range(arr_len)]
    pos_sre = raw.find(b"stream reading error: %w")
    pos_unauth_lower = raw.find(b"unauthenticated")
    assert pos_sre != -1 and pos_unauth_lower != -1
    va_sre = image_base + p.raw_to_rva(sections, pos_sre)
    va_unauth_lower = image_base + p.raw_to_rva(sections, pos_unauth_lower)

    if arr_len == 15:
        va0, _ = entries[0]
        va4, len4 = entries[4]
        orig_va4 = va4 if len4 == 32 else (va4 - 22)
        va13, _ = entries[13]
        entries[0] = [va0, 3]
        entries[1] = [va_sre, 7]
        entries[4] = [orig_va4 + 22, 10]
        entries[5] = [va_unauth_lower, 15]
        entries[13] = [va13, 6]
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

    bg_sites = p.locate_bg_task_timeout_sites(raw, funcs)
    assert len(bg_sites) == 2, f"Expected 2 bg task timeout sites, found {len(bg_sites)}"
    for name, off, before, after in bg_sites:
        cur = bytes(raw[off : off + len(before)])
        assert cur in (before, after), f"Unexpected bytes at {name}"
        raw[off : off + len(after)] = after
        patched_sites.append({"name": name, "offset": f"0x{off:x}"})

    ca_fn = "google3/third_party/jetski/codeassistclient/codeassistclient.RefreshingTokenSource"
    assert ca_fn in funcs, f"{ca_fn} not found"
    ca_off, ca_sz = funcs[ca_fn]
    ca_bytes = raw[ca_off : ca_off + ca_sz]
    idx_ca = ca_bytes.find(b"\x48\xb8" + NS_5_MIN)
    if idx_ca == -1:
        idx_ca = ca_bytes.find(b"\x48\xb8" + NS_20_MIN)
    assert idx_ca != -1, "Site 17 mov rax, imm64 not found"
    site17_off = ca_off + idx_ca + 2
    raw[site17_off : site17_off + 8] = NS_20_MIN
    patched_sites.append({"name": "Site 17 codeassistclient.RefreshingTokenSource 5m->20m", "offset": f"0x{site17_off:x}"})

    oa_fn = "golang.org/x/oauth2.(*Token).expired"
    if oa_fn in funcs:
        oa_off, oa_sz = funcs[oa_fn]
        oa_bytes = raw[oa_off : oa_off + oa_sz]
        idx_oa = oa_bytes.find(b"\x48\xb8" + NS_10_SEC)
        if idx_oa != -1:
            site18_off = oa_off + idx_oa + 2
            raw[site18_off : site18_off + 8] = NS_10_SEC
            patched_sites.append({"name": "Site 18 oauth2.(*Token).expired 10s default verified", "offset": f"0x{site18_off:x}"})

    candidate_ls_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_ls_path.write_bytes(raw)
    cand_sha = sha256_bytes(raw)
    return {
        "orig_sha256": base_sha,
        "candidate_sha256": cand_sha,
        "size_bytes": len(raw),
        "patched_sites_count": len(patched_sites),
        "patched_sites": patched_sites,
    }
