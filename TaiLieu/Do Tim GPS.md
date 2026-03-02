# Video2Map — Tổng hợp lệnh GPS & Frame Extraction
**Ngày:** 01/03/2026  
**Thiết bị:** Jetson Orin Nano Super 8GB | JetPack 6  
**Video test:** DJI Mini 4 Pro — bay tại Minh Thiện

---

## Phần 1: Tìm GPS trong video DJI

### 1.1 Cài exiftool — đọc metadata ảnh/video
```bash
sudo apt install -y libimage-exiftool-perl
```
> **Ý nghĩa:** exiftool là công cụ đọc metadata (EXIF, XMP, GPS...) từ file ảnh/video. 
> Cần cài để kiểm tra video DJI có chứa GPS ở đâu.

---

### 1.2 Thử tìm GPS bằng exiftool — KHÔNG TÌM THẤY
```bash
exiftool -ee -G3 Video_drone/DJI_20260226141901_0017_D_MINHTHIEN.MP4 | grep -i "gps\|lat\|lon\|alt\|gimbal"
```
> **Ý nghĩa từng phần:**
> - `exiftool` — chạy công cụ đọc metadata
> - `-ee` — Extract embedded data (đọc sâu vào các track bên trong MP4)
> - `-G3` — Hiển thị tên group chi tiết cho mỗi tag
> - `| grep -i "gps\|lat\|lon\|alt\|gimbal"` — Lọc chỉ hiển thị dòng chứa từ khóa GPS, latitude, longitude, altitude, gimbal (không phân biệt hoa/thường)
>
> **Kết quả:** Không có output → DJI Mini 4 Pro KHÔNG lưu GPS vào exif metadata thông thường.

---

### 1.3 Kiểm tra cấu trúc tracks trong video
```bash
ffprobe -v error -show_streams Video_drone/DJI_20260226141901_0017_D_MINHTHIEN.MP4 | grep -E "codec_type|codec_name|TAG"
```
> **Ý nghĩa từng phần:**
> - `ffprobe` — công cụ phân tích file media (đi kèm ffmpeg)
> - `-v error` — chỉ hiển thị lỗi, ẩn thông tin thừa
> - `-show_streams` — liệt kê tất cả tracks (video, audio, data) trong file
> - `| grep -E "codec_type|codec_name|TAG"` — Lọc hiển thị loại track, codec và tags
>
> **Kết quả:** Phát hiện 4 tracks:
> | Track | Loại | Nội dung |
> |-------|------|----------|
> | Stream 0:0 | video | H.264 4K (3840x2160 48fps) |
> | Stream 0:1 | data | **DJI meta** (djmd) ← GPS NẰM Ở ĐÂY |
> | Stream 0:2 | data | DJI dbgi (debug info) |
> | Stream 0:3 | video | MJPEG 720p (thumbnail) |

---

### 1.4 Extract DJI meta track ra file binary
```bash
ffmpeg -i Video_drone/DJI_20260226141901_0017_D_MINHTHIEN.MP4 -map 0:d:0 -f data -c copy /tmp/dji_meta.bin
```
> **Ý nghĩa từng phần:**
> - `ffmpeg -i <file>` — mở file video đầu vào
> - `-map 0:d:0` — chọn data track đầu tiên (d=data, 0=track số 0) → đây là "DJI meta"
> - `-f data` — xuất ra dạng raw data (không có container format)
> - `-c copy` — copy nguyên, không encode lại
> - `/tmp/dji_meta.bin` — file output
>
> **Kết quả:** File binary 111KB chứa metadata bay, bao gồm GPS.

---

### 1.5 Xem nội dung binary (hex dump)
```bash
xxd /tmp/dji_meta.bin | head -30
```
> **Ý nghĩa:**
> - `xxd` — chuyển file binary thành dạng hex để đọc được
> - `| head -30` — chỉ xem 30 dòng đầu
>
> **Kết quả:** Thấy header `dvtm_Mini4_Pro.proto` → xác nhận DJI dùng format Protobuf binary.

---

## Phần 2: Parse GPS từ binary

### 2.1 Cài protobuf
```bash
pip install protobuf
```
> **Ý nghĩa:** Thư viện Google Protocol Buffers — dùng để parse dữ liệu binary có cấu trúc.

---

