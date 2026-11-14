"""Cross-Platform (Windows PE x86_64 & macOS Mach-O ARM64) Go Binary Symbol & Patch Site Locator."""
import struct


def is_macho_arm64(data: bytes) -> bool:
    return len(data) >= 32 and data[:4] == b"\xcf\xfa\xed\xfe"


def parse_pe_sections(data: bytes):
    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    num_sec = struct.unpack_from("<H", data, pe_off + 6)[0]
    opt_sz = struct.unpack_from("<H", data, pe_off + 20)[0]
    image_base = struct.unpack_from("<Q", data, pe_off + 24 + 24)[0]
    sec_off = pe_off + 24 + opt_sz
    sections = []
    for i in range(num_sec):
        s = data[sec_off + i * 40 : sec_off + (i + 1) * 40]
        vsize, rva, rawsize, rawptr = struct.unpack_from("<IIII", s, 8)
        sections.append((rva, vsize, rawptr))
    return image_base, sections


def parse_macho_sections(data: bytes):
    sections = []
    cursor = 32
    ncmds = struct.unpack_from("<I", data, 16)[0]
    for _ in range(ncmds):
        kind, size = struct.unpack_from("<II", data, cursor)
        if kind == 0x19:  # LC_SEGMENT_64
            nsec = struct.unpack_from("<I", data, cursor + 64)[0]
            for i in range(nsec):
                name, seg, addr, length, offset = struct.unpack_from(
                    "<16s16sQQI", data, cursor + 72 + 80 * i
                )
                sections.append(
                    (
                        name.rstrip(b"\x00").decode("utf-8", "ignore"),
                        seg.rstrip(b"\x00").decode("utf-8", "ignore"),
                        addr,
                        length,
                        offset,
                    )
                )
        cursor += size
    return sections


def rva_to_raw(sections: list, target_rva: int):
    for rva, vsize, rawptr in sections:
        if rva <= target_rva < rva + vsize:
            return rawptr + (target_rva - rva)
    return None


def raw_to_rva(sections: list, target_raw: int):
    for rva, vsize, rawptr in sections:
        if rawptr <= target_raw < rawptr + vsize:
            return rva + (target_raw - rawptr)
    return None


def macho_va_to_raw(sections: list, target_va: int):
    for _, _, addr, length, offset in sections:
        if addr <= target_va < addr + length:
            return offset + (target_va - addr)
    return None


def macho_raw_to_va(sections: list, target_raw: int):
    for _, _, addr, length, offset in sections:
        if offset <= target_raw < offset + length:
            return addr + (target_raw - offset)
    return None


def parse_gopclntab(data: bytes):
    if is_macho_arm64(data):
        sections = parse_macho_sections(data)
        text_sec = next((s for s in sections if s[0] == "__text"), None)
        if not text_sec:
            raise RuntimeError("Could not find __text section in Mach-O binary")
        text_rptr = text_sec[4]
        pos = data.find(b"\xf1\xff\xff\xff\x00\x00\x04\x08")
        if pos == -1:
            raise RuntimeError("Could not find Go ARM64 .gopclntab magic in Mach-O binary")
    else:
        pe_off = struct.unpack_from("<I", data, 0x3C)[0]
        opt_sz = struct.unpack_from("<H", data, pe_off + 20)[0]
        sec_off = pe_off + 24 + opt_sz
        _, _, _, _, text_rptr = struct.unpack_from("<8sIIII", data, sec_off)
        pos = data.find(b"\xf1\xff\xff\xff\x00\x00\x01\x08")
        if pos == -1:
            raise RuntimeError("Could not find Go .gopclntab magic in binary")

    nfunc, nfiles, textStart, funcnameOff, cuOff, filetabOff, pctabOff, pclnOff = struct.unpack_from(
        "<QQQQQQQQ", data, pos + 8
    )
    ftab = pos + pclnOff
    funcs = {}
    for i in range(nfunc):
        eoff, foff = struct.unpack_from("<II", data, ftab + i * 8)
        next_eoff = struct.unpack_from("<I", data, ftab + (i + 1) * 8)[0]
        _, name_off = struct.unpack_from("<Ii", data, ftab + foff)
        name_pos = pos + funcnameOff + name_off
        end_pos = data.find(b"\x00", name_pos)
        fname = data[name_pos:end_pos].decode("utf-8", "ignore")
        funcs[fname] = (text_rptr + eoff, next_eoff - eoff)
    return funcs


def _find_orig_or_patch(buf: bytes, orig: bytes, patch: bytes, start: int = 0) -> int:
    pos = buf.find(orig, start)
    if pos == -1:
        pos = buf.find(patch, start)
    return pos


def _require_bytes(buf: bytes, needle: bytes) -> int:
    pos = buf.find(needle)
    if pos == -1:
        raise RuntimeError(f"Required byte anchor not found in binary: {needle!r}")
    return pos


H2_PROTOCOLS_CTX = bytes.fromhex("4883b8c800000000")
H2_PROTOCOLS_ORIG = bytes.fromhex("756e")
H2_PROTOCOLS_PATCH = bytes.fromhex("9090")

H2_PING_CTX = bytes.fromhex("48c7402000001000")
H2_PING_ORIG = bytes.fromhex("904883784000907f0e48b900d6117e0300000048894840c3")
H2_PING_PATCH_V4 = bytes.fromhex("48b900d6117e03000000488948404801c9488948389090c3")
H2_PING_PATCH = bytes.fromhex("48b900c817a8040000004889483848c1e1024889484090c3")

GONET_H2_ORIG = bytes.fromhex("c74010000010009048837828007f0e48b900d6117e0300000048894828c3")
GONET_H2_PATCH = bytes.fromhex("c74010000010009048b900ba1dd205000000488948204801c948894828c3")

ARM64_NOP = bytes.fromhex("1f2003d5")


def locate_h2ping_sites(data: bytes, funcs: dict):
    if is_macho_arm64(data):
        sites = []
        # 1. net/http.(*Transport).protocols (HTTP/2 enable with custom TLSClientConfig)
        f1_off, f1_sz = funcs["net/http.(*Transport).protocols"]
        f1_bytes = data[f1_off : f1_off + f1_sz]
        # Context: ldr x1, [x0, #0xc8] (016440f9) or v2.29.0+ ldr x1, [x0, #0x78] (013c40f9)
        # followed by cbnz x1, +0x80/+0x84 (210400b5)
        ctx1 = bytes.fromhex("016440f9")
        ctx1_v229 = bytes.fromhex("013c40f9")
        orig1 = bytes.fromhex("210400b5")
        idx1 = f1_bytes.find(ctx1)
        if idx1 == -1:
            idx1 = f1_bytes.find(ctx1_v229)
        if idx1 == -1:
            raise RuntimeError("Could not find TLSClientConfig check in ARM64 net/http.(*Transport).protocols")
        sites.append(
            ("net/http.(*Transport).protocols (HTTP/2 enable)", f1_off + idx1 + 4, orig1, ARM64_NOP)
        )

        # 2. net/http/internal/http2.setConfigDefaults (ReadIdle=20s, PingTimeout=80s)
        f2_off, f2_sz = funcs["net/http/internal/http2.setConfigDefaults"]
        f2_bytes = data[f2_off : f2_off + f2_sz]
        stdlib_slots = [
            (0x24, "050099d2", "stdlib-h2-idle-constant-0"),
            (0x50, "e502b5f2", "stdlib-h2-idle-constant-1"),
            (0x11C, "8500c0f2", "stdlib-h2-idle-constant-2"),
        ]
        for rel_off, patch_hex, label in stdlib_slots:
            sites.append(
                (
                    f"net/http/internal/http2.setConfigDefaults ({label})",
                    f2_off + rel_off,
                    ARM64_NOP,
                    bytes.fromhex(patch_hex),
                )
            )
        stdlib_tail_orig = bytes.fromhex(
            "012040f91f2003d53f0000f1ac00005401c09ad221c2aff26100c0f2012000f9c0035fd6"
        )
        stdlib_tail_patch = bytes.fromhex(
            "1f2003d5018843a93f0000f12110859aa5f47ed35f0000f142c0859a018803a9c0035fd6"
        )
        pos_f2_tail = _find_orig_or_patch(f2_bytes, stdlib_tail_orig, stdlib_tail_patch)
        if pos_f2_tail == -1:
            raise RuntimeError("Could not find tail in ARM64 net/http/internal/http2.setConfigDefaults")
        sites.append(
            (
                "net/http/internal/http2.setConfigDefaults (SendPing=20s, PingTimeout=80s)",
                f2_off + pos_f2_tail,
                stdlib_tail_orig,
                stdlib_tail_patch,
            )
        )

        # 3. go_net/http2.setConfigDefaults (ReadIdle=25s, PingTimeout=50s)
        gn_key = next((k for k in funcs if k.endswith("/go_net/http2/http2.setConfigDefaults")), None)
        if gn_key:
            gn_off, gn_sz = funcs[gn_key]
            gn_bytes = data[gn_off : gn_off + gn_sz]
            gonet_slots = [
                (0x10, "054097d2", "go-net-h2-idle-constant-0"),
                (0xBC, "a543baf2", "go-net-h2-idle-constant-1"),
            ]
            for rel_off, patch_hex, label in gonet_slots:
                sites.append(
                    (
                        f"go_net/http2.setConfigDefaults ({label})",
                        gn_off + rel_off,
                        ARM64_NOP,
                        bytes.fromhex(patch_hex),
                    )
                )
            gonet_tail_orig = bytes.fromhex(
                "011440f91f2003d53f0000f1ac00005401c09ad221c2aff26100c0f2011400f9c0035fd6"
            )
            gonet_tail_patch = bytes.fromhex(
                "a500c0f2010842a93f0000f12110859aa5f87fd35f0000f142c0859a010802a9c0035fd6"
            )
            pos_gn_tail = _find_orig_or_patch(gn_bytes, gonet_tail_orig, gonet_tail_patch)
            if pos_gn_tail == -1:
                raise RuntimeError("Could not find tail in ARM64 go_net/http2.setConfigDefaults")
            sites.append(
                (
                    "go_net/http2.setConfigDefaults (ReadIdle=25s, PingTimeout=50s)",
                    gn_off + pos_gn_tail,
                    gonet_tail_orig,
                    gonet_tail_patch,
                )
            )
        return sites

    sites = []
    # Site 1: net/http.(*Transport).protocols
    f1_off, f1_sz = funcs["net/http.(*Transport).protocols"]
    f1_bytes = data[f1_off : f1_off + f1_sz]
    idx1 = f1_bytes.find(H2_PROTOCOLS_CTX)
    if idx1 != -1:
        patch1_off = f1_off + idx1 + len(H2_PROTOCOLS_CTX)
        sites.append(("net/http.(*Transport).protocols (HTTP/2 enable)", patch1_off, H2_PROTOCOLS_ORIG, H2_PROTOCOLS_PATCH))
    else:
        # v2.29.0+: cmp qword ptr [rax+0x78], 0 (4883787800) followed by jne +0x66 (7566)
        ctx_v229 = bytes.fromhex("4883787800")
        orig_v229 = bytes.fromhex("7566")
        idx1_v229 = f1_bytes.find(ctx_v229)
        if idx1_v229 == -1:
            raise RuntimeError("Could not find TLSClientConfig check in net/http.(*Transport).protocols")
        patch1_off = f1_off + idx1_v229 + len(ctx_v229)
        sites.append(("net/http.(*Transport).protocols (v2.29.0+ HTTP/2 enable)", patch1_off, orig_v229, H2_PROTOCOLS_PATCH))

    # Site 2: net/http/internal/http2.setConfigDefaults
    f2_off, f2_sz = funcs["net/http/internal/http2.setConfigDefaults"]
    f2_bytes = data[f2_off : f2_off + f2_sz]
    idx2 = f2_bytes.find(H2_PING_CTX)
    if idx2 == -1:
        raise RuntimeError("Could not find MaxReadFrameSize in net/http/internal/http2.setConfigDefaults")
    patch2_off = f2_off + idx2 + len(H2_PING_CTX)
    sites.append(("net/http/internal/http2.setConfigDefaults (SendPing=20s, PingTimeout=80s)", patch2_off, H2_PING_ORIG, H2_PING_PATCH))

    # Site 12: google3/third_party/golang/go_net/http2/http2.setConfigDefaults
    gn_key = next((k for k in funcs if k.endswith("/go_net/http2/http2.setConfigDefaults")), None)
    if gn_key:
        gn_off, gn_sz = funcs[gn_key]
        gn_bytes = data[gn_off : gn_off + gn_sz]
        pos_gn = gn_bytes.find(GONET_H2_ORIG)
        if pos_gn == -1:
            pos_gn = gn_bytes.find(GONET_H2_PATCH)
        if pos_gn != -1:
            sites.append(("go_net/http2.setConfigDefaults (ReadIdle=25s, PingTimeout=50s)", gn_off + pos_gn, GONET_H2_ORIG, GONET_H2_PATCH))
    return sites


