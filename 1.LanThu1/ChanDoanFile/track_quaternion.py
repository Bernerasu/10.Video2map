"""
DJI Meta — Real Quaternion Tracker
- Skip trivial quaternion (0,0,1,0) va (0,0,0,1)
- Chi nhan quaternion co >= 2 thanh phan |val| > 0.05
- Track across 15 samples, so sanh voi GPS bearing

Chay: python3 track_quaternion.py /tmp/dji_meta.bin
"""

import struct
import math
import sys


def find_gps_records(data, max_records=5000):
    lat_min, lat_max = math.radians(5.0), math.radians(25.0)
    lon_min, lon_max = math.radians(100.0), math.radians(120.0)
    records = []
    i = 0
    while i < len(data) - 16 and len(records) < max_records:
        val = struct.unpack('<d', data[i:i+8])[0]
        if lat_min < val < lat_max:
            for j in range(1, 20):
                if i + j + 8 <= len(data):
                    val2 = struct.unpack('<d', data[i+j:i+j+8])[0]
                    if lon_min < val2 < lon_max:
                        records.append((i, math.degrees(val), math.degrees(val2)))
                        i += 100
                        break
            else:
                i += 1
                continue
            continue
        i += 1
    return records


def find_all_quaternions(data, center, radius=250):
    """
    Scan tat ca float32 trong vung, tim MOI quaternion:
    - 4 float cach 5 bytes
    - |q| trong [0.95, 1.05]
    - it nhat 2 thanh phan co |val| > 0.05 (loai trivial)
    """
    start = max(0, center - radius)
    end = min(len(data) - 4, center + radius)

    # Lay tat ca float
    floats = {}  # pos -> value
    for pos in range(start, end):
        f = struct.unpack('<f', data[pos:pos+4])[0]
        if not (math.isnan(f) or math.isinf(f)) and abs(f) <= 1.1:
            floats[pos] = f

    results = []
    checked = set()

    for pos in sorted(floats.keys()):
        if pos in checked:
            continue
        # Tim 4 float cach 5 bytes
        positions = [pos, pos+5, pos+10, pos+15]
        if all(p in floats for p in positions):
            vals = [floats[p] for p in positions]
            mag = math.sqrt(sum(v**2 for v in vals))

            if 0.95 < mag < 1.05:
                # Dem so thanh phan lon
                big = sum(1 for v in vals if abs(v) > 0.05)
                is_trivial = big < 2

                # Yaw, pitch, roll
                qx, qy, qz, qw = vals
                yaw = math.degrees(math.atan2(2*(qw*qz + qx*qy), 1 - 2*(qy**2 + qz**2)))
                if yaw < 0:
                    yaw += 360
                sinp = max(-1, min(1, 2*(qw*qy - qz*qx)))
                pitch = math.degrees(math.asin(sinp))
                roll = math.degrees(math.atan2(2*(qw*qx + qy*qz), 1 - 2*(qx**2 + qy**2)))

                rels = [p - center for p in positions]
                results.append({
                    'positions': positions,
                    'rels': rels,
                    'vals': vals,
                    'mag': mag,
                    'trivial': is_trivial,
                    'yaw': yaw,
                    'pitch': pitch,
                    'roll': roll,
                })
                for p in positions:
                    checked.add(p)

    return results


def find_altitude(data, center, radius=200):
    """Tim float 20-60m (loai 47.95 FPS)"""
    start = max(0, center - radius)
    end = min(len(data) - 4, center + radius)
    candidates = []
    for pos in range(start, end):
        f = struct.unpack('<f', data[pos:pos+4])[0]
        if not (math.isnan(f) or math.isinf(f)):
            if 20 < f < 60 and abs(f - 47.95) > 0.5 and abs(f - 47.952) > 0.5:
                candidates.append((pos, pos - center, f))
    return candidates


def find_gimbal(data, center, radius=200):
    """Tim float -90 to -1 (gimbal pitch)"""
    start = max(0, center - radius)
    end = min(len(data) - 4, center + radius)
    candidates = []
    for pos in range(start, end):
        f = struct.unpack('<f', data[pos:pos+4])[0]
        if not (math.isnan(f) or math.isinf(f)):
            if -90 <= f < -1:
                candidates.append((pos, pos - center, f))
    return candidates


