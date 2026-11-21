"""Survival-first Stealth & Vulture AI for Mech Royale."""
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
        self.trees = []
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
                    self.prisms.append({
                        "kind": p.get("kind", "building"),
                        "base": p.get("base", 0.0),
                        "top": p.get("top", 0.0),
                        "ring": ring,
                        "holes": p.get("holes", []),
                        "bbox": (min(xs), min(ys), max(xs), max(ys)),
                    })
            t_meta = w.get("terrain", {})
            self.nx = int(t_meta.get("nx", 0))
            self.ny = int(t_meta.get("ny", 0))
            self.min_x = float(t_meta.get("minX", 0.0))
            self.min_y = float(t_meta.get("minY", 0.0))
            self.step = float(t_meta.get("step", 1.0))

            t_data = w.get("trees", {})
            fields = t_data.get("fields", [])
            flat = t_data.get("data", [])
            if fields and flat:
                stride = len(fields)
                ix = fields.index("x")
                iy = fields.index("y")
                iz = fields.index("z")
                for k in range(0, len(flat), stride):
                    self.trees.append((flat[k + ix], flat[k + iy], flat[k + iz]))

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
        i = int(fx)
        j = int(fy)
        dx = fx - i
        dy = fy - j
        row0 = j * self.nx
        row1 = (j + 1) * self.nx
        z00 = self.terrain[row0 + i]
        z10 = self.terrain[row0 + i + 1]
        z01 = self.terrain[row1 + i]
        z11 = self.terrain[row1 + i + 1]
        z = (z00 * (1 - dx) * (1 - dy) + z10 * dx * (1 - dy) +
             z01 * (1 - dx) * dy + z11 * dx * dy) * 0.01
        return z

    def is_blocked(self, x: float, y: float, z: float) -> bool:
        for p in self.prisms:
            minx, miny, maxx, maxy = p["bbox"]
            if x < minx - 2.6 or x > maxx + 2.6 or y < miny - 2.6 or y > maxy + 2.6:
                continue
            if z + 8.5 < p["base"] or z + 1.3 > p["top"]:
                continue
            if point_in_poly(x, y, p["ring"]):
                in_hole = any(point_in_poly(x, y, h) for h in p["holes"])
                if not in_hole:
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
        self.scan_yaw = 0.0
        self.strafe_dir = 1.0

    def _choose_upgrade(self, offer: dict) -> int:
        choices = offer.get("choices", [])
        priority = ["armor", "hp", "regen", "speed", "energy", "sensors", "laser", "lightning", "gun"]
        best_idx = 0
        best_score = -1
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

    def act(self, obs: mf.Observation) -> mf.Action:
        tick = obs["tick"]
        t = obs["t"]
        me = obs["self"]
        x, y, z = me["pos"]
        vx, vy, vz = me["vel"]
        hp_frac = me["hp"] / max(1.0, me["max_hp"])
        energy = me["energy"]
        in_zone = me["in_zone"]

        if tick % 35 == 0:
            self.strafe_dir *= -1.0

        # Track stuck state
        if self.prev_pos is not None:
            moved = math.hypot(x - self.prev_pos[0], y - self.prev_pos[1])
            if moved < 0.15:
                self.stuck_ticks += 1
            else:
                self.stuck_ticks = 0
        self.prev_pos = (x, y, z)

        act: mf.Action = {"tick": tick}

        # 1. Upgrade selection
        if obs.get("offer"):
            act["pick"] = self._choose_upgrade(obs["offer"])

        # 2. Threat & Enemy perception
        mechs = [m for m in obs.get("mechs", []) if m["kind"] == "mech"]
        npcs = [m for m in obs.get("mechs", []) if m["kind"] == "npc"]
        mechs.sort(key=lambda m: math.dist(m["pos"], me["pos"]))
        npcs.sort(key=lambda m: math.dist(m["pos"], me["pos"]))

        for ev in obs.get("events", []):
            if ev.get("type") == "damaged" and ev.get("bearing") is not None:
                self.last_threat_yaw = ev["bearing"]
                self.last_threat_tick = tick

        for s in obs.get("sounds", []):
            if s.get("dist") in ("near", "mid") and s.get("kind") in ("gun", "laser", "lightning", "mortar", "boost"):
                self.last_threat_yaw = s["bearing"]
                self.last_threat_tick = tick

        # 3. Weapon pickup & smart swap
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
            if w1 is None:
                act["take"] = best_pickup["id"]
            elif w0 is None:
                act["take"] = best_pickup["id"]
            else:
                r0 = self._weapon_rank(w0["kind"])
                r1 = self._weapon_rank(w1["kind"])
                p_rank = self._weapon_rank(best_pickup["kind"])
                if p_rank > min(r0, r1):
                    drop_slot = 1 if r1 <= r0 else 0
                    # Do not drop a charging laser
                    slot_w = me["weapons"][drop_slot]
                    if not (slot_w and slot_w["kind"] == "laser" and slot_w.get("charge", 0) > 0):
                        act["take"] = best_pickup["id"]
                        act["drop"] = drop_slot

        # 4. Zone & Safe Perimeter Navigation
        zone = obs["zone"]
        zcx, zcy = zone["center"]
        zrad = zone["radius"]
        ncx, ncy = zone["next_center"]
        nrad = zone["next_radius"]

        dist_to_curr = math.hypot(x - zcx, y - zcy)
        dist_to_next = math.hypot(x - ncx, y - ncy)

        # Target a safe band at ~72% of next_radius (avoids center crossfire while staying inside shrink)
        safe_rad = max(12.0, nrad * 0.72)
        angle_from_next = math.atan2(y - ncy, x - ncx)
        target_x = ncx + math.cos(angle_from_next) * min(dist_to_next, safe_rad)
        target_y = ncy + math.sin(angle_from_next) * min(dist_to_next, safe_rad)

        urgent_zone = (not in_zone) or (dist_to_curr > zrad - 18.0) or (dist_to_next > nrad - 12.0 and t > zone["shrink_start"] - 12.0)

        # Opportunistic loot (cubes / close weapons) when safe and not low HP
        if not urgent_zone and hp_frac > 0.55 and not mechs:
            near_cubes = [c for c in obs.get("cubes", []) if math.dist(c["pos"][:2], (x, y)) < 35.0]
            if near_cubes:
                near_cubes.sort(key=lambda c: math.dist(c["pos"][:2], (x, y)))
                target_x, target_y = near_cubes[0]["pos"][0], near_cubes[0]["pos"][1]
            else:
                near_weaps = [p for p in obs.get("pickups", [])
                              if math.dist(p["pos"][:2], (x, y)) < 28.0 and self._weapon_rank(p["kind"]) >= 3]
                if near_weaps:
                    target_x, target_y = near_weaps[0]["pos"][0], near_weaps[0]["pos"][1]

        # Evasion / Kiting when enemies are visible
        move_yaw = math.atan2(target_y - y, target_x - x)
        speed_scale = 1.0
        want_boost = False
        want_jump = False

        if mechs:
            closest = mechs[0]
            ex, ey, ez = closest["pos"]
            edist = math.hypot(ex - x, ey - y)
            away_yaw = math.atan2(y - ey, x - ex)
            strafe_yaw = away_yaw + self.strafe_dir * (math.pi * 0.42)

            if hp_frac < 0.65 or len(mechs) >= 2 or edist < 55.0:
                # Retreat & strafe away while keeping zone safety in mind
                if urgent_zone:
                    move_yaw = math.atan2(ncx - y, ncx - x)
                else:
                    move_yaw = math.atan2(
                        0.65 * math.sin(away_yaw) + 0.35 * math.sin(strafe_yaw),
                        0.65 * math.cos(away_yaw) + 0.35 * math.cos(strafe_yaw),
                    )
                want_boost = (hp_frac < 0.55 and energy > 25.0)
            else:
                # Maintain optimal medium range (~75m) and strafe
                radial = 0.25 if edist < 75.0 else -0.2
                move_yaw = math.atan2(
                    radial * math.sin(away_yaw) + 0.85 * math.sin(strafe_yaw),
                    radial * math.cos(away_yaw) + 0.85 * math.cos(strafe_yaw),
                )
        elif urgent_zone:
            move_yaw = math.atan2(ncx - y, ncx - x)
            want_boost = (not in_zone and energy > 20.0)
        else:
            # Stealth walk when cruising safely inside the perimeter so step sounds aren't emitted
            dist_goal = math.hypot(target_x - x, target_y - y)
            if dist_goal < 6.0:
                speed_scale = 0.0
            else:
                walk_spd = me.get("stats", {}).get("walk_speed", 11.7)
                silent_scale = min(1.0, (self.step_speed * 0.88) / max(1.0, walk_spd))
                speed_scale = silent_scale if hp_frac > 0.75 else 1.0

        # Obstacle avoidance & unsticking using world map
        if speed_scale > 0.05:
            probe_x = x + math.cos(move_yaw) * 4.5
            probe_y = y + math.sin(move_yaw) * 4.5
            if self.arena.is_blocked(probe_x, probe_y, z):
                # Try steering left or right around building
                best_alt = None
                for offset in (0.6, -0.6, 1.2, -1.2, 1.8, -1.8, 2.5):
                    alt = wrap_angle(move_yaw + offset)
                    px = x + math.cos(alt) * 5.0
                    py = y + math.sin(alt) * 5.0
                    if not self.arena.is_blocked(px, py, z):
                        best_alt = alt
                        break
                if best_alt is not None:
                    move_yaw = best_alt
                elif energy > 30.0:
                    want_jump = True
                    want_boost = True

        if self.stuck_ticks >= 4 and speed_scale > 0.05:
            move_yaw = wrap_angle(move_yaw + (1.4 if (tick // 10) % 2 == 0 else -1.4))
            if energy > 25.0:
                want_jump = True
                want_boost = True

        act["move"] = [math.cos(move_yaw) * speed_scale, math.sin(move_yaw) * speed_scale]
        act["boost"] = want_boost
        act["jump"] = want_jump

        # 5. Aiming & Combat (Vulture targeting + Rear armor protection)
        target_actor = None
        if mechs:
            # Prefer wounded enemies or ones closer to us
            target_actor = min(
                mechs,
                key=lambda m: math.dist(m["pos"], me["pos"]) + (m.get("hp", 1000) * 0.05)
            )
        elif npcs and hp_frac > 0.75 and not urgent_zone and me["cubes"] < 8:
            if math.dist(npcs[0]["pos"], me["pos"]) < 65.0:
                target_actor = npcs[0]

        fire = [False, False]
        if target_actor is not None:
            tx, ty, tz = target_actor["pos"]
            tvx, tvy, tvz = target_actor.get("vel", [0.0, 0.0, 0.0])
            dist = math.hypot(tx - x, ty - y)

            # Lead target based on primary projectile speed
            proj_speed = 252.0
            if w0 and w0["kind"] == "gun":
                proj_speed = w0.get("stats", {}).get("speed", 252.0)
            tof = dist / max(50.0, proj_speed)
            lead_x = tx + tvx * tof
            lead_y = ty + tvy * tof
            lead_z = tz + 4.5 + tvz * tof

            muzzle_z = z + me.get("stats", {}).get("muzzle_height", 7.0)
            aim_yaw = math.atan2(lead_y - y, lead_x - x)
            aim_pitch = math.atan2(lead_z - muzzle_z, max(1.0, math.hypot(lead_x - x, lead_y - y)))
            act["aim"] = [aim_yaw, aim_pitch]

            # Decide whether to fire (hold fire if desperately retreating to allow HP regen)
            aim_err = abs(wrap_angle(aim_yaw - me["aim"][0]))
            can_shoot = (aim_err < 0.22) and not (hp_frac < 0.35 and dist > 70.0)

            if can_shoot:
                for slot_idx, w in enumerate(me["weapons"]):
                    if w is None:
                        continue
                    kind = w["kind"]
                    if kind == "gun":
                        rng = w.get("stats", {}).get("range", 230.0)
                        if dist <= rng:
                            fire[slot_idx] = True
                    elif kind == "lightning":
                        if dist <= 46.0 and energy > 18.0:
                            fire[slot_idx] = True
                    elif kind == "laser":
                        charge = w.get("charge", 0.0)
                        if dist <= 275.0:
                            # Hold trigger until ~0.72s charge, then release to fire beam
                            if charge < 0.72:
                                fire[slot_idx] = True
                            else:
                                fire[slot_idx] = False
                    elif kind == "mortar":
                        if 25.0 <= dist <= 180.0:
                            # Ballistic pitch adjustment for mortar
                            g = 22.0
                            v = w.get("stats", {}).get("speed", 72.0)
                            arg = min(1.0, max(-1.0, (g * dist) / (v * v)))
                            mortar_pitch = 0.5 * math.asin(arg)
                            act["aim"] = [aim_yaw, mortar_pitch]
                            fire[slot_idx] = True

            # Reload gun during downtime if ammo is low
            if w0 and w0["kind"] == "gun" and w0.get("ammo", 12) <= 3 and dist > 80.0:
                act["reload"] = True

            mf.draw.line([x, y, muzzle_z], [lead_x, lead_y, lead_z], "#ff5040")
        else:
            # Face last known threat bearing (rear armor protection) or slowly sweep 360°
            if tick - self.last_threat_tick < 45 and self.last_threat_yaw is not None:
                act["aim"] = [self.last_threat_yaw, 0.0]
            else:
                self.scan_yaw = wrap_angle(me["aim"][0] + 0.35)
                act["aim"] = [self.scan_yaw, 0.0]

            if w0 and w0["kind"] == "gun" and w0.get("ammo", 12) < w0.get("stats", {}).get("mag", 12):
                act["reload"] = True

        act["fire"] = fire
        mf.draw.point([target_x, target_y, z], "#40c0ff", 6)
        mf.draw.label(f"HP:{me['hp']:.0f} C:{me['cubes']:.0f} Alive:{obs['alive']}")
        return act


mf.run(Bot)