def locate_1m_context_sites(data: bytes, funcs: dict):
    sites = []
    chk_key = next((k for k in funcs if k.endswith("/utils.GetCheckpointConfig")), None)
    cas_key = next(
        (k for k in funcs if k.endswith(".GetDefaultConversationalCascadeConfig") or k.endswith(".defaultConversationalCascadeConfig")),
        None,
    )
    gw_key = next(
        (k for k in funcs if k.endswith(".modelEntryToModelDetails") or k.endswith(".GatewayModelDetails")),
        None,
    )
    hyd_key = next((k for k in funcs if k.endswith(".HydrateModelEntryDefaults")), None)

    if is_macho_arm64(data):
        if chk_key:
            f_off, f_sz = funcs[chk_key]
            f_bytes = data[f_off : f_off + f_sz]
            cap_orig = bytes.fromhex("02009dd26200a0f2")
            cap_patch = bytes.fromhex("020080d20202a0f2")
            p1 = _find_orig_or_patch(f_bytes, cap_orig, cap_patch, 0)
            if p1 != -1:
                sites.append(("utils.GetCheckpointConfig (cap 256K -> 1M #1)", f_off + p1, cap_orig, cap_patch))
                p2 = _find_orig_or_patch(f_bytes, cap_orig, cap_patch, p1 + len(cap_orig))
                if p2 != -1:
                    sites.append(("utils.GetCheckpointConfig (cap 256K -> 1M #2)", f_off + p2, cap_orig, cap_patch))
            else:
                # v2.26.0+ / v2.29.0 ARM64: mov x4, #0xe800; movk x4, #0x3, lsl #16 -> 1M
                cap29_orig = bytes.fromhex("04009dd26400a0f2")
                cap29_patch = bytes.fromhex("040080d20402a0f2")
                p1_29 = _find_orig_or_patch(f_bytes, cap29_orig, cap29_patch, 0)
                if p1_29 != -1:
                    sites.append(("utils.GetCheckpointConfig (cap 256K -> 1M #1)", f_off + p1_29, cap29_orig, cap29_patch))
                    p2_29 = _find_orig_or_patch(f_bytes, cap29_orig, cap29_patch, p1_29 + len(cap29_orig))
                    if p2_29 != -1:
                        sites.append(("utils.GetCheckpointConfig (cap 256K -> 1M #2)", f_off + p2_29, cap29_orig, cap29_patch))

            fb_orig = bytes.fromhex("03009dd26300a0f2")
            fb_patch = bytes.fromhex("034888d2c301a0f2")
            p_fb = _find_orig_or_patch(f_bytes, fb_orig, fb_patch)
            if p_fb != -1:
                sites.append(("utils.GetCheckpointConfig (fallback 256K -> 934,464)", f_off + p_fb, fb_orig, fb_patch))

        if cas_key:
            c_off, c_sz = funcs[cas_key]
            c_bytes = data[c_off : c_off + c_sz]
            kc_orig = bytes.fromhex("03a8c1f26300e0f2")
            kc_patch = bytes.fromhex("0300c0f20302e0f2")
            p_kc = _find_orig_or_patch(c_bytes, kc_orig, kc_patch)
            if p_kc != -1:
                sites.append(("GetDefaultConversationalCascadeConfig (knowledge 200K -> 1M)", c_off + p_kc, kc_orig, kc_patch))

        if gw_key:
            g_off, g_sz = funcs[gw_key]
            g_bytes = data[g_off : g_off + g_sz]
            gw_orig = bytes.fromhex("04a881d26400a0f2")
            gw_patch = bytes.fromhex("040080d20402a0f2")
            p_gw = _find_orig_or_patch(g_bytes, gw_orig, gw_patch)
            if p_gw != -1:
                sites.append(("gateway.modelEntryToModelDetails (default input 200K -> 1M)", g_off + p_gw, gw_orig, gw_patch))
            else:
                for off4 in range(0, g_sz - 7, 4):
                    w0, w1 = struct.unpack_from("<II", g_bytes, off4)
                    rd = w0 & 0x1F
                    if (w1 & 0x1F) == rd and (
                        ((w0 & 0xFFFFFFE0) == 0xD281A800 and (w1 & 0xFFFFFFE0) == 0xF2A00060)
                        or ((w0 & 0xFFFFFFE0) == 0xD2800000 and (w1 & 0xFFFFFFE0) == 0xF2A00200)
                    ):
                        dyn_gw_orig = struct.pack("<II", 0xD281A800 | rd, 0xF2A00060 | rd)
                        dyn_gw_patch = struct.pack("<II", 0xD2800000 | rd, 0xF2A00200 | rd)
                        sites.append(
                            (
                                "gateway.modelEntryToModelDetails (default input 200K -> 1M)",
                                g_off + off4,
                                dyn_gw_orig,
                                dyn_gw_patch,
                            )
                        )
                        break

        if hyd_key:
            h_off, h_sz = funcs[hyd_key]
            h_bytes = data[h_off : h_off + h_sz]
            hyd_orig = bytes.fromhex("06a881d26600a0f2")
            hyd_patch = bytes.fromhex("060080d20602a0f2")
            p_hyd = _find_orig_or_patch(h_bytes, hyd_orig, hyd_patch)
            if p_hyd != -1:
                sites.append(("gateway.HydrateModelEntryDefaults (default input 200K -> 1M)", h_off + p_hyd, hyd_orig, hyd_patch))

        # Site 22: utils.ClampCheckpointConfigForModelInfo (983,040 for 1M, preserves 200,000 for 200k under w3 in {0, 256000})
        clamp_key = next((k for k in funcs if k.endswith("/utils.ClampCheckpointConfigForModelInfo")), None)
        if clamp_key:
            cl_off, cl_sz = funcs[clamp_key]
            cl_bytes = data[cl_off : cl_off + cl_sz]
            cl_orig = bytes.fromhex("23010035231480b9048893d27f0004eb6c000054e3031faa02000014630004cb031000b9")
            cl_prev_v360 = bytes.fromhex("65f84051bf0020716418407ac1000054241480b9844040d19f00036b4d000054041000b9")
            cl_patch = bytes.fromhex("65f84051bf0020716418407ac1000054241480b9854040d19f004171a4c0841a041000b9")
            p_cl = _find_orig_or_patch(cl_bytes, cl_orig, cl_patch)
            if p_cl == -1:
                p_cl = cl_bytes.find(cl_prev_v360)
            if p_cl != -1:
                sites.append(
                    (
                        "Site 22 utils.ClampCheckpointConfigForModelInfo (983,040 for 1M, preserves 200k)",
                        cl_off + p_cl,
                        cl_orig,
                        cl_patch,
                    )
                )
            else:
                # v2.26.0+ / v2.29.0 ARM64: MaxTokenLimit field offset is [x0, #0xc] (str w3/w4, [x0, #0xc])
                cl29_orig = bytes.fromhex("23010035231480b9048893d27f0004eb6c000054e3031faa02000014630004cb030c00b9")
                cl29_patch = bytes.fromhex("65f84051bf0020716418407ac1000054241480b9854040d19f004171a4c0841a040c00b9")
                p_cl29 = _find_orig_or_patch(cl_bytes, cl29_orig, cl29_patch)
                if p_cl29 != -1:
                    sites.append(
                        (
                            "Site 22 utils.ClampCheckpointConfigForModelInfo (983,040 for 1M, preserves 200k)",
                            cl_off + p_cl29,
                            cl29_orig,
                            cl29_patch,
                        )
                    )

        # Site 23 (ARM64): (*TruncationDecorator).truncateIfTooLarge trajectory byte cap (25MB 0x01900000 -> 256MB 0x10000000)
        td_key = next((k for k in funcs if k.endswith("(*TruncationDecorator).truncateIfTooLarge")), None)
        if td_key:
            td_off, td_sz = funcs[td_key]
            if td_off % 4 == 0 and td_sz >= 4:
                td_bytes = bytes(data[td_off : td_off + td_sz])
                td_orig = bytearray(td_bytes)
                td_patch = bytearray(td_bytes)
                arm_hits = 0
                for off4 in range(0, td_sz - 3, 4):
                    w = struct.unpack_from("<I", td_bytes, off4)[0]
                    rd = w & 0x1F
                    op_mask = w & 0xFFFFFFE0
                    if op_mask in (0xD2A03200, 0xD2A20000):
                        struct.pack_into("<I", td_orig, off4, 0xD2A03200 | rd)
                        struct.pack_into("<I", td_patch, off4, 0xD2A20000 | rd)
                        arm_hits += 1
                    elif op_mask in (0x52A03200, 0x52A20000):
                        struct.pack_into("<I", td_orig, off4, 0x52A03200 | rd)
                        struct.pack_into("<I", td_patch, off4, 0x52A20000 | rd)
                        arm_hits += 1
                if arm_hits > 0 and (td_sz < 512 or arm_hits in (3, 4, 5, 6)):
                    sites.append(
                        (
                            "Site 23 (*TruncationDecorator).truncateIfTooLarge (trajectory byte cap 25MB -> 256MB)",
                            td_off,
                            bytes(td_orig),
                            bytes(td_patch),
                        )
                    )

        return sites

    if chk_key:
        f_off, f_sz = funcs[chk_key]
        f_bytes = data[f_off : f_off + f_sz]
        pat_orig = bytes.fromhex("81f900e80300ba00e80300")
        pat_patch = bytes.fromhex("81f900001000ba00001000")
        pos1 = f_bytes.find(pat_orig)
        if pos1 == -1:
            pos1 = f_bytes.find(pat_patch)
        if pos1 != -1:
            sites.append(("utils.GetCheckpointConfig (cap 256K -> 1M)", f_off + pos1, pat_orig, pat_patch))
        else:
            # v2.26.0: cmp edx, 0x3e800; mov esi, 0x3e800
            pat26_orig = bytes.fromhex("81fa00e80300be00e80300")
            pat26_patch = bytes.fromhex("81fa00001000be00001000")
            pos1_26 = f_bytes.find(pat26_orig)
            if pos1_26 == -1:
                pos1_26 = f_bytes.find(pat26_patch)
            if pos1_26 != -1:
                sites.append(("utils.GetCheckpointConfig (v2.26.0 cap 256K -> 1M)", f_off + pos1_26, pat26_orig, pat26_patch))

        fb_orig = bytes.fromhex("c7401000e80300")
        fb_patch = bytes.fromhex("c7401040420e00")
        pos2 = f_bytes.find(fb_orig)
        if pos2 == -1:
            pos2 = f_bytes.find(fb_patch)
        if pos2 != -1:
            sites.append(("utils.GetCheckpointConfig (fallback 256K -> 934,464)", f_off + pos2, fb_orig, fb_patch))

    if cas_key:
        c_off, c_sz = funcs[cas_key]
        c_bytes = data[c_off : c_off + c_sz]
        kc_orig = bytes.fromhex("48bafa030000400d0300")
        kc_patch = bytes.fromhex("48bafa03000000001000")
        pos3 = c_bytes.find(kc_orig)
        if pos3 == -1:
            pos3 = c_bytes.find(kc_patch)
        if pos3 != -1:
            sites.append(("GetDefaultConversationalCascadeConfig (knowledge 200K -> 1M)", c_off + pos3, kc_orig, kc_patch))

    if gw_key:
        g_off, g_sz = funcs[gw_key]
        g_bytes = data[g_off : g_off + g_sz]
        gw_orig = bytes.fromhex("4531c041b9400d030041baffff0000")
        gw_patch = bytes.fromhex("4531c041b90000100041baffff0000")
        pos4 = g_bytes.find(gw_orig)
        if pos4 == -1:
            pos4 = g_bytes.find(gw_patch)
        if pos4 != -1:
            sites.append(("gateway.modelEntryToModelDetails (default input 200K -> 1M)", g_off + pos4, gw_orig, gw_patch))
        else:
            # v2.25.0: 64-bit immediate mov rdx, 0x0000ffff00030d40 (200K | 65535<<32)
            gw25_orig = bytes.fromhex("48ba400d0300ffff0000")
            gw25_patch = bytes.fromhex("48ba00001000ffff0000")
            pos4_25 = g_bytes.find(gw25_orig)
            if pos4_25 == -1:
                pos4_25 = g_bytes.find(gw25_patch)
            if pos4_25 != -1:
                sites.append(("gateway.modelEntryToModelDetails (v2.25.0 default input 200K -> 1M)", g_off + pos4_25, gw25_orig, gw25_patch))

    if hyd_key:
        h_off, h_sz = funcs[hyd_key]
        h_bytes = data[h_off : h_off + h_sz]
        hyd_orig = bytes.fromhex("48c74740400d0300")
        hyd_patch = bytes.fromhex("48c7474000001000")
        pos_hyd = h_bytes.find(hyd_orig)
        if pos_hyd == -1:
            pos_hyd = h_bytes.find(hyd_patch)
        if pos_hyd != -1:
            sites.append(("gateway.HydrateModelEntryDefaults (v2.25.0 default input 200K -> 1M)", h_off + pos_hyd, hyd_orig, hyd_patch))

    # Site 22 (x64): utils.ClampCheckpointConfigForModelInfo (983,040 for 1M, preserves 200,000 for 200k under MaxTokenLimit in {0, 256000})
    clamp_key = next((k for k in funcs if k.endswith("/utils.ClampCheckpointConfigForModelInfo")), None)
    if clamp_key:
        cl_off, cl_sz = funcs[clamp_key]
        cl_bytes = data[cl_off : cl_off + cl_sz]
        cl_x64_orig = bytes.fromhex("83780c00751c486353144881fa409c00007f0531d290eb074881c2c063ffff89500c")
        cl_x64_patch = bytes.fromhex("8b500c85d2740881fa00e8030075138b531466837b16047e0681ea0000010089500c")
        pos_cl = _find_orig_or_patch(cl_bytes, cl_x64_orig, cl_x64_patch)
        if pos_cl != -1:
            sites.append(
                (
                    "Site 22 utils.ClampCheckpointConfigForModelInfo (983,040 for 1M, preserves 200k)",
                    cl_off + pos_cl,
                    cl_x64_orig,
                    cl_x64_patch,
                )
            )

    # Site 23 (x64): (*TruncationDecorator).truncateIfTooLarge trajectory byte cap (25MB 0x01900000 -> 256MB 0x10000000)
    td_key = next((k for k in funcs if k.endswith("(*TruncationDecorator).truncateIfTooLarge")), None)
    if td_key:
        td_off, td_sz = funcs[td_key]
        td_bytes = bytes(data[td_off : td_off + td_sz])
        td_orig = bytearray(td_bytes)
        td_patch = bytearray(td_bytes)
        x64_patterns = (
            (b"\x48\x3d", b""),
            (b"\x48\x81\xfa", b""),
            (b"\x48\x81\xf8", b""),
            (b"\x48\xc7\xc0", b""),
            (b"\x48\xb8", b"\x00\x00\x00\x00"),
            (b"\xb8", b""),
            (b"\x3d", b""),
        )
        spans = []
        for insn_prefix, insn_suffix in x64_patterns:
            p_orig = insn_prefix + b"\x00\x00\x90\x01" + insn_suffix
            p_patch = insn_prefix + b"\x00\x00\x00\x10" + insn_suffix
            idx = 0
            while True:
                i_orig = td_bytes.find(p_orig, idx)
                i_patch = td_bytes.find(p_patch, idx)
                cands = [x for x in (i_orig, i_patch) if x != -1]
                if not cands:
                    break
                pos_p = min(cands)
                # Guard against 1-byte opcode false-matching inside a REX instruction or 10-byte movabs immediate
                if insn_prefix in (b"\xb8", b"\x3d") and pos_p > 0 and 0x40 <= td_bytes[pos_p - 1] <= 0x4F:
                    idx = pos_p + 1
                    continue
                if any(
                    td_bytes[m] in (0x48, 0x49) and 0xB8 <= td_bytes[m + 1] <= 0xBF
                    for m in range(max(0, pos_p - 9), pos_p)
                ):
                    idx = pos_p + 1
                    continue
                spans.append((pos_p, pos_p + len(p_orig), p_orig, p_patch))
                idx = pos_p + len(p_orig)
        spans.sort(key=lambda s: s[0])
        non_overlapping = all(spans[i][1] <= spans[i + 1][0] for i in range(len(spans) - 1))
        if spans and non_overlapping and (td_sz < 512 or len(spans) in (4, 6)):
            for s_start, s_end, p_orig, p_patch in spans:
                td_orig[s_start:s_end] = p_orig
                td_patch[s_start:s_end] = p_patch
            sites.append(
                (
                    "Site 23 (*TruncationDecorator).truncateIfTooLarge (trajectory byte cap 25MB -> 256MB)",
                    td_off,
                    bytes(td_orig),
                    bytes(td_patch),
                )
            )

    return sites


