"""
Phân tích DJI meta binary (dvtm protobuf) — tìm altitude, heading, gimbal
Chạy trên Jetson: python3 analyze_djmd.py /tmp/dji_meta.bin

Chiến lược:
1. Tìm 5 GPS points đầu tiên (lat/lon đã biết cách tìm)
2. Với mỗi GPS point, dump tất cả float32 và double trong phạm vi ±200 bytes
3. So sánh giá trị giữa các record → field thay đổi = heading/gimbal, cố định = altitude
"""

import struct
import math
import sys

# ============================================================
# CONFIG
# ============================================================

# Giới hạn Việt Nam (radians)
LAT_RAD_RANGE = (math.radians(5.0), math.radians(25.0))
LON_RAD_RANGE = (math.radians(100.0), math.radians(120.0))

# Số record cần phân tích
NUM_RECORDS = 5


def find_gps_records(data: bytes, max_records: int = 5):
    """Tìm GPS records — trả về list (offset_lat, lat_deg, lon_deg, offset_lon, gap)"""
    records = []
    lat_min, lat_max = LAT_RAD_RANGE
    lon_min, lon_max = LON_RAD_RANGE
    
    i = 0
    while i < len(data) - 16 and len(records) < max_records:
        val = struct.unpack('<d', data[i:i+8])[0]
        
        if lat_min < val < lat_max:
            lat_deg = math.degrees(val)
            
            for j in range(1, 20):
                if i + j + 8 <= len(data):
                    val2 = struct.unpack('<d', data[i+j:i+j+8])[0]
                    if lon_min < val2 < lon_max:
                        lon_deg = math.degrees(val2)
                        records.append({
                            'lat_offset': i,
                            'lon_offset': i + j,
                            'gap': j,
                            'lat': lat_deg,
                            'lon': lon_deg,
                        })
                        i += 100  # Skip ahead
                        break
            else:
                i += 1
                continue
            continue
        i += 1
    
    return records


def scan_nearby_values(data: bytes, gps_offset: int, range_before: int = 200, range_after: int = 200):
    """Scan tất cả float32 và float64 xung quanh GPS offset"""
    start = max(0, gps_offset - range_before)
    end = min(len(data), gps_offset + range_after)
    
    results = {
        'floats': [],   # (offset, value, relative_offset)
        'doubles': [],  # (offset, value, relative_offset)
    }
    
    # Scan float32 (mỗi 1 byte để không bỏ sót)
    for pos in range(start, end - 4):
        try:
            f = struct.unpack('<f', data[pos:pos+4])[0]
            rel = pos - gps_offset
            
            # Lọc giá trị "có ý nghĩa"
            if math.isnan(f) or math.isinf(f):
                continue
            if abs(f) > 1e6 or f == 0.0:
                continue
            
            # Các khoảng giá trị quan tâm
            tags = []
            if 10 < f < 500:
                tags.append(f'ALT={f:.1f}m')
            if 0 <= f <= 360:
                tags.append(f'HDG={f:.1f}°')
            if -180 <= f <= 180 and abs(f) > 0.01:
                tags.append(f'ANG={f:.1f}°')
            if -1.1 <= f <= 1.1 and abs(f) > 0.01:
                tags.append(f'QUAT={f:.4f}')
            if 0.01 < f < 50:
                tags.append(f'SPD={f:.2f}m/s')
            
            if tags:
                results['floats'].append({
                    'offset': pos,
                    'rel': rel,
                    'value': f,
                    'hex': data[pos:pos+4].hex(),
                    'tags': tags,
                })
        except:
            pass
    
    # Scan double (8 bytes)
    for pos in range(start, end - 8):
        try:
            d = struct.unpack('<d', data[pos:pos+8])[0]
            rel = pos - gps_offset
            
            if math.isnan(d) or math.isinf(d):
                continue
            if abs(d) > 1e6 or d == 0.0:
                continue
            
            tags = []
            if 10 < d < 500:
                tags.append(f'ALT={d:.1f}m')
            if 0 <= d < 2 * math.pi:
                tags.append(f'RAD={d:.4f}→{math.degrees(d):.1f}°')
            
            if tags:
                results['doubles'].append({
                    'offset': pos,
                    'rel': rel,
                    'value': d,
                    'hex': data[pos:pos+8].hex(),
                    'tags': tags,
                })
        except:
            pass
    
    return results


