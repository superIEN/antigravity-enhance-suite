#!/usr/bin/env python3
"""
macOS ARM64 AGY language_server v3.6 Patcher (Sites 1-23)
=========================================================
Preserves all 18 sites from `/Users/ien/.gemini/agy-enhance/antigravity-enhance-suite/src/patch_sites.py`
and adds Sites 19-23 localized in `/Users/ien/agy治理/01_runtime_backend`:

- Site 19: `(*AgentExecutor).transitionFromRunningToIdleOrClosed` (`va=0x101d834b0`, `file_off=0x1d834ac`)
  Fixes reactive wakeup race condition when transitioning from RUNNING to IDLE:
  always jumps to `0x101d83560` to re-check `messageTracker` instead of returning early.
- Site 20: `(*AgentExecutor).startMessageWatcherLocked.func2` (`va=0x101d8f58c`, `file_off=0x1d8f588`)
  Unblocks `NotifyCh()` watcher loop so background task / subagent notifications are processed
  regardless of `a.userSettings.GetCascadeAutoRun()` state.
- Site 21: `(*AgentExecutor).startMessageWatcherLocked.func2.1-range1` (`va=0x101d8f8d0`, `file_off=0x1d8f8d0`)
  Forces `(*AgentState).SubagentsAndTasksIdle` (`bl 0x1015e9600`) -> `mov x0, #1` (`orr x0, xzr, #0x1`) and
  NOPs the `tbnz w3, #0x0` (`*prevIdle`) gate so background task completion notifications reliably wake
  the agent even when multiple background tasks/subagents overlap.
- Site 22: `ClampCheckpointConfigForModelInfo` (`va=0x10134fe8c`, `file_off=0x134fe8c`, 9 instructions / 36 bytes)
  Fixes the `256,000` (`0x3e800`) `checkpointConfig.MaxTokenLimit` override injected by
  `applyFeatureProviderCheckpointOverrideConfig` (`0x1014a0cb8`) / Desktop UI without regressing 200k models
  under either `w3 == 256,000` or `w3 == 0`!
  Replaces the 9-instruction initialization block (`0x10134fe8c`..`0x10134feac`) with an exact check:
  when `cfg.MaxTokenLimit` (`w3`) is `256,000` (`0x3e800`) or `0`, loads `x4 = modelInfo.ContextLength`,
  computes `x5 = x4 - 65,536` (`#0x10, lsl #12`), and selects `w4 = (w4 > 262,144) ? w5 : w4` (`csel w4, w5, w4, gt`).
  Result:
  - `gemini-4-argon-high` (`ContextLength = 1,048,576 > 262,144`): `cfg.MaxTokenLimit` -> `983,040` (`1,048,576 - 65,536`)
  - 200k Claude models (`ContextLength = 200,000 <= 262,144`): `w4 = 200,000` for both `w3 == 0` and `w3 == 256,000`,
    which the tail at `0x10134feb0` clamps to `200,000 - x2` (zero regression on 200k models).
  - Custom non-zero `MaxTokenLimit != 256,000`: preserved untouched.
- Site 23: `(*TruncationDecorator).truncateIfTooLarge` (trajectory byte cap `25MB -> 256MB`)
  Upgrades all `movz xN/wN, #0x190, lsl #16` (`0x01900000` = `25 MB`) instructions inside `truncateIfTooLarge`
  to `movz xN/wN, #0x1000, lsl #16` (`0x10000000` = `256 MB`).
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ENTITLEMENTS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>com.apple.security.cs.allow-jit</key>
\t<true/>
</dict>
</plist>
"""

SUITE_SRC = Path(__file__).resolve().parent
FALLBACK_SUITE_SRC = Path.home() / ".gemini" / "agy-enhance" / "antigravity-enhance-suite" / "src"
if str(SUITE_SRC) not in sys.path:
    sys.path.insert(0, str(SUITE_SRC))
if str(FALLBACK_SUITE_SRC) not in sys.path:
    sys.path.append(str(FALLBACK_SUITE_SRC))

try:
    from . import patch_sites as base_patcher  # type: ignore[no-redef]
except ImportError:
    import patch_sites as base_patcher  # type: ignore[no-redef]  # noqa: E402


@dataclass(frozen=True)
class Arm64FixedSite:
    site_id: int
    name: str
    symbol: str
    va: int
    file_off: int
    orig_bytes: bytes
    patch_bytes: bytes
    description: str
    prev_patch_bytes: tuple[bytes, ...] = ()


