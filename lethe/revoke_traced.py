"""The live LLM removal (`python -m lethe.revoke`) with Sentry tracing, without touching revoke.py.

    SENTRY_DSN=... uv run python -m lethe.revoke_traced --remove acme --out artifacts/runs/live

One Sentry transaction per run; each printed step ([1] PROBE ... [7] CUSTOMER VERIFIES) becomes a span with its
measured duration. A crash is captured as an exception. Same arguments and output as lethe.revoke.
"""
from __future__ import annotations

import re
import sys
import time

from . import obs, revoke


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not obs.init("lethe-llm"):
        return revoke.main(argv)
    slugs = [argv[i + 1] for i, a in enumerate(argv) if a == "--remove" and i + 1 < len(argv)]
    orig_head = revoke.Out.head
    pending: dict = {}  # a step whose header printed before its work: closed when the next header prints

    def name(title):
        return re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")

    def head(self, n, title, secs=None):
        now = time.time()
        if pending:
            obs.record_finished(pending["name"], max(0.0, now - pending["t"] - (secs or 0)), step=pending["n"])
            pending.clear()
        if secs is not None:  # header printed after the work, with its measured time
            obs.record_finished(name(title), secs, step=str(n))
        else:
            pending.update(name=name(title), t=now, n=str(n))
        return orig_head(self, n, title, secs)

    revoke.Out.head = head
    with obs.transaction(f"remove customer {'+'.join(slugs) or '-'}", op="lethe.llm.revoke", customers=slugs):
        rc = revoke.main(argv)
        if pending:
            obs.record_finished(pending["name"], time.time() - pending["t"], step=pending["n"])
        return rc


if __name__ == "__main__":
    sys.exit(main())
