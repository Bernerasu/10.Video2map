"""
DJI Meta — Brute Force Float Scanner
Scan tất cả float32 xung quanh mỗi GPS record, tìm pattern:
  - Quaternion: 4 float liên tiếp, |q| ≈ 1.0
  - Altitude: float 20-200m, stable across records  
  - Gimbal pitch: float -90 to 0

Chạy: python3 scan_djmd_floats.py /tmp/dji_meta.bin
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


def scan_floats_around(data, center, radius=200):
    """Scan tất cả float32 trong [center-radius, center+radius]"""
    start = max(0, center - radius)
    end = min(len(data) - 4, center + radius)
    floats = []
    for pos in range(start, end):
        f = struct.unpack('<f', data[pos:pos+4])[0]
        if math.isnan(f) or math.isinf(f):
            continue
        if f == 0.0 or abs(f) > 1e8:
            continue
        floats.append((pos, pos - center, f))
    return floats


def find_quaternion(floats_list):
    """Tìm 4 float liên tiếp cách nhau 5 bytes (1 tag + 4 data) với |q|≈1"""
    for i in range(len(floats_list)):
        pos_i, rel_i, val_i = floats_list[i]
        if not (-1.1 <= val_i <= 1.1):
            continue
        
        # Tìm 3 float tiếp theo cách nhau 5 bytes
        candidates = [floats_list[i]]
        for gap in [5, 10, 15]:  # 5 bytes apart each
            target_pos = pos_i + gap
            found = False
            for j in range(i+1, min(i+50, len(floats_list))):
                if floats_list[j][0] == target_pos:
                    candidates.append(floats_list[j])
                    found = True
                    break
            if not found:
                break
        
        if len(candidates) == 4:
            vals = [c[2] for c in candidates]
            mag = math.sqrt(sum(v**2 for v in vals))
            if 0.95 < mag < 1.05:
                return candidates, vals, mag
    
    return None, None, None


def quaternion_to_euler(qx, qy, qz, qw):
    """Quaternion → yaw, pitch, roll (degrees)"""
    # Yaw (Z-axis rotation)
    yaw = math.degrees(math.atan2(2*(qw*qz + qx*qy), 1 - 2*(qy**2 + qz**2)))
    if yaw < 0:
        yaw += 360
    
    # Pitch (Y-axis rotation)
    sinp = 2*(qw*qy - qz*qx)
    sinp = max(-1, min(1, sinp))
    pitch = math.degrees(math.asin(sinp))
    
    # Roll (X-axis rotation)
    roll = math.degrees(math.atan2(2*(qw*qx + qy*qz), 1 - 2*(qx**2 + qy**2)))
    
    return yaw, pitch, roll


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    bin_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/dji_meta.bin"

    with open(bin_path, 'rb') as f:
        data = f.read()

    print("=" * 130)
    print("DJI Meta — Brute Force Float Scanner")
    print("File: {} ({:,} bytes)".format(bin_path, len(data)))
    print("=" * 130)

    gps_records = find_gps_records(data)
    print("GPS records: {}".format(len(gps_records)))
    print()

    # ============================================================
    # PHẦN 1: Chi tiết Record 0 — tất cả float đáng chú ý
    # ============================================================
    print("=" * 130)
    print("RECORD 0 — Tat ca float32 dang chu y (radius 200 bytes quanh GPS)")
    print("=" * 130)
    
    offset0 = gps_records[0][0]
    floats0 = scan_floats_around(data, offset0, 200)
    
    print("{:>7} {:>6} | {:>15} | {}".format("Offset", "Rel", "Value", "Guess"))
    print("-" * 80)
    
    for pos, rel, val in floats0:
        guess = ""
        if 20 < val < 200:
            guess = "ALT? ({:.1f}m)".format(val)
        elif -90 <= val < -1:
            guess = "GIMBAL PITCH? ({:.1f} deg)".format(val)
        elif 100 < val < 400 and val != 100.0:
            guess = "HEADING? ({:.1f} deg)".format(val)
        elif -1.1 <= val <= 1.1 and abs(val) > 0.01:
            guess = "QUAT? ({:.4f})".format(val)
        elif val == 100.0:
            guess = "ALT_LIMIT or HEADING=100?"
        elif 0.5 < val < 10:
            guess = "speed/config ({:.2f})".format(val)
        elif 40 < val < 50:
            guess = "ALT or FPS? ({:.2f})".format(val)
        else:
            guess = "({:.4f})".format(val)
        
        print("{:7d} {:+6d} | {:15.6f} | {}".format(pos, rel, val, guess))
    
    # Tìm quaternion
    quat_cands, quat_vals, quat_mag = find_quaternion(floats0)
    if quat_vals:
        print()
        print(">>> QUATERNION FOUND:")
        for c in quat_cands:
            print("    offset {} (rel {:+d}): {:.6f}".format(c[0], c[1], c[2]))
        yaw, pitch, roll = quaternion_to_euler(*quat_vals)
        print("    |q| = {:.6f}".format(quat_mag))
        print("    Yaw (heading) = {:.1f} deg".format(yaw))
        print("    Pitch = {:.1f} deg".format(pitch))
        print("    Roll = {:.1f} deg".format(roll))
    else:
        print()
        print(">>> QUATERNION: not found with 5-byte spacing, trying other spacings...")
        # Thử tìm bất kỳ 4 float liên tiếp trong range -1..1 với |q|≈1
        quat_range = [(pos, rel, val) for pos, rel, val in floats0 
                      if -1.1 <= val <= 1.1 and abs(val) > 0.01]
        print("    Quaternion candidates ({} values in [-1.1, 1.1]):".format(len(quat_range)))
        for pos, rel, val in quat_range:
            print("      offset {} (rel {:+d}): {:.6f}".format(pos, rel, val))
        
        # Thử mọi tổ hợp 4 giá trị
        for i in range(len(quat_range)):
            for j in range(i+1, min(i+8, len(quat_range))):
                for k in range(j+1, min(j+8, len(quat_range))):
                    for l in range(k+1, min(k+8, len(quat_range))):
                        vals = [quat_range[i][2], quat_range[j][2], 
                                quat_range[k][2], quat_range[l][2]]
                        mag = math.sqrt(sum(v**2 for v in vals))
                        if 0.95 < mag < 1.05:
                            print("    >>> FOUND! offsets: {}, {}, {}, {}".format(
                                quat_range[i][0], quat_range[j][0],
                                quat_range[k][0], quat_range[l][0]))
                            print("    >>> values: {:.4f}, {:.4f}, {:.4f}, {:.4f} |q|={:.4f}".format(
                                *vals, mag))
                            yaw, pitch, roll = quaternion_to_euler(*vals)
                            print("    >>> yaw={:.1f} pitch={:.1f} roll={:.1f}".format(yaw, pitch, roll))

    # ============================================================
    # PHẦN 2: So sánh nhiều records
    # ============================================================
    print()
    print("=" * 130)
    print("SO SANH 10 MAU — Tim pattern thay doi")
    print("=" * 130)

    # Chọn 10 mẫu phân bố đều
    indices = []
    step = max(1, len(gps_records) // 9)
    for k in range(0, len(gps_records), step):
        indices.append(k)
    indices = indices[:10]
    if len(gps_records) - 1 not in indices:
        indices.append(len(gps_records) - 1)

    # Với mỗi mẫu, tìm: altitude candidates, quaternion, gimbal
    print()
    hdr = "{:>5} {:>7} | {:>12} {:>12} | {:>8} {:>8} {:>8} | {:>7} {:>7} {:>7} | {:>40}".format(
        "Idx", "Offset", "Lat", "Lon",
        "Alt_a", "Alt_b", "Gimbal",
        "Yaw", "Pitch", "Roll",
        "Quaternion"
    )
    print(hdr)
    print("-" * len(hdr))

    for idx in indices:
        if idx >= len(gps_records):
            continue
        offset, lat, lon = gps_records[idx]
        floats = scan_floats_around(data, offset, 200)
        
        # Altitude candidates (20-200, exclude 47.95 which is FPS)
        alts = [(p, r, v) for p, r, v in floats if 20 < v < 200 and abs(v - 47.95) > 0.1]
        alt_a = "{:.1f}".format(alts[0][2]) if len(alts) >= 1 else ""
        alt_b = "{:.1f}".format(alts[1][2]) if len(alts) >= 2 else ""
        
        # Gimbal pitch (-90 to 0)
        gimbals = [(p, r, v) for p, r, v in floats if -90 <= v < -1]
        gimbal_str = "{:.1f}".format(gimbals[0][2]) if gimbals else ""
        
        # Quaternion
        qc, qv, qm = find_quaternion(floats)
        yaw_str = ""
        pitch_str = ""
        roll_str = ""
        q_str = ""
        if qv:
            yaw, pitch, roll = quaternion_to_euler(*qv)
            yaw_str = "{:.1f}".format(yaw)
            pitch_str = "{:.1f}".format(pitch)
            roll_str = "{:.1f}".format(roll)
            q_str = "({:+.3f},{:+.3f},{:+.3f},{:+.3f})".format(*qv)
        
        row = "{:5d} {:7d} | {:12.7f} {:12.7f} | {:>8} {:>8} {:>8} | {:>7} {:>7} {:>7} | {:>40}".format(
            idx, offset, lat, lon,
            alt_a, alt_b, gimbal_str,
            yaw_str, pitch_str, roll_str,
            q_str
        )
        print(row)

    print()
    print("Luu y:")
    print("  - Alt_a, Alt_b: 2 altitude candidates dau tien (loai 47.95 = FPS)")
    print("  - Gimbal: float dau tien trong [-90, -1]")
    print("  - Yaw/Pitch/Roll: tinh tu quaternion")
    print("  - Neu cot trong -> khong tim thay pattern trong +-200 bytes quanh GPS")