ARM64_V36_SITES: tuple[Arm64FixedSite, ...] = (
    Arm64FixedSite(
        site_id=19,
        name="agent_executor_transition_recheck_inbox",
        symbol="(*AgentExecutor).transitionFromRunningToIdleOrClosed",
        va=0x101D834B0,
        file_off=0x1D834AC,
        orig_bytes=bytes.fromhex("e167433981050036a4144ba9"),
        patch_bytes=bytes.fromhex("e16743392c000014a4144ba9"),
        description="tbz w1, #0x0, 0x101d83560 -> b 0x101d83560 (always re-check inbox on RUNNING->IDLE)",
    ),
    Arm64FixedSite(
        site_id=20,
        name="agent_executor_watcher_outer_gate",
        symbol="(*AgentExecutor).startMessageWatcherLocked.func2",
        va=0x101D8F58C,
        file_off=0x1D8F588,
        orig_bytes=bytes.fromhex("e00f4139c0010036e0874ba9"),
        patch_bytes=bytes.fromhex("e00f41391f2003d5e0874ba9"),
        description="tbz w0, #0x0, 0x101d8f5c4 -> nop (always scan messageTracker on NotifyCh wakeup)",
    ),
    Arm64FixedSite(
        site_id=21,
        name="agent_executor_watcher_inner_range_gate",
        symbol="(*AgentExecutor).startMessageWatcherLocked.func2.1-range1",
        va=0x101D8F8D0,
        file_off=0x1D8F8D0,
        orig_bytes=bytes.fromhex("4c67e197e21f40f94300403903010037e0000036"),
        patch_bytes=bytes.fromhex("e00340b2e21f40f9430040391f2003d5e0000036"),
        description="bl (*AgentState).SubagentsAndTasksIdle -> mov x0, #1; tbnz w3, #0x0 -> nop",
    ),
    Arm64FixedSite(
        site_id=22,
        name="clamp_checkpoint_config_override_desktop_256k",
        symbol="ClampCheckpointConfigForModelInfo",
        va=0x10134FE8C,
        file_off=0x134FE8C,
        orig_bytes=bytes.fromhex(
            "23010035"  # 10134fe8c: cbnz  w3, 0x10134feb0
            "231480b9"  # 10134fe90: ldrsw x3, [x1, #0x14]
            "048893d2"  # 10134fe94: mov   x4, #0x9c40
            "7f0004eb"  # 10134fe98: cmp   x3, x4
            "6c000054"  # 10134fe9c: b.gt  0x10134fea8
            "e3031faa"  # 10134fea0: mov   x3, xzr
            "02000014"  # 10134fea4: b     0x10134feac
            "630004cb"  # 10134fea8: sub   x3, x3, x4
            "031000b9"  # 10134feac: str   w3, [x0, #0x10]
        ),
        patch_bytes=bytes.fromhex(
            "65f84051"  # 10134fe8c: sub   w5, w3, #0x3e, lsl #12  (w5 = w3 - 0x3e000)
            "bf002071"  # 10134fe90: cmp   w5, #0x800              (Z=1 if w3 == 256,000)
            "6418407a"  # 10134fe94: ccmp  w3, #0x0, #0x4, ne      (Z=1 if w3 == 256,000 || w3 == 0)
            "c1000054"  # 10134fe98: b.ne  0x10134feb0             (preserve custom w3)
            "241480b9"  # 10134fe9c: ldrsw x4, [x1, #0x14]         (x4 = model.ContextLength)
            "854040d1"  # 10134fea0: sub   x5, x4, #0x10, lsl #12  (x5 = ContextLength - 65,536)
            "9f004171"  # 10134fea4: cmp   w4, #0x40, lsl #12      (is ContextLength > 262,144?)
            "a4c0841a"  # 10134fea8: csel  w4, w5, w4, gt          (w4 = >262k ? ContextLength-65536 : ContextLength)
            "041000b9"  # 10134feac: str   w4, [x0, #0x10]          (983,040 for 1M; 200,000 for 200k)
        ),
        description="if MaxTokenLimit in {0, 256000}: MaxTokenLimit = (ContextLength > 262144) ? ContextLength - 65536 : ContextLength (983040 for 1M, 200000 for 200k)",
        prev_patch_bytes=(
            bytes.fromhex("65f84051bf0020716418407ac1000054241480b9844040d19f00036b4d000054041000b9"),
        ),
    ),
)


