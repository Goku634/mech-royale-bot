"""v3 Rooftop High-Ground + Cube Farmer + Aggressive Counter-Fire AI."""
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


class ArenaMap:
    def __init__(self, map_dir: str):
        self.prisms = []
        self.rooftops = []
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
                    base = float(p.get("base", 0.0))
                    top = float(p.get("top", 0.0))
                    cx, cy = (minx + maxx) * 0.5, (miny + maxy) * 0.5
                    prism = {
                        "base": base,
                        "top": top,
                        "ring": ring,
                        "holes": p.get("holes", []),
                        "bbox": (minx, miny, maxx, maxy),
                        "center": (cx, cy),
                    }
                    self.prisms.append(prism)
                    # Climbable rooftops (8m to 48m tall, big enough to stand & strafe on)
                    height = top - base
                    if 8.0 <= height <= 48.0 and (maxx - minx) >= 14.0 and (maxy - miny) >= 14.0:
                        if point_in_poly(cx, cy, ring) and not any(point_in_poly(cx, cy, h) for h in p.get("holes", [])):
                            self.rooftops.append((cx, cy, top))

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

    def roof_under(self, x: float, y: float) -> float:
        best_top = -999.0
        for p in self.prisms:
            minx, miny, maxx, maxy = p["bbox"]
            if minx <= x <= maxx and miny <= y <= maxy:
                if point_in_poly(x, y, p["ring"]):
                    if not any(point_in_poly(x, y, h) for h in p["holes"]):
                        if p["top"] > best_top:
                            best_top = p["top"]
        return best_top