# ============================================================
if __name__ == "__main__":
    bin_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/dji_meta.bin"

    with open(bin_path, 'rb') as f:
        data = f.read()

    print("=" * 150)
    print("DJI Meta — Real Quaternion Tracker")
    print("File: {} ({:,} bytes)".format(bin_path, len(data)))
    print("=" * 150)

    gps_records = find_gps_records(data)
    print("GPS records: {}".format(len(gps_records)))

    # ============================================================
    # PHAN 1: Record 0 — tat ca quaternion (trivial + real)
    # ============================================================
    print()
    print("=" * 150)
    print("RECORD 0 — TAT CA QUATERNION (trivial va real)")
    print("=" * 150)

    offset0 = gps_records[0][0]
    all_q0 = find_all_quaternions(data, offset0, 250)

    for i, q in enumerate(all_q0):
        status = "TRIVIAL" if q['trivial'] else ">>> REAL <<<"
        print("  Q#{}: rel={} vals=({:+.4f},{:+.4f},{:+.4f},{:+.4f}) |q|={:.4f} yaw={:.1f} pitch={:.1f} roll={:.1f} {}".format(
            i, q['rels'], q['vals'][0], q['vals'][1], q['vals'][2], q['vals'][3],
            q['mag'], q['yaw'], q['pitch'], q['roll'], status))

    # ============================================================
    # PHAN 2: Record 0 — altitude candidates
    # ============================================================
    print()
    print("RECORD 0 — ALTITUDE candidates (20-60m, != 47.95):")
    alts0 = find_altitude(data, offset0)
    for pos, rel, val in alts0:
        print("  offset {} (rel {:+d}): {:.2f}m".format(pos, rel, val))

    # ============================================================
    # PHAN 3: Record 0 — gimbal candidates
    # ============================================================
    print()
    print("RECORD 0 — GIMBAL candidates (-90 to -1):")
    gims0 = find_gimbal(data, offset0)
    for pos, rel, val in gims0:
        print("  offset {} (rel {:+d}): {:.2f} deg".format(pos, rel, val))

    # ============================================================
    # PHAN 4: TRACK 15 mau — chi REAL quaternion
    # ============================================================
    print()
    print("=" * 150)
    print("TRACK 15 MAU — Real quaternion + Altitude + Gimbal + GPS bearing")
    print("=" * 150)
    print()

    step = max(1, len(gps_records) // 14)
    indices = list(range(0, len(gps_records), step))[:15]
    if len(gps_records) - 1 not in indices:
        indices.append(len(gps_records) - 1)

    hdr = "{:>5} {:>7} | {:>12} {:>12} | {:>7} | {:>7} | {:>7} {:>7} {:>7} | {:>36} | {:>10}".format(
        "Idx", "Offset", "Lat", "Lon",
        "Alt(m)", "Gimbal",
        "Yaw", "Pitch", "Roll",
        "Quaternion (x,y,z,w)",
        "GPS bear."
    )
    print(hdr)
    print("-" * len(hdr))

    prev_lat = None
    prev_lon = None

    for idx in indices:
        if idx >= len(gps_records):
            continue
        offset, lat, lon = gps_records[idx]

        # Altitude
        alts = find_altitude(data, offset)
        alt_str = "{:.1f}".format(alts[0][2]) if alts else ""

        # Gimbal
        gims = find_gimbal(data, offset)
        gim_str = "{:.1f}".format(gims[0][2]) if gims else ""

        # Real quaternion (skip trivial)
        all_q = find_all_quaternions(data, offset, 250)
        real_q = [q for q in all_q if not q['trivial']]

        yaw_str = ""
        pitch_str = ""
        roll_str = ""
        q_str = ""

        if real_q:
            q = real_q[0]
            yaw_str = "{:.1f}".format(q['yaw'])
            pitch_str = "{:.1f}".format(q['pitch'])
            roll_str = "{:.1f}".format(q['roll'])
            q_str = "({:+.3f},{:+.3f},{:+.3f},{:+.3f})".format(*q['vals'])

        # GPS bearing
        gps_bear = ""
        if prev_lat is not None:
            dlat = lat - prev_lat
            dlon = lon - prev_lon
            dist = math.sqrt(dlat**2 + dlon**2)
            if dist > 0.00005:  # > ~5m
                bearing = math.degrees(math.atan2(dlon, dlat))
                if bearing < 0:
                    bearing += 360
                dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
                d_idx = int((bearing + 22.5) / 45) % 8
                gps_bear = "{:.0f} {}".format(bearing, dirs[d_idx])
        prev_lat = lat
        prev_lon = lon

        row = "{:5d} {:7d} | {:12.7f} {:12.7f} | {:>7} | {:>7} | {:>7} {:>7} {:>7} | {:>36} | {:>10}".format(
            idx, offset, lat, lon,
            alt_str, gim_str,
            yaw_str, pitch_str, roll_str,
            q_str, gps_bear
        )
        print(row)

    # ============================================================
    # PHAN 5: Kiem tra nhieu quaternion REAL trong 1 record
    # ============================================================
    print()
    print("=" * 150)
    print("PHAN TICH: So luong quaternion REAL trong moi record")
    print("=" * 150)
    
    for idx in indices[:5]:
        if idx >= len(gps_records):
            continue
        offset, lat, lon = gps_records[idx]
        all_q = find_all_quaternions(data, offset, 250)
        trivial = [q for q in all_q if q['trivial']]
        real = [q for q in all_q if not q['trivial']]
        
        print("  Record {}: {} trivial, {} real".format(idx, len(trivial), len(real)))
        for q in real:
            print("    REAL: rel={} vals=({:+.4f},{:+.4f},{:+.4f},{:+.4f}) yaw={:.1f} pitch={:.1f}".format(
                q['rels'], q['vals'][0], q['vals'][1], q['vals'][2], q['vals'][3],
                q['yaw'], q['pitch']))

    print()
    print("=" * 150)
    print("CACH DOC KET QUA:")
    print("  Neu Yaw THAY DOI va KHOP voi GPS bearing → quaternion = DRONE HEADING")
    print("  Neu Yaw CO DINH khi GPS bearing thay doi → quaternion = GIMBAL orientation")
    print("  Neu Alt CO DINH ~44m across all records → confirmed altitude AGL")
    print("  Neu Gimbal ~-48 nhung thay doi nhe → confirmed gimbal pitch")
    print("=" * 150)