### 2.2 Thử tìm GPS dạng int32 scaled — KHÔNG CHÍNH XÁC
```bash
python3 -c "
import struct

with open('/tmp/dji_meta.bin', 'rb') as f:
    data = f.read()

print(f'File size: {len(data)} bytes')

# Thử float32 (4 bytes)
print('=== Float32 scan ===')
for i in range(len(data) - 4):
    val = struct.unpack('<f', data[i:i+4])[0]
    if 8.0 < val < 23.0:
        print(f'  lat? offset={i}: {val:.6f}')
    elif 100.0 < val < 115.0:
        print(f'  lon? offset={i}: {val:.6f}')

# Thử int32 scaled (giá trị * 1e7)
print('=== Int32 scaled (x1e-7) scan ===')
for i in range(len(data) - 4):
    raw = struct.unpack('<i', data[i:i+4])[0]
    val = raw / 1e7
    if 8.0 < val < 23.0:
        print(f'  lat? offset={i}: raw={raw} -> {val:.7f}')
    elif 100.0 < val < 115.0:
        print(f'  lon? offset={i}: raw={raw} -> {val:.7f}')
"
```
> **Ý nghĩa:**
> - `struct.unpack('<f', ...)` — đọc 4 bytes thành số thực float32 (little-endian)
> - `struct.unpack('<i', ...)` — đọc 4 bytes thành số nguyên int32 (little-endian)
> - Scan từng byte trong file, thử decode thành số, kiểm tra có nằm trong vùng tọa độ Việt Nam không
> - Latitude Việt Nam: 8°-23°, Longitude: 100°-115°
>
> **Kết quả:** Quá nhiều noise (hàng trăm kết quả rải khắp file) → không phải format này.

---

### 2.3 Thử tìm GPS dạng double radians — THÀNH CÔNG ✅
```bash
python3 -c "
import struct, math

with open('/tmp/dji_meta.bin', 'rb') as f:
    data = f.read()

# DJI lưu GPS dạng radians (double 8 bytes)
# Lat VN: 8-23° → 0.14-0.40 rad
# Lon VN: 100-115° → 1.74-2.01 rad

print('=== Double (radians → degrees) ===')
for i in range(min(len(data) - 8, 5000)):  # Chỉ scan 5KB đầu
    val = struct.unpack('<d', data[i:i+8])[0]
    if 0.13 < val < 0.42:
        deg = math.degrees(val)
        if 8.0 < deg < 23.0:
            print(f'  LAT? offset={i}: {val:.10f} rad = {deg:.7f}°')
    elif 1.74 < val < 2.02:
        deg = math.degrees(val)
        if 100.0 < deg < 115.0:
            print(f'  LON? offset={i}: {val:.10f} rad = {deg:.7f}°')
"
```
> **Ý nghĩa:**
> - `struct.unpack('<d', ...)` — đọc 8 bytes thành số thực double (little-endian)
> - `math.degrees()` — chuyển radians sang degrees
> - Chỉ scan 5KB đầu file để tránh noise ở phần sau
> - Tìm giá trị nằm trong range radians tương ứng tọa độ Việt Nam
>
> **Kết quả:** Tìm thấy pattern rõ ràng:
> - **LAT: 0.189 rad = 10.8335° N**
> - **LON: 1.864 rad = 106.8168° E**
> - Lặp lại đều đặn mỗi ~294 bytes = 1 GPS point mỗi frame

---

### 2.4 Xác nhận tọa độ GPS
```bash
python3 -c "
import math

lat = math.degrees(0.1890806650)
lon = math.degrees(1.8643050940)
print(f'Latitude:  {lat:.7f}° N')
print(f'Longitude: {lon:.7f}° E')
print(f'Google Maps: https://maps.google.com/?q={lat},{lon}')
"
```
> **Ý nghĩa:** Chuyển giá trị radians tìm được sang degrees và tạo link Google Maps để xác nhận.
>
> **Kết quả:** Đúng vị trí bay drone tại Minh Thiện. ✅

---

## Phần 3: Extract GPS toàn bộ video

### 3.1 Extract + đếm GPS points từ video ngắn (8s)
```bash
python3 -c "
import struct, math

with open('/tmp/dji_meta.bin', 'rb') as f:
    data = f.read()

gps_points = []
for i in range(len(data) - 16):
    lat_rad = struct.unpack('<d', data[i:i+8])[0]
    if 0.13 < lat_rad < 0.42:
        lat_deg = math.degrees(lat_rad)
        if 10.0 < lat_deg < 11.0:  # Lọc đúng khu vực bay
            for j in range(1, 20):
                if i+j+8 <= len(data):
                    lon_rad = struct.unpack('<d', data[i+j:i+j+8])[0]
                    lon_deg = math.degrees(lon_rad)
                    if 106.0 < lon_deg < 107.0:
                        gps_points.append((lat_deg, lon_deg, i))
                        break

# Loại duplicate
filtered = [gps_points[0]] if gps_points else []
for p in gps_points[1:]:
    if p[2] - filtered[-1][2] > 100:
        filtered.append(p)

print(f'GPS points: {len(filtered)}')
for i, (lat, lon, offset) in enumerate(filtered[:5]):
    print(f'  [{i}] {lat:.7f}, {lon:.7f}')
"
```
> **Ý nghĩa:**
> - Scan toàn bộ file binary tìm tất cả cặp LAT/LON
> - Lọc theo khu vực bay cụ thể (lat 10-11°, lon 106-107°) để giảm false positive
> - Khi tìm thấy LAT, tiếp tục tìm LON trong 20 bytes tiếp theo (DJI lưu LAT-LON liền nhau)
> - Loại duplicate: 2 points cách nhau < 100 bytes là trùng
>
> **Kết quả:** 384 GPS points = đúng 384 frames (video 8s × 48fps).

---