def compare_records(all_scans, records):
    """So sánh float values giữa các record → tìm pattern"""
    if len(all_scans) < 2:
        return
    
    # Tính khoảng cách giữa các record
    record_gaps = []
    for i in range(1, len(records)):
        gap = records[i]['lat_offset'] - records[i-1]['lat_offset']
        record_gaps.append(gap)
    
    avg_gap = sum(record_gaps) / len(record_gaps) if record_gaps else 0
    print(f"\n{'='*70}")
    print(f"SO SÁNH GIỮA {len(records)} RECORDS")
    print(f"{'='*70}")
    print(f"Khoảng cách trung bình giữa records: {avg_gap:.0f} bytes")
    print()
    
    # Tìm relative offsets xuất hiện ở TẤT CẢ records
    # Mỗi relative offset → list giá trị qua các record
    rel_offset_values = {}
    
    for scan_idx, scan in enumerate(all_scans):
        for f in scan['floats']:
            rel = f['rel']
            if rel not in rel_offset_values:
                rel_offset_values[rel] = {}
            rel_offset_values[rel][scan_idx] = f['value']
    
    # Lọc: chỉ giữ relative offset xuất hiện ở >= 3 records
    min_appearances = min(3, len(all_scans))
    common_offsets = {k: v for k, v in rel_offset_values.items() 
                     if len(v) >= min_appearances}
    
    # Phân loại: STABLE (sai lệch < 1%) vs CHANGING
    print(f"{'Rel Offset':>12} │ {'Record Values':40} │ {'Biến đổi':>10} │ {'Khả năng'}")
    print(f"{'─'*12}─┼─{'─'*40}─┼─{'─'*10}─┼─{'─'*30}")
    
    interesting = []
    
    for rel in sorted(common_offsets.keys()):
        values = common_offsets[rel]
        vals = [values.get(i, None) for i in range(len(all_scans))]
        vals_present = [v for v in vals if v is not None]
        
        if not vals_present:
            continue
        
        avg = sum(vals_present) / len(vals_present)
        if avg == 0:
            continue
        
        max_dev = max(abs(v - avg) for v in vals_present)
        pct_dev = (max_dev / abs(avg)) * 100 if avg != 0 else 0
        
        # Classify
        if pct_dev < 0.1:
            change_type = "STABLE"
        elif pct_dev < 5:
            change_type = "~stable"
        else:
            change_type = "CHANGING"
        
        # Guess purpose
        guess = ""
        if change_type == "STABLE":
            if 10 < avg < 500:
                guess = "★ ALTITUDE?"
            elif abs(avg - 1.0) < 0.01:
                guess = "scale/flag"
            elif 0 < avg < 10:
                guess = "config?"
        else:  # CHANGING
            if 0 <= avg <= 360:
                guess = "★ HEADING/YAW?"
            if -90 <= avg <= 0:
                guess = "★ GIMBAL PITCH?"
            if -1.1 <= avg <= 1.1:
                guess = "★ QUATERNION?"
            if 0 < avg < 50:
                guess = "★ SPEED?"
        
        if guess or change_type == "CHANGING":
            val_str = " | ".join([f"{v:.4f}" if v is not None else "  N/A " for v in vals])
            print(f"  {rel:+6d}      │ {val_str:40} │ {change_type:>10} │ {guess}")
            interesting.append((rel, vals_present, change_type, guess))
    
    return interesting