def locate_media_compression_sites(data: bytes, funcs: dict):
    # v2.29.0+ introduces native media.smallerAsJPEG / media.compressedMedia /
    # media.CompressConfigFromProto / local.(*Backend).mediaCompressConfig, which
    # natively compares PNG vs JPEG encoded byte sizes and retires Sites 7-8.
    has_official_v229_media_compress = any(
        k.endswith("/media.smallerAsJPEG")
        or k.endswith("/media.CompressConfigFromProto")
        or k.endswith("(*Backend).mediaCompressConfig")
        for k in funcs
    )
    if has_official_v229_media_compress:
        return []

    sites = []
    # Site 7: media.DefaultCompressionConfig in .data (5MB/2576px/Q85 -> 512KB/1280px/Q80)
    cfg_orig = struct.pack("<QQQ", 5242880, 2576, 85)
    cfg_patch = struct.pack("<QQQ", 524288, 1280, 80)
    pos_cfg = data.find(cfg_orig)
    if pos_cfg == -1:
        pos_cfg = data.find(cfg_patch)
    if pos_cfg != -1:
        sites.append(("media.DefaultCompressionConfig (5MB/2576px -> 512KB/1280px/Q80)", pos_cfg, cfg_orig, cfg_patch))

    # Site 8: media.CompressMedia oversized PNG -> native JPEG transcode
    cm_key = next((k for k in funcs if k.endswith("/media.CompressMedia")), None)
    if cm_key:
        cm_off, cm_sz = funcs[cm_key]
        cm_bytes = data[cm_off : cm_off + cm_sz]
        if is_macho_arm64(data):
            # ARM64: at +0x388 inside media.CompressMedia: b.ne +0x3c (e1010054) -> b +0x120 (48000014)
            png_arm_orig = bytes.fromhex("e1010054")
            png_arm_patch = bytes.fromhex("48000014")
            rel = 0x388
            if cm_bytes[rel : rel + 4] in (png_arm_orig, png_arm_patch):
                sites.append(
                    ("media.CompressMedia (oversized PNG -> native JPEG Q80)", cm_off + rel, png_arm_orig, png_arm_patch)
                )
        else:
            png_orig = bytes.fromhex("49b8696d6167652f706e4c8b8c24e00000004d3901755041807908677549")
            png_patch = bytes.fromhex("49b8696d6167652f706e4c8b8c24e00000004d390175504180790867eb49")
            pos_png = cm_bytes.find(png_orig)
            if pos_png == -1:
                pos_png = cm_bytes.find(png_patch)
            if pos_png != -1:
                sites.append(("media.CompressMedia (oversized PNG -> native JPEG Q80)", cm_off + pos_png, png_orig, png_patch))

    return sites


