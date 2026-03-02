"""
Parse DJI meta binary (dvtm protobuf) — tìm altitude, heading, gimbal
Chạy: python3 parse_djmd_proto.py /tmp/dji_meta.bin
"""

import struct
import math
import sys


def read_varint(data, pos):
    result = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        result |= (b & 0x7F) << shift
        pos += 1
        if not (b & 0x80):
            break
        shift += 7
    return result, pos


def parse_protobuf(data, start, end):
    """Parse protobuf fields trong vùng [start, end)"""
    fields = []
    pos = start
    while pos < end:
        try:
            tag, pos2 = read_varint(data, pos)
            if tag == 0 or pos2 >= end:
                pos += 1
                continue

            wire_type = tag & 0x07
            field_num = tag >> 3

            if field_num > 500 or field_num < 1:
                pos += 1
                continue

            if wire_type == 0:  # Varint
                val, pos2 = read_varint(data, pos2)
                fields.append((field_num, 'varint', val, pos))
                pos = pos2
            elif wire_type == 1:  # 64-bit (double)
                if pos2 + 8 > end:
                    pos += 1
                    continue
                val = struct.unpack('<d', data[pos2:pos2+8])[0]
                fields.append((field_num, 'double', val, pos))
                pos = pos2 + 8
            elif wire_type == 2:  # Length-delimited
                length, pos2 = read_varint(data, pos2)
                if length > 10000 or pos2 + length > end:
                    pos += 1
                    continue
                raw = data[pos2:pos2+length]
                fields.append((field_num, 'bytes', raw, pos))
                pos = pos2 + length
            elif wire_type == 5:  # 32-bit (float)
                if pos2 + 4 > end:
                    pos += 1
                    continue
                val = struct.unpack('<f', data[pos2:pos2+4])[0]
                fields.append((field_num, 'float', val, pos))
                pos = pos2 + 4
            else:
                pos += 1
        except:
            pos += 1
    return fields


def find_gps_records(data):
    """Tìm tất cả GPS records"""
    lat_min, lat_max = math.radians(5.0), math.radians(25.0)
    lon_min, lon_max = math.radians(100.0), math.radians(120.0)

    records = []
    i = 0
    while i < len(data) - 16:
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


