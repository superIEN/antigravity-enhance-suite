"""Historical pattern locator only. No default paths, config/DB mutations or daemon."""
import struct

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

def parse_gopclntab(data: bytes):
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

H2_PROTOCOLS_CTX = bytes.fromhex("4883b8c800000000")

H2_PROTOCOLS_ORIG = bytes.fromhex("756e")

H2_PROTOCOLS_PATCH = bytes.fromhex("9090")

H2_PING_CTX = bytes.fromhex("48c7402000001000")

H2_PING_ORIG = bytes.fromhex("904883784000907f0e48b900d6117e0300000048894840c3")

H2_PING_PATCH_V4 = bytes.fromhex("48b900d6117e03000000488948404801c9488948389090c3")

H2_PING_PATCH = bytes.fromhex("48b900c817a8040000004889483848c1e1024889484090c3")

GONET_H2_ORIG = bytes.fromhex("c74010000010009048837828007f0e48b900d6117e0300000048894828c3")

GONET_H2_PATCH = bytes.fromhex("c74010000010009048b900ba1dd205000000488948204801c948894828c3")

def locate_h2ping_sites(data: bytes, funcs: dict):
    sites = []
    # Site 1: net/http.(*Transport).protocols
    f1_off, f1_sz = funcs["net/http.(*Transport).protocols"]
    f1_bytes = data[f1_off : f1_off + f1_sz]
    idx1 = f1_bytes.find(H2_PROTOCOLS_CTX)
    if idx1 == -1:
        raise RuntimeError("Could not find TLSClientConfig check in net/http.(*Transport).protocols")
    patch1_off = f1_off + idx1 + len(H2_PROTOCOLS_CTX)
    sites.append(("net/http.(*Transport).protocols (HTTP/2 enable)", patch1_off, H2_PROTOCOLS_ORIG, H2_PROTOCOLS_PATCH))

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
    gw_key = next((k for k in funcs if k.endswith(".modelEntryToModelDetails")), None)

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

    hyd_key = next((k for k in funcs if k.endswith(".HydrateModelEntryDefaults")), None)
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

    return sites

def locate_media_compression_sites(data: bytes, funcs: dict):
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
            elif arr_raw is not None and arr_len == 15:
                # v2.26.0: 15-slot lowercase table after strings.ToLower
                entries = [list(struct.unpack_from("<QQ", data, arr_raw + i * 16)) for i in range(15)]
                va0, _ = entries[0]
                va4, len4 = entries[4]
                orig_va4 = va4 if len4 == 32 else (va4 - 22)
                va13, _ = entries[13]
                raw_crbp = data.find(b"connection reset by peer")
                orig_va5 = image_base + raw_to_rva(sections, raw_crbp)
                va_sre = image_base + raw_to_rva(sections, data.find(b"stream reading error: %w"))
                va_unauth_lower = image_base + raw_to_rva(sections, data.find(b"unauthenticated"))

                orig_entries = list(entries)
                orig_entries[0] = [va0, 7]
                orig_entries[1] = [va0 + 7, 7]
                orig_entries[4] = [orig_va4, 32]
                orig_entries[5] = [orig_va5, 24]
                orig_entries[13] = [va13, 29]

                patch_entries = list(entries)
                patch_entries[0] = [va0, 3]
                patch_entries[1] = [va_sre, 7]
                patch_entries[4] = [orig_va4 + 22, 10]
                patch_entries[5] = [va_unauth_lower, 15]
                patch_entries[13] = [va13, 6]

                s14_26_orig = b"".join(struct.pack("<QQ", v, l) for v, l in orig_entries)
                s14_26_patch = b"".join(struct.pack("<QQ", v, l) for v, l in patch_entries)
                sites.append(("utils.IsRetryableAPIError (v2.26.0 15-slot retryableSubstrings expansion)", arr_raw, s14_26_orig, s14_26_patch))

    return sites

def locate_bg_task_timeout_sites(data: bytes, funcs: dict):
    sites = []
    # Site 15: (*RunCommandHandler).runTaskListeners non-daemon 60s default NotificationTimeoutSeconds wakeup
    rtl_key = next((k for k in funcs if k.endswith("/handlers.(*RunCommandHandler).runTaskListeners")), None)
    if rtl_key:
        rtl_off, rtl_sz = funcs[rtl_key]
        rtl_bytes = data[rtl_off : rtl_off + rtl_sz]
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

    return sites