def locate_utf8_and_uri_sites(data: bytes, funcs: dict):
    sites = []
    # Sites 9 & 10: json.(*Decoder).parseString invalid UTF-8 & unpaired UTF-16 surrogate tolerance
    ps_key = next((k for k in funcs if k.endswith("/json.(*Decoder).parseString")), None)
    if ps_key:
        ps_off, ps_sz = funcs[ps_key]
        ps_bytes = data[ps_off : ps_off + ps_sz]
        if is_macho_arm64(data):
            u8_arm_orig = bytes.fromhex("00350054")
            pos_u8 = _find_orig_or_patch(ps_bytes, u8_arm_orig, ARM64_NOP, 0x220)
            if pos_u8 != -1 and pos_u8 <= 0x230:
                sites.append(
                    ("json.(*Decoder).parseString (tolerate invalid UTF-8 in stream)", ps_off + pos_u8, u8_arm_orig, ARM64_NOP)
                )
            sr_arm_orig = bytes.fromhex("a0140054")
            pos_sr = _find_orig_or_patch(ps_bytes, sr_arm_orig, ARM64_NOP, 0x678)
            if pos_sr != -1 and pos_sr <= 0x688:
                sites.append(
                    ("json.(*Decoder).parseString (tolerate unpaired UTF-16 surrogate)", ps_off + pos_sr, sr_arm_orig, ARM64_NOP)
                )
            return sites

        u8_orig = bytes.fromhex("81fefdff0000750f0f1f4400004883ff010f8409090000")
        u8_patch = bytes.fromhex("81fefdff0000750f0f1f4400004883ff01909090909090")
        pos_u8 = ps_bytes.find(u8_orig)
        if pos_u8 == -1:
            pos_u8 = ps_bytes.find(u8_patch)
        if pos_u8 != -1:
            sites.append(("json.(*Decoder).parseString (tolerate invalid UTF-8 in stream)", ps_off + pos_u8, u8_orig, u8_patch))

        sr_orig = bytes.fromhex("81fafdff00000f84790300004885db0f8570030000")
        sr_patch = bytes.fromhex("81fafdff00009090909090904885db0f8570030000")
        pos_sr = ps_bytes.find(sr_orig)
        if pos_sr == -1:
            pos_sr = ps_bytes.find(sr_patch)
        if pos_sr != -1:
            sites.append(("json.(*Decoder).parseString (tolerate unpaired UTF-16 surrogate)", ps_off + pos_sr, sr_orig, sr_patch))

    # Site 11: local.pathFromFile Windows 2-slash file://D:/ URI fix ("//%s%s" len 6 -> "%s%s" len 4)
    pf_key = next((k for k in funcs if k.endswith("/local.pathFromFile")), None)
    if pf_key:
        pf_off, pf_sz = funcs[pf_key]
        pf_bytes = data[pf_off : pf_off + pf_sz]
        tail_ctx = bytes.fromhex("488d4c2450bf02000000")
        idx_tail = pf_bytes.find(tail_ctx)
        if idx_tail >= 12 and pf_bytes[idx_tail - 12 : idx_tail - 9] == b"\x48\x8d\x05":
            site_off = pf_off + idx_tail - 12
            disp_val = struct.unpack_from("<i", pf_bytes, idx_tail - 9)[0]
            len_op = pf_bytes[idx_tail - 5 : idx_tail]
            if len_op == b"\xbb\x06\x00\x00\x00":
                orig_disp = disp_val
            elif len_op == b"\xbb\x04\x00\x00\x00":
                orig_disp = disp_val - 2
            else:
                orig_disp = None
            if orig_disp is not None:
                pf_orig = b"\x48\x8d\x05" + struct.pack("<i", orig_disp) + b"\xbb\x06\x00\x00\x00"
                pf_patch = b"\x48\x8d\x05" + struct.pack("<i", orig_disp + 2) + b"\xbb\x04\x00\x00\x00"
                sites.append(("local.pathFromFile (Windows 2-slash file://D:/ -> local drive)", site_off, pf_orig, pf_patch))

    return sites