def _resolve_dynamic_v36_sites(data: bytes | bytearray) -> list[Arm64FixedSite]:
    funcs = base_patcher.parse_gopclntab(data)
    sections = base_patcher.parse_macho_sections(data)
    bg_sites = base_patcher.locate_bg_task_timeout_sites(data, funcs)
    ctx_sites = base_patcher.locate_1m_context_sites(data, funcs)

    by_id: dict[int, tuple[str, int, bytes, bytes]] = {}
    for label, off, orig_b, patch_b in bg_sites + ctx_sites:
        if "Site 19" in label:
            by_id[19] = (label, off, orig_b, patch_b)
        elif "Site 20" in label:
            by_id[20] = (label, off, orig_b, patch_b)
        elif "Site 21" in label:
            by_id[21] = (label, off, orig_b, patch_b)
        elif "Site 22" in label:
            by_id[22] = (label, off, orig_b, patch_b)
        elif "Site 23" in label:
            by_id[23] = (label, off, orig_b, patch_b)

    resolved: list[Arm64FixedSite] = []
    for site in ARM64_V36_SITES:
        if site.site_id in by_id:
            _, dyn_off, dyn_orig, dyn_patch = by_id[site.site_id]
            dyn_va = base_patcher.macho_raw_to_va(sections, dyn_off) or (0x100000000 + dyn_off)
            resolved.append(
                Arm64FixedSite(
                    site_id=site.site_id,
                    name=site.name,
                    symbol=site.symbol,
                    va=dyn_va,
                    file_off=dyn_off,
                    orig_bytes=dyn_orig,
                    patch_bytes=dyn_patch,
                    description=site.description,
                    prev_patch_bytes=site.prev_patch_bytes,
                )
            )
        else:
            resolved.append(site)

    if 23 not in by_id:
        raise RuntimeError("Site 23 (TruncationDecorator.truncateIfTooLarge) could not be resolved in Mach-O binary")
    _, dyn_off, dyn_orig, dyn_patch = by_id[23]
    dyn_va = base_patcher.macho_raw_to_va(sections, dyn_off) or (0x100000000 + dyn_off)
    resolved.append(
        Arm64FixedSite(
            site_id=23,
            name="truncation_decorator_trajectory_byte_cap_256mb",
            symbol="(*TruncationDecorator).truncateIfTooLarge",
            va=dyn_va,
            file_off=dyn_off,
            orig_bytes=dyn_orig,
            patch_bytes=dyn_patch,
            description="trajectory byte cap 25MB (0x01900000) -> 256MB (0x10000000)",
        )
    )
    return resolved


def _is_valid_mixed_site23(cur: bytes, before: bytes, after: bytes) -> bool:
    if len(cur) != len(before) or len(before) != len(after):
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


def inspect_v36_sites(data: bytes | bytearray) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for site in _resolve_dynamic_v36_sites(data):
        end = site.file_off + len(site.orig_bytes)
        actual = bytes(data[site.file_off:end])
        if actual == site.patch_bytes:
            state = "PATCHED"
        elif actual in site.prev_patch_bytes:
            state = "PATCHED_V36_0"
        elif actual == site.orig_bytes:
            state = "ORIGINAL"
        elif site.site_id == 23 and _is_valid_mixed_site23(actual, site.orig_bytes, site.patch_bytes):
            state = "MIXED"
        else:
            # Also search entire binary in case offsets shifted
            idx_patch = data.find(site.patch_bytes)
            idx_orig = data.find(site.orig_bytes)
            if idx_patch != -1:
                state = f"PATCHED_AT_0x{idx_patch:x}"
            elif idx_orig != -1:
                state = f"ORIGINAL_AT_0x{idx_orig:x}"
            else:
                state = f"UNKNOWN({actual.hex()})"
        results.append(
            {
                "site_id": site.site_id,
                "name": site.name,
                "symbol": site.symbol,
                "va": f"0x{site.va:x}",
                "file_off": f"0x{site.file_off:x}",
                "state": state,
                "description": site.description,
            }
        )
    return results


