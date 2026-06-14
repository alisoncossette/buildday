"""Stead — live Bolo MCP client (@bolospot/mcp), the real consent layer.

A thin, swappable adapter that lets `ConsentEngine`'s `BoloBackend` talk to a running Bolo
MCP server instead of the in-memory / Store backend. Bolo is "peer-to-peer digital permissions
for AI agents" (@bolospot/mcp v0.4.x); we use four of its tools — create_grant / check_access /
revoke_grant / request_access — to hold grants in the owner's real Bolo account.

The engine calls this object SYNCHRONOUSLY:
    client.create_grant(grantee, scope, **params)
    client.check_access(grantee, scope)        -> live grant dict (with params) or None
    client.revoke_grant(grantee, scope)        -> truthy on success
    client.request_access(grantee, scope, **params)   (optional)

The MCP Python SDK (`mcp`, pulled in by `anthropic[mcp]`) is async, so this class owns a private
asyncio loop on a background thread and bridges each sync call onto it — the rest of Stead stays
plain synchronous Python.

OFFLINE-FIRST: this module is NEVER imported by the default demo path. Stead runs on the
deterministic in-memory backend unless a BoloClient is explicitly injected
(`ConsentEngine(bolo=BoloClient())`). With no BOLO_API_KEY, construction raises a clear error
that points back to that fallback — the demo always runs on a hotspot without it.

Keys (environment):
    BOLO_API_KEY    required — your Bolo API key (https://bolospot.com/dashboard/api-keys)

Recommended wiring (registers the server with Claude Code / the Agent SDK):
    claude mcp add bolospot --scope user --env BOLO_API_KEY=sk-... -- npx -y @bolospot/mcp

This module talks to the SAME server binary directly over stdio (`npx -y @bolospot/mcp`), so it
works whether or not `claude mcp add` was run; that command is the convenient way to make the
same server available to the interactive agent.

Docs: https://bolospot.com/docs/mcp-tools  (tool surface, confirmed against @bolospot/mcp 0.4.3)
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
from typing import Any, Optional


class BoloClient:
    """Synchronous facade over a live @bolospot/mcp server (spoken to over stdio).

    Satisfies the interface `BoloBackend` expects:
    create_grant / check_access / revoke_grant / request_access. Construct once and inject:

        from integrations.bolo_client import BoloClient
        from consent_agent import ConsentEngine
        consent = ConsentEngine(owner="mom", bolo=BoloClient())

    Falls back is NOT this class's job — if there is no key it refuses to construct so the caller
    drops to the in-memory backend (see ConsentEngine(bolo=None)).
    """

    # Default transport: launch the published server with npx. Swap `command`/`args` for an
    # already-installed `bolo-mcp` binary if you've globally installed @bolospot/mcp.
    _DEFAULT_COMMAND = "npx"
    _DEFAULT_ARGS = ("-y", "@bolospot/mcp")

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        command: Optional[str] = None,
        args: Optional[list[str]] = None,
        startup_timeout: float = 30.0,
    ) -> None:
        """Spawn and initialize the Bolo MCP server over stdio.

        Args:
            api_key: Bolo API key. Defaults to env BOLO_API_KEY.
            command: Executable to launch the server (default 'npx').
            args: Args for the launcher (default ['-y', '@bolospot/mcp']).
            startup_timeout: Seconds to wait for the MCP handshake before giving up.

        Raises:
            RuntimeError: if no API key is found — the caller should fall back to the in-memory
                backend (ConsentEngine(bolo=None)).
            ImportError: if the `mcp` package is missing (install `anthropic[mcp]`).
        """
        self._api_key = api_key or os.environ.get("BOLO_API_KEY", "").strip()
        if not self._api_key:
            raise RuntimeError(
                "BOLO_API_KEY is not set, so the live Bolo MCP client cannot start. "
                "Get a key at https://bolospot.com/dashboard/api-keys, then either pass "
                "api_key=... or export BOLO_API_KEY. For an OFFLINE demo, do NOT inject a "
                "BoloClient — construct ConsentEngine(bolo=None) to use the deterministic "
                "in-memory backend (default)."
            )

        # Import here so the offline path never requires the MCP SDK to be installed.
        try:
            from mcp import ClientSession, StdioServerParameters  # noqa: F401
            from mcp.client.stdio import stdio_client  # noqa: F401
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise ImportError(
                "The Bolo MCP client needs the Model Context Protocol SDK. "
                "Install it with:  pip install 'anthropic[mcp]'  (or  pip install mcp )."
            ) from exc

        self._command = command or self._DEFAULT_COMMAND
        self._args = list(args) if args is not None else list(self._DEFAULT_ARGS)
        self._startup_timeout = startup_timeout

        # Own a dedicated event loop on a background thread. Every sync call below is marshalled
        # onto it, so the long-lived stdio session (which must live inside one loop/task tree)
        # outlives individual calls.
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._closed = False
        self._session: Any = None  # mcp.ClientSession, set on the loop thread
        self._stack: Any = None    # contextlib.AsyncExitStack
        self._thread = threading.Thread(
            target=self._run_loop, name="bolo-mcp-loop", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=self._startup_timeout)
        if self._session is None:
            raise RuntimeError(
                "Timed out starting the Bolo MCP server "
                f"({self._command} {' '.join(self._args)}). Is Node/npx installed and the key "
                "valid? Fall back to ConsentEngine(bolo=None) for the offline in-memory backend."
            )

    # ----------------------------------------------------------------- lifecycle

    def _run_loop(self) -> None:
        """Background-thread entry: stand up the stdio session, then serve until close()."""
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        finally:
            self._loop.close()

    async def _serve(self) -> None:
        from contextlib import AsyncExitStack

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        # Pass the key through the child process env, as `claude mcp add ... --env BOLO_API_KEY`
        # would. We forward the parent env too so npx/node resolve normally.
        env = dict(os.environ)
        env["BOLO_API_KEY"] = self._api_key
        params = StdioServerParameters(command=self._command, args=self._args, env=env)

        self._stack = AsyncExitStack()
        try:
            read, write = await self._stack.enter_async_context(stdio_client(params))
            self._session = await self._stack.enter_async_context(ClientSession(read, write))
            await self._session.initialize()
        except Exception:
            # Leave _session as None so __init__ raises a clear startup error.
            self._session = None
            self._ready.set()
            await self._stack.aclose()
            return

        self._ready.set()
        # Idle until close() flips the flag; the session stays open for call_tool.
        while not self._closed:
            await asyncio.sleep(0.1)
        await self._stack.aclose()

    def close(self) -> None:
        """Shut the server down and stop the background loop. Safe to call repeatedly."""
        if self._closed:
            return
        self._closed = True
        try:
            self._loop.call_soon_threadsafe(lambda: None)  # wake the idle sleep
        except RuntimeError:
            pass
        self._thread.join(timeout=10.0)

    def __enter__(self) -> "BoloClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ----------------------------------------------------------------- tool bridge

    def _call_tool(self, name: str, arguments: dict) -> Any:
        """Run a single MCP tool call on the background loop and return its parsed result.

        Returns the tool's structured content when present, else its parsed text content,
        else None. Raises RuntimeError on an MCP tool error (isError).
        """
        if self._session is None:  # pragma: no cover - guarded by __init__
            raise RuntimeError("Bolo MCP session is not running.")
        fut = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(name, arguments), self._loop
        )
        result = fut.result(timeout=60.0)
        return self._parse_result(name, result)

    @staticmethod
    def _parse_result(name: str, result: Any) -> Any:
        """Normalize a CallToolResult into a plain Python value.

        MCP results carry `content` (a list of TextContent/…) and, on newer servers, a
        `structuredContent` dict. We prefer structured content, then JSON-decoded text.
        """
        if getattr(result, "isError", False):
            text = BoloClient._first_text(result)
            raise RuntimeError(f"Bolo MCP tool {name!r} failed: {text or result!r}")

        structured = getattr(result, "structuredContent", None)
        if structured:
            return structured

        text = BoloClient._first_text(result)
        if text is None:
            return None
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return text

    @staticmethod
    def _first_text(result: Any) -> Optional[str]:
        for block in getattr(result, "content", None) or []:
            txt = getattr(block, "text", None)
            if txt is not None:
                return txt
        return None

    # ----------------------------------------------------------------- ConsentEngine surface
    #
    # NOTE on parameter names: Bolo's grant tools take `grantee` and `scope`; the remaining
    # constraint params (vendor / cap / payment / amount) are forwarded as-is. The exact wire
    # names for those constraints are not pinned in public docs yet — if the server rejects a
    # field, map it here. TODO(verify against https://bolospot.com/docs/mcp-tools): confirm
    # whether constraints go under a `params`/`constraints` key vs. flat kwargs, and the exact
    # name for the grantee identifier (grantee vs. handle vs. agent).

    def create_grant(self, grantee: str, scope: str, **params: Any) -> dict:
        """Mint (or update) a scoped grant for `grantee` via Bolo `create_grant`.

        Args:
            grantee: The actor receiving the grant (e.g. 'agent:ruby').
            scope: The capability scope (e.g. 'order:food').
            **params: Constraints carried by the grant (vendor, cap, payment, …).

        Returns:
            The created grant as a dict (server-shaped).
        """
        args = {"grantee": grantee, "scope": scope, **params}
        res = self._call_tool("create_grant", args)
        return res if isinstance(res, dict) else {"grantee": grantee, "scope": scope, **params}

    def check_access(self, grantee: str, scope: str) -> Optional[dict]:
        """Return `grantee`'s live grant for `scope` (with its params) via Bolo `check_access`,
        or None if there is no live grant.

        BoloBackend.get_grant relies on this returning the grant's constraint params (vendor,
        cap, …) so the engine can enforce them; a bare boolean true is normalized to an empty
        dict (grant exists, unconstrained).
        """
        res = self._call_tool("check_access", {"grantee": grantee, "scope": scope})
        if not res:
            return None
        if isinstance(res, dict):
            # Some servers wrap the grant: {"allowed": true, "grant": {...}} — unwrap if so.
            if "grant" in res and isinstance(res["grant"], dict):
                return res["grant"]
            if res.get("allowed") is False or res.get("access") is False:
                return None
            return res
        # truthy non-dict (e.g. bare True): grant exists with no readable constraints.
        return {}

    def revoke_grant(self, grantee: str, scope: str) -> bool:
        """Revoke `grantee`'s `scope` grant via Bolo `revoke_grant`. Effective on the next check.

        Returns True on success.
        """
        res = self._call_tool("revoke_grant", {"grantee": grantee, "scope": scope})
        if isinstance(res, dict):
            # Treat an explicit failure flag as False; otherwise a returned record means success.
            return res.get("revoked", res.get("success", True)) is not False
        return bool(res) if res is not None else True

    def request_access(self, grantee: str, scope: str, **params: Any) -> dict:
        """Park a request for a capability `grantee` does NOT yet hold, via Bolo `request_access`.

        Optional in the engine's eyes (it best-effort-calls this); the owner approves out of band
        in their Bolo dashboard / relay. Returns the request record as a dict.
        """
        args = {"grantee": grantee, "scope": scope, **params}
        res = self._call_tool("request_access", args)
        return res if isinstance(res, dict) else {"grantee": grantee, "scope": scope, **params}


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    # Smoke test: needs BOLO_API_KEY and Node/npx. Mints, checks, and revokes a throwaway grant.
    with BoloClient() as bolo:
        print("create:", bolo.create_grant("agent:ruby", "order:food", vendor="Tony's", cap=30))
        print("check: ", bolo.check_access("agent:ruby", "order:food"))
        print("revoke:", bolo.revoke_grant("agent:ruby", "order:food"))
        print("after: ", bolo.check_access("agent:ruby", "order:food"))