def locate_stream_retry_sites(data: bytes, funcs: dict):
    sites = []
    ir_key = next((k for k in funcs if k.endswith("/utils.IsRetryableAPIError")), None)
    if not ir_key:
        return sites
    ir_off, ir_sz = funcs[ir_key]
    ir_bytes = data[ir_off : ir_off + ir_sz]

    if is_macho_arm64(data):
        sections = parse_macho_sections(data)
        fn_va = macho_raw_to_va(sections, ir_off)
        # Site 13: unexpected EOF suffix match
        s13_arm_orig = bytes.fromhex("42f00c91630480d2")
        s13_arm_patch = bytes.fromhex("42440d91c30180d2")
        pos13 = _find_orig_or_patch(ir_bytes, s13_arm_orig, s13_arm_patch)
        if pos13 != -1:
            sites.append(
                (
                    "utils.IsRetryableAPIError ('unexpected EOF' mid-stream auto-retry)",
                    ir_off + pos13,
                    s13_arm_orig,
                    s13_arm_patch,
                )
            )
        else:
            for i in range(0, len(ir_bytes) - 12, 4):
                w1, w2, w3 = struct.unpack_from("<III", ir_bytes, i)
                # adrp x2, page; add x2, x2, #imm; mov x3, #35 (or #14)
                if (w1 & 0x9F00001F) == 0x90000002 and (w2 & 0xFFC003FF) == 0x91000042 and w3 in (0xD2800463, 0xD28001C3):
                    immlo = (w1 >> 29) & 0x3
                    immhi = (w1 >> 5) & 0x7FFFF
                    imm = ((immhi << 2) | immlo) << 12
                    if imm & (1 << 32):
                        imm -= 1 << 33
                    page = ((fn_va + i) & ~0xFFF) + imm
                    add_imm = (w2 >> 10) & 0xFFF
                    orig_add_imm = add_imm if w3 == 0xD2800463 else (add_imm - 21)
                    str_raw = macho_va_to_raw(sections, page + orig_add_imm)
                    if str_raw is not None and data[str_raw : str_raw + 35] == b"incomplete envelope: unexpected EOF":
                        w2_orig = (w2 & 0xFFC003FF) | ((orig_add_imm & 0xFFF) << 10)
                        w2_patch = (w2 & 0xFFC003FF) | (((orig_add_imm + 21) & 0xFFF) << 10)
                        dyn13_orig = struct.pack("<II", w2_orig, 0xD2800463)
                        dyn13_patch = struct.pack("<II", w2_patch, 0xD28001C3)
                        sites.append(
                            (
                                "utils.IsRetryableAPIError ('unexpected EOF' mid-stream auto-retry)",
                                ir_off + i + 4,
                                dyn13_orig,
                                dyn13_patch,
                            )
                        )
                        break

        # Site 14: retryableSubstrings table in Mach-O __DATA
        arr_raw = None
        arr_len = 0
        for i in range(0, len(ir_bytes) - 8, 4):
            w1, w2 = struct.unpack_from("<II", ir_bytes, i)
            if (w1 & 0x9F000000) == 0x90000000 and (w2 & 0xFF800000) == 0x91000000:
                rd1 = w1 & 0x1F
                rn2 = (w2 >> 5) & 0x1F
                if rd1 != rn2:
                    continue
                immlo = (w1 >> 29) & 0x3
                immhi = (w1 >> 5) & 0x7FFFF
                imm = ((immhi << 2) | immlo) << 12
                if imm & (1 << 32):
                    imm -= 1 << 33
                page = ((fn_va + i) & ~0xFFF) + imm
                add_imm = (w2 >> 10) & 0xFFF
                hdr_va = page + add_imm
                hdr_raw = macho_va_to_raw(sections, hdr_va)
                if hdr_raw is not None and hdr_raw + 24 <= len(data):
                    cand_va, cand_len, cand_cap = struct.unpack_from("<QQQ", data, hdr_raw)
                    if cand_len == cand_cap and cand_len in (7, 15, 16):
                        cand_arr_raw = macho_va_to_raw(sections, cand_va)
                        if cand_arr_raw is not None:
                            arr_raw = cand_arr_raw
                            arr_len = cand_len
                            break

        if arr_raw is not None and arr_len == 7:
            entries = [struct.unpack_from("<QQ", data, arr_raw + i * 16) for i in range(7)]
            va2, len2 = entries[2]
            va3, len3 = entries[3]
            va4, len4 = entries[4]
            va6, len6 = entries[6]
            orig_va4 = va4 if len4 == 32 else (va4 - 22)
            va_wsasend = macho_raw_to_va(sections, data.find(b"wsasendwsarecv"))
            va_crbp = macho_raw_to_va(sections, data.find(b"connection reset by peer"))
            va_sre = macho_raw_to_va(sections, data.find(b"stream reading error: %w"))
            va_se = macho_raw_to_va(sections, data.find(b"stream error: stream ID "))
            va_unauth = macho_raw_to_va(sections, data.find(b"unauthenticated"))

            s14_orig = b"".join(
                struct.pack("<QQ", v, l)
                for v, l in [
                    (va_wsasend, 7),
                    (va_wsasend + 7, 7),
                    (va2, len2),
                    (va3, len3),
                    (orig_va4, 32),
                    (va_crbp, 24),
                    (va6, len6),
                ]
            )
            s14_patch = b"".join(
                struct.pack("<QQ", v, l)
                for v, l in [
                    (va_sre, 21),        # "stream reading error:"
                    (va_se, 13),         # "stream error:"
                    (va2, len2),         # "no such host"
                    (va3, len3),         # "bad record MAC"
                    (orig_va4 + 22, 10), # "connection"
                    (va_unauth, 15),     # "unauthenticated"
                    (va6, len6),         # "i/o timeout"
                ]
            )
            sites.append(
                ("utils.IsRetryableAPIError (retryableSubstrings table expansion)", arr_raw, s14_orig, s14_patch)
            )
        elif arr_raw is not None and arr_len in (15, 16):
            # v2.26.0+ / v2.31.0+ ARM64: 15/16-slot lowercase table after strings.ToLower
            entries = [list(struct.unpack_from("<QQ", data, arr_raw + i * 16)) for i in range(arr_len)]
            va0, _ = entries[0]
            va4, len4 = entries[4]
            orig_va4 = va4 if len4 == 32 else (va4 - 22)
            va12, len12 = entries[12]
            orig_va12 = va12 if len12 == 19 else (va12 - 10)
            va13, _ = entries[13]
            va_last, len_last = entries[arr_len - 1]
            orig_va_last = va_last if len_last == 11 else (va_last - 4)

            orig_va5 = macho_raw_to_va(sections, _require_bytes(data, b"connection reset by peer"))
            orig_va7 = macho_raw_to_va(sections, _require_bytes(data, b"software caused connection abort"))
            orig_va11 = macho_raw_to_va(sections, _require_bytes(data, b"connection timed out"))
            va_sre = macho_raw_to_va(sections, _require_bytes(data, b"stream reading error: %w"))
            va_unauth_lower = macho_raw_to_va(sections, _require_bytes(data, b"unauthenticated"))
            va_eof = macho_raw_to_va(sections, _require_bytes(data, b"oneof_index") + 2)
            va_proxyconn = macho_raw_to_va(sections, _require_bytes(data, b"proxyconnect"))
            va_deadline = macho_raw_to_va(sections, _require_bytes(data, b"context deadline exceeded") + 8)

            orig_entries = [list(e) for e in entries]
            orig_entries[0] = [va0, 7]
            orig_entries[1] = [va0 + 7, 7]
            orig_entries[4] = [orig_va4, 32]
            orig_entries[5] = [orig_va5, 24]
            orig_entries[7] = [orig_va7, 32]
            orig_entries[11] = [orig_va11, 20]
            orig_entries[12] = [orig_va12, 19]
            orig_entries[13] = [va13, 29]
            if arr_len == 16:
                orig_va14 = macho_raw_to_va(sections, _require_bytes(data, b"http2: timeout awaiting response headers"))
                orig_entries[14] = [orig_va14, 40]
            orig_entries[arr_len - 1] = [orig_va_last, 11]

            patch_entries = [list(e) for e in entries]
            patch_entries[0] = [va0, 3]                   # "wsa"
            patch_entries[1] = [va_sre, 7]                # "stream "
            patch_entries[4] = [orig_va4 + 22, 10]        # "connection"
            patch_entries[5] = [va_unauth_lower, 15]      # "unauthenticated"
            patch_entries[7] = [va_eof, 3]                # "eof"
            patch_entries[11] = [va_proxyconn, 12]        # "proxyconnect"
            patch_entries[12] = [orig_va12 + 10, 9]       # "timed out"
            patch_entries[13] = [va13, 6]                 # "http2:"
            if arr_len == 16:
                patch_entries[14] = [va_deadline, 17]     # "deadline exceeded"
            patch_entries[arr_len - 1] = [orig_va_last + 4, 7]  # "timeout" (covers TLS handshake timeout, i/o timeout)

            s14_26_orig = b"".join(struct.pack("<QQ", v, l) for v, l in orig_entries)
            s14_26_patch = b"".join(struct.pack("<QQ", v, l) for v, l in patch_entries)
            sites.append(
                (f"utils.IsRetryableAPIError (v2.26.0+ {arr_len}-slot retryableSubstrings expansion)", arr_raw, s14_26_orig, s14_26_patch)
            )
        return sites

    image_base, sections = parse_pe_sections(data)

    # Site 13: utils.IsRetryableAPIError ("incomplete envelope: unexpected EOF" len 35 -> "unexpected EOF" len 14)
    s13_ctx = bytes.fromhex("488b8c2448010000488b4918488b842450010000ffd1488d0d")
    idx13 = ir_bytes.find(s13_ctx)
    if idx13 != -1:
        site13_off = ir_off + idx13 + 22
        disp_val = struct.unpack_from("<i", data, site13_off + 3)[0]
        len_op = data[site13_off + 7 : site13_off + 12]
        if len_op == b"\xbf\x23\x00\x00\x00":
            orig_disp = disp_val
        elif len_op == b"\xbf\x0e\x00\x00\x00":
            orig_disp = disp_val - 21
        else:
            orig_disp = None
        if orig_disp is not None:
            s13_orig = b"\x48\x8d\x0d" + struct.pack("<i", orig_disp) + b"\xbf\x23\x00\x00\x00"
            s13_patch = b"\x48\x8d\x0d" + struct.pack("<i", orig_disp + 21) + b"\xbf\x0e\x00\x00\x00"
            sites.append(("utils.IsRetryableAPIError ('unexpected EOF' mid-stream auto-retry)", site13_off, s13_orig, s13_patch))

    # Site 14: utils.IsRetryableAPIError retryableSubstrings .data table expansion
    s14_ctx = bytes.fromhex("48898424d000000048895c2468488b0d")
    idx14 = ir_bytes.find(s14_ctx)
    if idx14 != -1:
        lea_pos = ir_off + idx14 + 13
        disp14 = struct.unpack_from("<i", data, lea_pos + 3)[0]
        hdr_rva = raw_to_rva(sections, lea_pos) + 7 + disp14
        hdr_raw = rva_to_raw(sections, hdr_rva)
        if hdr_raw is not None:
            arr_va, arr_len, _ = struct.unpack_from("<QQQ", data, hdr_raw)
            arr_raw = rva_to_raw(sections, arr_va - image_base)
            if arr_raw is not None and arr_len == 7:
                entries = [struct.unpack_from("<QQ", data, arr_raw + i * 16) for i in range(7)]
                va0, _ = entries[0]
                va2, len2 = entries[2]
                va3, len3 = entries[3]
                va4, len4 = entries[4]
                va6, len6 = entries[6]
                orig_va4 = va4 if len4 == 32 else (va4 - 22)
                raw_crbp = data.find(b"connection reset by peer")
                orig_va5 = image_base + raw_to_rva(sections, raw_crbp)
                va_sre = image_base + raw_to_rva(sections, data.find(b"stream reading error: %w"))
                va_se = image_base + raw_to_rva(sections, data.find(b"stream error: stream ID "))

                s14_orig = b"".join(
                    struct.pack("<QQ", v, l)
                    for v, l in [
                        (va0, 7),
                        (va0 + 7, 7),
                        (va2, len2),
                        (va3, len3),
                        (orig_va4, 32),
                        (orig_va5, 24),
                        (va6, len6),
                    ]
                )
                s14_patch = b"".join(
                    struct.pack("<QQ", v, l)
                    for v, l in [
                        (va0, 3),            # "wsa" (matches wsasend, wsarecv, wsaconnect)
                        (va_sre, 21),        # "stream reading error:"
                        (va2, len2),         # "no such host"
                        (va3, len3),         # "bad record MAC"
                        (orig_va4 + 22, 10), # "connection" (matches closed network connection, connection reset, client connection lost)
                        (va_se, 13),         # "stream error:"
                        (va6, len6),         # "i/o timeout"
                    ]
                )
                sites.append(("utils.IsRetryableAPIError (retryableSubstrings table expansion)", arr_raw, s14_orig, s14_patch))
            elif arr_raw is not None and arr_len in (15, 16):
                # v2.26.0–v2.31.0+: 15/16-slot lowercase table after strings.ToLower
                entries = [list(struct.unpack_from("<QQ", data, arr_raw + i * 16)) for i in range(arr_len)]
                va0, _ = entries[0]
                va4, len4 = entries[4]
                orig_va4 = va4 if len4 == 32 else (va4 - 22)
                va12, len12 = entries[12]
                orig_va12 = va12 if len12 == 19 else (va12 - 10)
                va13, _ = entries[13]
                va_last, len_last = entries[arr_len - 1]
                orig_va_last = va_last if len_last == 11 else (va_last - 4)

                orig_va5 = image_base + raw_to_rva(sections, _require_bytes(data, b"connection reset by peer"))
                orig_va7 = image_base + raw_to_rva(sections, _require_bytes(data, b"software caused connection abort"))
                orig_va11 = image_base + raw_to_rva(sections, _require_bytes(data, b"connection timed out"))
                va_sre = image_base + raw_to_rva(sections, _require_bytes(data, b"stream reading error: %w"))
                va_unauth_lower = image_base + raw_to_rva(sections, _require_bytes(data, b"unauthenticated"))
                va_eof = image_base + raw_to_rva(sections, _require_bytes(data, b"oneof_index") + 2)
                va_proxyconn = image_base + raw_to_rva(sections, _require_bytes(data, b"proxyconnect"))
                va_deadline = image_base + raw_to_rva(sections, _require_bytes(data, b"context deadline exceeded") + 8)

                orig_entries = [list(e) for e in entries]
                orig_entries[0] = [va0, 7]
                orig_entries[1] = [va0 + 7, 7]
                orig_entries[4] = [orig_va4, 32]
                orig_entries[5] = [orig_va5, 24]
                orig_entries[7] = [orig_va7, 32]
                orig_entries[11] = [orig_va11, 20]
                orig_entries[12] = [orig_va12, 19]
                orig_entries[13] = [va13, 29]
                if arr_len == 16:
                    orig_va14 = image_base + raw_to_rva(sections, _require_bytes(data, b"http2: timeout awaiting response headers"))
                    orig_entries[14] = [orig_va14, 40]
                orig_entries[arr_len - 1] = [orig_va_last, 11]

                patch_entries = [list(e) for e in entries]
                patch_entries[0] = [va0, 3]                   # "wsa"
                patch_entries[1] = [va_sre, 7]                # "stream "
                patch_entries[4] = [orig_va4 + 22, 10]        # "connection"
                patch_entries[5] = [va_unauth_lower, 15]      # "unauthenticated"
                patch_entries[7] = [va_eof, 3]                # "eof"
                patch_entries[11] = [va_proxyconn, 12]        # "proxyconnect"
                patch_entries[12] = [orig_va12 + 10, 9]       # "timed out"
                patch_entries[13] = [va13, 6]                 # "http2:"
                if arr_len == 16:
                    patch_entries[14] = [va_deadline, 17]     # "deadline exceeded"
                patch_entries[arr_len - 1] = [orig_va_last + 4, 7]  # "timeout" (covers TLS handshake timeout, i/o timeout)

                s14_26_orig = b"".join(struct.pack("<QQ", v, l) for v, l in orig_entries)
                s14_26_patch = b"".join(struct.pack("<QQ", v, l) for v, l in patch_entries)
                sites.append((f"utils.IsRetryableAPIError (v2.26.0+ {arr_len}-slot retryableSubstrings expansion)", arr_raw, s14_26_orig, s14_26_patch))

    return sites