def apply_v36_sites(buf: bytearray) -> list[str]:
    actions: list[str] = []
    for site in _resolve_dynamic_v36_sites(buf):
        end = site.file_off + len(site.orig_bytes)
        actual = bytes(buf[site.file_off:end])
        if actual == site.patch_bytes:
            actions.append(f"[Site {site.site_id}] {site.name}: already PATCHED at 0x{site.file_off:x}")
        elif (
            actual == site.orig_bytes
            or actual in site.prev_patch_bytes
            or (site.site_id == 23 and _is_valid_mixed_site23(actual, site.orig_bytes, site.patch_bytes))
        ):
            buf[site.file_off:end] = site.patch_bytes
            actions.append(f"[Site {site.site_id}] {site.name}: PATCHED at 0x{site.file_off:x} ({site.description})")
        else:
            raise RuntimeError(
                f"[Site {site.site_id}] {site.name}: unexpected bytes at 0x{site.file_off:x}: "
                f"got {actual.hex()}, expected orig={site.orig_bytes.hex()} or patch={site.patch_bytes.hex()}"
            )
    return actions


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect or patch macOS ARM64 AGY language_server with Sites 1-23.")
    parser.add_argument(
        "--binary",
        type=Path,
        default=Path("/Users/ien/Applications/Antigravity.app/Contents/Resources/bin/language_server"),
        help="Path to input language_server binary",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Path to output patched binary (if omitted, runs in --check mode only unless --in-place is passed)",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Patch --binary in-place (creates a .bak backup first) and re-sign with ad-hoc codesign",
    )
    args = parser.parse_args()

    raw = args.binary.read_bytes()
    sha256_before = hashlib.sha256(raw).hexdigest()
    print(f"Binary: {args.binary}")
    print(f"Size:   {len(raw):,} bytes")
    print(f"SHA256: {sha256_before}")
    print("-" * 72)

    # Check Sites 19-23
    for info in inspect_v36_sites(raw):
        print(
            f"Site {info['site_id']:2d} | {info['state']:10s} | va={info['va']} off={info['file_off']} | "
            f"{info['symbol']} — {info['description']}"
        )

    if args.output is None and not args.in_place:
        return 0

    target_path = args.binary if args.in_place else args.output
    assert target_path is not None

    if args.in_place:
        bak_path = args.binary.with_suffix(".pre_v36.bak")
        if not bak_path.exists():
            shutil.copy2(args.binary, bak_path)
            print(f"Created backup: {bak_path}")

    # First verify / ensure base sites from patch_sites.py are applied
    buf = bytearray(raw)
    funcs = base_patcher.parse_gopclntab(buf)
    base_sites = (
        base_patcher.locate_h2ping_sites(buf, funcs)
        + base_patcher.locate_1m_context_sites(buf, funcs)
        + base_patcher.locate_media_compression_sites(buf, funcs)
        + base_patcher.locate_utf8_and_uri_sites(buf, funcs)
        + base_patcher.locate_stream_retry_sites(buf, funcs)
        + base_patcher.locate_bg_task_timeout_sites(buf, funcs)
        + base_patcher.locate_oauth_token_sites(buf, funcs)
    )
    base_already = 0
    base_patched = 0
    for label, off, orig_b, patch_b in base_sites:
        cur = bytes(buf[off : off + len(patch_b)])
        if cur == patch_b:
            base_already += 1
        elif cur == orig_b or ("Site 23" in label and _is_valid_mixed_site23(cur, orig_b, patch_b)):
            buf[off : off + len(patch_b)] = patch_b
            base_patched += 1
        else:
            raise RuntimeError(f"Unexpected bytes at base site {label} (0x{off:x})")
    print("-" * 72)
    print(f"Base Sites status: {len(base_sites)} located ({base_already} already patched, {base_patched} newly patched)")

    # Apply Sites 19-23
    v36_actions = apply_v36_sites(buf)
    for line in v36_actions:
        print(line)

    target_path.write_bytes(buf)
    target_path.chmod(0o755)
    sha256_after = hashlib.sha256(buf).hexdigest()
    print("-" * 72)
    print(f"Wrote:  {target_path}")
    print(f"SHA256: {sha256_after}")

    # Ad-hoc codesign on macOS preserving Hardened Runtime, Identifier=language_server, and allow-jit entitlement
    with tempfile.NamedTemporaryFile("w", suffix=".entitlements.plist", delete=False) as tf:
        tf.write(ENTITLEMENTS_XML)
        ent_path = Path(tf.name)
    try:
        subprocess.run(
            [
                "codesign",
                "-f",
                "-s",
                "-",
                "--identifier",
                "language_server",
                "--options",
                "runtime",
                "--entitlements",
                str(ent_path),
                str(target_path),
            ],
            check=True,
        )
    finally:
        ent_path.unlink(missing_ok=True)
    verify = subprocess.run(["codesign", "-vv", str(target_path)], capture_output=True, text=True, check=True)
    print(verify.stdout.strip() or verify.stderr.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
