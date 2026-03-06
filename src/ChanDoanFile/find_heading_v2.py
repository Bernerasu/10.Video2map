"""
DJI Meta — Find Heading Convention v2
Dung quaternion SCANNER (khong fixed offset) de tim du 15 mau
Thu tat ca conventions, so sanh voi GPS bearing

Chay: python3 find_heading_v2.py /tmp/dji_meta.bin
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


def scan_quaternion(data, center, radius=250):
    """
    Scan tat ca float32, tim quaternion REAL:
    - 4 float cach 5 bytes
    - |q| in [0.95, 1.05]
    - >= 2 thanh phan co |val| > 0.05
    Tra ve quaternion dau tien tim thay (hoac None)
    """
    start = max(0, center - radius)
    end = min(len(data) - 4, center + radius)

    floats = {}
    for pos in range(start, end):
        f = struct.unpack('<f', data[pos:pos+4])[0]
        if not (math.isnan(f) or math.isinf(f)) and abs(f) <= 1.1:
            floats[pos] = f

    for pos in sorted(floats.keys()):
        positions = [pos, pos+5, pos+10, pos+15]
        if all(p in floats for p in positions):
            vals = [floats[p] for p in positions]
            mag = math.sqrt(sum(v**2 for v in vals))
            if 0.95 < mag < 1.05:
                big = sum(1 for v in vals if abs(v) > 0.05)
                if big >= 2:
                    return vals, [p - center for p in positions]
    return None, None


def normalize_angle(a):
    while a < 0: a += 360
    while a >= 360: a -= 360
    return a


def angle_diff(a, b):
    d = abs(normalize_angle(a) - normalize_angle(b))
    return min(d, 360 - d)


def euler_ZYX(qx, qy, qz, qw):
    yaw = math.atan2(2*(qw*qz + qx*qy), 1 - 2*(qy**2 + qz**2))
    sinp = max(-1, min(1, 2*(qw*qy - qz*qx)))
    pitch = math.asin(sinp)
    roll = math.atan2(2*(qw*qx + qy*qz), 1 - 2*(qx**2 + qy**2))
    return math.degrees(yaw), math.degrees(pitch), math.degrees(roll)


def quat_rotate_vector(q, v):
    qx, qy, qz, qw = q
    vx, vy, vz = v
    tx = 2 * (qy * vz - qz * vy)
    ty = 2 * (qz * vx - qx * vz)
    tz = 2 * (qx * vy - qy * vx)
    rx = vx + qw * tx + (qy * tz - qz * ty)
    ry = vy + qw * ty + (qz * tx - qx * tz)
    rz = vz + qw * tz + (qx * ty - qy * tx)
    return (rx, ry, rz)


# ============================================================
if __name__ == "__main__":
    bin_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/dji_meta.bin"

    with open(bin_path, 'rb') as f:
        data = f.read()

    print("=" * 140)
    print("DJI Meta — Find Heading Convention v2 (with quaternion scanner)")
    print("=" * 140)

    gps_records = find_gps_records(data)
    print("GPS records: {}".format(len(gps_records)))

    # 20 mau phan bo deu
    step = max(1, len(gps_records) // 19)
    indices = list(range(0, len(gps_records), step))[:20]
    if len(gps_records) - 1 not in indices:
        indices.append(len(gps_records) - 1)

    # Thu thap samples
    samples = []
    prev_lat = None
    prev_lon = None

    print()
    print("Scanning quaternions...")
    for idx in indices:
        if idx >= len(gps_records):
            continue
        offset, lat, lon = gps_records[idx]
        q, rels = scan_quaternion(data, offset, 250)

        gps_bear = None
        if prev_lat is not None:
            dlat = lat - prev_lat
            dlon = lon - prev_lon
            dist = math.sqrt(dlat**2 + dlon**2)
            if dist > 0.00003:
                gps_bear = normalize_angle(math.degrees(math.atan2(dlon, dlat)))

        status = "OK q=({:.3f},{:.3f},{:.3f},{:.3f}) rels={}".format(
            q[0], q[1], q[2], q[3], rels) if q else "NO QUATERNION"
        bear_str = "gps={:.0f}".format(gps_bear) if gps_bear is not None else "no_bearing"
        print("  idx={:5d} {} {}".format(idx, status, bear_str))

        if q is not None and gps_bear is not None:
            samples.append({
                'idx': idx,
                'lat': lat,
                'lon': lon,
                'q': q,
                'gps_bear': gps_bear,
                'rels': rels,
            })

        prev_lat = lat
        prev_lon = lon

    print()
    print("Valid samples (quaternion + GPS bearing): {}".format(len(samples)))
    print()

    if len(samples) < 3:
        print("KHONG DU MAU! Can it nhat 3 samples.")
        sys.exit(1)

    # ============================================================
    # Thu tat ca conventions
    # ============================================================
    orderings = [
        ("XYZW", lambda v: (v[0], v[1], v[2], v[3])),
        ("WXYZ", lambda v: (v[1], v[2], v[3], v[0])),
        ("XYWZ", lambda v: (v[0], v[1], v[3], v[2])),
        ("XZYW", lambda v: (v[0], v[2], v[1], v[3])),
        ("YXZW", lambda v: (v[1], v[0], v[2], v[3])),
        ("YZXW", lambda v: (v[1], v[2], v[0], v[3])),
        ("ZXYW", lambda v: (v[2], v[0], v[1], v[3])),
        ("ZYXW", lambda v: (v[2], v[1], v[0], v[3])),
        ("WXZY", lambda v: (v[1], v[3], v[2], v[0])),
        ("WYZX", lambda v: (v[2], v[3], v[0], v[1])),
        ("WZXY", lambda v: (v[3], v[1], v[2], v[0])),
        ("WZYX", lambda v: (v[3], v[2], v[1], v[0])),
    ]

    # ============================================================
    # METHOD 1: Euler decomposition
    # ============================================================
    print("=" * 140)
    print("METHOD 1: Euler decomposition — 12 orderings x 6 components = 72 combos")
    print("=" * 140)

    results1 = []
    for order_name, order_fn in orderings:
        for comp_name, extract_fn in [
            ("yaw",    lambda y,p,r: y),
            ("pitch",  lambda y,p,r: p),
            ("roll",   lambda y,p,r: r),
            ("-yaw",   lambda y,p,r: -y),
            ("-pitch", lambda y,p,r: -p),
            ("-roll",  lambda y,p,r: -r),
        ]:
            errors = []
            headings = []
            for s in samples:
                qx, qy, qz, qw = order_fn(s['q'])
                yaw, pitch, roll = euler_ZYX(qx, qy, qz, qw)
                heading = normalize_angle(extract_fn(yaw, pitch, roll))
                err = angle_diff(heading, s['gps_bear'])
                errors.append(err)
                headings.append(heading)
            avg = sum(errors) / len(errors)
            median = sorted(errors)[len(errors)//2]
            results1.append((avg, median, order_name, comp_name, headings, errors))

    results1.sort(key=lambda x: x[0])

    print()
    print("{:>6} {:>6} {:>8} {:>8} | {}".format(
        "Avg", "Med", "Order", "Comp", "heading/gps_bear (error)"))
    print("-" * 140)

    for avg, med, oname, cname, headings, errors in results1[:10]:
        detail = ""
        for i, s in enumerate(samples):
            detail += " {:.0f}/{:.0f}({:.0f})".format(headings[i], s['gps_bear'], errors[i])
        print("{:6.1f} {:6.1f} {:>8} {:>8} |{}".format(avg, med, oname, cname, detail))

    # ============================================================
    # METHOD 2: Vector rotation
    # ============================================================
    print()
    print("=" * 140)
    print("METHOD 2: Vector rotation — 12 orderings x 4 vectors x 2 frames = 96 combos")
    print("=" * 140)

    fwd_vecs = [("X+", (1,0,0)), ("X-", (-1,0,0)), ("Y+", (0,1,0)), ("Y-", (0,-1,0))]
    frames = [
        ("NED", lambda rx, ry: math.degrees(math.atan2(ry, rx))),
        ("ENU", lambda rx, ry: math.degrees(math.atan2(rx, ry))),
    ]

    results2 = []
    for order_name, order_fn in orderings:
        for fwd_name, fwd_vec in fwd_vecs:
            for frame_name, heading_fn in frames:
                errors = []
                headings = []
                for s in samples:
                    qx, qy, qz, qw = order_fn(s['q'])
                    rx, ry, rz = quat_rotate_vector((qx, qy, qz, qw), fwd_vec)
                    heading = normalize_angle(heading_fn(rx, ry))
                    err = angle_diff(heading, s['gps_bear'])
                    errors.append(err)
                    headings.append(heading)
                avg = sum(errors) / len(errors)
                median = sorted(errors)[len(errors)//2]
                results2.append((avg, median, order_name, fwd_name, frame_name, headings, errors))

    results2.sort(key=lambda x: x[0])

    print()
    print("{:>6} {:>6} {:>8} {:>4} {:>5} | {}".format(
        "Avg", "Med", "Order", "Fwd", "Frame", "heading/gps_bear (error)"))
    print("-" * 140)

    for avg, med, oname, fname, frname, headings, errors in results2[:10]:
        detail = ""
        for i, s in enumerate(samples):
            detail += " {:.0f}/{:.0f}({:.0f})".format(headings[i], s['gps_bear'], errors[i])
        print("{:6.1f} {:6.1f} {:>8} {:>4} {:>5} |{}".format(avg, med, oname, fname, frname, detail))

    # ============================================================
    # BEST RESULT
    # ============================================================
    print()
    print("=" * 140)

    # Pick overall best
    best1 = results1[0]
    best2 = results2[0]

    if best1[0] <= best2[0]:
        avg, med, oname, cname, headings, errors = best1
        print("BEST: Euler {} {} — avg={:.1f} median={:.1f}".format(oname, cname, avg, med))
    else:
        avg, med, oname, fname, frname, headings, errors = best2
        print("BEST: Vector {} {} {} — avg={:.1f} median={:.1f}".format(oname, fname, frname, avg, med))

    print()
    print("{:>5} {:>12} {:>12} | {:>8} {:>8} {:>8}".format(
        "Idx", "Lat", "Lon", "Heading", "GPS", "Error"))
    print("-" * 70)
    for i, s in enumerate(samples):
        print("{:5d} {:12.7f} {:12.7f} | {:8.1f} {:8.1f} {:8.1f}".format(
            s['idx'], s['lat'], s['lon'], headings[i], s['gps_bear'], errors[i]))

    print()
    avg_good = sum(1 for e in errors if e < 20) / len(errors) * 100
    print("Samples with error < 20 deg: {:.0f}%".format(avg_good))
    print()
    if avg < 25:
        print(">>> KET LUAN: Quaternion = DRONE HEADING (avg error {:.1f} deg)".format(avg))
        print("    Convention: see BEST above")
    elif avg < 45:
        print(">>> KET LUAN: Quaternion CO THE la drone heading nhung co noise")
        print("    Hoac drone bay ngang (crab angle) o mot so diem")
    else:
        print(">>> KET LUAN: Quaternion KHONG PHAI drone heading")
        print("    Co the la GIMBAL orientation hoac CAMERA orientation")
        print("    Can dung GPS bearing thay the cho heading")
    print("=" * 140)