def locate_bg_task_timeout_sites(data: bytes, funcs: dict):
    sites = []
    # Site 15: (*RunCommandHandler).runTaskListeners non-daemon 60s default NotificationTimeoutSeconds wakeup
    rtl_key = next((k for k in funcs if k.endswith("/handlers.(*RunCommandHandler).runTaskListeners")), None)
    if rtl_key:
        rtl_off, rtl_sz = funcs[rtl_key]
        rtl_bytes = data[rtl_off : rtl_off + rtl_sz]
        if is_macho_arm64(data):
            rtl_arm_orig = bytes.fromhex(
                "650300b4260040f9c60200b4c64c40f9860200b4c600403986000037e3031faae6031faa14000014"
            )
            rtl_arm_patch = bytes.fromhex(
                "040300b486a04139c6020035bf0000f1cc000054850780d2e54b00f9030000141f2003d51f2003d5"
            )
            pos_rtl = _find_orig_or_patch(rtl_bytes, rtl_arm_orig, rtl_arm_patch)
            if pos_rtl != -1:
                sites.append(
                    (
                        "RunCommandHandler.runTaskListeners (non-daemon 60s default timeout wakeup)",
                        rtl_off + pos_rtl,
                        rtl_arm_orig,
                        rtl_arm_patch,
                    )
                )
            else:
                # v2.29.0 ARM64: x1=cmdSpec, x2=timeoutSec (at [sp, #0x88])
                rtl29_arm_orig = bytes.fromhex(
                    "820300b4e53741f9a60040f9c60200b4c64c40f9860200b4c600403986000037e3031faae60340b214000014"
                )
                rtl29_arm_patch = bytes.fromhex(
                    "5f0000f14c010054e10200b426a04139a6020035820780d2e24700f9040000141f2003d51f2003d51f2003d5"
                )
                pos_rtl29 = _find_orig_or_patch(rtl_bytes, rtl29_arm_orig, rtl29_arm_patch)
                if pos_rtl29 != -1:
                    sites.append(
                        (
                            "RunCommandHandler.runTaskListeners (v2.29.0 non-daemon 60s default timeout wakeup)",
                            rtl_off + pos_rtl29,
                            rtl29_arm_orig,
                            rtl29_arm_patch,
                        )
                    )
        else:
            rtl_orig = bytes.fromhex(
                "4885f60f849e000000488b394885ff0f8482000000488bbf98000000"
                "4885ff7471803f0075104885dbbf0000000041b800000000eb7f"
            )
            rtl_patch = bytes.fromhex(
                "4885f67f314885db0f8499000000807b68000f858f000000be3c000000"
                "4889b42480000000eb0f909090909090909090909090909090"
            )
            pos_rtl = rtl_bytes.find(rtl_orig)
            if pos_rtl == -1:
                pos_rtl = rtl_bytes.find(rtl_patch)
            if pos_rtl != -1:
                sites.append((
                    "RunCommandHandler.runTaskListeners (non-daemon 60s default timeout wakeup)",
                    rtl_off + pos_rtl,
                    rtl_orig,
                    rtl_patch,
                ))
            else:
                # v2.26.0: 57-byte block (rcx=timeoutSec, rdx=cmdSpec)
                rtl26_orig = bytes.fromhex(
                    "4885c90f8494000000488b9c2460020000488b334885f67475"
                    "488bb6980000004885f67464803e00750f4885d2be00000000bf01000000eb71"
                )
                rtl26_patch = bytes.fromhex(
                    "4885c97f344885d20f848f000000807a68000f8585000000b93c000000"
                    "48898c2480000000eb12909090909090909090909090909090909090"
                )
                pos_rtl26 = rtl_bytes.find(rtl26_orig)
                if pos_rtl26 == -1:
                    pos_rtl26 = rtl_bytes.find(rtl26_patch)
                if pos_rtl26 != -1:
                    sites.append((
                        "RunCommandHandler.runTaskListeners (v2.26.0 non-daemon 60s default timeout wakeup)",
                        rtl_off + pos_rtl26,
                        rtl26_orig,
                        rtl26_patch,
                    ))

    # Site 16: (*RunCommandTool).runCommand non-daemon 60s default NotificationTimeoutSeconds wakeup
    rc_key = next((k for k in funcs if k.endswith("/tools.(*RunCommandTool).runCommand")), None)
    if rc_key:
        rc_off, rc_sz = funcs[rc_key]
        rc_bytes = data[rc_off : rc_off + rc_sz]
        if is_macho_arm64(data):
            rc_arm_orig = bytes.fromhex(
                "e14342f9221c40f9034099d24373a7f2607c029be23742f943a0403963000036e0031faa1c000014"
                "420040f9220300b4424c40f9e20200b442004039620200361f0000f16c000054e0031faa12000014"
            )
            rc_arm_patch = bytes.fromhex(
                "e14342f922c0403942040035221c40f95f0000f16c000054820780d2221c00f9034099d24373a7f2"
                "607c029be23742f943a04039e3000036e0031faa160000141f2003d51f2003d51f2003d51f2003d5"
            )
            pos_rc = _find_orig_or_patch(rc_bytes, rc_arm_orig, rc_arm_patch)
            if pos_rc != -1:
                sites.append(
                    (
                        "RunCommandTool.runCommand (non-daemon 60s default timeout wakeup)",
                        rc_off + pos_rc,
                        rc_arm_orig,
                        rc_arm_patch,
                    )
                )
        else:
            for stack_disp_hex in ("68040000", "80040000"):
                rc_orig = bytes.fromhex(
                    "488b094885c90f8488000000488b89980000004885c9747c8039007473"
                    f"488b8c24{stack_disp_hex}488b51384869c200ca9a3b4885c07f0431c0eb5d6690"
                )
                rc_patch = bytes.fromhex(
                    f"488b8c24{stack_disp_hex}807930000f8582000000488b51384885d27f09ba3c000000"
                    "488951384869c200ca9a3beb0e9090909090909090909090909090"
                )
                pos_rc = rc_bytes.find(rc_orig)
                if pos_rc == -1:
                    pos_rc = rc_bytes.find(rc_patch)
                if pos_rc != -1:
                    sites.append((
                        f"RunCommandTool.runCommand (non-daemon 60s default timeout wakeup, disp=0x{stack_disp_hex})",
                        rc_off + pos_rc,
                        rc_orig,
                        rc_patch,
                    ))
                    break

    if is_macho_arm64(data):
        # Site 16 (v2.28.0+ ARM64): (*runningCommand).waitInBackground non-daemon 60s default NotificationTimeoutSeconds wakeup
        wib_key = next((k for k in funcs if k.endswith("/tools.(*runningCommand).waitInBackground")), None)
        if wib_key:
            wib_off, wib_sz = funcs[wib_key]
            wib_bytes = data[wib_off : wib_off + wib_sz]
            wib_arm_orig = bytes.fromhex(
                "631c40f9044099d24473a7f2637c049b44a0403984000036e2031faae30340b21e000014"
                "420040f9420300b4424c40f9020300b442004039620200367f0000f18c000054e2031faae30340b213000014"
            )
            wib_arm_patch = bytes.fromhex(
                "64c0403904040035641c40f99f0000f16c000054840780d2641c00f9054099d24573a7f2"
                "837c059b44a04039c4020037" + ("1f2003d5" * 8)
            )
            # RunCommandTool's feature flag moved from +0x28 to +0x50 (v2.30)
            # and +0x48 (v2.31). The surrounding 80-byte control flow is unchanged.
            wib_arm_candidates = [
                (
                    wib_arm_orig.replace(bytes.fromhex("44a04039"), bytes.fromhex(flag_load)),
                    wib_arm_patch.replace(bytes.fromhex("44a04039"), bytes.fromhex(flag_load)),
                )
                for flag_load in ("44204139", "44404139", "44a04039")
            ]
            pos_wib = -1
            for candidate_orig, candidate_patch in wib_arm_candidates:
                pos_wib = _find_orig_or_patch(wib_bytes, candidate_orig, candidate_patch)
                if pos_wib != -1:
                    wib_arm_orig, wib_arm_patch = candidate_orig, candidate_patch
                    break
            if pos_wib != -1:
                sites.append(
                    (
                        "runningCommand.waitInBackground (v2.28.0+ non-daemon 60s default timeout wakeup)",
                        wib_off + pos_wib,
                        wib_arm_orig,
                        wib_arm_patch,
                    )
                )

        # Site 19 (ARM64): (*AgentExecutor).transitionFromRunningToIdleOrClosed
        tr_key = next((k for k in funcs if k.endswith("/agentexecutor.(*AgentExecutor).transitionFromRunningToIdleOrClosed")), None)
        if tr_key:
            tr_off, tr_sz = funcs[tr_key]
            tr_bytes = data[tr_off : tr_off + tr_sz]
            tr_arm_candidates = [
                # v2.31+: PostInvocationEvaluator arguments at [sp, #0xe0];
                # ldrb w7, [x6, #0x1a]; tbz w7 -> b (same target, +0xb0).
                (
                    bytes.fromhex("e67340f9c768403987050036a4144ba9"),
                    bytes.fromhex("e67340f9c76840392c000014a4144ba9"),
                ),
                (
                    bytes.fromhex("e167433981050036a4144ba9"),
                    bytes.fromhex("e16743392c000014a4144ba9"),
                ),
            ]
            for tr_arm_orig, tr_arm_patch in tr_arm_candidates:
                pos_tr = _find_orig_or_patch(tr_bytes, tr_arm_orig, tr_arm_patch)
                if pos_tr != -1:
                    sites.append(
                        (
                            "Site 19 (*AgentExecutor).transitionFromRunningToIdleOrClosed (always recheck inbox)",
                            tr_off + pos_tr,
                            tr_arm_orig,
                            tr_arm_patch,
                        )
                    )
                    break

        # Site 20 (ARM64): (*AgentExecutor).startMessageWatcherLocked.func2
        mw2_key = next((k for k in funcs if k.endswith("/agentexecutor.(*AgentExecutor).startMessageWatcherLocked.func2")), None)
        if mw2_key:
            mw2_off, mw2_sz = funcs[mw2_key]
            mw2_bytes = data[mw2_off : mw2_off + mw2_sz]
            mw2_arm_orig = bytes.fromhex("e00f4139c0010036e0874ba9")
            mw2_arm_patch = bytes.fromhex("e00f41391f2003d5e0874ba9")
            pos_mw2 = _find_orig_or_patch(mw2_bytes, mw2_arm_orig, mw2_arm_patch)
            if pos_mw2 != -1:
                sites.append(
                    (
                        "Site 20 (*AgentExecutor).startMessageWatcherLocked.func2 (unblock NotifyCh outer gate)",
                        mw2_off + pos_mw2,
                        mw2_arm_orig,
                        mw2_arm_patch,
                    )
                )

        # Site 21 (ARM64): (*AgentExecutor).startMessageWatcherLocked.func2.1-range1
        mwr_key = next((k for k in funcs if k.endswith("/agentexecutor.(*AgentExecutor).startMessageWatcherLocked.func2.1-range1")), None)
        if mwr_key:
            mwr_off, mwr_sz = funcs[mwr_key]
            mwr_bytes = data[mwr_off : mwr_off + mwr_sz]
            for ldr_x2_hex in ("e21f40f9", "e22740f9"):
                mwr_tail_orig = bytes.fromhex(f"{ldr_x2_hex}4300403903010037e0000036")
                mwr_arm_patch = bytes.fromhex(f"e00340b2{ldr_x2_hex}430040391f2003d5e0000036")
                pos_tail = mwr_bytes.find(mwr_tail_orig)
                if pos_tail >= 4 and (struct.unpack_from("<I", mwr_bytes, pos_tail - 4)[0] >> 26) == 0b100101:
                    pos_mwr = pos_tail - 4
                    mwr_arm_orig = bytes(mwr_bytes[pos_mwr : pos_mwr + 20])
                else:
                    pos_mwr = mwr_bytes.find(mwr_arm_patch)
                    sub_idle_key = next((k for k in funcs if k.endswith(".(*AgentState).SubagentsAndTasksIdle")), None)
                    if sub_idle_key and pos_mwr != -1:
                        delta_words = (funcs[sub_idle_key][0] - (mwr_off + pos_mwr)) // 4
                        bl_word = 0x94000000 | (delta_words & 0x03FFFFFF)
                        bl_orig = struct.pack("<I", bl_word)
                    else:
                        bl_orig = bytes.fromhex("4c67e197")
                    mwr_arm_orig = bl_orig + mwr_tail_orig
                if pos_mwr != -1:
                    sites.append(
                        (
                            "Site 21 (*AgentExecutor).startMessageWatcherLocked.func2.1-range1 (SubagentsAndTasksIdle -> 1)",
                            mwr_off + pos_mwr,
                            mwr_arm_orig,
                            mwr_arm_patch,
                        )
                    )
                    break
        return sites

    # Site 16 (v2.28.0+ x64): (*runningCommand).waitInBackground non-daemon 60s default NotificationTimeoutSeconds wakeup
    wib_key = next((k for k in funcs if k.endswith("/tools.(*runningCommand).waitInBackground")), None)
    if wib_key:
        wib_off, wib_sz = funcs[wib_key]
        wib_bytes = data[wib_off : wib_off + wib_sz]
        wib_orig_v228 = bytes.fromhex(
            "488b8c2420020000488b59088403488b31807e2800740c31d2bb01000000e98d000000"
            "488b164885d2747e488b92980000004885d27472803a007464488b53384869d200ca9a3b"
            "4885d27f0931d2bb01000000eb5b4889d0"
        )
        wib_orig_v230 = bytes.fromhex(
            "488b8c2420020000488b59088403488b31807e5000740c31d2bb01000000e98d000000"
            "488b164885d2747e488b92980000004885d27472803a007464488b53384869d200ca9a3b"
            "4885d27f0931d2bb01000000eb5b4889d0"
        )
        wib_orig_v231 = bytes.fromhex(
            "488b8c2420020000488b59088403488b31807e4800740c31d2bb01000000e98d000000"
            "488b164885d2747e488b92980000004885d27472803a007464488b53384869d200ca9a3b"
            "4885d27f0931d2bb01000000eb5b4889d0"
        )
        wib_patch = bytes.fromhex(
            "488b8c2420020000488b5908807b30000f8593000000488b53384885d27f09ba3c000000"
            "488953384869c200ca9a3beb27" + ("90" * 39)
        )
        if wib_orig_v231 in wib_bytes:
            wib_orig = wib_orig_v231
        elif wib_orig_v230 in wib_bytes:
            wib_orig = wib_orig_v230
        else:
            wib_orig = wib_orig_v228
        pos_wib = wib_bytes.find(wib_orig)
        if pos_wib == -1:
            pos_wib = wib_bytes.find(wib_patch)
        if pos_wib != -1:
            sites.append((
                "runningCommand.waitInBackground (v2.28.0 non-daemon 60s default timeout wakeup)",
                wib_off + pos_wib,
                wib_orig,
                wib_patch,
            ))

    # Site 19 (x64): (*AgentExecutor).transitionFromRunningToIdleOrClosed
    tr_key = next((k for k in funcs if k.endswith("/agentexecutor.(*AgentExecutor).transitionFromRunningToIdleOrClosed")), None)
    if tr_key:
        tr_off, tr_sz = funcs[tr_key]
        tr_bytes = data[tr_off : tr_off + tr_sz]
        tr_candidates = [
            # v2.31.0+: mov r8, [rsp+0xd8]; cmp byte ptr [r8+0x1a], 0; nop dword ptr [rax]; jz +0xcb -> jmp +0xcc; nop
            (
                bytes.fromhex("4c8b8424d80000004180781a000f1f000f84cb000000"),
                bytes.fromhex("4c8b8424d80000004180781a000f1f00e9cc00000090"),
            ),
            # v2.30.0+: movzx r8d, byte ptr [rsp+0xca]; jz +0xcb -> jmp +0xcc; nop
            (
                bytes.fromhex("440fb68424ca0000004584c00f84cb000000"),
                bytes.fromhex("440fb68424ca0000004584c0e9cc00000090"),
            ),
            # v2.29.0+: jz +0xcb -> jmp +0xcc; nop
            (
                bytes.fromhex("440fb68424c90000004584c00f84cb000000"),
                bytes.fromhex("440fb68424c90000004584c0e9cc00000090"),
            ),
            # v2.28.x: jz +0xcc -> jmp +0xcd; nop
            (
                bytes.fromhex("440fb68424c90000004584c00f84cc000000"),
                bytes.fromhex("440fb68424c90000004584c0e9cd00000090"),
            ),
        ]
        for tr_orig, tr_patch in tr_candidates:
            pos_tr = _find_orig_or_patch(tr_bytes, tr_orig, tr_patch)
            if pos_tr != -1:
                sites.append((
                    "AgentExecutor.transitionFromRunningToIdleOrClosed (always restart message watcher on Idle)",
                    tr_off + pos_tr,
                    tr_orig,
                    tr_patch,
                ))
                break

    # Site 20 (x64): (*AgentExecutor).startMessageWatcherLocked.func2
    mw2_key = next((k for k in funcs if k.endswith("/agentexecutor.(*AgentExecutor).startMessageWatcherLocked.func2")), None)
    if mw2_key:
        mw2_off, mw2_sz = funcs[mw2_key]
        mw2_bytes = data[mw2_off : mw2_off + mw2_sz]
        mw2_candidates = [
            # v2.29.0+: movzx edx, byte ptr [rsp+0x33]; test dl, dl; jz +0x41; mov rax, [rsp+0xa8]
            (
                bytes.fromhex("0fb654243384d27441488b8424a8000000"),
                bytes.fromhex("0fb654243384d29090488b8424a8000000"),
            ),
            # v2.28.x: movzx ecx, byte ptr [rsp+0x33]; test cl, cl; jz +0x41; mov rax, [rsp+0xa8]
            (
                bytes.fromhex("0fb64c243384c97441488b8424a8000000"),
                bytes.fromhex("0fb64c243384c99090488b8424a8000000"),
            ),
        ]
        for mw2_orig, mw2_patch in mw2_candidates:
            pos_mw2 = _find_orig_or_patch(mw2_bytes, mw2_orig, mw2_patch)
            if pos_mw2 != -1:
                sites.append((
                    "AgentExecutor.startMessageWatcherLocked.func2 (force immediate shouldWakeUp check)",
                    mw2_off + pos_mw2,
                    mw2_orig,
                    mw2_patch,
                ))
                break

    # Site 21 (x64): (*AgentExecutor).startMessageWatcherLocked.func2.1-range1
    mwr_key = next((k for k in funcs if k.endswith("/agentexecutor.(*AgentExecutor).startMessageWatcherLocked.func2.1-range1")), None)
    if mwr_key:
        mwr_off, mwr_sz = funcs[mwr_key]
        mwr_bytes = data[mwr_off : mwr_off + mwr_sz]
        mwr_tail_orig = bytes.fromhex("488b4c2438803900752184c0741d")
        mwr_patch = bytes.fromhex("b001909090488b4c2438803900909084c0741d")
        pos_tail = mwr_bytes.find(mwr_tail_orig)
        if pos_tail >= 5 and mwr_bytes[pos_tail - 5] == 0xE8:
            pos_mwr = pos_tail - 5
            mwr_orig = bytes(mwr_bytes[pos_mwr : pos_mwr + len(mwr_patch)])
        else:
            pos_mwr = mwr_bytes.find(mwr_patch)
            is_run_key = next((k for k in funcs if k.endswith(".(*AgentState).IsRunning")), None)
            if is_run_key and pos_mwr != -1:
                disp32 = funcs[is_run_key][0] - (mwr_off + pos_mwr + 5)
                call_orig = b"\xe8" + struct.pack("<i", disp32)
            else:
                call_orig = bytes.fromhex("e8a7bb70ff")
            mwr_orig = call_orig + mwr_tail_orig
        if pos_mwr != -1:
            sites.append((
                "AgentExecutor.startMessageWatcherLocked.func2.1-range1 (eliminate NotifyCh steal drop on Idle state updates)",
                mwr_off + pos_mwr,
                mwr_orig,
                mwr_patch,
            ))

    return sites


