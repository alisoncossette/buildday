"""Stead — thin, import-safe observability shim for the consent engine.

Every consent decision (grant / update / revoke / act->done|halted / can_view) is a thing the owner
should be able to SEE. This module wires those decisions to Langfuse WHEN it is available and
configured, and is a silent no-op otherwise.

Contract:
- `from consent_agent.trace import tracer` ALWAYS works: zero env vars, langfuse not installed.
- `tracer.event(name, **attrs)` records a point-in-time decision.
- `with tracer.span(name, **attrs) as span:` wraps a block; `span.update(**attrs)` adds attrs.
- Langfuse is used ONLY if the `langfuse` SDK imports AND both LANGFUSE_PUBLIC_KEY and
  LANGFUSE_SECRET_KEY are set in the environment. Anything missing -> no-op (no network, no crash).

Relevant attrs (all optional): actor, scope, action, status, decision, reason, cap, amount, vendor,
grantee, resource.

To send REAL traces, set:
    LANGFUSE_PUBLIC_KEY=pk-...
    LANGFUSE_SECRET_KEY=sk-...
    LANGFUSE_HOST=https://cloud.langfuse.com   (optional; defaults to Langfuse cloud)
and `pip install langfuse`.
"""
import os
from contextlib import contextmanager


def _langfuse_enabled():
    """True only when the SDK is importable AND both keys are present in the env."""
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        return False
    try:
        import langfuse  # noqa: F401
    except Exception:
        return False
    return True


class _NoopSpan:
    """A span handle that swallows everything. Returned by the no-op tracer's span()."""

    def update(self, **attrs):
        return self

    def event(self, name, **attrs):
        return self


class _NoopTracer:
    """The default tracer: every call is a silent no-op. Never touches the network or env."""

    enabled = False

    def event(self, name, **attrs):
        return None

    @contextmanager
    def span(self, name, **attrs):
        yield _NoopSpan()

    def flush(self):
        return None


class _LangfuseSpan:
    """Wraps a live Langfuse span so the rest of the code uses one stable interface."""

    def __init__(self, span):
        self._span = span

    def update(self, **attrs):
        try:
            self._span.update(metadata=_clean(attrs))
        except Exception:
            pass
        return self

    def event(self, name, **attrs):
        try:
            self._span.event(name=name, metadata=_clean(attrs))
        except Exception:
            pass
        return self


class _LangfuseTracer:
    """Live tracer backed by the Langfuse SDK. Constructed only when _langfuse_enabled()."""

    enabled = True

    def __init__(self):
        from langfuse import Langfuse
        self._client = Langfuse()  # reads LANGFUSE_* from env

    def event(self, name, **attrs):
        try:
            self._client.create_event(name=name, metadata=_clean(attrs))
        except Exception:
            # Observability must NEVER break a consent decision.
            pass
        return None

    @contextmanager
    def span(self, name, **attrs):
        cm = None
        try:
            cm = self._client.start_as_current_span(name=name, metadata=_clean(attrs))
            raw = cm.__enter__()
            handle = _LangfuseSpan(raw)
        except Exception:
            cm = None
            handle = _NoopSpan()
        try:
            yield handle
        finally:
            if cm is not None:
                try:
                    cm.__exit__(None, None, None)
                except Exception:
                    pass

    def flush(self):
        try:
            self._client.flush()
        except Exception:
            pass


def _clean(attrs):
    """Drop None-valued attrs so traces stay readable; coerce keys to str."""
    return {str(k): v for k, v in attrs.items() if v is not None}


def _make_tracer():
    if _langfuse_enabled():
        try:
            return _LangfuseTracer()
        except Exception:
            # If construction fails for any reason, degrade to no-op rather than crash.
            return _NoopTracer()
    return _NoopTracer()


# Module-level singleton. Import-safe with zero env and zero langfuse installed.
tracer = _make_tracer()
