"""`pf` — a tiny terminal client for a PIXEL FLIPPERS server running over HTTP.

Start a server with ``PIXEL_FLIPPERS_TRANSPORT=http`` (it keeps the emulator
alive between calls), then drive it from any shell — or from Claude Code:

    pf tools                          # list what this tier can do
    pf press up up a                  # press_buttons
    pf screenshot /tmp/now.png        # get_screenshot → PNG file
    pf call save_state '{"name": "before-brock"}'
    pf call read_last_session

Text results print to stdout; images are written to the path you give (or a
temp file, whose path is printed).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import tempfile
from typing import Any

DEFAULT_URL = os.environ.get("PIXEL_FLIPPERS_URL", "http://127.0.0.1:8765/mcp")


async def _call(url: str, tool: str, args: dict[str, Any], image_out: str | None) -> int:
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            await session.initialize()
            if tool == "__tools__":
                listing = await session.list_tools()
                for t in listing.tools:
                    first = (t.description or "").strip().splitlines()[0] if t.description else ""
                    print(f"{t.name:20s} {first}")
                return 0
            result = await session.call_tool(tool, args)
            for block in result.content:
                kind = getattr(block, "type", "")
                if kind == "text":
                    print(block.text)
                elif kind == "image":
                    path = image_out or tempfile.mktemp(prefix="pf-", suffix=".png")
                    with open(path, "wb") as fh:
                        fh.write(base64.b64decode(block.data))
                    print(f"[image saved to {path}]")
                else:
                    print(f"[{kind}]")
            return 1 if getattr(result, "isError", False) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pf", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_URL, help=f"server URL (default {DEFAULT_URL})")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tools", help="list available tools")
    p = sub.add_parser("press", help="press buttons in sequence")
    p.add_argument("buttons", nargs="+")
    p.add_argument("--hold", type=int, default=None, help="hold_frames")
    p.add_argument("--wait", type=int, default=None, help="wait_frames")
    s = sub.add_parser("screenshot", help="save a screenshot")
    s.add_argument("path", nargs="?", default=None)
    c = sub.add_parser("call", help="call any tool: pf call <tool> '<json args>'")
    c.add_argument("tool")
    c.add_argument("args", nargs="?", default="{}")
    c.add_argument("--image-out", default=None)
    ns = parser.parse_args(argv)

    if ns.cmd == "tools":
        tool, args, out = "__tools__", {}, None
    elif ns.cmd == "press":
        args = {"buttons": ns.buttons}
        if ns.hold is not None:
            args["hold_frames"] = ns.hold
        if ns.wait is not None:
            args["wait_frames"] = ns.wait
        tool, out = "press_buttons", None
    elif ns.cmd == "screenshot":
        tool, args, out = "get_screenshot", {}, ns.path
    else:
        try:
            args = json.loads(ns.args)
        except json.JSONDecodeError as exc:
            print(f"args must be JSON: {exc}", file=sys.stderr)
            return 2
        tool, out = ns.tool, ns.image_out
    try:
        return asyncio.run(_call(ns.url, tool, args, out))
    except BaseException as exc:  # ExceptionGroup from anyio wraps the real error
        if isinstance(exc, KeyboardInterrupt):
            raise
        print(f"pf: could not talk to {ns.url} — is a server running with "
              f"PIXEL_FLIPPERS_TRANSPORT=http? ({type(exc).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
