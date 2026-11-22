"""floor bot: walk to next zone center, never shoot. Baseline measurement only."""
import json
import math
import sys

NL = chr(10)


def send(o):
    sys.stdout.write(json.dumps(o, separators=(",", ":")) + NL)
    sys.stdout.flush()


def main():
    hello = None
    prev = None
    stuck = 0
    for line in sys.stdin:
        try:
            m = json.loads(line)
        except Exception:
            continue
        tp = m.get("type")
        if tp == "hello":
            send({"type": "ready", "name": "floor-bot"})
            continue
        if tp == "end":
            return
        if tp != "obs":
            continue
        me = m["self"]
        x, y = me["pos"][0], me["pos"][1]
        zn = m["zone"]
        cx, cy = zn["next_center"]
        yaw = math.atan2(cy - y, cx - x)
        if prev is not None and math.hypot(x - prev[0], y - prev[1]) < 0.15:
            stuck += 1
        else:
            stuck = 0
        prev = (x, y)
        jump = False
        if stuck >= 4:
            yaw += 1.4 if (m["tick"] // 10) % 2 == 0 else -1.4
            jump = True
        act = {"tick": m["tick"], "move": [math.cos(yaw), math.sin(yaw)], "boost": False,
               "jump": jump, "aim": [yaw, 0.0], "fire": [False, False]}
        if m.get("offer"):
            act["pick"] = 0
        send(act)


main()
