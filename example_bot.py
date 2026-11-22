"""v29 Edge-Ghost Pure Survival: Quiet Outer-Rim Building Shadows, Silent Movement & Self-Defense Fire."""
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


def poly_area(ring: list) -> float:
    n = len(ring) // 2
    if n < 3:
        return 0.0
    area = 0.0
    j = n - 1
    for i in range(n):
        area += (ring[2 * j] + ring[2 * i]) * (ring[2 * j + 1] - ring[2 * i + 1])
        j = i
    return abs(area) * 0.5


class ArenaMap:
    def __init__(self, map_dir: str):
        self.prisms = []
        self.cover_spots = []
        self.terrain = None
        self.nx = 0
        self.ny = 0
        self.min_x = -513.0
        self.min_y = -334.0
        self.step = 1.0

        world_path = os.path.join(map_dir, "v2", "world.json")
        terr_path = os.path.join(map_dir, "v2", "terrain.i16")
        try:
            with open(world_path, "r") as f:
                w = json.load(f)

            t_meta = w.get("terrain", {})
            self.nx = int(t_meta.get("nx", 0))
            self.ny = int(t_meta.get("ny", 0))
            self.min_x = float(t_meta.get("minX", -513.0))
            self.min_y = float(t_meta.get("minY", -334.0))
            self.step = float(t_meta.get("step", 1.0))

            if os.path.exists(terr_path) and self.nx > 0 and self.ny > 0:
                with open(terr_path, "rb") as f:
                    raw = f.read()
                count = self.nx * self.ny
                if len(raw) >= count * 2:
                    self.terrain = struct.unpack(f"<{count}h", raw[: count * 2])

            for p in w.get("prisms", []):
                ring = p.get("ring", [])
                if len(ring) < 6 or poly_area(ring) < 15.0:
                    continue
                xs = ring[0::2]
                ys = ring[1::2]
                minx, miny, maxx, maxy = min(xs), min(ys), max(xs), max(ys)
                base = float(p.get("base", 0.0))
                top = float(p.get("top", 0.0))
                cx, cy = (minx + maxx) * 0.5, (miny + maxy) * 0.5
                holes = p.get("holes", [])
                prism = {
                    "base": base,
                    "top": top,
                    "ring": ring,
                    "holes": holes,
                    "bbox": (minx, miny, maxx, maxy),
                    "center": (cx, cy),
                }
                self.prisms.append(prism)
                if (top - base) >= 12.0 and (maxx - minx) >= 14.0 and (maxy - miny) >= 14.0:
                    for idx in range(0, len(ring), 4):
                        vx, vy = ring[idx], ring[idx + 1]
                        dx, dy = vx - cx, vy - cy
                        d = math.hypot(dx, dy)
                        if d > 1.0:
                            self.cover_spots.append((vx + (dx / d) * 4.5, vy + (dy / d) * 4.5))
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

    def blocking_prism(self, x: float, y: float, z: float, pad: float = 2.8):
        for p in self.prisms:
            minx, miny, maxx, maxy = p["bbox"]
            if x < minx - pad or x > maxx + pad or y < miny - pad or y > maxy + pad:
                continue
            if z + 8.5 < p["base"] or z + 1.3 > p["top"]:
                continue
            if point_in_poly(x, y, p["ring"]):
                if not any(point_in_poly(x, y, h) for h in p["holes"]):
                    return p
        return None

    def is_blocked(self, x: float, y: float, z: float, pad: float = 2.6) -> bool:
        return self.blocking_prism(x, y, z, pad) is not None

    def has_clear_shot(self, x1: float, y1: float, z1: float, x2: float, y2: float, z2: float) -> bool:
        min_sx, max_sx = (x1, x2) if x1 < x2 else (x2, x1)
        min_sy, max_sy = (y1, y2) if y1 < y2 else (y2, y1)
        cand = [
            p for p in self.prisms
            if not (max_sx < p["bbox"][0] or min_sx > p["bbox"][2] or max_sy < p["bbox"][1] or min_sy > p["bbox"][3])
        ]
        if not cand:
            return True
        dist = math.hypot(x2 - x1, y2 - y1)
        steps = max(2, min(12, int(dist / 9.0)))
        for s in range(2, steps):
            t = s / steps
            px = x1 + (x2 - x1) * t
            py = y1 + (y2 - y1) * t
            pz = z1 + (z2 - z1) * t
            for p in cand:
                minx, miny, maxx, maxy = p["bbox"]
                if px < minx or px > maxx or py < miny or py > maxy:
                    continue
                if pz < p["base"] or pz > p["top"] - 0.6:
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
        self.cube_cap = int(cfg.get("cubes", {}).get("stat_cap", 10))
        self.mortar_g = float(cfg.get("weapons", {}).get("mortar", {}).get("gravity", 22.0))
        map_dir = hello.get("map", {}).get("dir", "/arena")
        self.arena = ArenaMap(map_dir)

        self.prev_pos = None
        self.stuck_ticks = 0
        self.last_threat_yaw = None
        self.last_threat_tick = -999
        self.provoked_until = {}
        self.combat_sounds = []
        self.strafe_dir = 1.0
        self.anchor = None
        self.anchor_tick = -999

        # Pure survival upgrade priority
        self.upgrade_weights = {
            "armor": 100,
            "nanites": 98,
            "plating": 95,
            "jets": 86,
            "capacitor": 82,
            "servos": 78,
            "reactor": 75,
            "laser_charge": 72,
            "laser_damage": 70,
            "gyros": 68,
            "gun_rate": 64,
            "gun_damage": 60,
            "lightning_efficiency": 58,
            "lightning_range": 55,
            "sensors": 52,
            "gun_velocity": 50,
            "mortar_reload": 45,
            "mortar_velocity": 42,
            "gun_mag": 40,
        }

    def _choose_upgrade(self, offer: dict) -> int:
        choices = offer.get("choices", [])
        best_idx, best_score = 0, -1
        for idx, c in enumerate(choices):
            cid = c.get("id", "").lower()
            score = self.upgrade_weights.get(cid, 20)
            if score > best_score:
                best_score = score
                best_idx = idx
        return best_idx

    def _weapon_rank(self, kind: str) -> int:
        return {"laser": 4, "lightning": 3, "mortar": 2, "gun": 1}.get(kind, 0)

    def _sound_danger(self, x: float, y: float, px: float, py: float) -> float:
        if not self.combat_sounds:
            return 0.0
        ang = math.atan2(py - y, px - x)
        danger = 0.0
        for _, bearing, w in self.combat_sounds:
            diff = abs(wrap_angle(ang - bearing))
            if diff < 0.90:
                danger += w * (1.0 - diff / 0.90)
        return danger

    def _pick_shadow_anchor(self, x: float, y: float, zone: dict, alive: int) -> tuple:
        ncx, ncy = zone["next_center"]
        nrad = zone["next_radius"]
        max_r = max(12.0, nrad * 0.92)
        rim_min = nrad * (0.68 if alive > 4 else 0.35)

        best = None
        best_score = -1e9

        nearby = []
        for sx, sy in self.arena.cover_spots:
            d_next = math.hypot(sx - ncx, sy - ncy)
            if d_next <= max_r:
                d_me = math.hypot(sx - x, sy - y)
                if d_me <= 240.0:
                    nearby.append((d_me, d_next, sx, sy))
        nearby.sort(key=lambda item: item[0])

        for d_me, d_next, sx, sy in nearby[:28]:
            gz = self.arena.ground_z(sx, sy)
            if self.arena.is_blocked(sx, sy, gz, pad=2.5):
                continue
            shielded = not self.arena.has_clear_shot(sx, sy, gz + 6.5, ncx, ncy, gz + 6.5)
            danger = self._sound_danger(x, y, sx, sy)
            score = -d_me * 0.50 + (110.0 if shielded else 0.0) - danger * 65.0
            if rim_min <= d_next <= max_r:
                score += 80.0
            if score > best_score:
                best_score = score
                best = (sx, sy, gz)

        if best is not None:
            return best
        ang = math.atan2(y - ncy, x - ncx)
        return (ncx + math.cos(ang) * (max_r * 0.80), ncy + math.sin(ang) * (max_r * 0.80), 0.0)

    def _find_escape_cover(self, x: float, y: float, z: float, ex: float, ey: float, ez: float) -> tuple:
        best = None
        best_d = 55.0
        for sx, sy in self.arena.cover_spots:
            d = math.hypot(sx - x, sy - y)
            if d < best_d:
                gz = self.arena.ground_z(sx, sy)
                if abs(gz - z) <= 5.5 and not self.arena.is_blocked(sx, sy, gz, pad=2.4):
                    if not self.arena.has_clear_shot(sx, sy, gz + 6.5, ex, ey, ez + 4.5):
                        best_d = d
                        best = (sx, sy)
        return best

    def _mortar_pitch(self, dist: float, dz: float, speed: float) -> float:
        g = self.mortar_g
        v2 = speed * speed
        disc = v2 * v2 - g * (g * dist * dist + 2.0 * dz * v2)
        if disc < 0.0:
            return 0.72
        tan_theta = (v2 - math.sqrt(disc)) / max(1.0, g * dist)
        return math.atan(tan_theta)

    def act(self, obs: mf.Observation) -> mf.Action:
        tick = int(obs["tick"])
        t = float(obs["t"])
        me = obs["self"]
        x, y, z = me["pos"]
        hp_frac = float(me["hp"]) / max(1.0, float(me["max_hp"]))
        energy = float(me["energy"])
        in_zone = bool(me["in_zone"])
        grounded = bool(me["grounded"])
        alive = int(obs["alive"])
        cubes = int(me["cubes"])

        if tick % 18 == 0:
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

        for ev in obs.get("events", []):
            if ev.get("type") == "damaged":
                if ev.get("by") is not None:
                    self.provoked_until[ev["by"]] = tick + 100
                if ev.get("bearing") is not None:
                    self.last_threat_yaw = float(ev["bearing"])
                    self.last_threat_tick = tick

        self.combat_sounds = [item for item in self.combat_sounds if tick - item[0] <= 70]
        for s in obs.get("sounds", []):
            if s.get("dist") in ("near", "mid"):
                self.last_threat_yaw = float(s["bearing"])
                self.last_threat_tick = tick
                if s.get("kind") in ("gun", "laser", "mortar", "explosion", "lightning"):
                    w = 2.0 if s.get("dist") == "near" else 1.0
                    self.combat_sounds.append((tick, float(s["bearing"]), w))
        if len(self.combat_sounds) > 18:
            self.combat_sounds = self.combat_sounds[-18:]

        w0, w1 = me["weapons"][0], me["weapons"][1]
        best_pickup = None
        for p in obs.get("pickups", []):
            if math.dist(p["pos"][:2], (x, y)) <= self.reach and abs(p["pos"][2] - z) <= 4.0:
                p_kind = p["kind"]
                if (w0 and w0["kind"] == p_kind) or (w1 and w1["kind"] == p_kind):
                    continue
                if self._weapon_rank(p_kind) < 2:
                    continue
                if best_pickup is None or self._weapon_rank(p_kind) > self._weapon_rank(best_pickup["kind"]):
                    best_pickup = p

        if best_pickup:
            if w1 is None:
                act["take"] = int(best_pickup["id"])
            elif self._weapon_rank(best_pickup["kind"]) > self._weapon_rank(w1["kind"]):
                if not (w1["kind"] == "laser" and w1.get("charge", 0) > 0):
                    act["take"] = int(best_pickup["id"])
                    act["drop"] = 1

        zone = obs["zone"]
        zcx, zcy = zone["center"]
        zrad = zone["radius"]
        ncx, ncy = zone["next_center"]
        nrad = zone["next_radius"]

        dist_curr = math.hypot(x - zcx, y - zcy)
        dist_next = math.hypot(x - ncx, y - ncy)
        urgent_zone = (not in_zone) or (dist_curr > zrad - 18.0) or (dist_next > nrad - 10.0 and t > zone["shrink_start"] - 14.0)

        if (self.anchor is None or tick - self.anchor_tick > 60 or
                math.hypot(self.anchor[0] - ncx, self.anchor[1] - ncy) > nrad * 0.93):
            self.anchor = self._pick_shadow_anchor(x, y, zone, alive)
            self.anchor_tick = tick

        goal_x, goal_y, goal_z = self.anchor

        # Only grab ultra-safe nearby cubes/weapons (< 28m) when completely alone
        if not urgent_zone and not mechs and hp_frac > 0.65:
            if cubes < self.cube_cap:
                near_cubes = [
                    c for c in obs.get("cubes", [])
                    if math.dist(c["pos"][:2], (x, y)) < 28.0 and abs(c["pos"][2] - z) < 5.0
                ]
                if near_cubes:
                    near_cubes.sort(key=lambda c: math.dist(c["pos"][:2], (x, y)))
                    goal_x, goal_y, goal_z = near_cubes[0]["pos"]
            if w1 is None:
                near_weaps = [
                    p for p in obs.get("pickups", [])
                    if math.dist(p["pos"][:2], (x, y)) < 30.0 and abs(p["pos"][2] - z) < 5.0 and self._weapon_rank(p["kind"]) >= 2
                ]
                if near_weaps:
                    goal_x, goal_y, goal_z = near_weaps[0]["pos"]

        dist_goal = math.hypot(goal_x - x, goal_y - y)
        move_yaw = math.atan2(goal_y - y, goal_x - x)
        walk_spd = float(me.get("stats", {}).get("walk_speed", 11.7))
        silent_scale = min(0.42, (self.step_speed * 0.85) / max(1.0, walk_spd))
        speed_scale = 1.0
        want_boost = False
        want_jump = False
        holding_shadow = False

        enemy_charging = next((m for m in mechs if m.get("charging")), None)
        incoming = [pr for pr in obs.get("projectiles", []) if math.dist(pr["pos"][:2], (x, y)) < 36.0]

        if enemy_charging is not None:
            ex, ey, _ = enemy_charging["pos"]
            to_me = math.atan2(y - ey, x - ex)
            move_yaw = wrap_angle(to_me + self.strafe_dir * (math.pi * 0.5))
            want_boost = bool(energy > 25.0)
        elif incoming:
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
            strafe_yaw = away_yaw + self.strafe_dir * (math.pi * 0.46)

            if alive > 4 or hp_frac < 0.55 or len(mechs) >= 2:
                # Edge-Ghost escape: break LOS behind nearest building corner!
                cover_pt = self._find_escape_cover(x, y, z, ex, ey, ez)
                if cover_pt is not None:
                    move_yaw = math.atan2(cover_pt[1] - y, cover_pt[0] - x)
                else:
                    move_yaw = math.atan2(
                        0.72 * math.sin(away_yaw) + 0.28 * math.sin(move_yaw),
                        0.72 * math.cos(away_yaw) + 0.28 * math.cos(move_yaw),
                    )
                want_boost = bool((edist < 75.0 or hp_frac < 0.55) and energy > 25.0)
            else:
                # Top 4 endgame: kite and strafe
                radial = 0.25 if edist < 65.0 else -0.20
                move_yaw = math.atan2(
                    radial * math.sin(away_yaw) + 0.85 * math.sin(strafe_yaw),
                    radial * math.cos(away_yaw) + 0.85 * math.cos(strafe_yaw),
                )
        else:
            if dist_goal < 4.5:
                # Stay 100% silent (< 5 m/s) tucked behind building shadow
                orbit_ang = (tick * 0.12) % (2.0 * math.pi)
                wx = goal_x + math.cos(orbit_ang) * 2.0
                wy = goal_y + math.sin(orbit_ang) * 2.0
                move_yaw = math.atan2(wy - y, wx - x)
                speed_scale = silent_scale
                holding_shadow = True
            elif dist_goal < 24.0 and self.combat_sounds:
                # Creep silently into cover if fights are nearby
                speed_scale = silent_scale

        if speed_scale > 0.05 and not holding_shadow:
            probe_x = x + math.cos(move_yaw) * 5.0
            probe_y = y + math.sin(move_yaw) * 5.0
            if self.arena.is_blocked(probe_x, probe_y, z, pad=2.8):
                for offset in (0.6, -0.6, 1.2, -1.2, 1.8, -1.8, 2.4):
                    alt = wrap_angle(move_yaw + offset)
                    if not self.arena.is_blocked(x + math.cos(alt) * 5.5, y + math.sin(alt) * 5.5, z):
                        move_yaw = alt
                        break

        if self.stuck_ticks >= 4 and speed_scale > 0.05 and not holding_shadow:
            move_yaw = wrap_angle(move_yaw + (1.4 if (tick // 8) % 2 == 0 else -1.4))
            if energy > 25.0 and grounded:
                want_jump = True

        act["move"] = [round(math.cos(move_yaw) * speed_scale, 4), round(math.sin(move_yaw) * speed_scale, 4)]
        act["boost"] = bool(want_boost)
        act["jump"] = bool(want_jump)

        muzzle_z = z + me.get("stats", {}).get("muzzle_height", 7.0)
        fire = [False, False]
        has_mortar = any(w and w["kind"] == "mortar" for w in me["weapons"])

        clear_mechs = [
            m for m in mechs
            if self.arena.has_clear_shot(x, y, muzzle_z, m["pos"][0], m["pos"][1], m["pos"][2] + 4.5)
        ]

        # Strict Self-Defense Trigger Discipline:
        # Don't broadcast 320-380m gunfire when alive > 4 unless forced!
        def must_defend(m) -> bool:
            if alive <= 4:
                return True
            d = math.dist(m["pos"][:2], (x, y))
            if d <= 55.0:
                return True
            if self.provoked_until.get(m["id"], -1) >= tick:
                return True
            to_me = math.atan2(y - m["pos"][1], x - m["pos"][0])
            e_yaw = m.get("aim", [0.0, 0.0])[0]
            if d <= 110.0 and abs(wrap_angle(to_me - e_yaw)) < 0.38:
                return True
            return False

        defend_targets = [m for m in clear_mechs if must_defend(m)]

        if defend_targets:
            target_obj = min(defend_targets, key=lambda m: math.dist(m["pos"], me["pos"]))
            tx, ty, tz = target_obj["pos"]
            tvx, tvy, tvz = target_obj.get("vel", [0.0, 0.0, 0.0])
            target_h = 4.5 * (1.0 + 0.05 * target_obj.get("cubes", 0))
            dist = math.hypot(tx - x, ty - y)

            laser_ready = False
            for w in me["weapons"]:
                if w and w["kind"] == "laser":
                    req_c = float(w.get("stats", {}).get("charge", 0.75)) * 0.84
                    if float(w.get("charge", 0.0)) >= req_c:
                        laser_ready = True

            proj_speed = w0.get("stats", {}).get("speed", 252.0) if (w0 and w0["kind"] == "gun") else 252.0
            tof = 0.0 if laser_ready else (dist / max(50.0, proj_speed))

            lead_x = tx + tvx * tof
            lead_y = ty + tvy * tof
            lead_z = tz + target_h + tvz * tof
            aim_yaw = math.atan2(lead_y - y, lead_x - x)
            aim_pitch = math.atan2(lead_z - muzzle_z, max(1.0, math.hypot(lead_x - x, lead_y - y)))
            act["aim"] = [aim_yaw, aim_pitch]

            yaw_err = abs(wrap_angle(aim_yaw - me["aim"][0]))
            pitch_err = abs(aim_pitch - me["aim"][1])

            for slot_idx, w in enumerate(me["weapons"]):
                if w is None:
                    continue
                kind = w["kind"]
                if kind == "laser":
                    l_range = float(w.get("stats", {}).get("range", 280.0))
                    if dist > l_range - 2.0:
                        fire[slot_idx] = False
                    else:
                        chg = float(w.get("charge", 0.0))
                        req_chg = float(w.get("stats", {}).get("charge", 0.75)) * 0.84
                        if chg < req_chg:
                            fire[slot_idx] = True
                        else:
                            if (yaw_err < 0.12 and pitch_err < 0.16) or chg > req_chg + 0.65:
                                fire[slot_idx] = False
                            else:
                                fire[slot_idx] = True
                elif kind == "gun":
                    g_range = float(w.get("stats", {}).get("range", 230.0))
                    if dist <= g_range and yaw_err < 0.20 and pitch_err < 0.24:
                        fire[slot_idx] = True
                elif kind == "lightning":
                    lt_range = float(w.get("stats", {}).get("range", 45.0))
                    lt_cone = math.radians(float(w.get("stats", {}).get("cone_deg", 30.0))) * 0.55
                    if dist <= lt_range and energy > 12.0 and yaw_err <= max(0.28, lt_cone):
                        fire[slot_idx] = True
                elif kind == "mortar":
                    if 22.0 <= dist <= 215.0:
                        v = float(w.get("stats", {}).get("speed", 72.0))
                        mpitch = self._mortar_pitch(dist, (tz + target_h) - (z + 9.5), v)
                        act["aim"] = [aim_yaw, mpitch]
                        if yaw_err < 0.20:
                            fire[slot_idx] = True
        else:
            # Always keep 180-deg front armor facing nearest mech or threat bearing!
            if mechs:
                act["aim"] = [math.atan2(mechs[0]["pos"][1] - y, mechs[0]["pos"][0] - x), 0.0]
            elif tick - self.last_threat_tick < 60 and self.last_threat_yaw is not None:
                act["aim"] = [self.last_threat_yaw, 0.0]
            else:
                center_yaw = math.atan2(ncy - y, ncx - x)
                act["aim"] = [wrap_angle(center_yaw + 0.90 * math.sin(tick * 0.16)), 0.0]

            if w0 and w0["kind"] == "gun" and w0.get("ammo", 12) < w0.get("stats", {}).get("mag", 12):
                act["reload"] = True

        act["fire"] = fire
        mf.draw.point([goal_x, goal_y, goal_z], "#40c0ff", 6)
        mf.draw.label(f"v29 GHOST | A:{alive} HP:{me['hp']:.0f} C:{cubes}")
        return act


mf.run(Bot)
