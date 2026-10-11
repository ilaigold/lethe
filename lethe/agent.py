"""Lethe operator agent: plain-English erasure requests -> a plan -> a human confirms -> the removal runs.

The language model is Gemma running on the Arduino UNO Q (llama.cpp server, OpenAI-compatible endpoint). It only
turns text into a small JSON intent. It never executes anything, never sees patient rows, and cannot widen a plan:
ids are validated against the registry, and nothing runs until a human types CONFIRM.

    GEMMA_URL=http://<board>.local:<port>/v1/chat/completions   (unset -> offline rule parser)
    GEMMA_MODEL=gemma-3-1b-it                                    (the name your llama.cpp server reports)
    GEMMA_TIMEOUT=30                                             (seconds; falls back to the offline parser)

Every Gemma call is a Sentry gen_ai.chat span and every tool a gen_ai.execute_tool span (see lethe/obs.py).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request

from . import obs

CUSTOMERS = {"acme": "Acme Robotics", "brightline": "Brightline Freight", "cobalt": "Cobalt Payroll",
             "dunmore": "Dunmore Health", "elmstead": "Elmstead Credit", "fernway": "Fernway Retail",
             "granite": "Granite Insurance", "halcyon": "Halcyon Travel"}
SYSTEM = ('Reply with JSON only: {"action": "forget_patients"|"remove_customers"|"status"|"unknown", '
          '"patients": [ints], "customers": [slugs]}. Customers: ' + ", ".join(CUSTOMERS) + ".")
AGENT = "Lethe operator"


# ---------------------------------------------------------------- intent
def offline_intent(msg: str) -> dict:
    m = msg.lower()
    pats = [int(x) for x in re.findall(r"\b(?:patient|pt|id)\s*#?\s*(\d{3,7})", m)]
    pats += [int(x) for x in re.findall(r"\band\s+#?(\d{3,7})\b", m)] if pats else []
    custs = [s for s, n in CUSTOMERS.items() if s in m or n.lower() in m]
    if any(w in m for w in ("forget", "erase", "delete", "remove", "offboard", "leav")):
        if pats:
            return {"action": "forget_patients", "patients": sorted(set(pats)), "customers": []}
        if custs:
            return {"action": "remove_customers", "patients": [], "customers": custs}
    if "status" in m or "show" in m:
        return {"action": "status", "patients": [], "customers": []}
    return {"action": "unknown", "patients": [], "customers": []}


def _validate(raw: dict) -> dict:
    act = raw.get("action")
    if act not in ("forget_patients", "remove_customers", "status", "unknown"):
        act = "unknown"
    pats = [int(p) for p in raw.get("patients") or [] if str(p).isdigit()][:5]
    custs = [str(c).lower() for c in raw.get("customers") or [] if str(c).lower() in CUSTOMERS][:3]
    return {"action": act, "patients": pats, "customers": custs}


def gemma_intent(msg: str) -> tuple[dict | None, str]:
    url, model = os.environ.get("GEMMA_URL"), os.environ.get("GEMMA_MODEL", "gemma-3-1b-it")
    if not url:
        return None, "offline (GEMMA_URL not set)"
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": msg}]
    body = {"model": model, "messages": messages, "temperature": 0, "max_tokens": 60,
            "response_format": {"type": "json_object"}}
    t = time.time()
    with obs.gen_ai_chat(model, "llama.cpp", messages, system=SYSTEM, agent=AGENT) as sp:
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=float(os.environ.get("GEMMA_TIMEOUT", "30"))) as r:
                res = json.loads(r.read())
        except Exception as e:
            sp.set_status("internal_error")
            sp.set_data("error.type", type(e).__name__)
            return None, f"offline (Gemma unreachable: {type(e).__name__})"
        text = res["choices"][0]["message"]["content"]
        usage = res.get("usage") or {}
        sp.set_data("gen_ai.response.model", res.get("model", model))
        sp.set_data("gen_ai.usage.input_tokens", usage.get("prompt_tokens", 0))
        sp.set_data("gen_ai.usage.output_tokens", usage.get("completion_tokens", 0))
        sp.set_data("gen_ai.response.finish_reasons", json.dumps([res["choices"][0].get("finish_reason")]))
        if os.environ.get("LETHE_SENTRY_PROMPTS") == "1":
            sp.set_data("gen_ai.output.messages", json.dumps(
                [{"role": "assistant", "parts": [{"type": "text", "content": text}]}]))
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return _validate(json.loads(m.group(0))), f"{model} via llama.cpp ({time.time() - t:.1f}s)"
    except Exception:
        return None, f"offline (Gemma reply was not valid JSON: {text[:60]!r})"


# ---------------------------------------------------------------- plan + run
def plan(intent: dict) -> dict:
    with obs.gen_ai_tool("plan_erasure", "dry-run plan: what retrains, what is deleted", intent, agent=AGENT) as sp:
        out = {"intent": intent, "steps": [], "ok": True}
        if intent["action"] == "forget_patients":
            from .tabular import engine

            st = engine.load_state()
            for p in intent["patients"]:
                k = st["home"].get(str(p))
                if k is None:
                    out["ok"] = False
                    out["steps"].append(f"patient {p}: not registered -- nothing to do")
                elif engine.pseudonym(p) in st["revoked"]:
                    out["ok"] = False
                    out["steps"].append(f"patient {p}: already removed")
                else:
                    v = st["shards"][k]
                    out["steps"].append(f"patient {p}: delete their rows in {st['source'].split(':')[0]}, retrain "
                                        f"shard {k} ({v['n_rows']} rows) from scratch; {len(st['shards']) - 1} "
                                        f"other shards untouched; issue a deletion certificate")
        elif intent["action"] == "remove_customers":
            for c in intent["customers"]:
                out["steps"].append(f"customer {CUSTOMERS[c]}: retrain its LLM shard without its records "
                                    f"(python -m lethe.revoke --remove {c}); issue a deletion certificate")
        else:
            out["ok"] = intent["action"] == "status"
        sp.set_data("gen_ai.tool.call.result", json.dumps(out))
        return out


def run_turn(message: str, assume_yes: bool = False) -> int:
    obs.init("lethe-agent")
    with obs.gen_ai_agent(AGENT, os.environ.get("GEMMA_MODEL", "offline")):
        intent, provider = gemma_intent(message)
        if intent is None or intent["action"] == "unknown":
            fallback = offline_intent(message)
            if intent is None or fallback["action"] != "unknown":
                intent, provider = fallback, provider if intent is None else provider + " + offline parser"
        print(f"[agent: {provider}]")
        print(f"intent: {json.dumps(intent)}")
        if intent["action"] == "status":
            from .tabular import engine

            engine.status(engine.Out())
            return 0
        pl = plan(intent)
        for s in pl["steps"]:
            print(f"  - {s}")
        if not pl["ok"] or not pl["steps"]:
            print("No executable plan. Nothing was changed.")
            return 1
        if not assume_yes:
            try:
                answer = input("Type CONFIRM to run this plan: ").strip()
            except EOFError:
                answer = ""
            if answer != "CONFIRM":
                print("Not confirmed. Nothing was changed.")
                return 1
        actor = {"requested_by": f"agent proposal ({provider})", "confirmed_by": "human operator"}
        if intent["action"] == "forget_patients":
            from pathlib import Path

            from .tabular import engine

            with obs.gen_ai_tool("forget_patients", "exact removal after human confirmation",
                                 {"patients": intent["patients"]}, agent=AGENT):
                engine.forget(intent["patients"], engine.Out(),
                              engine.ART / "runs" / time.strftime("agent-%Y%m%d-%H%M%S"), actor=actor)
        else:
            cmd = [sys.executable, "-m", "lethe.revoke"] + sum([["--remove", c] for c in intent["customers"]], [])
            with obs.gen_ai_tool("remove_customers", "LLM shard retrain after human confirmation",
                                 {"customers": intent["customers"]}, agent=AGENT):
                return subprocess.call(cmd)
    return 0