def protobuf_scan(data: bytes, gps_offset: int, range_before: int = 100):
    """Thử parse protobuf tags xung quanh GPS"""
    print(f"\n  --- Protobuf tags gần GPS (offset {gps_offset}) ---")
    
    start = max(0, gps_offset - range_before)
    end = min(len(data), gps_offset + 150)
    
    pos = start
    while pos < end:
        if pos + 1 >= len(data):
            break
        
        byte = data[pos]
        wire_type = byte & 0x07
        field_num = byte >> 3
        
        # Chỉ xử lý wire types hợp lệ
        if wire_type == 1 and field_num > 0 and field_num < 30:  # 64-bit (double)
            if pos + 9 <= len(data):
                val = struct.unpack('<d', data[pos+1:pos+9])[0]
                rel = pos - gps_offset
                if not (math.isnan(val) or math.isinf(val)) and abs(val) < 1e6:
                    extra = ""
                    if LAT_RAD_RANGE[0] < val < LAT_RAD_RANGE[1]:
                        extra = f" ← LAT ({math.degrees(val):.6f}°)"
                    elif LON_RAD_RANGE[0] < val < LON_RAD_RANGE[1]:
                        extra = f" ← LON ({math.degrees(val):.6f}°)"
                    elif 0 < val < 500:
                        extra = f" ← alt? ({val:.1f}m)"
                    print(f"    offset {pos:5d} (rel {rel:+4d}): "
                          f"field={field_num} double={val:.7f}{extra}")
                pos += 9
                continue
                
        elif wire_type == 5 and field_num > 0 and field_num < 30:  # 32-bit (float)
            if pos + 5 <= len(data):
                val = struct.unpack('<f', data[pos+1:pos+5])[0]
                rel = pos - gps_offset
                if not (math.isnan(val) or math.isinf(val)) and abs(val) < 1e6 and val != 0:
                    extra = ""
                    if 10 < val < 500:
                        extra = f" ← ALT? ({val:.1f}m)"
                    elif 0 <= val <= 360:
                        extra = f" ← angle? ({val:.1f}°)"
                    elif -90 <= val <= 90:
                        extra = f" ← angle? ({val:.1f}°)"
                    elif -1.5 <= val <= 1.5:
                        extra = f" ← quat/rad? ({val:.4f})"
                    print(f"    offset {pos:5d} (rel {rel:+4d}): "
                          f"field={field_num} float ={val:.6f}{extra}")
                pos += 5
                continue
        
        pos += 1


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    bin_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/dji_meta.bin"
    
    with open(bin_path, 'rb') as f:
        data = f.read()
    
    print(f"{'='*70}")
    print(f"DJI Meta Binary Analyzer")
    print(f"File: {bin_path} ({len(data):,} bytes)")
    print(f"{'='*70}\n")
    
    # === Bước 1: Tìm GPS records ===
    print(f"── Bước 1: Tìm {NUM_RECORDS} GPS records ──\n")
    records = find_gps_records(data, NUM_RECORDS)
    
    for idx, rec in enumerate(records):
        print(f"  Record {idx}: offset={rec['lat_offset']:5d} | "
              f"lat={rec['lat']:.7f} lon={rec['lon']:.7f} | "
              f"gap lat→lon={rec['gap']} bytes")
    
    if len(records) >= 2:
        gap = records[1]['lat_offset'] - records[0]['lat_offset']
        print(f"\n  Record spacing: ~{gap} bytes/record")
    
    # === Bước 2: Scan giá trị xung quanh mỗi GPS ===
    print(f"\n── Bước 2: Protobuf tags gần GPS ──")
    all_scans = []
    
    for idx, rec in enumerate(records[:3]):  # Chi tiết 3 record đầu
        print(f"\n  ▶ Record {idx} (GPS offset={rec['lat_offset']})")
        protobuf_scan(data, rec['lat_offset'])
        scan = scan_nearby_values(data, rec['lat_offset'])
        all_scans.append(scan)
    
    # Scan thêm records còn lại (không in chi tiết)
    for idx, rec in enumerate(records[3:], 3):
        scan = scan_nearby_values(data, rec['lat_offset'])
        all_scans.append(scan)
    
    # === Bước 3: So sánh ===
    compare_records(all_scans, records)
    
    # === Bước 4: Summary ===
    print(f"\n{'='*70}")
    print("GỢI Ý BƯỚC TIẾP THEO")
    print(f"{'='*70}")
    print("""
Từ bảng trên, tìm:
  ★ ALTITUDE:     STABLE, giá trị 10-500m (thường cố định khi bay đều)
  ★ HEADING:      CHANGING, giá trị 0-360° (thay đổi khi drone xoay)
  ★ GIMBAL PITCH: STABLE ~ -90° (camera nhìn xuống) hoặc CHANGING
  ★ SPEED:        CHANGING, giá trị 0-20 m/s
  ★ QUATERNION:   CHANGING, giá trị -1 đến 1 (4 giá trị liên tiếp)

Chạy thêm với đoạn video drone XOAY HƯỚNG để thấy heading thay đổi rõ.
""")