def analyze_record(data, gps_offset):
    """Parse protobuf xung quanh 1 GPS record, trả về telemetry"""
    start = max(0, gps_offset - 250)
    end = min(len(data), gps_offset + 250)
    fields = parse_protobuf(data, start, end)

    result = {
        'altitudes': [],
        'angles': [],
        'quaternion': None,
        'yaw': None,
        'pitch': None,
    }

    quaternion_candidates = []

    for f in fields:
        fn, wt, val = f[0], f[1], f[2]

        if wt == 'float' and not (math.isnan(val) or math.isinf(val)):
            # Altitude candidates
            if 10 < val < 500:
                result['altitudes'].append((fn, val))

            # Angle candidates
            if -180 <= val <= 360 and abs(val) > 0.5:
                result['angles'].append((fn, val))

            # Quaternion candidates
            if -1.1 <= val <= 1.1 and abs(val) > 0.001:
                quaternion_candidates.append((fn, val))

    # Tìm cụm quaternion (4 float liên tiếp field 1,2,3,4 với |q|≈1)
    for qi in range(len(quaternion_candidates) - 3):
        q = quaternion_candidates[qi:qi+4]
        fns = [x[0] for x in q]
        vals = [x[1] for x in q]
        mag = math.sqrt(sum(v**2 for v in vals))
        if 0.95 < mag < 1.05:
            qx, qy, qz, qw = vals
            result['quaternion'] = (qx, qy, qz, qw)

            # Yaw (heading) from quaternion
            yaw = math.degrees(math.atan2(
                2*(qw*qz + qx*qy),
                1 - 2*(qy**2 + qz**2)
            ))
            if yaw < 0:
                yaw += 360
            result['yaw'] = yaw

            # Pitch
            pitch_arg = 2*(qw*qy - qz*qx)
            pitch_arg = max(-1, min(1, pitch_arg))
            result['pitch'] = math.degrees(math.asin(pitch_arg))
            break

    return result


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    bin_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/dji_meta.bin"

    with open(bin_path, 'rb') as f:
        data = f.read()

    print("=" * 100)
    print("DJI Meta Protobuf Parser")
    print("File: {} ({:,} bytes)".format(bin_path, len(data)))
    print("=" * 100)

    # Tìm GPS records
    gps_records = find_gps_records(data)
    print("Tổng GPS records: {}".format(len(gps_records)))
    print()

    # Lấy 10 mẫu phân bố đều
    sample_indices = [0, 1, 2]
    step = max(1, len(gps_records) // 7)
    for k in range(3, len(gps_records), step):
        sample_indices.append(k)
    sample_indices = sorted(set(sample_indices))[:12]

    # Header
    hdr = "{:>5} {:>7} | {:>12} {:>12} | {:>8} {:>8} {:>10} | {:>42} | {:>8} {:>8}".format(
        "Idx", "Offset", "Lat", "Lon",
        "Alt1", "Alt2", "Gimbal?",
        "Quaternion (x, y, z, w)",
        "Yaw°", "Pitch°"
    )
    print(hdr)
    print("-" * len(hdr))

    for idx in sample_indices:
        if idx >= len(gps_records):
            continue
        offset, lat, lon = gps_records[idx]
        r = analyze_record(data, offset)

        # Altitude: lấy 2 candidates đầu
        alt1 = ""
        alt2 = ""
        if len(r['altitudes']) >= 1:
            alt1 = "{:.1f}".format(r['altitudes'][0][1])
        if len(r['altitudes']) >= 2:
            alt2 = "{:.1f}".format(r['altitudes'][1][1])

        # Gimbal pitch candidate
        gimbal = ""
        for fn, v in r['angles']:
            if -90 <= v <= 0:
                gimbal = "{:.1f}".format(v)
                break

        # Quaternion
        q_str = ""
        yaw_str = ""
        pitch_str = ""
        if r['quaternion']:
            qx, qy, qz, qw = r['quaternion']
            q_str = "({:+.4f}, {:+.4f}, {:+.4f}, {:+.4f})".format(qx, qy, qz, qw)
        if r['yaw'] is not None:
            yaw_str = "{:.1f}".format(r['yaw'])
        if r['pitch'] is not None:
            pitch_str = "{:.1f}".format(r['pitch'])

        row = "{:5d} {:7d} | {:12.7f} {:12.7f} | {:>8} {:>8} {:>10} | {:>42} | {:>8} {:>8}".format(
            idx, offset, lat, lon,
            alt1, alt2, gimbal,
            q_str,
            yaw_str, pitch_str
        )
        print(row)

    # === Chi tiết record 0 ===
    print()
    print("=" * 100)
    print("CHI TIET RECORD 0 — Tat ca float/double")
    print("=" * 100)
    
    offset0 = gps_records[0][0]
    start = max(0, offset0 - 250)
    end = min(len(data), offset0 + 250)
    fields = parse_protobuf(data, start, end)

    for f in fields:
        fn, wt, val = f[0], f[1], f[2]
        foffset = f[3]
        rel = foffset - offset0

        if wt in ('float', 'double'):
            if math.isnan(val) or math.isinf(val):
                continue
            if abs(val) > 1e8 or val == 0.0:
                continue

            tag = ""
            if wt == 'double':
                lat_min_r, lat_max_r = math.radians(5.0), math.radians(25.0)
                lon_min_r, lon_max_r = math.radians(100.0), math.radians(120.0)
                if lat_min_r < val < lat_max_r:
                    tag = " <<<< LATITUDE = {:.7f}".format(math.degrees(val))
                elif lon_min_r < val < lon_max_r:
                    tag = " <<<< LONGITUDE = {:.7f}".format(math.degrees(val))

            if wt == 'float':
                if 10 < val < 500:
                    tag = " <<<< ALT?"
                elif -90 <= val < 0:
                    tag = " <<<< GIMBAL PITCH?"
                elif 0 < val <= 360 and abs(val) > 1:
                    tag = " <<<< ANGLE/HEADING?"
                elif -1.1 <= val <= 1.1 and abs(val) > 0.01:
                    tag = " <<<< QUATERNION?"

            print("  rel {:+5d} | field {:2d} | {:6} = {:15.6f}{}".format(
                rel, fn, wt, val, tag))
