"""v4 Tactical Rooftop Sniper, Line-of-Sight Regen, Supply Drop & Rear-Burst AI."""
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
                    height = top - base
                    if 9.0 <= height <= 52.0 and (maxx - minx) >= 16.0 and (maxy - miny) >= 16.0:
                        if point_in_poly(cx, cy, ring) and not any(point_in_poly(cx, cy, h) for h in p.get("holes", [])):
                            self.rooftops.append((cx, cy, top, (maxx - minx) * 0.5, (maxy - miny) * 0.5))

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

    def has_line_of_sight(self, x1: float, y1: float, z1: float, x2: float, y2: float, z2: float) -> bool:
        min_sx, max_sx = (x1, x2) if x1 < x2 else (x2, x1)
        min_sy, max_sy = (y1, y2) if y1 < y2 else (y2, y1)
        cand = [
            p for p in self.prisms
            if not (max_sx < p["bbox"][0] or min_sx > p["bbox"][2] or max_sy < p["bbox"][1] or min_sy > p["bbox"][3])
        ]
        if not cand:
            return True
        dist = math.hypot(x2 - x1, y2 - y1)
        steps = max(2, min(14, int(dist / 8.0)))
        for s in range(1, steps):
            t = s / steps
            px = x1 + (x2 - x1) * t
            py = y1 + (y2 - y1) * t
            pz = z1 + (z2 - z1) * t
            for p in cand:
                minx, miny, maxx, maxy = p["bbox"]
                if px < minx or px > maxx or py < miny or py > maxy:
                    continue
                if pz < p["base"] or pz > p["top"]:
                    continue
                if point_in_poly(px, py, p["ring"]):
                    if not any(point_in_poly(px, py, h) for h in p["holes"]):
                        return False
        return True