class Bot:
    name = "Lebron3-1"

    def __init__(self, hello: mf.Hello):
        cfg = hello.get("config", {})
        self.reach = float(cfg.get("loot", {}).get("pickup_radius", 4.0))
        map_dir = hello.get("map", {}).get("dir", "/arena")
        self.arena = ArenaMap(map_dir)

        self.prev_pos = None
        self.stuck_ticks = 0
        self.last_threat_yaw = None
        self.last_threat_tick = -999
        self.strafe_dir = 1.0
        self.roof_goal = None
        self.roof_tick = -999

    def _choose_upgrade(self, offer: dict) -> int:
        choices = offer.get("choices", [])
        priority = ["armor", "hp", "regen", "laser", "lightning", "gun", "speed", "energy", "sensors"]
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

    def _pick_rooftop(self, x: float, y: float, z: float, zone: dict) -> tuple:
        ncx, ncy = zone["next_center"]
        nrad = zone["next_radius"]
        max_r = max(14.0, nrad * 0.85)

        best = None
        best_score = -1e9
        for rx, ry, rtop in self.arena.rooftops:
            d_next = math.hypot(rx - ncx, ry - ncy)
            if d_next > max_r:
                continue
            d_me = math.hypot(rx - x, ry - y)
            if d_me > 180.0:
                continue
            # Prefer mid-ring rooftops (away from dead center) that are close and 12-35m high
            score = -d_me * 0.7 + min(32.0, rtop) * 1.5
            if nrad * 0.35 <= d_next <= max_r:
                score += 40.0
            if score > best_score:
                best_score = score
                best = (rx, ry, rtop)

        if best is not None:
            return best
        ang = math.atan2(y - ncy, x - ncx)
        return (ncx + math.cos(ang) * (max_r * 0.65), ncy + math.sin(ang) * (max_r * 0.65), 0.0)

    def act(self, obs: mf.Observation) -> mf.Action:
        tick = obs["tick"]
        t = obs["t"]
        me = obs["self"]
        x, y, z = me["pos"]
        hp_frac = me["hp"] / max(1.0, me["max_hp"])
        energy = float(me["energy"])
        in_zone = bool(me["in_zone"])
        grounded = bool(me["grounded"])

        if tick % 24 == 0:
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
        npcs = [m for m in obs.get("mechs", []) if m["kind"] == "npc"]
        mechs.sort(key=lambda m: math.dist(m["pos"], me["pos"]))
        npcs.sort(key=lambda m: math.dist(m["pos"], me["pos"]))

        for ev in obs.get("events", []):
            if ev.get("type") == "damaged" and ev.get("bearing") is not None:
                self.last_threat_yaw = ev["bearing"]
                self.last_threat_tick = tick

        for s in obs.get("sounds", []):
            if s.get("dist") in ("near", "mid"):
                self.last_threat_yaw = s["bearing"]
                self.last_threat_tick = tick

        # Weapon pickup & upgrade swap
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
        urgent_zone = (not in_zone) or (dist_curr > zrad - 18.0) or (dist_next > nrad - 12.0 and t > zone["shrink_start"] - 14.0)

        # Pick or refresh rooftop fortress
        if (self.roof_goal is None or tick - self.roof_tick > 60 or
                math.hypot(self.roof_goal[0] - ncx, self.roof_goal[1] - ncy) > nrad * 0.88):
            self.roof_goal = self._pick_rooftop(x, y, z, zone)
            self.roof_tick = tick

        goal_x, goal_y, goal_z = self.roof_goal

        # Opportunistic loot farming (cubes -> strong weapons -> crates) when safe
        farming_loot = False
        if not urgent_zone and not mechs and hp_frac > 0.50:
            near_cubes = [c for c in obs.get("cubes", []) if math.dist(c["pos"][:2], (x, y)) < 45.0]
            if near_cubes:
                near_cubes.sort(key=lambda c: math.dist(c["pos"][:2], (x, y)))
                goal_x, goal_y, goal_z = near_cubes[0]["pos"]
                farming_loot = True
            else:
                near_weaps = [p for p in obs.get("pickups", [])
                              if math.dist(p["pos"][:2], (x, y)) < 40.0 and self._weapon_rank(p["kind"]) >= 2]
                if near_weaps:
                    goal_x, goal_y, goal_z = near_weaps[0]["pos"]
                    farming_loot = True

        dist_goal = math.hypot(goal_x - x, goal_y - y)
        move_yaw = math.atan2(goal_y - y, goal_x - x)
        speed_scale = 1.0
        want_boost = False
        want_jump = False

        # Dodge incoming bullets perpendicularly
        incoming = [pr for pr in obs.get("projectiles", []) if math.dist(pr["pos"][:2], (x, y)) < 35.0]
        if incoming:
            pr = incoming[0]
            p_yaw = math.atan2(pr["vel"][1], pr["vel"][0])
            move_yaw = wrap_angle(p_yaw + self.strafe_dir * (math.pi * 0.5))
            want_boost = bool(energy > 35.0)
        elif urgent_zone:
            move_yaw = math.atan2(ncy - y, ncx - x)
            want_boost = bool((not in_zone) and energy > 20.0)
        elif mechs:
            closest = mechs[0]
            ex, ey, ez = closest["pos"]
            edist = math.hypot(ex - x, ey - y)
            away_yaw = math.atan2(y - ey, x - ex)
            strafe_yaw = away_yaw + self.strafe_dir * (math.pi * 0.45)

            # If we are already on high ground (z > ez + 5), strafe near roof center to use roof edge as cover!
            if z > ez + 5.0 and dist_goal < 14.0:
                if hp_frac < 0.55:
                    # Back away from enemy edge to break LOS and regenerate 45 HP/s
                    move_yaw = away_yaw
                else:
                    move_yaw = strafe_yaw
                    speed_scale = 0.75
            elif hp_frac < 0.50 or len(mechs) >= 2:
                # Retreat toward rooftop or away from swarm
                move_yaw = math.atan2(
                    0.6 * math.sin(move_yaw) + 0.4 * math.sin(away_yaw),
                    0.6 * math.cos(move_yaw) + 0.4 * math.cos(away_yaw),
                )
                want_boost = bool(energy > 25.0)
            else:
                # Kite & strafe at optimal range (~65m)
                radial = 0.25 if edist < 65.0 else -0.20
                move_yaw = math.atan2(
                    radial * math.sin(away_yaw) + 0.85 * math.sin(strafe_yaw),
                    radial * math.cos(away_yaw) + 0.85 * math.cos(strafe_yaw),
                )
        else:
            if dist_goal < 3.5 and abs(z - goal_z) < 2.0:
                speed_scale = 0.0

        # Jet-climb logic when approaching a rooftop target!
        if not farming_loot and goal_z > z + 2.0 and dist_goal < 26.0 and energy > 22.0:
            if grounded:
                want_jump = True
            want_boost = True

        # Wall sliding & obstacle unsticking
        if speed_scale > 0.05 and not want_boost:
            probe_x = x + math.cos(move_yaw) * 4.5
            probe_y = y + math.sin(move_yaw) * 4.5
            if self.arena.is_blocked(probe_x, probe_y, z):
                # If blocked by our target rooftop building, climb it!
                if dist_goal < 28.0 and goal_z > z + 2.0 and energy > 25.0:
                    if grounded:
                        want_jump = True
                    want_boost = True
                else:
                    for offset in (0.6, -0.6, 1.2, -1.2, 1.8, -1.8, 2.4):
                        alt = wrap_angle(move_yaw + offset)
                        if not self.arena.is_blocked(x + math.cos(alt) * 5.0, y + math.sin(alt) * 5.0, z):
                            move_yaw = alt
                            break

        if self.stuck_ticks >= 4 and speed_scale > 0.05:
            move_yaw = wrap_angle(move_yaw + (1.4 if (tick // 8) % 2 == 0 else -1.4))
            if energy > 25.0:
                if grounded:
                    want_jump = True
                want_boost = True

        act["move"] = [math.cos(move_yaw) * speed_scale, math.sin(move_yaw) * speed_scale]
        act["boost"] = bool(want_boost)
        act["jump"] = bool(want_jump)

        # Combat: Focus weakest enemy mech -> nearby NPCs -> nearby crates (for cubes/weapons!)
        fire = [False, False]
        target_pos = None
        target_vel = [0.0, 0.0, 0.0]
        target_h = 4.5

        if mechs:
            # Focus fire on lowest-HP / closest enemy to eliminate threats fast and steal cubes
            target = min(mechs, key=lambda m: math.dist(m["pos"], me["pos"]) + m.get("hp", 1000) * 0.06)
            target_pos = target["pos"]
            target_vel = target.get("vel", [0.0, 0.0, 0.0])
            target_h = 4.5
        elif npcs and me["cubes"] < 10 and hp_frac > 0.55 and math.dist(npcs[0]["pos"][:2], (x, y)) < 65.0:
            target_pos = npcs[0]["pos"]
            target_vel = npcs[0].get("vel", [0.0, 0.0, 0.0])
            target_h = 1.8
        elif obs.get("crates") and me["cubes"] < 10 and hp_frac > 0.65:
            crates = sorted(obs["crates"], key=lambda c: math.dist(c["pos"][:2], (x, y)))
            if math.dist(crates[0]["pos"][:2], (x, y)) < 45.0:
                target_pos = crates[0]["pos"]
                target_vel = [0.0, 0.0, 0.0]
                target_h = 1.2

        if target_pos is not None:
            tx, ty, tz = target_pos
            tvx, tvy, tvz = target_vel
            dist = math.hypot(tx - x, ty - y)
            proj_speed = w0.get("stats", {}).get("speed", 252.0) if (w0 and w0["kind"] == "gun") else 252.0
            tof = dist / max(50.0, proj_speed)
            lead_x = tx + tvx * tof
            lead_y = ty + tvy * tof
            lead_z = tz + target_h + tvz * tof
            muzzle_z = z + me.get("stats", {}).get("muzzle_height", 7.0)
            aim_yaw = math.atan2(lead_y - y, lead_x - x)
            aim_pitch = math.atan2(lead_z - muzzle_z, max(1.0, math.hypot(lead_x - x, lead_y - y)))
            act["aim"] = [aim_yaw, aim_pitch]

            aim_err = abs(wrap_angle(aim_yaw - me["aim"][0]))
            if aim_err < 0.24:
                for slot_idx, w in enumerate(me["weapons"]):
                    if w is None:
                        continue
                    kind = w["kind"]
                    if kind == "gun" and dist <= w.get("stats", {}).get("range", 230.0):
                        fire[slot_idx] = True
                    elif kind == "lightning" and dist <= 46.0 and energy > 15.0:
                        fire[slot_idx] = True
                    elif kind == "laser" and dist <= 275.0:
                        fire[slot_idx] = bool(w.get("charge", 0.0) < 0.72)
                    elif kind == "mortar" and 25.0 <= dist <= 185.0:
                        g, v = 22.0, w.get("stats", {}).get("speed", 72.0)
                        arg = min(1.0, max(-1.0, (g * dist) / (v * v)))
                        act["aim"] = [aim_yaw, 0.5 * math.asin(arg)]
                        fire[slot_idx] = True
            mf.draw.line([x, y, muzzle_z], [lead_x, lead_y, lead_z], "#ff5040")
        else:
            if tick - self.last_threat_tick < 60 and self.last_threat_yaw is not None:
                act["aim"] = [self.last_threat_yaw, 0.0]
            else:
                act["aim"] = [wrap_angle(me["aim"][0] + 0.32), 0.0]

            if w0 and w0["kind"] == "gun" and w0.get("ammo", 12) < w0.get("stats", {}).get("mag", 12):
                act["reload"] = True

        act["fire"] = fire
        mf.draw.point([goal_x, goal_y, goal_z], "#40c0ff", 6)
        mf.draw.label(f"v3 Roof | Z:{z:.0f} HP:{me['hp']:.0f} C:{me['cubes']:.0f}")
        return act


mf.run(Bot)