def locate_oauth_token_sites(data: bytes, funcs: dict):
    sites = []
    ca_key = next((k for k in funcs if k.endswith("/codeassistclient.RefreshingTokenSource")), None)
    if ca_key:
        ca_off, ca_sz = funcs[ca_key]
        ca_bytes = data[ca_off : ca_off + ca_sz]
        if is_macho_arm64(data):
            x6_5m = bytes.fromhex("060097d2862cbbf2a608c0f2")
            x6_20m = bytes.fromhex("06009cd246b2acf2e622c0f2")
            p_x6 = _find_orig_or_patch(ca_bytes, x6_5m, x6_20m)
            if p_x6 != -1:
                sites.append(
                    ("codeassistclient.RefreshingTokenSource (5m -> 20m #1)", ca_off + p_x6, x6_5m, x6_20m)
                )
            x2_5m = bytes.fromhex("020097d2822cbbf2a208c0f2")
            x2_20m = bytes.fromhex("02009cd242b2acf2e222c0f2")
            p_x2_1 = _find_orig_or_patch(ca_bytes, x2_5m, x2_20m, 0)
            if p_x2_1 != -1:
                sites.append(
                    ("codeassistclient.RefreshingTokenSource (5m -> 20m #2)", ca_off + p_x2_1, x2_5m, x2_20m)
                )
                p_x2_2 = _find_orig_or_patch(ca_bytes, x2_5m, x2_20m, p_x2_1 + len(x2_5m))
                if p_x2_2 != -1:
                    sites.append(
                        ("codeassistclient.RefreshingTokenSource (5m -> 20m #3)", ca_off + p_x2_2, x2_5m, x2_20m)
                    )
        else:
            ns_5m = struct.pack("<Q", 300_000_000_000)
            ns_20m = struct.pack("<Q", 1_200_000_000_000)
            total_imm_hits = ca_bytes.count(ns_5m) + ca_bytes.count(ns_20m)
            # 1) Legacy single-instruction candidate (`movabs rax, imm64`, total_imm_hits == 1)
            idx_ca = _find_orig_or_patch(ca_bytes, b"\x48\xb8" + ns_5m, b"\x48\xb8" + ns_20m)
            if idx_ca != -1 and total_imm_hits == 1:
                sites.append(
                    (
                        "codeassistclient.RefreshingTokenSource (5m -> 20m)",
                        ca_off + idx_ca,
                        b"\x48\xb8" + ns_5m,
                        b"\x48\xb8" + ns_20m,
                    )
                )
            elif total_imm_hits == 3:
                # 2) v2.28-v2.31+ x64: 1x `movabs r8, imm64; mov [rdx+0x60], r8` + 2x `movabs rcx, imm64; mov [rax+0x20], rcx`
                r8_5m = b"\x49\xb8" + ns_5m + bytes.fromhex("4c894260")
                r8_20m = b"\x49\xb8" + ns_20m + bytes.fromhex("4c894260")
                p_r8 = _find_orig_or_patch(ca_bytes, r8_5m, r8_20m)
                for mid_nop_hex in ("6690", ""):
                    rcx_5m = (
                        b"\x48\xb9" + ns_5m + bytes.fromhex(f"48894820{mid_nop_hex}eb0e")
                        + b"\x48\xb9" + ns_5m + bytes.fromhex("48894820")
                    )
                    rcx_20m = (
                        b"\x48\xb9" + ns_20m + bytes.fromhex(f"48894820{mid_nop_hex}eb0e")
                        + b"\x48\xb9" + ns_20m + bytes.fromhex("48894820")
                    )
                    p_rcx = _find_orig_or_patch(ca_bytes, rcx_5m, rcx_20m)
                    if p_r8 != -1 and p_rcx != -1 and p_r8 < p_rcx:
                        span_end = p_rcx + len(rcx_5m)
                        span_orig = bytearray(ca_bytes[p_r8:span_end])
                        span_patch = bytearray(ca_bytes[p_r8:span_end])
                        span_orig[0 : len(r8_5m)] = r8_5m
                        span_orig[p_rcx - p_r8 : span_end - p_r8] = rcx_5m
                        span_patch[0 : len(r8_20m)] = r8_20m
                        span_patch[p_rcx - p_r8 : span_end - p_r8] = rcx_20m
                        sites.append(
                            (
                                "codeassistclient.RefreshingTokenSource (5m -> 20m)",
                                ca_off + p_r8,
                                bytes(span_orig),
                                bytes(span_patch),
                            )
                        )
                        break
    return sites
