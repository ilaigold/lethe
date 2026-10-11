# Lethe LED: watches the result file the tabular CLI writes after each removal and shows it on the board.
# Point LETHE_RESULT_FILE (on the CLI side) at a path this app can read, e.g. this app's folder.
import json
import os
from pathlib import Path
from time import sleep

from arduino.app_utils import App, Bridge

RESULT = Path(os.environ.get("LETHE_RESULT_FILE", Path(__file__).resolve().parent.parent / "last_result.json"))
seen = {"ts": None}


def loop():
    try:
        r = json.loads(RESULT.read_text())
    except Exception:
        sleep(1)
        return
    if r.get("ts") != seen["ts"]:
        seen["ts"] = r.get("ts")
        print("removal", r.get("patients"), "verified" if r.get("ok") else "FAILED")
        Bridge.call("set_result", bool(r.get("ok")))
    sleep(1)


App.run(user_loop=loop)
