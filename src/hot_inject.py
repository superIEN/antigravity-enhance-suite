"""Live CDP Hot-Injector for Antigravity Enhance Suite."""
import base64
import json
import os
import socket
import struct
import time
import urllib.request
from pathlib import Path


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError("WebSocket closed early")
        buf.extend(chunk)
    return bytes(buf)


def cdp_eval(port: int, ws_path: str, req_id: int, expression: str, timeout_s: float = 12.0):
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout_s)
    try:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (
            f"GET {ws_path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(handshake.encode("ascii"))
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = sock.recv(4096)
            if not chunk:
                break
            resp += chunk

        payload = json.dumps({
            "id": req_id,
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True, "awaitPromise": True},
        }).encode("utf-8")
        mask = os.urandom(4)
        hdr = bytearray([0x81])
        plen = len(payload)
        if plen < 126:
            hdr.append(0x80 | plen)
        elif plen < 65536:
            hdr.append(0x80 | 126)
            hdr.extend(struct.pack(">H", plen))
        else:
            hdr.append(0x80 | 127)
            hdr.extend(struct.pack(">Q", plen))
        hdr.extend(mask)
        masked = bytearray(payload[i] ^ mask[i & 3] for i in range(plen))
        sock.sendall(hdr + masked)

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            first2 = recv_exact(sock, 2)
            rlen = first2[1] & 0x7F
            if rlen == 126:
                rlen = struct.unpack(">H", recv_exact(sock, 2))[0]
            elif rlen == 127:
                rlen = struct.unpack(">Q", recv_exact(sock, 8))[0]
            buf = recv_exact(sock, rlen)
            msg = json.loads(buf.decode("utf-8", errors="replace"))
            if msg.get("id") == req_id:
                return msg
        return None
    finally:
        sock.close()


def hot_inject_running_windows(loader_path: Path, devtools_port_file: Path | None = None) -> list[dict]:
    if devtools_port_file is None:
        appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        devtools_port_file = Path(appdata) / "Antigravity" / "DevToolsActivePort"
    if not devtools_port_file.exists():
        return []

    lines = devtools_port_file.read_text(encoding="utf-8").strip().splitlines()
    if not lines:
        return []
    port = int(lines[0])
    targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3).read().decode("utf-8"))
    pages = [t for t in targets if t.get("type") == "page" and "127.0.0.1:" in t.get("url", "")]

    loader_code = loader_path.read_text(encoding="utf-8")
    expr = (
        "(async () => {\n"
        "  if (globalThis.__AGY_V3__ && typeof globalThis.__AGY_V3__.dispose === 'function') {\n"
        "    globalThis.__AGY_V3__.dispose();\n"
        "    delete globalThis.__AGY_V3__;\n"
        "  }\n"
        + loader_code
        + "\n"
        "  return await globalThis.__AGY_V3__.readyPromise;\n"
        "})()"
    )

    results = []
    for idx, pg in enumerate(pages):
        ws_url = pg.get("webSocketDebuggerUrl", "")
        ws_path = "/" + ws_url.split("/", 3)[-1]
        res = cdp_eval(port, ws_path, 3001 + idx, expr)
        val = res.get("result", {}).get("result", {}).get("value") if res else None
        results.append({"title": pg.get("title", ""), "url": pg.get("url", ""), "receipt": val})
    return results
