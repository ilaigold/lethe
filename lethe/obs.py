"""Sentry tracing for Lethe: one transaction per removal, one span per stage, and AI-agent spans for every Gemma call.

No-op unless SENTRY_DSN is set, so the demo never depends on the network.

    SENTRY_DSN=...                  turn it on
    SENTRY_ENVIRONMENT=hackathon    optional
    LETHE_SENTRY_PROMPTS=1          also send prompt/response text to Sentry's AI view (demo data is synthetic)
"""
from __future__ import annotations

import contextlib
import json
import os
import time

_on = False


def init(component: str) -> bool:
    global _on
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn or _on:
        return _on
    import sentry_sdk

    sentry_sdk.init(dsn=dsn, traces_sample_rate=1.0, environment=os.environ.get("SENTRY_ENVIRONMENT", "hackathon"),
                    send_default_pii=os.environ.get("LETHE_SENTRY_PROMPTS") == "1", release="lethe@0.1.0")
    sentry_sdk.set_tag("component", component)
    _on = True
    return True


def enabled() -> bool:
    return _on


class _Null:
    def set_data(self, *a, **k):
        pass

    def set_tag(self, *a, **k):
        pass

    def set_status(self, *a, **k):
        pass


@contextlib.contextmanager
def transaction(name: str, op: str = "lethe.removal", **data):
    """Root of a trace (a removal job, an agent turn)."""
    if not _on:
        yield _Null()
        return
    import sentry_sdk

    nested = sentry_sdk.get_current_span() is not None  # e.g. a removal started by the agent: one trace, not two
    ctx = sentry_sdk.start_span(op=op, name=name) if nested else sentry_sdk.start_transaction(op=op, name=name)
    with ctx as tx:
        for k, v in data.items():
            tx.set_data(k, v)
        try:
            yield tx
        except Exception as e:
            tx.set_status("internal_error")
            sentry_sdk.capture_exception(e)
            raise
        finally:
            if not nested:
                sentry_sdk.flush(timeout=5)


def record_finished(name: str, seconds: float, **data):
    """A span for a step that has already run (used to trace the existing LLM CLI without changing it)."""
    if not _on:
        return
    from datetime import datetime, timedelta, timezone

    import sentry_sdk

    start = datetime.now(timezone.utc) - timedelta(seconds=seconds or 0)
    sp = sentry_sdk.start_span(op=f"lethe.{name}", name=name, start_timestamp=start)
    for k, v in data.items():
        sp.set_data(k, v)
    sp.finish()


@contextlib.contextmanager
def stage(name: str, **data):
    """One pipeline stage (probe, delete_from_source, retrain_shard, ...) inside the current transaction."""
    if not _on:
        yield _Null()
        return
    import sentry_sdk

    with sentry_sdk.start_span(op=f"lethe.{name}", name=name) as sp:
        for k, v in data.items():
            sp.set_data(k, v)
        yield sp


@contextlib.contextmanager
def gen_ai_chat(model: str, provider: str, messages: list[dict], system: str = "", agent: str = "Lethe operator"):
    """Sentry AI-agent span for one LLM call (op gen_ai.chat). The caller fills the response via .set_data."""
    if not _on:
        yield _Null()
        return
    import sentry_sdk

    prompts = os.environ.get("LETHE_SENTRY_PROMPTS") == "1"
    with sentry_sdk.start_span(op="gen_ai.chat", name=f"chat {model}") as sp:
        sp.set_data("gen_ai.operation.name", "chat")
        sp.set_data("gen_ai.request.model", model)
        sp.set_data("gen_ai.provider.name", provider)
        sp.set_data("gen_ai.system", provider)
        sp.set_data("gen_ai.agent.name", agent)
        if prompts:
            sp.set_data("gen_ai.system_instructions", system)
            sp.set_data("gen_ai.input.messages", json.dumps(
                [{"role": m["role"], "parts": [{"type": "text", "content": m["content"]}]} for m in messages]))
        yield sp


@contextlib.contextmanager
def gen_ai_tool(tool: str, description: str, arguments: dict, agent: str = "Lethe operator"):
    if not _on:
        yield _Null()
        return
    import sentry_sdk

    with sentry_sdk.start_span(op="gen_ai.execute_tool", name=f"execute_tool {tool}") as sp:
        sp.set_data("gen_ai.operation.name", "execute_tool")
        sp.set_data("gen_ai.agent.name", agent)
        sp.set_data("gen_ai.tool.name", tool)
        sp.set_data("gen_ai.tool.description", description)
        sp.set_data("gen_ai.tool.call.arguments", json.dumps(arguments))
        try:
            yield sp
        except Exception as e:
            sp.set_status("internal_error")
            sp.set_data("error.type", type(e).__name__)
            raise


@contextlib.contextmanager
def gen_ai_agent(name: str = "Lethe operator", model: str = ""):
    if not _on:
        yield _Null()
        return
    import sentry_sdk

    with sentry_sdk.start_transaction(op="gen_ai.invoke_agent", name=f"invoke_agent {name}") as tx:
        tx.set_data("gen_ai.operation.name", "invoke_agent")
        tx.set_data("gen_ai.agent.name", name)
        tx.set_data("gen_ai.request.model", model)
        t0 = time.time()
        try:
            yield tx
        finally:
            tx.set_data("lethe.seconds", round(time.time() - t0, 3))
            sentry_sdk.flush(timeout=5)


def metric(name: str, value: float, **tags):
    """Attach a measured number (e.g. canary exposure) to the current span so it shows in the trace."""
    if not _on:
        return
    import sentry_sdk

    sp = sentry_sdk.get_current_span()
    if sp is not None:
        sp.set_data(name, value)
        for k, v in tags.items():
            sp.set_tag(k, v)
