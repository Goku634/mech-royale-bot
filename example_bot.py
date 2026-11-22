"""v20 Built Directly on v17 (3.77) + Canopy-Shielded Ground Fallback & Smart Magazine Discipline."""
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
        self.cover_spots = []
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
                    if 9.0 <= height <= 18.0 and (maxx - minx) >= 14.0 and (maxy - miny) >= 14.0:
                        if point_in_poly(cx, cy, ring) and not any(point_in_poly(cx, cy, h) for h in p.get("holes", [])):
                            self.rooftops.append((cx, cy, top, height))
                    if height >= 12.0 and (maxx - minx) >= 14.0 and (maxy - miny) >= 14.0:
                        for idx in range(0, len(ring), 4):
                            vx, vy = ring[idx], ring[idx + 1]
                            dx, dy = vx - cx, vy - cy
                            d = math.hypot(dx, dy)
                            if d > 1.0:
                                self.cover_spots.append((vx + (dx / d) * 4.5, vy + (dy / d) * 4.5))

            trees_obj = w.get("trees", {})
            t_fields = trees_obj.get("fields", [])
            t_data = trees_obj.get("data", [])
            if t_fields and t_data:
                stride = len(t_fields)
                ix = t_fields.index("x") if "x" in t_fields else 0
                iy = t_fields.index("y") if "y" in t_fields else 1
                icr = t_fields.index("crownR") if "crownR" in t_fields else -1
                for i in range(0, len(t_data) - stride + 1, stride * 2):
                    cr = float(t_data[i + icr]) if icr >= 0 else 4.0
                    if cr >= 3.5:
                        self.trees.append((float(t_data[i + ix]), float(t_data[i + iy])))

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

    def canopy_density(self, x: float, y: float) -> int:
        cnt = 0
        for tx, ty in self.trees:
            if abs(tx - x) < 16.0 and abs(ty - y) < 16.0:
                cnt += 1
                if cnt >= 4:
                    break
        return cnt

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
        self.strafe_dir = 1.0
        self.anchor = None
        self.anchor_tick = -999

        self.upgrade_weights = {
            "armor": 100,
            "nanites": 96,
            "plating": 92,
            "laser_charge": 88,
            "laser_damage": 84,
            "jets": 78,
            "capacitor": 75,
            "gun_rate": 70,
            "gun_damage": 66,
            "gyros": 62,
            "servos": 60,
            "reactor": 58,
            "gun_velocity": 55,
            "lightning_efficiency": 52,
            "lightning_range": 50,
            "mortar_reload": 48,
            "mortar_velocity": 45,
            "gun_mag": 42,
            "sensors": 35,
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

    def _pick_anchor(self, x: float, y: float, z: float, zone: dict, alive: int, cubes: int) -> tuple:
        ncx, ncy = zone["next_center"]
        nrad = zone["next_radius"]
        max_r = max(14.0, nrad * 0.86)

        best = None
        best_score = -1e9

        for rx, ry, rtop, h in self.arena.rooftops:
            d_next = math.hypot(rx - ncx, ry - ncy)
            if d_next > max_r:
                continue
            d_me = math.hypot(rx - x, ry - y)
            if d_me > 185.0:
                continue
            score = -d_me * 0.60 + (22.0 - abs(h - 13.0)) * 1.6
            if nrad * 0.42 <= d_next <= max_r:
                score += 65.0
            if alive <= 6:
                score += 35.0
            if score > best_score:
                best_score = score
                best = (rx, ry, rtop)

        if alive > 5 and self.arena.cover_spots:
            nearby = []
            for sx, sy in self.arena.cover_spots:
                d_next = math.hypot(sx - ncx, sy - ncy)
                if d_next <= max_r:
                    d_me = math.hypot(sx - x, sy - y)
                    if d_me <= 165.0:
                        nearby.append((d_me, d_next, sx, sy))
            nearby.sort(key=lambda item: item[0])
            for d_me, d_next, sx, sy in nearby[:14]:
                gz = self.arena.ground_z(sx, sy)
                if self.arena.is_blocked(sx, sy, gz, pad=2.4):
                    continue
                if not self.arena.has_clear_shot(sx, sy, gz + 6.0, ncx, ncy, gz + 6.0):
                    canopy_bonus = self.arena.canopy_density(sx, sy) * 6.0
                    score = -d_me * 0.60 + 82.0 + canopy_bonus + (45.0 if d_next >= nrad * 0.45 else 0.0)
                    if score > best_score:
                        best_score = score
                        best = (sx, sy, gz)

        if best is not None:
            return best
        ang = math.atan2(y - ncy, x - ncx)
        return (ncx + math.cos(ang) * (max_r * 0.72), ncy + math.sin(ang) * (max_r * 0.72), 0.0)

    def _mortar_pitch(self, dist: float, dz: float, speed: float) -> float:
        g = self.mortar_g
        v2 = speed * speed
        disc = v2 * v2 - g * (g * dist * dist + 2.0 * dz * v2)
        if disc < 0.0:
            return 0.72
        tan_theta = (v2 - math.sqrt(disc)) / max(1.0, g * dist)
        return math.atan(tan_theta)

    def act(self, obs: mf.Observation) -> mf.Action:
        tick = obs["tick"]
        t = obs["t"]
        me = obs["self"]
        x, y, z = me["pos"]
        hp_frac = me["hp"] / max(1.0, me["max_hp"])
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
                act["take"] = best_pickup["id"]
            elif self._weapon_rank(best_pickup["kind"]) > self._weapon_rank(w1["kind"]):
                if not (w1["kind"] == "laser" and w1.get("charge", 0) > 0):
                    act["take"] = best_pickup["id"]
                    act["drop"] = 1

        zone = obs["zone"]
        zcx, zcy = zone["center"]
        zrad = zone["radius"]
        ncx, ncy = zone["next_center"]
        nrad = zone["next_radius"]

        dist_curr = math.hypot(x - zcx, y - zcy)
        dist_next = math.hypot(x - ncx, y - ncy)
        # Exact v17 zone rotation timing (prevents storm-wall pinches)
        urgent_zone = (not in_zone) or (dist_curr > zrad - 18.0) or (dist_next > nrad - 12.0 and t > zone["shrink_start"] - 14.0)

        if (self.anchor is None or tick - self.anchor_tick > 55 or
                math.hypot(self.anchor[0] - ncx, self.anchor[1] - ncy) > nrad * 0.88):
            self.anchor = self._pick_anchor(x, y, z, zone, alive, cubes)
            self.anchor_tick = tick

        goal_x, goal_y, goal_z = self.anchor

        capturing_drop = False
        if not urgent_zone and len(mechs) <= 1 and (cubes < self.cube_cap or hp_frac < 0.82):
            for dr in obs.get("drops", []):
                if dr.get("lands_in", 99.0) <= 12.0 and dr.get("holder") in (None, me["id"]):
                    if math.dist(dr["pos"][:2], (x, y)) < 135.0:
                        goal_x, goal_y, goal_z = dr["pos"]
                        capturing_drop = True
                        break

        farming_loot = False
        if not urgent_zone and not capturing_drop and not mechs and hp_frac > 0.52:
            if cubes < self.cube_cap:
                near_cubes = [
                    c for c in obs.get("cubes", [])
                    if math.dist(c["pos"][:2], (x, y)) < 75.0 and abs(c["pos"][2] - z) < 14.0
                ]
                if near_cubes:
                    near_cubes.sort(key=lambda c: math.dist(c["pos"][:2], (x, y)))
                    goal_x, goal_y, goal_z = near_cubes[0]["pos"]
                    farming_loot = True
            if not farming_loot:
                near_weaps = [
                    p for p in obs.get("pickups", [])
                    if math.dist(p["pos"][:2], (x, y)) < 68.0 and abs(p["pos"][2] - z) < 12.0 and self._weapon_rank(p["kind"]) >= 2
                ]
                if near_weaps:
                    goal_x, goal_y, goal_z = near_weaps[0]["pos"]
                    farming_loot = True

        avoid_cube = None
        if cubes >= self.cube_cap and not capturing_drop:
            for c in obs.get("cubes", []):
                if math.dist(c["pos"][:2], (x, y)) < 6.0 and abs(c["pos"][2] - z) < 4.0:
                    avoid_cube = c["pos"]
                    break

        dist_goal = math.hypot(goal_x - x, goal_y - y)
        move_yaw = math.atan2(goal_y - y, goal_x - x)
        speed_scale = 1.0
        want_boost = False
        want_jump = False
        micro_weaving = False

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
        elif avoid_cube is not None:
            move_yaw = math.atan2(y - avoid_cube[1], x - avoid_cube[0])
            speed_scale = 0.8
        elif mechs and not (capturing_drop and dist_goal < 8.0):
            closest = mechs[0]
            ex, ey, ez = closest["pos"]
            edist = math.hypot(ex - x, ey - y)
            away_yaw = math.atan2(y - ey, x - ex)
            strafe_yaw = away_yaw + self.strafe_dir * (math.pi * 0.46)

            if hp_frac < 0.50 or len(mechs) >= 2:
                move_yaw = math.atan2(
                    0.65 * math.sin(away_yaw) + 0.35 * math.sin(move_yaw),
                    0.65 * math.cos(away_yaw) + 0.35 * math.cos(move_yaw),
                )
                want_boost = bool(hp_frac < 0.40 and energy > 25.0)
            elif z > ez + 5.0 and dist_goal < 14.0:
                if dist_goal > 5.5:
                    move_yaw = math.atan2(goal_y - y, goal_x - x)
                    speed_scale = 0.85
                else:
                    move_yaw = strafe_yaw
                    speed_scale = 0.70
            else:
                radial = 0.25 if edist < 65.0 else -0.20
                move_yaw = math.atan2(
                    radial * math.sin(away_yaw) + 0.85 * math.sin(strafe_yaw),
                    radial * math.cos(away_yaw) + 0.85 * math.cos(strafe_yaw),
                )
        else:
            if dist_goal < 4.5 and abs(z - goal_z) < 2.0:
                orbit_ang = (tick * 0.14) % (2.0 * math.pi)
                wx = goal_x + math.cos(orbit_ang) * 2.8
                wy = goal_y + math.sin(orbit_ang) * 2.8
                move_yaw = math.atan2(wy - y, wx - x)
                walk_spd = me.get("stats", {}).get("walk_speed", 11.7)
                speed_scale = min(0.42, (self.step_speed * 0.86) / max(1.0, walk_spd))
                micro_weaving = True

        if speed_scale > 0.05 and not micro_weaving:
            probe_x = x + math.cos(move_yaw) * 5.0
            probe_y = y + math.sin(move_yaw) * 5.0
            wall = self.arena.blocking_prism(probe_x, probe_y, z, pad=2.8)
            if wall is not None:
                wall_h = wall["top"] - z
                if wall_h <= 18.5 and ((not grounded and energy > 10.0) or (grounded and energy >= 38.0)):
                    if grounded:
                        want_jump = True
                    want_boost = True
                else:
                    for offset in (0.6, -0.6, 1.2, -1.2, 1.8, -1.8, 2.4):
                        alt = wrap_angle(move_yaw + offset)
                        if not self.arena.is_blocked(x + math.cos(alt) * 5.5, y + math.sin(alt) * 5.5, z):
                            move_yaw = alt
                            break

        if not grounded and goal_z > z + 0.5 and dist_goal < 28.0 and energy > 10.0:
            want_boost = True

        if self.stuck_ticks >= 4 and speed_scale > 0.05 and not micro_weaving:
            move_yaw = wrap_angle(move_yaw + (1.4 if (tick // 8) % 2 == 0 else -1.4))
            if energy > 25.0:
                if grounded:
                    want_jump = True
                want_boost = True

        act["move"] = [math.cos(move_yaw) * speed_scale, math.sin(move_yaw) * speed_scale]
        act["boost"] = bool(want_boost)
        act["jump"] = bool(want_jump)

        muzzle_z = z + me.get("stats", {}).get("muzzle_height", 7.0)
        fire = [False, False]
        has_mortar = any(w and w["kind"] == "mortar" for w in me["weapons"])

        clear_mechs = [
            m for m in mechs
            if self.arena.has_clear_shot(x, y, muzzle_z, m["pos"][0], m["pos"][1], m["pos"][2] + 4.5)
        ]
        occluded_mechs = [m for m in mechs if m not in clear_mechs] if has_mortar else []

        target_obj = None
        target_is_mech = False
        target_is_occluded = False
        target_pos = None
        target_vel = [0.0, 0.0, 0.0]
        target_h = 4.5

        if clear_mechs:
            def target_score(m):
                d = math.dist(m["pos"], me["pos"])
                hp = m.get("hp", 1000)
                to_me = math.atan2(y - m["pos"][1], x - m["pos"][0])
                e_yaw = m.get("aim", [0.0, 0.0])[0]
                is_rear = abs(wrap_angle(to_me - e_yaw)) > (math.pi * 0.5)
                holds_mortar = "mortar" in m.get("weapons", [])
                return d + hp * 0.06 - (50.0 if is_rear else 0.0) - (35.0 if holds_mortar else 0.0)

            target_obj = min(clear_mechs, key=target_score)
            target_is_mech = True
            target_pos = target_obj["pos"]
            target_vel = target_obj.get("vel", [0.0, 0.0, 0.0])
            target_h = 4.5
        elif occluded_mechs and math.dist(occluded_mechs[0]["pos"][:2], (x, y)) <= 190.0:
            target_obj = occluded_mechs[0]
            target_is_mech = True
            target_is_occluded = True
            target_pos = target_obj["pos"]
            target_vel = target_obj.get("vel", [0.0, 0.0, 0.0])
            target_h = 2.0
        elif not mechs and npcs and cubes < self.cube_cap and hp_frac > 0.60 and math.dist(npcs[0]["pos"][:2], (x, y)) < 55.0:
            if self.arena.has_clear_shot(x, y, muzzle_z, npcs[0]["pos"][0], npcs[0]["pos"][1], npcs[0]["pos"][2] + 1.8):
                target_pos = npcs[0]["pos"]
                target_vel = npcs[0].get("vel", [0.0, 0.0, 0.0])
                target_h = 1.8
        elif not mechs and obs.get("crates") and cubes < self.cube_cap and hp_frac > 0.65:
            crates = sorted(obs["crates"], key=lambda c: math.dist(c["pos"][:2], (x, y)))
            if math.dist(crates[0]["pos"][:2], (x, y)) < 40.0:
                if self.arena.has_clear_shot(x, y, muzzle_z, crates[0]["pos"][0], crates[0]["pos"][1], crates[0]["pos"][2] + 1.2):
                    target_pos = crates[0]["pos"]
                    target_vel = [0.0, 0.0, 0.0]
                    target_h = 1.2

        if target_pos is not None:
            tx, ty, tz = target_pos
            tvx, tvy, tvz = target_vel
            dist = math.hypot(tx - x, ty - y)

            laser_ready_to_release = False
            for w in me["weapons"]:
                if w and w["kind"] == "laser" and not target_is_occluded and target_is_mech:
                    req_c = float(w.get("stats", {}).get("charge", 0.75)) * 0.84
                    if float(w.get("charge", 0.0)) >= req_c:
                        laser_ready_to_release = True

            proj_speed = w0.get("stats", {}).get("speed", 252.0) if (w0 and w0["kind"] == "gun") else 252.0
            tof = 0.0 if laser_ready_to_release else (dist / max(50.0, proj_speed))

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
                    if target_is_occluded or not target_is_mech or dist > l_range - 2.0:
                        fire[slot_idx] = False
                    else:
                        chg = float(w.get("charge", 0.0))
                        req_chg = float(w.get("stats", {}).get("charge", 0.75)) * 0.84
                        if chg < req_chg:
                            fire[slot_idx] = True
                        else:
                            if (yaw_err < 0.14 and pitch_err < 0.18) or chg > req_chg + 0.65:
                                fire[slot_idx] = False
                            else:
                                fire[slot_idx] = True
                elif kind == "gun":
                    g_range = float(w.get("stats", {}).get("range", 230.0))
                    # Tighten spread tolerance at extreme range (>150m) so 12-round mag stays loaded for real hits
                    max_gun_err = 0.13 if dist > 150.0 else 0.22
                    if not target_is_occluded and dist <= g_range:
                        if yaw_err < max_gun_err and pitch_err < 0.24:
                            fire[slot_idx] = True
                elif kind == "lightning":
                    lt_range = float(w.get("stats", {}).get("range", 45.0))
                    lt_cone = math.radians(float(w.get("stats", {}).get("cone_deg", 30.0))) * 0.55
                    if not target_is_occluded and dist <= lt_range and energy > 12.0:
                        if yaw_err <= max(0.28, lt_cone):
                            fire[slot_idx] = True
                elif kind == "mortar":
                    if 22.0 <= dist <= 215.0:
                        v = float(w.get("stats", {}).get("speed", 72.0))
                        mpitch = self._mortar_pitch(dist, (tz + target_h) - (z + 9.5), v)
                        if target_is_occluded:
                            mpitch = max(mpitch, 0.65)
                        act["aim"] = [aim_yaw, mpitch]
                        if yaw_err < 0.22:
                            fire[slot_idx] = True

            mf.draw.line([x, y, muzzle_z], [lead_x, lead_y, lead_z], "#ff5040")
        else:
            if mechs:
                act["aim"] = [math.atan2(mechs[0]["pos"][1] - y, mechs[0]["pos"][0] - x), 0.0]
            elif tick - self.last_threat_tick < 60 and self.last_threat_yaw is not None:
                act["aim"] = [self.last_threat_yaw, 0.0]
            elif micro_weaving:
                center_yaw = math.atan2(ncy - y, ncx - x)
                act["aim"] = [wrap_angle(center_yaw + 0.95 * math.sin(tick * 0.18)), 0.0]
            else:
                act["aim"] = [wrap_angle(move_yaw + 0.95 * math.sin(tick * 0.22)), 0.0]

            if w0 and w0["kind"] == "gun" and w0.get("ammo", 12) < w0.get("stats", {}).get("mag", 12):
                act["reload"] = True

        act["fire"] = fire
        mf.draw.point([goal_x, goal_y, goal_z], "#40c0ff", 6)
        mf.draw.label(f"v20 | Alive:{alive} HP:{me['hp']:.0f} C:{cubes}")
        return act


mf.run(Bot)
