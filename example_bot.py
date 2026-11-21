"""v2 Ghost & Building-Cover AI: breaks line-of-sight behind OSU buildings to beat 15v1 house bots."""
import json
import math
import os
import struct
import mechfight as mf


def wrap_angle(a: float) -> float:
    while a > math.pi:
        a -= 2.0 * math.pi
    while a < -math.pi:
        a += 2.0 * math.pi
    return a


def point_in_poly(x: float, y: float, ring: list) -> bool:
    inside = False
    n = len(ring) // 2
    j = n - 1
    for i in range(n):
        xi, yi = ring[2 * i], ring[2 * i + 1]
        xj, yj = ring[2 * j], ring[2 * j + 1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def seg_intersects_bbox(x1: float, y1: float, x2: float, y2: float, bbox: tuple) -> bool:
    minx, miny, maxx, maxy = bbox
    if max(x1, x2) < minx or min(x1, x2) > maxx or max(y1, y2) < miny or min(y1, y2) > maxy:
        return False
    # Sample points along segment for fast, rock-solid prism checks
    return True


class ArenaMap:
    def __init__(self, map_dir: str):
        self.prisms = []
        self.cover_spots = []
        self.terrain = None
        self.nx = 0
        self.ny = 0
        self.min_x = 0.0
        self.min_y = 0.0
        self.step = 1.0

        world_path = os.path.join(map_dir, "v2", "world.json")
        terr_path = os.path.join(map_dir, "v2", "terrain.i16")
        try:
            with open(world_path, "r") as f:
                w = json.load(f)
            for p in w.get("prisms", []):
                ring = p.get("ring", [])
                if len(ring) >= 6:
                    xs = ring[0::2]
                    ys = ring[1::2]
                    minx, miny, maxx, maxy = min(xs), min(ys), max(xs), max(ys)
                    base = p.get("base", 0.0)
                    top = p.get("top", 0.0)
                    prism = {
                        "kind": p.get("kind", "building"),
                        "base": base,
                        "top": top,
                        "ring": ring,
                        "holes": p.get("holes", []),
                        "bbox": (minx, miny, maxx, maxy),
                        "center": ((minx + maxx) * 0.5, (miny + maxy) * 0.5),
                    }
                    self.prisms.append(prism)
                    # Precompute wall-hugging cover spots around tall buildings
                    if top - base >= 10.0 and (maxx - minx) >= 12.0 and (maxy - miny) >= 12.0:
                        cx, cy = prism["center"]
                        for idx in range(0, len(ring), 2):
                            vx, vy = ring[idx], ring[idx + 1]
                            dx, dy = vx - cx, vy - cy
                            d = math.hypot(dx, dy)
                            if d > 1.0:
                                sx = vx + (dx / d) * 4.5
                                sy = vy + (dy / d) * 4.5
                                self.cover_spots.append((sx, sy, cx, cy))

            t_meta = w.get("terrain", {})
            self.nx = int(t_meta.get("nx", 0))
            self.ny = int(t_meta.get("ny", 0))
            self.min_x = float(t_meta.get("minX", 0.0))
            self.min_y = float(t_meta.get("minY", 0.0))
            self.step = float(t_meta.get("step", 1.0))

            if os.path.exists(terr_path) and self.nx > 0 and self.ny > 0:
                with open(terr_path, "rb") as f:
                    raw = f.read()
                count = self.nx * self.ny
                if len(raw) >= count * 2:
                    self.terrain = struct.unpack(f"<{count}h", raw[: count * 2])
        except Exception as e:
            mf.log(f"Map load warning: {e}")

    def ground_z(self, x: float, y: float) -> float:
        if not self.terrain:
            return 0.0
        fx = max(0.0, min(self.nx - 1.001, (x - self.min_x) / self.step))
        fy = max(0.0, min(self.ny - 1.001, (y - self.min_y) / self.step))
        i, j = int(fx), int(fy)
        dx, dy = fx - i, fy - j
        row0 = j * self.nx
        row1 = (j + 1) * self.nx
        z00 = self.terrain[row0 + i]
        z10 = self.terrain[row0 + i + 1]
        z01 = self.terrain[row1 + i]
        z11 = self.terrain[row1 + i + 1]
        return (z00 * (1 - dx) * (1 - dy) + z10 * dx * (1 - dy) +
                z01 * (1 - dx) * dy + z11 * dx * dy) * 0.01

    def is_blocked(self, x: float, y: float, z: float, pad: float = 2.6) -> bool:
        for p in self.prisms:
            minx, miny, maxx, maxy = p["bbox"]
            if x < minx - pad or x > maxx + pad or y < miny - pad or y > maxy + pad:
                continue
            if z + 8.5 < p["base"] or z + 1.3 > p["top"]:
                continue
            if point_in_poly(x, y, p["ring"]):
                if not any(point_in_poly(x, y, h) for h in p["holes"]):
                    return True
        return False

    def line_blocked(self, x1: float, y1: float, z1: float, x2: float, y2: float, z2: float) -> bool:
        dist = math.hypot(x2 - x1, y2 - y1)
        steps = max(2, int(dist / 4.0))
        for s in range(1, steps):
            t = s / steps
            px = x1 + (x2 - x1) * t
            py = y1 + (y2 - y1) * t
            pz = z1 + (z2 - z1) * t
            for p in self.prisms:
                minx, miny, maxx, maxy = p["bbox"]
                if px < minx or px > maxx or py < miny or py > maxy:
                    continue
                if pz < p["base"] or pz > p["top"]:
                    continue
                if point_in_poly(px, py, p["ring"]):
                    if not any(point_in_poly(px, py, h) for h in p["holes"]):
                        return True
        return False


class Bot:
    name = "Lebron3-1"

    def __init__(self, hello: mf.Hello):
        cfg = hello.get("config", {})
        self.reach = cfg.get("loot", {}).get("pickup_radius", 4.0)
        self.step_speed = cfg.get("sound", {}).get("step_speed", 5.0)
        map_dir = hello.get("map", {}).get("dir", "/arena")
        self.arena = ArenaMap(map_dir)

        self.prev_pos = None
        self.stuck_ticks = 0
        self.last_threat_yaw = None
        self.last_threat_tick = -999
        self.evade_until_tick = -999
        self.strafe_dir = 1.0
        self.cover_target = None
        self.cover_refresh_tick = -999

    def _choose_upgrade(self, offer: dict) -> int:
        choices = offer.get("choices", [])
        priority = ["armor", "hp", "regen", "speed", "energy", "sensors", "laser", "lightning", "gun"]
        best_idx, best_score = 0, -1
        for idx, c in enumerate(choices):
            cid = c.get("id", "").lower()
            desc = c.get("desc", "").lower()
            score = 1
            for rank, kw in enumerate(reversed(priority)):
                if kw in cid or kw in desc:
                    score = max(score, rank + 2)
            if score > best_score:
                best_score = score
                best_idx = idx
        return best_idx

    def _weapon_rank(self, kind: str) -> int:
        return {"laser": 4, "lightning": 3, "mortar": 2, "gun": 1}.get(kind, 0)

    def _pick_best_cover(self, x: float, y: float, z: float, zone: dict, threat_pos=None) -> tuple:
        ncx, ncy = zone["next_center"]
        nrad = zone["next_radius"]
        max_r = max(12.0, nrad * 0.85)
        min_r = max(5.0, nrad * 0.35)

        best_spot = None
        best_score = -1e9

        # Sample candidate cover points around buildings
        for sx, sy, bcx, bcy in self.arena.cover_spots:
            d_next = math.hypot(sx - ncx, sy - ncy)
            if d_next > max_r:
                continue
            gz = self.arena.ground_z(sx, sy)
            if self.arena.is_blocked(sx, sy, gz, pad=2.2):
                continue

            dist_me = math.hypot(sx - x, sy - y)
            if dist_me > 160.0:
                continue

            # Prefer spots near outer safe ring (away from center) and close to current pos
            score = -dist_me * 0.6
            if min_r <= d_next <= max_r:
                score += 45.0

            # Bonus if building sits between the spot and the zone center (natural shield)
            if self.arena.line_blocked(sx, sy, gz + 6.0, ncx, ncy, gz + 6.0):
                score += 65.0

            # Huge bonus if building blocks line-of-sight from active enemy!
            if threat_pos is not None:
                tx, ty, tz = threat_pos
                if self.arena.line_blocked(sx, sy, gz + 6.0, tx, ty, tz + 6.0):
                    score += 140.0
                score += math.hypot(sx - tx, sy - ty) * 0.4

            if score > best_score:
                best_score = score
                best_spot = (sx, sy)

        if best_spot is not None:
            return best_spot

        # Fallback: stay on perimeter away from threat
        if threat_pos is not None:
            away = math.atan2(y - threat_pos[1], x - threat_pos[0])
            fx = ncx + math.cos(away) * (max_r * 0.75)
            fy = ncy + math.sin(away) * (max_r * 0.75)
            return (fx, fy)

        ang = math.atan2(y - ncy, x - ncx)
        return (ncx + math.cos(ang) * (max_r * 0.75), ncy + math.sin(ang) * (max_r * 0.75))

    def act(self, obs: mf.Observation) -> mf.Action:
        tick = obs["tick"]
        t = obs["t"]
        me = obs["self"]
        x, y, z = me["pos"]
        hp_frac = me["hp"] / max(1.0, me["max_hp"])
        energy = me["energy"]
        in_zone = me["in_zone"]

        if tick % 25 == 0:
            self.strafe_dir *= -1.0

        if self.prev_pos is not None:
            if math.hypot(x - self.prev_pos[0], y - self.prev_pos[1]) < 0.15:
                self.stuck_ticks += 1
            else:
                self.stuck_ticks = 0
        self.prev_pos = (x, y, z)

        act: mf.Action = {"tick": tick}

        if obs.get("offer"):
            act["pick"] = self._choose_upgrade(obs["offer"])

        mechs = [m for m in obs.get("mechs", []) if m["kind"] == "mech"]
        mechs.sort(key=lambda m: math.dist(m["pos"], me["pos"]))

        took_damage = False
        for ev in obs.get("events", []):
            if ev.get("type") == "damaged":
                took_damage = True
                if ev.get("bearing") is not None:
                    self.last_threat_yaw = ev["bearing"]
                    self.last_threat_tick = tick
                self.evade_until_tick = tick + 65  # 6.5s evade window to break 5s enemy memory

        for s in obs.get("sounds", []):
            if s.get("dist") == "near" and s.get("kind") in ("gun", "laser", "lightning", "mortar", "boost"):
                self.last_threat_yaw = s["bearing"]
                self.last_threat_tick = tick

        if mechs:
            self.evade_until_tick = max(self.evade_until_tick, tick + 55)

        # Smart silent weapon pickup
        w0, w1 = me["weapons"][0], me["weapons"][1]
        best_pickup = None
        for p in obs.get("pickups", []):
            if math.dist(p["pos"][:2], (x, y)) <= self.reach and abs(p["pos"][2] - z) <= 4.0:
                p_kind = p["kind"]
                if (w0 and w0["kind"] == p_kind) or (w1 and w1["kind"] == p_kind):
                    continue
                if best_pickup is None or self._weapon_rank(p_kind) > self._weapon_rank(best_pickup["kind"]):
                    best_pickup = p

        if best_pickup:
            if w1 is None or w0 is None:
                act["take"] = best_pickup["id"]
            else:
                r0, r1 = self._weapon_rank(w0["kind"]), self._weapon_rank(w1["kind"])
                if self._weapon_rank(best_pickup["kind"]) > min(r0, r1):
                    drop_slot = 1 if r1 <= r0 else 0
                    slot_w = me["weapons"][drop_slot]
                    if not (slot_w and slot_w["kind"] == "laser" and slot_w.get("charge", 0) > 0):
                        act["take"] = best_pickup["id"]
                        act["drop"] = drop_slot

        zone = obs["zone"]
        zcx, zcy = zone["center"]
        zrad = zone["radius"]
        ncx, ncy = zone["next_center"]
        nrad = zone["next_radius"]

        dist_curr = math.hypot(x - zcx, y - zcy)
        dist_next = math.hypot(x - ncx, y - ncy)
        urgent_zone = (not in_zone) or (dist_curr > zrad - 16.0) or (dist_next > nrad - 10.0 and t > zone["shrink_start"] - 14.0)

        threat_pos = mechs[0]["pos"] if mechs else None

        # Refresh cover spot when threatened, zone shifts, or periodically
        if (self.cover_target is None or tick - self.cover_refresh_tick > 40 or
                took_damage or urgent_zone or
                math.hypot(self.cover_target[0] - ncx, self.cover_target[1] - ncy) > nrad * 0.88):
            self.cover_target = self._pick_best_cover(x, y, z, zone, threat_pos)
            self.cover_refresh_tick = tick

        goal_x, goal_y = self.cover_target

        # Grab adjacent cubes only if totally unthreatened
        if not mechs and tick > self.evade_until_tick and not urgent_zone:
            near_cubes = [c for c in obs.get("cubes", []) if math.dist(c["pos"][:2], (x, y)) < 22.0]
            if near_cubes:
                near_cubes.sort(key=lambda c: math.dist(c["pos"][:2], (x, y)))
                goal_x, goal_y = near_cubes[0]["pos"][0], near_cubes[0]["pos"][1]

        dist_goal = math.hypot(goal_x - x, goal_y - y)
        move_yaw = math.atan2(goal_y - y, goal_x - x)
        speed_scale = 1.0
        want_boost = False
        want_jump = False

        # Dodge incoming projectiles perpendicularly!
        incoming = [
            pr for pr in obs.get("projectiles", [])
            if math.dist(pr["pos"][:2], (x, y)) < 38.0
        ]

        if incoming:
            pr = incoming[0]
            p_yaw = math.atan2(pr["vel"][1], pr["vel"][0])
            move_yaw = wrap_angle(p_yaw + self.strafe_dir * (math.pi * 0.5))
            speed_scale = 1.0
            want_boost = energy > 30.0
        elif mechs or tick < self.evade_until_tick:
            # High-speed dash into building cover to break line-of-sight!
            if mechs:
                ex, ey, _ = mechs[0]["pos"]
                away_yaw = math.atan2(y - ey, x - ex)
                # Blend cover direction with away-from-enemy vector
                move_yaw = math.atan2(
                    0.7 * math.sin(move_yaw) + 0.3 * math.sin(away_yaw),
                    0.7 * math.cos(move_yaw) + 0.3 * math.cos(away_yaw),
                )
            elif self.last_threat_yaw is not None and dist_goal < 8.0:
                move_yaw = wrap_angle(self.last_threat_yaw + math.pi)
            speed_scale = 1.0
            want_boost = (took_damage or (mechs and math.dist(mechs[0]["pos"][:2], (x, y)) < 65.0)) and energy > 25.0
        elif urgent_zone:
            move_yaw = math.atan2(goal_y - y, goal_x - x)
            speed_scale = 1.0
            want_boost = (not in_zone) and energy > 20.0
        else:
            # Complete stealth: hold still in cover or walk below step_speed (5.0 m/s = silent)
            if dist_goal < 4.5:
                speed_scale = 0.0
            else:
                walk_spd = me.get("stats", {}).get("walk_speed", 11.7)
                speed_scale = min(1.0, (self.step_speed * 0.85) / max(1.0, walk_spd))

        # Building navigation & obstacle sliding
        if speed_scale > 0.05:
            probe_x = x + math.cos(move_yaw) * 4.5
            probe_y = y + math.sin(move_yaw) * 4.5
            if self.arena.is_blocked(probe_x, probe_y, z):
                for offset in (0.55, -0.55, 1.1, -1.1, 1.65, -1.65, 2.2, -2.2):
                    alt = wrap_angle(move_yaw + offset)
                    if not self.arena.is_blocked(x + math.cos(alt) * 5.0, y + math.sin(alt) * 5.0, z):
                        move_yaw = alt
                        break
                else:
                    if energy > 30.0:
                        want_jump = True
                        want_boost = True

        if self.stuck_ticks >= 4 and speed_scale > 0.05:
            move_yaw = wrap_angle(move_yaw + (1.5 if (tick // 8) % 2 == 0 else -1.5))
            if energy > 25.0:
                want_jump = True
                want_boost = True

        act["move"] = [math.cos(move_yaw) * speed_scale, math.sin(move_yaw) * speed_scale]
        act["boost"] = want_boost
        act["jump"] = want_jump

        # Combat: Only fire when an enemy is close/exposed OR in final 1v1/1v2
        # Never shoot NPCs or distant enemies (shooting reveals location & cancels 45 HP/s regen!)
        fire = [False, False]
        target_actor = None
        if mechs:
            closest = mechs[0]
            cdist = math.dist(closest["pos"][:2], (x, y))
            # Only shoot if enemy is aggressively pushing close (<75m) or endgame (<= 3 alive)
            if cdist < 75.0 or obs["alive"] <= 3 or closest.get("hp", 1000) < 250:
                target_actor = closest

        if target_actor is not None:
            tx, ty, tz = target_actor["pos"]
            tvx, tvy, tvz = target_actor.get("vel", [0.0, 0.0, 0.0])
            dist = math.hypot(tx - x, ty - y)
            proj_speed = w0.get("stats", {}).get("speed", 252.0) if (w0 and w0["kind"] == "gun") else 252.0
            tof = dist / max(50.0, proj_speed)
            lead_x, lead_y, lead_z = tx + tvx * tof, ty + tvy * tof, tz + 4.5 + tvz * tof
            muzzle_z = z + me.get("stats", {}).get("muzzle_height", 7.0)
            aim_yaw = math.atan2(lead_y - y, lead_x - x)
            aim_pitch = math.atan2(lead_z - muzzle_z, max(1.0, math.hypot(lead_x - x, lead_y - y)))
            act["aim"] = [aim_yaw, aim_pitch]

            aim_err = abs(wrap_angle(aim_yaw - me["aim"][0]))
            if aim_err < 0.22:
                for slot_idx, w in enumerate(me["weapons"]):
                    if w is None:
                        continue
                    kind = w["kind"]
                    if kind == "gun" and dist <= w.get("stats", {}).get("range", 230.0):
                        fire[slot_idx] = True
                    elif kind == "lightning" and dist <= 46.0 and energy > 18.0:
                        fire[slot_idx] = True
                    elif kind == "laser" and dist <= 275.0:
                        fire[slot_idx] = (w.get("charge", 0.0) < 0.72)
                    elif kind == "mortar" and 25.0 <= dist <= 180.0:
                        g, v = 22.0, w.get("stats", {}).get("speed", 72.0)
                        arg = min(1.0, max(-1.0, (g * dist) / (v * v)))
                        act["aim"] = [aim_yaw, 0.5 * math.asin(arg)]
                        fire[slot_idx] = True
            mf.draw.line([x, y, muzzle_z], [lead_x, lead_y, lead_z], "#ff5040")
        else:
            # Always face threat direction (front armor prevents 1.5x rear multiplier)
            if mechs:
                act["aim"] = [math.atan2(mechs[0]["pos"][1] - y, mechs[0]["pos"][0] - x), 0.0]
            elif tick - self.last_threat_tick < 60 and self.last_threat_yaw is not None:
                act["aim"] = [self.last_threat_yaw, 0.0]
            else:
                act["aim"] = [wrap_angle(me["aim"][0] + 0.25), 0.0]

            if w0 and w0["kind"] == "gun" and w0.get("ammo", 12) < w0.get("stats", {}).get("mag", 12):
                act["reload"] = True

        act["fire"] = fire
        mf.draw.point([goal_x, goal_y, z], "#40c0ff", 6)
        mf.draw.label(f"v2 Ghost | HP:{me['hp']:.0f} Alive:{obs['alive']}")
        return act


mf.run(Bot)
EOFcat << 'EOF' > ~/mech-bot/example_bot.py
"""v2 Ghost & Building-Cover AI: breaks line-of-sight behind OSU buildings to beat 15v1 house bots."""
import json
import math
import os
import struct
import mechfight as mf


def wrap_angle(a: float) -> float:
    while a > math.pi:
        a -= 2.0 * math.pi
    while a < -math.pi:
        a += 2.0 * math.pi
    return a


def point_in_poly(x: float, y: float, ring: list) -> bool:
    inside = False
    n = len(ring) // 2
    j = n - 1
    for i in range(n):
        xi, yi = ring[2 * i], ring[2 * i + 1]
        xj, yj = ring[2 * j], ring[2 * j + 1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def seg_intersects_bbox(x1: float, y1: float, x2: float, y2: float, bbox: tuple) -> bool:
    minx, miny, maxx, maxy = bbox
    if max(x1, x2) < minx or min(x1, x2) > maxx or max(y1, y2) < miny or min(y1, y2) > maxy:
        return False
    # Sample points along segment for fast, rock-solid prism checks
    return True


class ArenaMap:
    def __init__(self, map_dir: str):
        self.prisms = []
        self.cover_spots = []
        self.terrain = None
        self.nx = 0
        self.ny = 0
        self.min_x = 0.0
        self.min_y = 0.0
        self.step = 1.0

        world_path = os.path.join(map_dir, "v2", "world.json")
        terr_path = os.path.join(map_dir, "v2", "terrain.i16")
        try:
            with open(world_path, "r") as f:
                w = json.load(f)
            for p in w.get("prisms", []):
                ring = p.get("ring", [])
                if len(ring) >= 6:
                    xs = ring[0::2]
                    ys = ring[1::2]
                    minx, miny, maxx, maxy = min(xs), min(ys), max(xs), max(ys)
                    base = p.get("base", 0.0)
                    top = p.get("top", 0.0)
                    prism = {
                        "kind": p.get("kind", "building"),
                        "base": base,
                        "top": top,
                        "ring": ring,
                        "holes": p.get("holes", []),
                        "bbox": (minx, miny, maxx, maxy),
                        "center": ((minx + maxx) * 0.5, (miny + maxy) * 0.5),
                    }
                    self.prisms.append(prism)
                    # Precompute wall-hugging cover spots around tall buildings
                    if top - base >= 10.0 and (maxx - minx) >= 12.0 and (maxy - miny) >= 12.0:
                        cx, cy = prism["center"]
                        for idx in range(0, len(ring), 2):
                            vx, vy = ring[idx], ring[idx + 1]
                            dx, dy = vx - cx, vy - cy
                            d = math.hypot(dx, dy)
                            if d > 1.0:
                                sx = vx + (dx / d) * 4.5
                                sy = vy + (dy / d) * 4.5
                                self.cover_spots.append((sx, sy, cx, cy))

            t_meta = w.get("terrain", {})
            self.nx = int(t_meta.get("nx", 0))
            self.ny = int(t_meta.get("ny", 0))
            self.min_x = float(t_meta.get("minX", 0.0))
            self.min_y = float(t_meta.get("minY", 0.0))
            self.step = float(t_meta.get("step", 1.0))

            if os.path.exists(terr_path) and self.nx > 0 and self.ny > 0:
                with open(terr_path, "rb") as f:
                    raw = f.read()
                count = self.nx * self.ny
                if len(raw) >= count * 2:
                    self.terrain = struct.unpack(f"<{count}h", raw[: count * 2])
        except Exception as e:
            mf.log(f"Map load warning: {e}")

    def ground_z(self, x: float, y: float) -> float:
        if not self.terrain:
            return 0.0
        fx = max(0.0, min(self.nx - 1.001, (x - self.min_x) / self.step))
        fy = max(0.0, min(self.ny - 1.001, (y - self.min_y) / self.step))
        i, j = int(fx), int(fy)
        dx, dy = fx - i, fy - j
        row0 = j * self.nx
        row1 = (j + 1) * self.nx
        z00 = self.terrain[row0 + i]
        z10 = self.terrain[row0 + i + 1]
        z01 = self.terrain[row1 + i]
        z11 = self.terrain[row1 + i + 1]
        return (z00 * (1 - dx) * (1 - dy) + z10 * dx * (1 - dy) +
                z01 * (1 - dx) * dy + z11 * dx * dy) * 0.01

    def is_blocked(self, x: float, y: float, z: float, pad: float = 2.6) -> bool:
        for p in self.prisms:
            minx, miny, maxx, maxy = p["bbox"]
            if x < minx - pad or x > maxx + pad or y < miny - pad or y > maxy + pad:
                continue
            if z + 8.5 < p["base"] or z + 1.3 > p["top"]:
                continue
            if point_in_poly(x, y, p["ring"]):
                if not any(point_in_poly(x, y, h) for h in p["holes"]):
                    return True
        return False

    def line_blocked(self, x1: float, y1: float, z1: float, x2: float, y2: float, z2: float) -> bool:
        dist = math.hypot(x2 - x1, y2 - y1)
        steps = max(2, int(dist / 4.0))
        for s in range(1, steps):
            t = s / steps
            px = x1 + (x2 - x1) * t
            py = y1 + (y2 - y1) * t
            pz = z1 + (z2 - z1) * t
            for p in self.prisms:
                minx, miny, maxx, maxy = p["bbox"]
                if px < minx or px > maxx or py < miny or py > maxy:
                    continue
                if pz < p["base"] or pz > p["top"]:
                    continue
                if point_in_poly(px, py, p["ring"]):
                    if not any(point_in_poly(px, py, h) for h in p["holes"]):
                        return True
        return False


class Bot:
    name = "Lebron3-1"

    def __init__(self, hello: mf.Hello):
        cfg = hello.get("config", {})
        self.reach = cfg.get("loot", {}).get("pickup_radius", 4.0)
        self.step_speed = cfg.get("sound", {}).get("step_speed", 5.0)
        map_dir = hello.get("map", {}).get("dir", "/arena")
        self.arena = ArenaMap(map_dir)

        self.prev_pos = None
        self.stuck_ticks = 0
        self.last_threat_yaw = None
        self.last_threat_tick = -999
        self.evade_until_tick = -999
        self.strafe_dir = 1.0
        self.cover_target = None
        self.cover_refresh_tick = -999

    def _choose_upgrade(self, offer: dict) -> int:
        choices = offer.get("choices", [])
        priority = ["armor", "hp", "regen", "speed", "energy", "sensors", "laser", "lightning", "gun"]
        best_idx, best_score = 0, -1
        for idx, c in enumerate(choices):
            cid = c.get("id", "").lower()
            desc = c.get("desc", "").lower()
            score = 1
            for rank, kw in enumerate(reversed(priority)):
                if kw in cid or kw in desc:
                    score = max(score, rank + 2)
            if score > best_score:
                best_score = score
                best_idx = idx
        return best_idx

    def _weapon_rank(self, kind: str) -> int:
        return {"laser": 4, "lightning": 3, "mortar": 2, "gun": 1}.get(kind, 0)

    def _pick_best_cover(self, x: float, y: float, z: float, zone: dict, threat_pos=None) -> tuple:
        ncx, ncy = zone["next_center"]
        nrad = zone["next_radius"]
        max_r = max(12.0, nrad * 0.85)
        min_r = max(5.0, nrad * 0.35)

        best_spot = None
        best_score = -1e9

        # Sample candidate cover points around buildings
        for sx, sy, bcx, bcy in self.arena.cover_spots:
            d_next = math.hypot(sx - ncx, sy - ncy)
            if d_next > max_r:
                continue
            gz = self.arena.ground_z(sx, sy)
            if self.arena.is_blocked(sx, sy, gz, pad=2.2):
                continue

            dist_me = math.hypot(sx - x, sy - y)
            if dist_me > 160.0:
                continue

            # Prefer spots near outer safe ring (away from center) and close to current pos
            score = -dist_me * 0.6
            if min_r <= d_next <= max_r:
                score += 45.0

            # Bonus if building sits between the spot and the zone center (natural shield)
            if self.arena.line_blocked(sx, sy, gz + 6.0, ncx, ncy, gz + 6.0):
                score += 65.0

            # Huge bonus if building blocks line-of-sight from active enemy!
            if threat_pos is not None:
                tx, ty, tz = threat_pos
                if self.arena.line_blocked(sx, sy, gz + 6.0, tx, ty, tz + 6.0):
                    score += 140.0
                score += math.hypot(sx - tx, sy - ty) * 0.4

            if score > best_score:
                best_score = score
                best_spot = (sx, sy)

        if best_spot is not None:
            return best_spot

        # Fallback: stay on perimeter away from threat
        if threat_pos is not None:
            away = math.atan2(y - threat_pos[1], x - threat_pos[0])
            fx = ncx + math.cos(away) * (max_r * 0.75)
            fy = ncy + math.sin(away) * (max_r * 0.75)
            return (fx, fy)

        ang = math.atan2(y - ncy, x - ncx)
        return (ncx + math.cos(ang) * (max_r * 0.75), ncy + math.sin(ang) * (max_r * 0.75))

    def act(self, obs: mf.Observation) -> mf.Action:
        tick = obs["tick"]
        t = obs["t"]
        me = obs["self"]
        x, y, z = me["pos"]
        hp_frac = me["hp"] / max(1.0, me["max_hp"])
        energy = me["energy"]
        in_zone = me["in_zone"]

        if tick % 25 == 0:
            self.strafe_dir *= -1.0

        if self.prev_pos is not None:
            if math.hypot(x - self.prev_pos[0], y - self.prev_pos[1]) < 0.15:
                self.stuck_ticks += 1
            else:
                self.stuck_ticks = 0
        self.prev_pos = (x, y, z)

        act: mf.Action = {"tick": tick}

        if obs.get("offer"):
            act["pick"] = self._choose_upgrade(obs["offer"])

        mechs = [m for m in obs.get("mechs", []) if m["kind"] == "mech"]
        mechs.sort(key=lambda m: math.dist(m["pos"], me["pos"]))

        took_damage = False
        for ev in obs.get("events", []):
            if ev.get("type") == "damaged":
                took_damage = True
                if ev.get("bearing") is not None:
                    self.last_threat_yaw = ev["bearing"]
                    self.last_threat_tick = tick
                self.evade_until_tick = tick + 65  # 6.5s evade window to break 5s enemy memory

        for s in obs.get("sounds", []):
            if s.get("dist") == "near" and s.get("kind") in ("gun", "laser", "lightning", "mortar", "boost"):
                self.last_threat_yaw = s["bearing"]
                self.last_threat_tick = tick

        if mechs:
            self.evade_until_tick = max(self.evade_until_tick, tick + 55)

        # Smart silent weapon pickup
        w0, w1 = me["weapons"][0], me["weapons"][1]
        best_pickup = None
        for p in obs.get("pickups", []):
            if math.dist(p["pos"][:2], (x, y)) <= self.reach and abs(p["pos"][2] - z) <= 4.0:
                p_kind = p["kind"]
                if (w0 and w0["kind"] == p_kind) or (w1 and w1["kind"] == p_kind):
                    continue
                if best_pickup is None or self._weapon_rank(p_kind) > self._weapon_rank(best_pickup["kind"]):
                    best_pickup = p

        if best_pickup:
            if w1 is None or w0 is None:
                act["take"] = best_pickup["id"]
            else:
                r0, r1 = self._weapon_rank(w0["kind"]), self._weapon_rank(w1["kind"])
                if self._weapon_rank(best_pickup["kind"]) > min(r0, r1):
                    drop_slot = 1 if r1 <= r0 else 0
                    slot_w = me["weapons"][drop_slot]
                    if not (slot_w and slot_w["kind"] == "laser" and slot_w.get("charge", 0) > 0):
                        act["take"] = best_pickup["id"]
                        act["drop"] = drop_slot

        zone = obs["zone"]
        zcx, zcy = zone["center"]
        zrad = zone["radius"]
        ncx, ncy = zone["next_center"]
        nrad = zone["next_radius"]

        dist_curr = math.hypot(x - zcx, y - zcy)
        dist_next = math.hypot(x - ncx, y - ncy)
        urgent_zone = (not in_zone) or (dist_curr > zrad - 16.0) or (dist_next > nrad - 10.0 and t > zone["shrink_start"] - 14.0)

        threat_pos = mechs[0]["pos"] if mechs else None

        # Refresh cover spot when threatened, zone shifts, or periodically
        if (self.cover_target is None or tick - self.cover_refresh_tick > 40 or
                took_damage or urgent_zone or
                math.hypot(self.cover_target[0] - ncx, self.cover_target[1] - ncy) > nrad * 0.88):
            self.cover_target = self._pick_best_cover(x, y, z, zone, threat_pos)
            self.cover_refresh_tick = tick

        goal_x, goal_y = self.cover_target

        # Grab adjacent cubes only if totally unthreatened
        if not mechs and tick > self.evade_until_tick and not urgent_zone:
            near_cubes = [c for c in obs.get("cubes", []) if math.dist(c["pos"][:2], (x, y)) < 22.0]
            if near_cubes:
                near_cubes.sort(key=lambda c: math.dist(c["pos"][:2], (x, y)))
                goal_x, goal_y = near_cubes[0]["pos"][0], near_cubes[0]["pos"][1]

        dist_goal = math.hypot(goal_x - x, goal_y - y)
        move_yaw = math.atan2(goal_y - y, goal_x - x)
        speed_scale = 1.0
        want_boost = False
        want_jump = False

        # Dodge incoming projectiles perpendicularly!
        incoming = [
            pr for pr in obs.get("projectiles", [])
            if math.dist(pr["pos"][:2], (x, y)) < 38.0
        ]

        if incoming:
            pr = incoming[0]
            p_yaw = math.atan2(pr["vel"][1], pr["vel"][0])
            move_yaw = wrap_angle(p_yaw + self.strafe_dir * (math.pi * 0.5))
            speed_scale = 1.0
            want_boost = energy > 30.0
        elif mechs or tick < self.evade_until_tick:
            # High-speed dash into building cover to break line-of-sight!
            if mechs:
                ex, ey, _ = mechs[0]["pos"]
                away_yaw = math.atan2(y - ey, x - ex)
                # Blend cover direction with away-from-enemy vector
                move_yaw = math.atan2(
                    0.7 * math.sin(move_yaw) + 0.3 * math.sin(away_yaw),
                    0.7 * math.cos(move_yaw) + 0.3 * math.cos(away_yaw),
                )
            elif self.last_threat_yaw is not None and dist_goal < 8.0:
                move_yaw = wrap_angle(self.last_threat_yaw + math.pi)
            speed_scale = 1.0
            want_boost = (took_damage or (mechs and math.dist(mechs[0]["pos"][:2], (x, y)) < 65.0)) and energy > 25.0
        elif urgent_zone:
            move_yaw = math.atan2(goal_y - y, goal_x - x)
            speed_scale = 1.0
            want_boost = (not in_zone) and energy > 20.0
        else:
            # Complete stealth: hold still in cover or walk below step_speed (5.0 m/s = silent)
            if dist_goal < 4.5:
                speed_scale = 0.0
            else:
                walk_spd = me.get("stats", {}).get("walk_speed", 11.7)
                speed_scale = min(1.0, (self.step_speed * 0.85) / max(1.0, walk_spd))

        # Building navigation & obstacle sliding
        if speed_scale > 0.05:
            probe_x = x + math.cos(move_yaw) * 4.5
            probe_y = y + math.sin(move_yaw) * 4.5
            if self.arena.is_blocked(probe_x, probe_y, z):
                for offset in (0.55, -0.55, 1.1, -1.1, 1.65, -1.65, 2.2, -2.2):
                    alt = wrap_angle(move_yaw + offset)
                    if not self.arena.is_blocked(x + math.cos(alt) * 5.0, y + math.sin(alt) * 5.0, z):
                        move_yaw = alt
                        break
                else:
                    if energy > 30.0:
                        want_jump = True
                        want_boost = True

        if self.stuck_ticks >= 4 and speed_scale > 0.05:
            move_yaw = wrap_angle(move_yaw + (1.5 if (tick // 8) % 2 == 0 else -1.5))
            if energy > 25.0:
                want_jump = True
                want_boost = True

        act["move"] = [math.cos(move_yaw) * speed_scale, math.sin(move_yaw) * speed_scale]
        act["boost"] = want_boost
        act["jump"] = want_jump

        # Combat: Only fire when an enemy is close/exposed OR in final 1v1/1v2
        # Never shoot NPCs or distant enemies (shooting reveals location & cancels 45 HP/s regen!)
        fire = [False, False]
        target_actor = None
        if mechs:
            closest = mechs[0]
            cdist = math.dist(closest["pos"][:2], (x, y))
            # Only shoot if enemy is aggressively pushing close (<75m) or endgame (<= 3 alive)
            if cdist < 75.0 or obs["alive"] <= 3 or closest.get("hp", 1000) < 250:
                target_actor = closest

        if target_actor is not None:
            tx, ty, tz = target_actor["pos"]
            tvx, tvy, tvz = target_actor.get("vel", [0.0, 0.0, 0.0])
            dist = math.hypot(tx - x, ty - y)
            proj_speed = w0.get("stats", {}).get("speed", 252.0) if (w0 and w0["kind"] == "gun") else 252.0
            tof = dist / max(50.0, proj_speed)
            lead_x, lead_y, lead_z = tx + tvx * tof, ty + tvy * tof, tz + 4.5 + tvz * tof
            muzzle_z = z + me.get("stats", {}).get("muzzle_height", 7.0)
            aim_yaw = math.atan2(lead_y - y, lead_x - x)
            aim_pitch = math.atan2(lead_z - muzzle_z, max(1.0, math.hypot(lead_x - x, lead_y - y)))
            act["aim"] = [aim_yaw, aim_pitch]

            aim_err = abs(wrap_angle(aim_yaw - me["aim"][0]))
            if aim_err < 0.22:
                for slot_idx, w in enumerate(me["weapons"]):
                    if w is None:
                        continue
                    kind = w["kind"]
                    if kind == "gun" and dist <= w.get("stats", {}).get("range", 230.0):
                        fire[slot_idx] = True
                    elif kind == "lightning" and dist <= 46.0 and energy > 18.0:
                        fire[slot_idx] = True
                    elif kind == "laser" and dist <= 275.0:
                        fire[slot_idx] = (w.get("charge", 0.0) < 0.72)
                    elif kind == "mortar" and 25.0 <= dist <= 180.0:
                        g, v = 22.0, w.get("stats", {}).get("speed", 72.0)
                        arg = min(1.0, max(-1.0, (g * dist) / (v * v)))
                        act["aim"] = [aim_yaw, 0.5 * math.asin(arg)]
                        fire[slot_idx] = True
            mf.draw.line([x, y, muzzle_z], [lead_x, lead_y, lead_z], "#ff5040")
        else:
            # Always face threat direction (front armor prevents 1.5x rear multiplier)
            if mechs:
                act["aim"] = [math.atan2(mechs[0]["pos"][1] - y, mechs[0]["pos"][0] - x), 0.0]
            elif tick - self.last_threat_tick < 60 and self.last_threat_yaw is not None:
                act["aim"] = [self.last_threat_yaw, 0.0]
            else:
                act["aim"] = [wrap_angle(me["aim"][0] + 0.25), 0.0]

            if w0 and w0["kind"] == "gun" and w0.get("ammo", 12) < w0.get("stats", {}).get("mag", 12):
                act["reload"] = True

        act["fire"] = fire
        mf.draw.point([goal_x, goal_y, z], "#40c0ff", 6)
        mf.draw.label(f"v2 Ghost | HP:{me['hp']:.0f} Alive:{obs['alive']}")
        return act


mf.run(Bot)
