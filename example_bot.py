"""v32 Built on v31 (3.96): Progressive Laser Release, Anti-2v1 Flee Boost & 15-Cube 5th Upgrade Tier."""
import json
import math
import os
import queue
import random
import sys
import threading
import time
from array import array

TAU = 2.0 * math.pi
NL = chr(10)


def wrap(a):
    return (a + math.pi) % TAU - math.pi


def log(*a):
    try:
        print(*a, file=sys.stderr, flush=True)
    except Exception:
        pass


def hyp(ax, ay, bx, by):
    return math.hypot(ax - bx, ay - by)


def clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def pip(x, y, ring):
    inside = False
    n = len(ring) // 2
    j = n - 1
    for i in range(n):
        xi = ring[2 * i]
        yi = ring[2 * i + 1]
        xj = ring[2 * j]
        yj = ring[2 * j + 1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi:
            inside = not inside
        j = i
    return inside


class ArenaMap:
    CELL = 40.0

    def __init__(self, map_dir):
        self.prisms = []
        self.grid = {}
        self.rooftops = []
        self.cover_spots = []
        self.terrain = None
        self.nx = 0
        self.ny = 0
        self.min_x = 0.0
        self.min_y = 0.0
        self.step = 1.0
        try:
            self._load(map_dir)
        except Exception as e:
            log("map load failed:", repr(e))

    def _cell(self, v):
        return int(math.floor(v / self.CELL))

    def _load(self, d):
        with open(os.path.join(d, "v2", "world.json"), "r") as f:
            w = json.load(f)
        for p in w.get("prisms", []):
            ring = p.get("ring") or []
            if len(ring) < 6:
                continue
            xs = ring[0::2]
            ys = ring[1::2]
            minx, miny, maxx, maxy = min(xs), min(ys), max(xs), max(ys)
            base = float(p.get("base", 0.0))
            top = float(p.get("top", 0.0))
            holes = p.get("holes") or []
            prism = {"base": base, "top": top, "ring": ring, "holes": holes,
                     "bbox": (minx, miny, maxx, maxy)}
            idx = len(self.prisms)
            self.prisms.append(prism)
            for cx in range(self._cell(minx), self._cell(maxx) + 1):
                for cy in range(self._cell(miny), self._cell(maxy) + 1):
                    self.grid.setdefault((cx, cy), []).append(idx)
            height = top - base
            cx_, cy_ = (minx + maxx) * 0.5, (miny + maxy) * 0.5
            big = (maxx - minx) >= 14.0 and (maxy - miny) >= 14.0
            if 9.0 <= height <= 18.0 and big and self.inside_prism(prism, cx_, cy_):
                self.rooftops.append((cx_, cy_, top, height))
            if height >= 12.0 and big:
                for k in range(0, len(ring) - 1, 4):
                    vx, vy = ring[k], ring[k + 1]
                    dx, dy = vx - cx_, vy - cy_
                    dd = math.hypot(dx, dy)
                    if dd > 1.0:
                        self.cover_spots.append((vx + dx / dd * 4.5, vy + dy / dd * 4.5))
        tm = w.get("terrain", {}) or {}
        self.nx = int(tm.get("nx", 0))
        self.ny = int(tm.get("ny", 0))
        self.min_x = float(tm.get("minX", 0.0))
        self.min_y = float(tm.get("minY", 0.0))
        self.step = float(tm.get("step", 1.0))
        del w
        tp = os.path.join(d, "v2", "terrain.i16")
        if os.path.exists(tp) and self.nx > 0 and self.ny > 0:
            with open(tp, "rb") as f:
                raw = f.read()
            cnt = self.nx * self.ny
            if len(raw) >= cnt * 2:
                arr = array("h")
                arr.frombytes(raw[: cnt * 2])
                if sys.byteorder == "big":
                    arr.byteswap()
                self.terrain = arr

    def inside_prism(self, p, x, y):
        minx, miny, maxx, maxy = p["bbox"]
        if x < minx or x > maxx or y < miny or y > maxy:
            return False
        if not pip(x, y, p["ring"]):
            return False
        for h in p["holes"]:
            if pip(x, y, h):
                return False
        return True

    def ground_z(self, x, y):
        if self.terrain is None:
            return 0.0
        fx = clamp((x - self.min_x) / self.step, 0.0, self.nx - 1.001)
        fy = clamp((y - self.min_y) / self.step, 0.0, self.ny - 1.001)
        i, j = int(fx), int(fy)
        dx, dy = fx - i, fy - j
        r0 = j * self.nx + i
        r1 = r0 + self.nx
        t = self.terrain
        return (t[r0] * (1 - dx) * (1 - dy) + t[r0 + 1] * dx * (1 - dy) +
                t[r1] * (1 - dx) * dy + t[r1 + 1] * dx * dy) * 0.01

    def blocking_prism(self, x, y, z):
        for idx in self.grid.get((self._cell(x), self._cell(y)), ()):
            p = self.prisms[idx]
            if z + 8.5 < p["base"] or z + 1.3 > p["top"]:
                continue
            if self.inside_prism(p, x, y):
                return p
        return None

    def has_clear_shot(self, x1, y1, z1, x2, y2, z2):
        dist = math.hypot(x2 - x1, y2 - y1)
        if dist < 6.0:
            return True
        n = max(3, min(64, int(dist / 4.0)))
        for s in range(1, n):
            tt = s / n
            if tt * dist < 2.5 or (1.0 - tt) * dist < 2.5:
                continue
            px = x1 + (x2 - x1) * tt
            py = y1 + (y2 - y1) * tt
            pz = z1 + (z2 - z1) * tt
            for idx in self.grid.get((self._cell(px), self._cell(py)), ()):
                p = self.prisms[idx]
                if pz < p["base"] or pz > p["top"] - 0.6:
                    continue
                if self.inside_prism(p, px, py):
                    return False
        return True


UPGRADE_WEIGHTS = {
    "armor": 100, "nanites": 96, "plating": 92, "laser_charge": 88, "laser_damage": 84,
    "jets": 78, "capacitor": 75, "gun_rate": 72, "gun_damage": 68, "gyros": 62,
    "servos": 60, "reactor": 58, "gun_velocity": 55, "lightning_efficiency": 52,
    "lightning_range": 50, "mortar_reload": 10, "mortar_velocity": 8, "gun_mag": 42,
    "sensors": 35,
}
WEAPON_RANK = {"laser": 4, "lightning": 3}


class Bot:
    name = "Lebron3-1"

    def __init__(self, hello):
        cfg = hello.get("config", {}) or {}
        W = cfg.get("weapons", {}) or {}
        self.cw = {k: (W.get(k) or {}) for k in ("gun", "laser", "lightning", "mortar")}
        cubes = cfg.get("cubes", {}) or {}
        self.cube_cap = int(cubes.get("stat_cap", 10))
        self.cube_upgrade_goal = 15
        self.dpc = float(cubes.get("damage_per_cube", 0.04))
        self.reach = float((cfg.get("loot", {}) or {}).get("pickup_radius", 4.0))
        self.step_speed = float((cfg.get("sound", {}) or {}).get("step_speed", 5.0))
        mech = cfg.get("mech", {}) or {}
        self.min_pitch = float(mech.get("min_pitch", -0.8))
        self.max_pitch = float(mech.get("max_pitch", 0.8))
        self.arena = ArenaMap((hello.get("map", {}) or {}).get("dir", "/arena"))
        self.rng = random.Random(int(hello.get("seed", 1)))

        self.prev_pos = None
        self.stuck = 0
        self.escape_until = -1
        self.escape_sign = 1.0
        self.strafe = 1.0
        self.next_flip = 0
        self.deflect_sign = 1
        self.anchor = None
        self.anchor_tick = -999
        self.last_hit_tick = -999
        self.last_hit_bearing = None
        self.last_dmg_tick = -999
        self.last_sound_tick = -999
        self.last_sound_bearing = None
        self.engaged_tick = -999
        self.flee_until = -1
        self.mem = {"cubes": {}, "pickups": {}, "crates": {}}
        self.last_out = None
        self.cur_target_id = None
        self.wp = None
        self.wp_tick = -999
        self.visited = []

    def choose_upgrade(self, offer):
        best_i, best_s = 0, -1
        for i, c in enumerate(offer.get("choices", [])):
            s = UPGRADE_WEIGHTS.get(str(c.get("id", "")).lower(), 20)
            if s > best_s:
                best_s, best_i = s, i
        return best_i

    def wdps(self, kind, d, mult, st=None):
        st = st or {}
        c = self.cw.get(kind, {})
        if kind == "gun":
            if d > 220.0:
                return 0.0
            dmg = float(st.get("damage", c.get("damage", 45.0)))
            cd = max(0.05, float(st.get("cooldown", c.get("cooldown", 0.24))))
            return dmg / cd * max(0.15, 0.95 - d / 240.0) * mult
        if kind == "laser":
            if d > 270.0:
                return 0.0
            dmg = float(st.get("damage", c.get("damage", 315.0)))
            cd = float(st.get("cooldown", c.get("cooldown", 1.65)))
            ch = float(st.get("charge", c.get("charge", 0.75)))
            return dmg * 0.9 / (ch + cd) * 0.8 * mult
        if kind == "lightning":
            if d > 46.0:
                return 0.0
            dmg = float(st.get("damage", c.get("damage", 60.0)))
            cd = max(0.05, float(st.get("cooldown", c.get("cooldown", 0.25))))
            return dmg / cd * 0.85 * mult
        if kind == "mortar":
            if d > 225.0:
                return 0.0
            return 70.0 * mult
        return 0.0

    def clear_dist(self, x, y, z, yaw, grounded):
        cs, sn = math.cos(yaw), math.sin(yaw)
        on_ground = False
        prev = 0.0
        if grounded and self.arena.terrain is not None:
            prev = self.arena.ground_z(x, y)
            on_ground = abs(z - prev) < 1.6
        last_d = 0.0
        for dd in (3.0, 6.0, 10.0, 14.0):
            px, py = x + cs * dd, y + sn * dd
            if self.arena.blocking_prism(px, py, z) is not None:
                return dd
            if on_ground:
                g = self.arena.ground_z(px, py)
                if (g - prev) / (dd - last_d) > 1.25:
                    return dd
                prev = g
            last_d = dd
        return 99.0

    def plan_heading(self, x, y, z, grounded, desired):
        order = [0.0]
        for k in (0.3, 0.6, 0.95, 1.3, 1.7, 2.1, 2.6):
            if self.deflect_sign >= 0:
                order += [k, -k]
            else:
                order += [-k, k]
        order.append(math.pi)
        for off in order:
            yaw = wrap(desired + off)
            if self.clear_dist(x, y, z, yaw, grounded) >= 8.0:
                if off != 0.0:
                    self.deflect_sign = 1 if off > 0 else -1
                return yaw
        return wrap(desired + 1.57)

    def flee_heading(self, x, y, z, grounded, desired, threats):
        cands = []
        for k in range(16):
            yaw = k * TAU / 16.0 - math.pi
            s = math.cos(wrap(yaw - desired))
            cands.append((s, yaw))
        cands.sort(reverse=True)
        best, best_s = None, -9.0
        for s, yaw in cands[:8]:
            if self.clear_dist(x, y, z, yaw, grounded) < 8.0:
                continue
            px, py = x + math.cos(yaw) * 16.0, y + math.sin(yaw) * 16.0
            hid = 0
            for th in threats[:2]:
                ex, ey, ez = th["pos"]
                if not self.arena.has_clear_shot(ex, ey, ez + 8.0, px, py, z + 4.5):
                    hid += 1
            sc = s + 1.1 * hid
            if sc > best_s:
                best_s, best = sc, yaw
        if best is None:
            return self.plan_heading(x, y, z, grounded, desired)
        return best

    def pick_anchor(self, x, y, zone, alive):
        ncx, ncy = zone["next_center"]
        nrad = zone["next_radius"]
        max_r = max(14.0, nrad * 0.86)
        best, best_s = None, -1e9
        for rx, ry, rtop, h in self.arena.rooftops:
            dn = hyp(rx, ry, ncx, ncy)
            if dn > max_r:
                continue
            dm = hyp(rx, ry, x, y)
            if dm > 185.0:
                continue
            s = -dm * 0.60 + (22.0 - abs(h - 13.0)) * 1.6
            if nrad * 0.42 <= dn <= max_r:
                s += 65.0
            if alive <= 6:
                s += 35.0
            if s > best_s:
                best_s, best = s, (rx, ry, rtop)
        if alive > 5 and self.arena.cover_spots:
            near = []
            for sx, sy in self.arena.cover_spots:
                dn = hyp(sx, sy, ncx, ncy)
                if dn <= max_r:
                    dm = hyp(sx, sy, x, y)
                    if dm <= 165.0:
                        near.append((dm, dn, sx, sy))
            near.sort()
            for dm, dn, sx, sy in near[:12]:
                gz = self.arena.ground_z(sx, sy)
                if self.arena.blocking_prism(sx, sy, gz) is not None:
                    continue
                if not self.arena.has_clear_shot(sx, sy, gz + 6.0, ncx, ncy, gz + 6.0):
                    s = -dm * 0.60 + 82.0 + (45.0 if dn >= nrad * 0.45 else 0.0)
                    if s > best_s:
                        best_s, best = s, (sx, sy, gz)
        if best is not None:
            return best
        ang = math.atan2(y - ncy, x - ncx)
        return (ncx + math.cos(ang) * max_r * 0.72, ncy + math.sin(ang) * max_r * 0.72, 0.0)

    def pick_waypoint(self, x, y, zone, t):
        ncx, ncy = zone["next_center"]
        nrad = zone["next_radius"]
        zcx, zcy = zone["center"]
        zrad = zone["radius"]
        if t > zone["shrink_start"] - 40.0:
            cx, cy, rr = ncx, ncy, nrad * 0.9
        else:
            cx, cy, rr = zcx, zcy, zrad - 40.0
        best = None
        for _ in range(14):
            ang = self.rng.uniform(-math.pi, math.pi)
            dd = self.rng.uniform(50.0, 140.0)
            px, py = x + math.cos(ang) * dd, y + math.sin(ang) * dd
            if hyp(px, py, cx, cy) > rr:
                continue
            gz = self.arena.ground_z(px, py)
            if self.arena.blocking_prism(px, py, gz) is not None:
                continue
            nv = min([hyp(px, py, vx, vy) for vx, vy in self.visited] or [999.0])
            if nv < 45.0:
                continue
            if best is None or nv > best[0]:
                best = (nv, px, py, gz)
        if best is None:
            return None
        self.visited.append((best[1], best[2]))
        if len(self.visited) > 10:
            self.visited.pop(0)
        return (best[1], best[2], best[3])

    def update_mem(self, obs, x, y, tick):
        for key in ("cubes", "pickups", "crates"):
            cur = {}
            for o in obs.get(key, []) or []:
                cur[o["id"]] = o
            mem = self.mem[key]
            for i, o in cur.items():
                p = o["pos"]
                mem[i] = (p[0], p[1], p[2], tick, o.get("kind"), o.get("hp", 0))
            for i in list(mem.keys()):
                if i in cur:
                    continue
                e = mem[i]
                keep = 1200 if key == "pickups" else (600 if key == "crates" else 260)
                if hyp(e[0], e[1], x, y) < 20.0 or tick - e[3] > keep:
                    del mem[i]

    def lead_point(self, mx, my, mz, pos, vel, cz, speed):
        px, py, pz = pos[0], pos[1], pos[2] + cz
        vx, vy = vel[0], vel[1]
        tt = math.dist((px, py, pz), (mx, my, mz)) / speed
        for _ in range(3):
            qx, qy = px + vx * (tt + 0.05), py + vy * (tt + 0.05)
            tt = math.dist((qx, qy, pz), (mx, my, mz)) / speed
        return px + vx * (tt + 0.05), py + vy * (tt + 0.05), pz

    def aim_to(self, mx, my, mz, tx, ty, tz):
        dx, dy = tx - mx, ty - my
        h = math.hypot(dx, dy)
        yaw = math.atan2(dy, dx)
        pitch = clamp(math.atan2(tz - mz, max(0.1, h)), self.min_pitch, self.max_pitch)
        return yaw, pitch

    def act(self, obs):
        t0 = time.perf_counter()
        tick = int(obs["tick"])
        t = float(obs["t"])
        me = obs["self"]
        x, y, z = me["pos"][0], me["pos"][1], me["pos"][2]
        stats = me.get("stats", {}) or {}
        hp = float(me["hp"])
        max_hp = max(1.0, float(me["max_hp"]))
        hpf = hp / max_hp
        energy = float(me["energy"])
        grounded = bool(me["grounded"])
        cubes = int(me["cubes"])
        in_zone = bool(me.get("in_zone", True))
        ayaw, apitch = float(me["aim"][0]), float(me["aim"][1])
        walk = float(stats.get("walk_speed", 11.7))
        muzzle_h = float(stats.get("muzzle_height", 7.0))
        alive = int(obs.get("alive", 16))
        out = {"tick": tick}

        if obs.get("offer"):
            out["pick"] = self.choose_upgrade(obs["offer"])

        if tick >= self.next_flip:
            self.strafe *= -1.0
            self.next_flip = tick + self.rng.randint(8, 22)

        if self.prev_pos is not None and hyp(x, y, self.prev_pos[0], self.prev_pos[1]) < 0.15:
            self.stuck += 1
        else:
            self.stuck = 0
        self.prev_pos = (x, y, z)

        for ev in obs.get("events", []) or []:
            if ev.get("type") == "damaged" and ev.get("weapon") != "zone":
                self.last_dmg_tick = tick
                if ev.get("bearing") is not None:
                    self.last_hit_tick = tick
                    self.last_hit_bearing = float(ev["bearing"])
        for s in obs.get("sounds", []) or []:
            if (s.get("kind") in ("gun", "laser", "laser_charge", "lightning", "mortar", "explosion")
                    and s.get("dist") in ("near", "mid") and s.get("bearing") is not None):
                self.last_sound_tick = tick
                self.last_sound_bearing = float(s["bearing"])

        self.update_mem(obs, x, y, tick)

        weapons = me["weapons"]
        w0 = weapons[0] if len(weapons) > 0 else None
        w1 = weapons[1] if len(weapons) > 1 else None
        best_p = None
        for p in obs.get("pickups", []) or []:
            if hyp(p["pos"][0], p["pos"][1], x, y) > self.reach * 0.95 or abs(p["pos"][2] - z) > 3.8:
                continue
            k = p.get("kind")
            rk = WEAPON_RANK.get(k, 0)
            if rk == 0:
                continue
            if (w0 and w0["kind"] == k) or (w1 and w1["kind"] == k):
                continue
            if best_p is None or rk > WEAPON_RANK.get(best_p["kind"], 0):
                best_p = p
        if best_p is not None:
            if w1 is None:
                out["take"] = best_p["id"]
            elif WEAPON_RANK.get(best_p["kind"], 0) > WEAPON_RANK.get(w1["kind"], 0):
                if not (w1["kind"] == "laser" and w1.get("charge", 0) > 0):
                    out["take"] = best_p["id"]
                    out["drop"] = 1

        mine = [w for w in weapons if w]
        mine_kinds = [w["kind"] for w in mine]
        mult_me = 1.0 + self.dpc * min(cubes, self.cube_cap)

        zone = obs["zone"]
        zcx, zcy = zone["center"]
        zrad = zone["radius"]
        ncx, ncy = zone["next_center"]
        nrad = zone["next_radius"]
        d_curr = hyp(x, y, zcx, zcy)
        d_next = hyp(x, y, ncx, ncy)
        urgent_zone = ((not in_zone) or (d_curr > zrad - 22.0) or
                       (d_next > nrad - 14.0 and t > zone["shrink_start"] - 18.0))

        if (self.anchor is None or tick - self.anchor_tick > 55 or
                hyp(self.anchor[0], self.anchor[1], ncx, ncy) > nrad * 0.88):
            self.anchor = self.pick_anchor(x, y, zone, alive)
            self.anchor_tick = tick

        enemies, npcs = [], []
        for m in obs.get("mechs", []) or []:
            m["_d"] = hyp(m["pos"][0], m["pos"][1], x, y)
            (enemies if m.get("kind") == "mech" else npcs).append(m)
        enemies.sort(key=lambda m: m["_d"])
        npcs.sort(key=lambda m: m["_d"])
        contacts = [m for m in enemies if m["_d"] <= 210.0]
        for i, m in enumerate(contacts):
            if i < 6:
                ex, ey, ez = m["pos"]
                sc = 1.0 + 0.05 * m.get("cubes", 0)
                m["_clear"] = self.arena.has_clear_shot(x, y, z + muzzle_h, ex, ey, ez + 4.5 * sc)
            else:
                m["_clear"] = False

        # Fight / Flee evaluator (fixed close-range & 2v1 thresholds)
        target = None
        fight = False
        R = 0.0
        threat_total = 0.0
        if contacts:
            for m in contacts:
                kinds = list(m.get("weapons") or ["gun"])
                if "gun" not in kinds:
                    kinds.append("gun")
                m["_kinds"] = kinds
                mm = 1.0 + self.dpc * min(m.get("cubes", 0), self.cube_cap)
                bear = math.atan2(y - m["pos"][1], x - m["pos"][0])
                eyaw = m.get("aim", [0.0, 0.0])[0]
                face = 1.0 if abs(wrap(bear - eyaw)) < 0.7 else 0.55
                m["_threat"] = face * sum(self.wdps(k, m["_d"], mm) for k in kinds)
                threat_total += m["_threat"]

            def tscore(m):
                s = m["_d"] + float(m.get("hp", 1000.0)) * 0.08
                if not m["_clear"]:
                    s += 70.0
                if m["id"] == self.cur_target_id:
                    s -= 15.0
                return s

            target = min(contacts, key=tscore)
            d_t = target["_d"]
            my_dps = sum(self.wdps(w["kind"], d_t, mult_me, w.get("stats")) for w in mine)
            t_hp = float(target.get("hp", target.get("max_hp", 1400.0)))
            ttk_me = t_hp / max(8.0, my_dps)
            ttk_them = hp / max(8.0, threat_total)
            tri = 1.0
            ek = target["_kinds"]
            if "laser" in mine_kinds and "mortar" in ek:
                tri *= 1.25
            if "lightning" in mine_kinds and "laser" in ek:
                tri *= 1.2
            if "lightning" in ek and "laser" in mine_kinds and "lightning" not in mine_kinds:
                tri *= 0.8
            R = ttk_them / max(0.1, ttk_me) * tri
            engaged = tick - self.engaged_tick < 25
            # Don't commit to losing 2-to-1 close-range trades; penalize 2v1s
            thr = 1.25 if d_t > 100.0 else (0.95 if d_t > 45.0 else 0.80)
            if len(contacts) >= 2:
                thr += 0.35
            if engaged:
                thr *= 0.85
            if tick < self.flee_until:
                thr *= 1.25
            if "laser" in mine_kinds:
                thr *= 0.85
            elif "lightning" not in mine_kinds:
                thr *= 1.3
            fight = R >= thr
            if fight:
                self.engaged_tick = tick
                self.cur_target_id = target["id"]
            else:
                self.flee_until = max(self.flee_until, tick + 18)

        goal = None
        goal_kind = "anchor"
        mode = "calm"
        desired = None
        speed = 1.0
        boost = False
        jump = False
        weave = False
        farm = None
        stand_off = 0.0

        if contacts and fight:
            mode = "fight"
        elif contacts:
            mode = "flee"

        lacks_laser = ("laser" not in mine_kinds)
        farm_r = 100.0 if lacks_laser else 62.0

        if not contacts and hpf > 0.5:
            cand = []
            if cubes < self.cube_upgrade_goal:
                cand += [n for n in npcs if n["_d"] < (farm_r if cubes < self.cube_cap else 50.0)]
            for c in obs.get("crates", []) or []:
                dd = hyp(c["pos"][0], c["pos"][1], x, y)
                if dd < farm_r:
                    cand.append({"pos": c["pos"], "vel": [0.0, 0.0, 0.0], "_d": dd, "_crate": True,
                                 "id": c["id"]})
            cand.sort(key=lambda m: m["_d"])
            for c in cand[:3]:
                cz = 1.5 if c.get("_crate") else 1.8
                if self.arena.has_clear_shot(x, y, z + muzzle_h, c["pos"][0], c["pos"][1], c["pos"][2] + cz):
                    farm = c
                    break

        if mode == "calm":
            for dr in obs.get("drops", []) or []:
                if (dr.get("lands_in", 99.0) <= 12.0 and dr.get("holder") in (None, me["id"])
                        and hpf > 0.4 and hyp(dr["pos"][0], dr["pos"][1], x, y) < 150.0 and not urgent_zone):
                    goal = (dr["pos"][0], dr["pos"][1], dr["pos"][2])
                    goal_kind = "drop"
                    break
            if goal is None and not urgent_zone and hpf > (0.35 if lacks_laser else 0.62):
                best = None
                lacks = lacks_laser
                for i, e in self.mem["pickups"].items():
                    k = e[4]
                    rk = WEAPON_RANK.get(k, 0)
                    if rk == 0 or k in mine_kinds:
                        continue
                    if w1 is not None and rk <= WEAPON_RANK.get(w1["kind"], 0):
                        continue
                    dd = hyp(e[0], e[1], x, y)
                    lim = 280.0 if (lacks or w1 is None) else 90.0
                    if dd > lim or abs(e[2] - z) > 13.0:
                        continue
                    if e[2] - z > 3.0 and energy < 55.0:
                        continue
                    sc = dd - rk * 25.0
                    if best is None or sc < best[0]:
                        best = (sc, e)
                if best is not None:
                    e = best[1]
                    goal = (e[0], e[1], e[2])
                    goal_kind = "weapon"
                # Grab cubes up to 15 (tighter 50m radius past 10 to unlock 5th upgrade without overextending)
                if goal is None and cubes < self.cube_upgrade_goal:
                    cube_r = 85.0 if cubes < self.cube_cap else 50.0
                    bc = None
                    for i, e in self.mem["cubes"].items():
                        dd = hyp(e[0], e[1], x, y)
                        if dd < cube_r and abs(e[2] - z) < 13.0 and (e[2] - z < 3.0 or energy > 55.0):
                            if bc is None or dd < bc[0]:
                                bc = (dd, e)
                    if bc is not None:
                        goal = (bc[1][0], bc[1][1], bc[1][2])
                        goal_kind = "cube"
                if goal is None and (cubes < self.cube_cap or lacks_laser):
                    bt = None
                    for n in npcs:
                        if cubes < self.cube_cap and n["_d"] < 75.0 and abs(n["pos"][2] - z) < 10.0:
                            bt = (n["_d"], n["pos"], 50.0)
                            break
                    for i, e in self.mem["crates"].items():
                        dd = hyp(e[0], e[1], x, y)
                        if dd < (130.0 if lacks_laser else 75.0) and abs(e[2] - z) < 10.0 and (bt is None or dd < bt[0]):
                            bt = (dd, (e[0], e[1], e[2]), 38.0)
                    if bt is not None:
                        goal = (bt[1][0], bt[1][1], bt[1][2])
                        goal_kind = "farm"
                        stand_off = bt[2]
            if goal is None and lacks_laser and not urgent_zone and t < 240.0 and hpf > 0.5:
                if self.wp is None or hyp(self.wp[0], self.wp[1], x, y) < 14.0 or tick - self.wp_tick > 300:
                    self.wp = self.pick_waypoint(x, y, zone, t)
                    self.wp_tick = tick
                if self.wp is not None:
                    goal = self.wp
                    goal_kind = "explore"
            if goal is None:
                goal = self.anchor
                goal_kind = "anchor"

        gx, gy, gz = goal if goal is not None else (self.anchor[0], self.anchor[1], self.anchor[2])
        dist_goal = hyp(gx, gy, x, y)
        goal_yaw = math.atan2(gy - y, gx - x)

        tcl = None
        if mode == "fight" and target is not None:
            ex, ey, ez = target["pos"]
            d_t = target["_d"]
            away = math.atan2(y - ey, x - ex)
            ek = target["_kinds"]
            if "lightning" in mine_kinds and "lightning" not in ek:
                pref = 30.0
            elif "lightning" in ek and "lightning" not in mine_kinds:
                pref = 80.0
            elif "laser" in mine_kinds:
                pref = 130.0
            else:
                pref = 85.0
            if not target["_clear"]:
                radial = -1.0
            elif d_t > pref + 18.0:
                radial = -0.9
            elif d_t < pref - 18.0:
                radial = 0.9
            else:
                radial = 0.0
            sa = away + self.strafe * (math.pi * 0.5)
            vx_ = radial * math.cos(away) + 0.95 * math.cos(sa)
            vy_ = radial * math.sin(away) + 0.95 * math.sin(sa)
            desired = math.atan2(vy_, vx_)
            speed = 1.0
            if not target["_clear"]:
                speed = 0.9
            tcl = target
        elif mode == "flee":
            ax = ay = 0.0
            for m in contacts[:4]:
                dd = max(8.0, m["_d"])
                ax += (x - m["pos"][0]) / dd / (dd + 20.0)
                ay += (y - m["pos"][1]) / dd / (dd + 20.0)
            nrm = math.hypot(ax, ay)
            if nrm > 1e-9:
                ax, ay = ax / nrm, ay / nrm
            zx, zy = (ncx - x), (ncy - y)
            zn = max(1.0, math.hypot(zx, zy))
            vx_ = ax * 0.75 + zx / zn * 0.30
            vy_ = ay * 0.75 + zy / zn * 0.30
            desired = math.atan2(vy_, vx_)
            near_t = contacts[0]["_d"]
            # Boost into cover whenever close OR actively taking fire
            boost = bool((near_t < 85.0 or (tick - self.last_dmg_tick <= 15)) and energy > 30.0)
            tcl = target
        else:
            if urgent_zone:
                desired = math.atan2(ncy - y, ncx - x) if in_zone else math.atan2(zcy - y, zcx - x)
                boost = bool((not in_zone) and energy > 20.0)
            elif goal_kind == "anchor" and dist_goal < 4.5 and abs(z - gz) < 2.5:
                oa = (tick * 0.14) % TAU
                desired = math.atan2(gy + math.sin(oa) * 2.8 - y, gx + math.cos(oa) * 2.8 - x)
                speed = min(0.42, self.step_speed * 0.86 / max(1.0, walk))
                weave = True
            elif goal_kind in ("farm",) and dist_goal < stand_off:
                oa = (tick * 0.1) % TAU
                desired = math.atan2(gy + math.sin(oa) * 10.0 - y, gx + math.cos(oa) * 10.0 - x)
                speed = 0.5
            elif goal_kind in ("weapon", "cube", "drop") and dist_goal < 1.5:
                desired = goal_yaw
                speed = 0.3
            else:
                desired = goal_yaw

        if mode in ("fight", "flee") and urgent_zone:
            zyaw = math.atan2(ncy - y, ncx - x) if in_zone else math.atan2(zcy - y, zcx - x)
            vx_ = math.cos(desired) * 0.5 + math.cos(zyaw) * 1.0
            vy_ = math.sin(desired) * 0.5 + math.sin(zyaw) * 1.0
            desired = math.atan2(vy_, vx_)
            if not in_zone and energy > 20.0:
                boost = True

        dodge_yaw = None
        best_tt = 9.0
        for pr in obs.get("projectiles", []) or []:
            if pr.get("kind") == "npc":
                continue
            vx2, vy2, vz2 = pr["vel"]
            v2 = vx2 * vx2 + vy2 * vy2 + vz2 * vz2
            if v2 < 1.0:
                continue
            rx, ry, rz = x - pr["pos"][0], y - pr["pos"][1], (z + 4.5) - pr["pos"][2]
            tt = (rx * vx2 + ry * vy2 + rz * vz2) / v2
            if tt < 0.1 or tt > 1.2:
                continue
            cx2, cy2, cz2 = rx - vx2 * tt, ry - vy2 * tt, rz - vz2 * tt
            miss = math.sqrt(cx2 * cx2 + cy2 * cy2 + cz2 * cz2)
            lim = 9.5 if pr.get("kind") == "mortar" else 4.2
            if miss < lim and tt < best_tt:
                best_tt = tt
                if math.hypot(cx2, cy2) > 0.4:
                    dodge_yaw = math.atan2(cy2, cx2)
                else:
                    dodge_yaw = math.atan2(vy2, vx2) + self.strafe * math.pi * 0.5
        for m in contacts[:5]:
            if m.get("charging") and m["_d"] < 270.0:
                ex, ey = m["pos"][0], m["pos"][1]
                bear = math.atan2(y - ey, x - ex)
                eyaw = m.get("aim", [0.0, 0.0])[0]
                if abs(wrap(bear - eyaw)) < 0.22:
                    side = math.sin(wrap(bear - eyaw))
                    sgn = 1.0 if side >= 0 else -1.0
                    dodge_yaw = eyaw + sgn * math.pi * 0.5
                    if energy > 28.0:
                        boost = True
                    break
        if dodge_yaw is not None:
            if urgent_zone and not in_zone:
                vx_ = math.cos(desired) + 0.6 * math.cos(dodge_yaw)
                vy_ = math.sin(desired) + 0.6 * math.sin(dodge_yaw)
                desired = math.atan2(vy_, vx_)
            else:
                desired = dodge_yaw
                speed = 1.0
                weave = False
            if energy > 40.0 and best_tt < 0.8:
                boost = True

        move_yaw = desired
        climb_ok = (gz > z + 2.5 and dist_goal < 45.0 and mode == "calm" and goal_kind != "farm")
        slow_for_time = (time.perf_counter() - t0) > 0.030
        if speed > 0.05 and not weave:
            if self.escape_until > tick:
                move_yaw = wrap(desired + self.escape_sign * 1.4)
            else:
                wall = self.arena.blocking_prism(x + math.cos(desired) * 5.0, y + math.sin(desired) * 5.0, z)
                if (wall is not None and climb_ok and (wall["top"] - z) <= 18.5 and
                        ((not grounded and energy > 10.0) or (grounded and energy >= 38.0))):
                    if grounded:
                        jump = True
                    boost = True
                elif slow_for_time:
                    move_yaw = desired
                elif mode == "flee" and contacts:
                    move_yaw = self.flee_heading(x, y, z, grounded, desired, contacts)
                else:
                    move_yaw = self.plan_heading(x, y, z, grounded, desired)
        if not grounded and gz > z + 0.5 and dist_goal < 28.0 and energy > 10.0 and mode == "calm":
            boost = True

        if self.stuck >= 5 and speed > 0.05 and not weave and self.escape_until <= tick:
            self.escape_until = tick + 8
            self.escape_sign = 1.0 if self.rng.random() < 0.5 else -1.0
            if energy > 25.0:
                if grounded:
                    jump = True
                boost = True

        out["move"] = [math.cos(move_yaw) * speed, math.sin(move_yaw) * speed]
        out["boost"] = bool(boost)
        out["jump"] = bool(jump)

        fire = [False, False]
        gun_i = laser_i = lt_i = None
        for i, w in enumerate(weapons):
            if not w:
                continue
            if w["kind"] == "gun":
                gun_i = i
            elif w["kind"] == "laser":
                laser_i = i
            elif w["kind"] == "lightning":
                lt_i = i

        aim_t = None
        aim_is_mech = False
        if tcl is not None and tcl.get("_clear") and tcl["_d"] <= 268.0:
            sc = 1.0 + 0.05 * tcl.get("cubes", 0)
            aim_t = (tcl["pos"], tcl.get("vel", [0.0, 0.0, 0.0]), 4.5 * sc, tcl["_d"])
            aim_is_mech = True
        elif mode == "calm" and farm is not None:
            cz = 1.5 if farm.get("_crate") else 1.8
            aim_t = (farm["pos"], farm.get("vel", [0.0, 0.0, 0.0]), cz, farm["_d"])

        mx, my, mz = x, y, z + muzzle_h
        if aim_t is not None:
            tpos, tvel, tcz, td = aim_t
            gspeed = 252.0
            if gun_i is not None:
                gspeed = float(weapons[gun_i].get("stats", {}).get("speed", 252.0))
            lx, ly, lz = self.lead_point(mx, my, mz, tpos, tvel, tcz, max(50.0, gspeed))
            lead_yaw, lead_pitch = self.aim_to(mx, my, mz, lx, ly, lz)
            dir_yaw, dir_pitch = self.aim_to(mx, my, mz, tpos[0], tpos[1], tpos[2] + tcz)
            cmd_yaw, cmd_pitch = lead_yaw, lead_pitch

            if laser_i is not None and aim_is_mech:
                lw = weapons[laser_i]
                charge = float(lw.get("charge", 0.0) or 0.0)
                lst = lw.get("stats", {}) or {}
                full = float(lst.get("charge", self.cw["laser"].get("charge", 0.75)))
                ready = float(lw.get("cooldown", 0.0)) <= 0.06
                if charge > 0.0:
                    fire[laser_i] = True
                    if charge >= full - 0.04:
                        cmd_yaw, cmd_pitch = dir_yaw, dir_pitch
                        # Progressive tolerance so we don't walk at 50% speed for 2s chasing a 0.015 rad lock
                        over = max(0.0, charge - full)
                        tol_l = clamp(math.atan2(2.4, max(10.0, td)) * (0.95 + over * 2.5), 0.016, 0.14)
                        err_l = math.hypot(wrap(dir_yaw - ayaw) * math.cos(apitch), (dir_pitch - apitch) * 0.5)
                        if err_l < tol_l or over > 0.45:
                            fire[laser_i] = False
                elif ready and mode == "fight" and td <= 262.0:
                    fire[laser_i] = True

            err = math.hypot(wrap(cmd_yaw - ayaw) * math.cos(apitch), (cmd_pitch - apitch) * 0.5)
            if gun_i is not None:
                gw = weapons[gun_i]
                gst = gw.get("stats", {}) or {}
                grange = float(gst.get("range", 230.0))
                ammo = float(gw.get("ammo", 1))
                rel = float(gw.get("reload", 0.0) or 0.0)
                tol_g = clamp(math.atan2(2.6, max(8.0, td)) * 1.5, 0.025, 0.14)
                if ammo > 0 and rel <= 0.0 and td <= grange * 0.93:
                    if err < tol_g:
                        fire[gun_i] = True
                elif ammo <= 0 and rel <= 0.0:
                    out["reload"] = True
            if lt_i is not None and td <= 43.0:
                e_l = abs(wrap(dir_yaw - ayaw))
                ok_energy = energy > 14.0 and (aim_is_mech or energy > 60.0)
                if e_l < 0.40 and ok_energy:
                    fire[lt_i] = True
            out["aim"] = [cmd_yaw, cmd_pitch]
        else:
            if laser_i is not None and float(weapons[laser_i].get("charge", 0.0) or 0.0) > 0.0:
                fire[laser_i] = False
            look = None
            if mode in ("fight", "flee") and contacts:
                c0 = target if target is not None else contacts[0]
                look = math.atan2(c0["pos"][1] - y, c0["pos"][0] - x)
            elif tick - self.last_hit_tick < 30 and self.last_hit_bearing is not None:
                look = self.last_hit_bearing
            elif tick - self.last_sound_tick < 12 and self.last_sound_bearing is not None:
                look = self.last_sound_bearing
            elif weave:
                look = (tick * 0.09) % TAU
            else:
                look = move_yaw + 1.8 * math.sin(tick * 0.12)
            out["aim"] = [wrap(look), 0.0]
            if gun_i is not None:
                gw = weapons[gun_i]
                if not contacts and float(gw.get("ammo", 12)) < 6 and float(gw.get("reload", 0.0) or 0.0) <= 0.0:
                    out["reload"] = True

        out["fire"] = [bool(fire[0]), bool(fire[1])]
        return out


def send(obj):
    sys.stdout.write(json.dumps(obj, separators=(",", ":")) + NL)
    sys.stdout.flush()


def main():
    q = queue.Queue()

    def reader():
        try:
            for line in sys.stdin:
                q.put(line)
        except Exception:
            pass
        q.put(None)

    threading.Thread(target=reader, daemon=True).start()

    hello = None
    while hello is None:
        raw = q.get()
        if raw is None:
            return
        try:
            m = json.loads(raw)
        except Exception:
            continue
        if m.get("type") == "hello":
            hello = m
        elif m.get("type") == "end":
            return
    bot = Bot(hello)
    send({"type": "ready", "name": Bot.name})

    while True:
        raw = q.get()
        batch = [raw]
        try:
            while True:
                batch.append(q.get_nowait())
        except queue.Empty:
            pass
        obs = None
        for r in batch:
            if r is None:
                return
            try:
                m = json.loads(r)
            except Exception:
                continue
            tp = m.get("type")
            if tp == "end":
                return
            if tp == "obs":
                obs = m
        if obs is None:
            continue
        try:
            act = bot.act(obs)
        except Exception as e:
            log("act error:", repr(e))
            act = {"tick": obs.get("tick", 0)}
            lo = bot.last_out
            if lo:
                for k in ("move", "boost", "aim"):
                    if k in lo:
                        act[k] = lo[k]
        bot.last_out = act
        send(act)


if __name__ == "__main__":
    main()