class Bot:
    name = "Lebron3-1"

    def __init__(self, hello: mf.Hello):
        cfg = hello.get("config", {})
        self.reach = float(cfg.get("loot", {}).get("pickup_radius", 4.0))
        self.step_speed = float(cfg.get("sound", {}).get("step_speed", 5.0))
        map_dir = hello.get("map", {}).get("dir", "/arena")
        self.arena = ArenaMap(map_dir)

        self.prev_pos = None
        self.stuck_ticks = 0
        self.last_threat_yaw = None
        self.last_threat_tick = -999
        self.last_combat_tick = -999
        self.strafe_dir = 1.0
        self.roof_goal = None
        self.roof_tick = -999

    def _choose_upgrade(self, offer: dict) -> int:
        choices = offer.get("choices", [])
        priority = ["armor", "regen", "hp", "laser", "lightning", "speed", "energy", "gun", "sensors"]
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
        max_r = max(14.0, nrad * 0.84)

        best = None
        best_score = -1e9
        for rx, ry, rtop, hx, hy in self.arena.rooftops:
            d_next = math.hypot(rx - ncx, ry - ncy)
            if d_next > max_r:
                continue
            d_me = math.hypot(rx - x, ry - y)
            if d_me > 190.0:
                continue
            # Prefer rooftops on the outer half of next zone (away from center crossfire)
            score = -d_me * 0.65 + min(36.0, rtop) * 1.6
            if nrad * 0.35 <= d_next <= max_r:
                score += 45.0
            if score > best_score:
                best_score = score
                best = (rx, ry, rtop)

        if best is not None:
            return best
        ang = math.atan2(y - ncy, x - ncx)
        return (ncx + math.cos(ang) * (max_r * 0.68), ncy + math.sin(ang) * (max_r * 0.68), 0.0)

    def act(self, obs: mf.Observation) -> mf.Action:
        tick = obs["tick"]
        t = obs["t"]
        me = obs["self"]
        x, y, z = me["pos"]
        hp_frac = me["hp"] / max(1.0, me["max_hp"])
        energy = float(me["energy"])
        in_zone = bool(me["in_zone"])
        grounded = bool(me["grounded"])

        if tick % 22 == 0:
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
            etype = ev.get("type")
            if etype == "damaged":
                self.last_combat_tick = tick
                if ev.get("bearing") is not None:
                    self.last_threat_yaw = ev["bearing"]
                    self.last_threat_tick = tick
            elif etype == "hit":
                self.last_combat_tick = tick

        for s in obs.get("sounds", []):
            if s.get("dist") in ("near", "mid"):
                self.last_threat_yaw = s["bearing"]
                self.last_threat_tick = tick

        # Weapon pickup & swap (always upgrade to Laser / Lightning / Mortar)
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

        if (self.roof_goal is None or tick - self.roof_tick > 65 or
                math.hypot(self.roof_goal[0] - ncx, self.roof_goal[1] - ncy) > nrad * 0.88):
            self.roof_goal = self._pick_rooftop(x, y, z, zone)
            self.roof_tick = tick

        goal_x, goal_y, goal_z = self.roof_goal

        # Check for safe, close supply drops (gives +3 cubes & FULL HEAL!)
        chasing_drop = False
        if not urgent_zone and not mechs:
            for d in obs.get("drops", []):
                dx, dy, dz = d["pos"]
                d_dist = math.hypot(dx - x, dy - y)
                if d_dist < 75.0 and d["lands_in"] <= 6.0 and math.hypot(dx - ncx, dy - ncy) < nrad * 0.85:
                    if d["holder"] in (None, me["id"]):
                        goal_x, goal_y, goal_z = dx, dy, dz
                        chasing_drop = True
                        break

        # Opportunistic loot farming (cubes & high-tier weapons) when safe
        farming_loot = False
        if not urgent_zone and not chasing_drop and not mechs and hp_frac > 0.55:
            near_cubes = [c for c in obs.get("cubes", []) if math.dist(c["pos"][:2], (x, y)) < 45.0]
            if near_cubes:
                near_cubes.sort(key=lambda c: math.dist(c["pos"][:2], (x, y)))
                goal_x, goal_y, goal_z = near_cubes[0]["pos"]
                farming_loot = True
            else:
                near_weaps = [p for p in obs.get("pickups", [])
                              if math.dist(p["pos"][:2], (x, y)) < 40.0 and self._weapon_rank(p["kind"]) >= 3]
                if near_weaps:
                    goal_x, goal_y, goal_z = near_weaps[0]["pos"]
                    farming_loot = True

        dist_goal = math.hypot(goal_x - x, goal_y - y)
        move_yaw = math.atan2(goal_y - y, goal_x - x)
        speed_scale = 1.0
        want_boost = False
        want_jump = False

        # Are we actively trying to trigger 45 HP/s health regeneration?
        need_regen = (hp_frac < 0.62) and (obs["alive"] > 2)

        incoming = [pr for pr in obs.get("projectiles", []) if math.dist(pr["pos"][:2], (x, y)) < 36.0]
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

            if need_regen or len(mechs) >= 2:
                # Pull away from enemy & roof edge to break LOS and trigger 45 HP/s regen!
                move_yaw = math.atan2(
                    0.65 * math.sin(away_yaw) + 0.35 * math.sin(move_yaw),
                    0.65 * math.cos(away_yaw) + 0.35 * math.cos(move_yaw),
                )
                want_boost = bool(hp_frac < 0.45 and energy > 25.0)
            elif z > ez + 5.0 and dist_goal < 14.0:
                # High-ground roof strafing
                move_yaw = strafe_yaw
                speed_scale = 0.75
            else:
                radial = 0.25 if edist < 70.0 else -0.20
                move_yaw = math.atan2(
                    radial * math.sin(away_yaw) + 0.85 * math.sin(strafe_yaw),
                    radial * math.cos(away_yaw) + 0.85 * math.cos(strafe_yaw),
                )
        else:
            if dist_goal < 3.5 and abs(z - goal_z) < 2.0:
                speed_scale = 0.0
            elif not chasing_drop and not farming_loot and hp_frac > 0.75:
                # Silent stealth movement (<5 m/s) when repositioning safely
                walk_spd = me.get("stats", {}).get("walk_speed", 11.7)
                speed_scale = min(1.0, (self.step_speed * 0.88) / max(1.0, walk_spd))

        # Jet-climb onto rooftop fortress
        if not farming_loot and not chasing_drop and goal_z > z + 2.0 and dist_goal < 26.0 and energy > 22.0:
            if grounded:
                want_jump = True
            want_boost = True

        # Building wall sliding
        if speed_scale > 0.05 and not want_boost:
            probe_x = x + math.cos(move_yaw) * 4.5
            probe_y = y + math.sin(move_yaw) * 4.5
            if self.arena.is_blocked(probe_x, probe_y, z):
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

        # Smart Target Selection with Line-of-Sight check + Rear-hit bonus + Regen discipline
        muzzle_z = z + me.get("stats", {}).get("muzzle_height", 7.0)
        fire = [False, False]
        target_pos = None
        target_vel = [0.0, 0.0, 0.0]
        target_h = 4.5

        visible_mechs = [
            m for m in mechs
            if self.arena.has_line_of_sight(x, y, muzzle_z, m["pos"][0], m["pos"][1], m["pos"][2] + 4.5)
        ]

        # Hold fire if we are hurt and trying to trigger 5s passive health regen (unless enemy is point-blank <40m)
        hold_fire_for_regen = need_regen and (not visible_mechs or math.dist(visible_mechs[0]["pos"][:2], (x, y)) > 40.0)

        if visible_mechs and not hold_fire_for_regen:
            def target_score(m):
                d = math.dist(m["pos"], me["pos"])
                hp = m.get("hp", 1000)
                # Bonus if enemy's back is turned to us (1.5x rear damage!)
                to_me = math.atan2(y - m["pos"][1], x - m["pos"][0])
                e_yaw = m.get("aim", [0.0, 0.0])[0]
                is_rear = abs(wrap_angle(to_me - e_yaw)) > (math.pi * 0.5)
                return d + hp * 0.05 - (45.0 if is_rear else 0.0)

            best_m = min(visible_mechs, key=target_score)
            target_pos = best_m["pos"]
            target_vel = best_m.get("vel", [0.0, 0.0, 0.0])
            target_h = 4.5
        elif not hold_fire_for_regen and npcs and me["cubes"] < 10 and hp_frac > 0.65:
            if math.dist(npcs[0]["pos"][:2], (x, y)) < 55.0:
                if self.arena.has_line_of_sight(x, y, muzzle_z, npcs[0]["pos"][0], npcs[0]["pos"][1], npcs[0]["pos"][2] + 1.8):
                    target_pos = npcs[0]["pos"]
                    target_vel = npcs[0].get("vel", [0.0, 0.0, 0.0])
                    target_h = 1.8
        elif not hold_fire_for_regen and obs.get("crates") and me["cubes"] < 10 and hp_frac > 0.75:
            crates = sorted(obs["crates"], key=lambda c: math.dist(c["pos"][:2], (x, y)))
            if math.dist(crates[0]["pos"][:2], (x, y)) < 38.0:
                if self.arena.has_line_of_sight(x, y, muzzle_z, crates[0]["pos"][0], crates[0]["pos"][1], crates[0]["pos"][2] + 1.2):
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
            # Always keep front armor aimed toward closest visible mech or threat bearing
            if mechs:
                act["aim"] = [math.atan2(mechs[0]["pos"][1] - y, mechs[0]["pos"][0] - x), 0.0]
            elif tick - self.last_threat_tick < 60 and self.last_threat_yaw is not None:
                act["aim"] = [self.last_threat_yaw, 0.0]
            else:
                act["aim"] = [wrap_angle(me["aim"][0] + 0.30), 0.0]

            if w0 and w0["kind"] == "gun" and w0.get("ammo", 12) < w0.get("stats", {}).get("mag", 12):
                act["reload"] = True

        act["fire"] = fire
        mf.draw.point([goal_x, goal_y, goal_z], "#40c0ff", 6)
        mf.draw.label(f"v4 | HP:{me['hp']:.0f} C:{me['cubes']:.0f} Alive:{obs['alive']}")
        return act


mf.run(Bot)