### 3.2 Test video dài (80s) + tính khoảng cách bay
```bash
# Extract DJI meta từ video dài
ffmpeg -i Video_drone/DJI_20260226142532_0019_D_MINHTHIEN.MP4 -map 0:d:0 -f data -c copy /tmp/dji_meta_019.bin

python3 -c "
import struct, math

with open('/tmp/dji_meta_019.bin', 'rb') as f:
    data = f.read()

# ... (cùng thuật toán scan GPS) ...

print(f'GPS points: {len(filtered)}')
print(f'Đầu:  {filtered[0][0]:.7f}, {filtered[0][1]:.7f}')
print(f'Giữa: {filtered[len(filtered)//2][0]:.7f}, {filtered[len(filtered)//2][1]:.7f}')
print(f'Cuối: {filtered[-1][0]:.7f}, {filtered[-1][1]:.7f}')

# Tính khoảng cách bay (Haversine đơn giản)
lat1, lon1 = filtered[0][0], filtered[0][1]
lat2, lon2 = filtered[-1][0], filtered[-1][1]
dlat = (lat2-lat1) * 111320  # 1° lat ≈ 111.32 km
dlon = (lon2-lon1) * 111320 * math.cos(math.radians(lat1))
dist = math.sqrt(dlat**2 + dlon**2)
print(f'Khoảng cách bay: ~{dist:.0f}m')
"
```
> **Ý nghĩa:**
> - Test với video dài hơn để xác nhận GPS thay đổi theo thời gian bay
> - `111320` = số mét trong 1° latitude (hằng số địa lý)
> - `cos(lat)` = hiệu chỉnh longitude theo vĩ độ (gần xích đạo ≈ 1)
>
> **Kết quả:** 3837 GPS points, khoảng cách bay ~140m. GPS thay đổi rõ ràng từ đầu đến cuối.

---

## Phần 4: Module chính thức

### 4.1 Chạy module frame_extract.py
```bash
# Copy module vào project
cp frame_extract.py ~/Video2Map/src/frame_extract.py

# Chạy test
cd ~/Video2Map
python3 src/frame_extract.py
```
> **Ý nghĩa:** Module hoàn chỉnh kết hợp:
> 1. Extract DJI meta track (ffmpeg)
> 2. Parse GPS từ binary (struct + math)
> 3. Decode video qua NVDEC (GStreamer)
> 4. Trích keyframes theo interval (mỗi 2 giây)
> 5. Ghép GPS tương ứng cho từng frame
> 6. Lưu keyframes (.jpg) + GPS log (.csv)
>
> **Kết quả:**
> ```
> GPS points: 3837
> Keyframes: 10 (mỗi 2 giây, giới hạn 10 frame test)
> GPS start: 10.8318899, 106.8188239
> GPS end:   10.8328262, 106.8196573
> Khoảng cách: 138m
> Output: output/keyframes/ (10 JPG + gps_log.csv)
> ```

---

## Phần 5: Lệnh kiểm tra file video hỏng

### 5.1 Video 0018 (3.5GB) — file hỏng
```bash
ffmpeg -i Video_drone/DJI_20260226141928_0018_D_MINHTHIEN.MP4 -map 0:d:0 -f data -c copy /tmp/dji_meta_018.bin
```
> **Kết quả:** Lỗi `moov atom not found` → file MP4 bị thiếu header (copy dở hoặc quay bị gián đoạn).
> File này cần xử lý riêng (recover moov atom) hoặc bỏ qua.

---

## Tóm tắt phát hiện kỹ thuật

| Phát hiện | Chi tiết |
|-----------|----------|
| **GPS format** | Double (8 bytes), đơn vị **radians** (không phải degrees) |
| **Vị trí lưu** | Track riêng "DJI meta" (codec: djmd), không phải exif |
| **Cấu trúc binary** | Protobuf (`dvtm_Mini4_Pro.proto`) |
| **Pattern** | LAT offset N, LON offset N+9, mỗi sample cách ~294 bytes |
| **Tỷ lệ** | 1 GPS point = 1 video frame (48fps = 48 GPS/giây) |
| **Chuyển đổi** | `degrees = math.degrees(radians_value)` |
| **Công cụ cần** | ffmpeg (extract track), struct (parse binary), math (radians→degrees) |

---

## Cấu trúc file hiện tại

```
~/Video2Map/
├── src/
│   ├── video_input/
│   │   └── video_input.py     ← Module 1: Nhận video + NVDEC decode
│   └── frame_extract.py       ← Module 2: GPS + Frame extraction
├── output/
│   └── keyframes/
│       ├── frame_000000.jpg   ← Keyframe + GPS
│       ├── frame_000095.jpg
│       ├── ...
│       └── gps_log.csv        ← Tọa độ GPS mỗi frame
├── Video_drone/               ← Video DJI gốc
│   ├── DJI_..._0017_D_MINHTHIEN.MP4  (150MB, 8s)  ✅
│   ├── DJI_..._0018_D_MINHTHIEN.MP4  (3.5GB)      ❌ hỏng
│   └── DJI_..._0019_D_MINHTHIEN.MP4  (1.5GB, 80s) ✅
└── venv/
```