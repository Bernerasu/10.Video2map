# 🗺️ DroneMap — Bản đồ Cứu hộ Thời gian Thực từ Flycam

> **Mã nguồn mở — Miễn phí — Dành cho cứu hộ cứu nạn**
>
> Biến video flycam thành bản đồ 2D có thể xem trên web trong ~10 phút.
> Hoạt động KHÔNG CẦN INTERNET. Chạy trên Jetson Orin Nano Super 8GB.

---

## 🎯 Mục tiêu

Khi thiên tai xảy ra (lũ lụt, sạt lở, bão), đội cứu hộ cần:
- Bản đồ **hiện trạng** khu vực — không phải ảnh vệ tinh cũ vài tháng
- Biết **người ở đâu**, đường nào còn đi được, cầu nào sập
- **Không phụ thuộc internet** — vì mạng thường mất khi thiên tai

**DroneMap** giải quyết tất cả bằng: flycam + Jetson + AI.

---

## 🔧 Yêu cầu phần cứng

| Thiết bị | Yêu cầu tối thiểu | Ghi chú |
|---|---|---|
| **Jetson Orin Nano Super** | 8GB RAM | Thiết bị xử lý chính |
| **SSD NVMe** | ≥256GB | Cài OS + lưu dữ liệu bản đồ |
| **Flycam / Drone** | Có GPS, quay video HD | DJI Mini/Air/Mavic hoặc tương đương |
| **Bộ thu video** (tùy chọn) | Nhận video stream realtime | Cho chế độ realtime |
| **Router WiFi portable** | Bất kỳ | Phát WiFi cho thiết bị xem bản đồ |
| **Pin/Generator** | Cấp nguồn Jetson + router | Cho khu vực mất điện |
| **4 đèn LED GCP** (tùy chọn) | Arduino + LED | Tăng độ chính xác nếu không có GPS |

**Tổng chi phí ước tính: 15-25 triệu VND**

---

## ⚡ Cài đặt nhanh (5 phút)

### Bước 1: Cài JetPack OS

Jetson Orin Nano Super cần JetPack 6.x. Xem hướng dẫn:
https://developer.nvidia.com/embedded/jetpack

### Bước 2: Clone repo và cài đặt

```bash
git clone https://github.com/YOUR_USERNAME/drone-map.git
cd drone-map
chmod +x scripts/setup_jetson.sh
sudo ./scripts/setup_jetson.sh
```

Script sẽ tự động cài đặt tất cả dependencies (~10-15 phút).

### Bước 3: Chạy

```bash
# Xử lý video offline (cách đơn giản nhất)
python3 src/pipeline.py --video /path/to/video.mp4

# Sau đó mở trình duyệt:
# http://jetson-ip:8080
```

---

## 📖 Hướng dẫn sử dụng

### Chế độ 1: Offline (đơn giản nhất — bắt đầu từ đây)

```
1. Bay drone, quay video, lưu file MP4
2. Copy file MP4 vào Jetson (USB hoặc thẻ nhớ)
3. Chạy: python3 src/pipeline.py --video video.mp4
4. Đợi ~10 phút
5. Mở trình duyệt → http://jetson-ip:8080
6. Xem bản đồ
```

### Chế độ 2: Near-realtime (drone có GPS)

```
1. Đặt 4 đèn GCP (tùy chọn, tăng độ chính xác)
2. Bay drone theo pattern cắt cỏ
3. Copy video + GPS log vào Jetson
4. Chạy: python3 src/pipeline.py --video video.mp4 --gps gps_log.srt
5. Bản đồ tự động cập nhật trên web
```

### Chế độ 3: Cứu hộ (AI detection)

```
1. Bay drone trên khu vực thiên tai (50-80m cho detect người)
2. Copy video vào Jetson
3. Chạy: python3 src/pipeline.py --video video.mp4 --detect person,vehicle,flood
4. Mở web → thấy bản đồ + đánh dấu người, xe, vùng ngập
5. Chia sẻ qua WiFi portable cho đội cứu hộ
```

---

## 🏗️ Kiến trúc hệ thống

```
[Drone + Camera + GPS]
        ↓ video MP4 + GPS log
[Jetson Orin Nano Super 8GB]
    ├─ Module 1: Video Parser     — decode + extract keyframes
    ├─ Module 2: GPS Sync         — gán tọa độ cho mỗi frame
    ├─ Module 3: Undistort        — sửa méo lens
    ├─ Module 4: GCP Detector     — detect đèn mốc (tùy chọn)
    ├─ Module 5: Stitcher         — ghép ảnh panorama
    ├─ Module 6: AI Detector      — người, xe, ngập, hư hỏng
    ├─ Module 7: Georeferencer    — gán tọa độ thực cho bản đồ
    ├─ Module 8: Tile Generator   — cắt tile cho web
    └─ Module 9: Web Server       — serve bản đồ + detections
        ↓
[Trình duyệt web — laptop/tablet/điện thoại]
    ├─ Bản đồ 2D (zoom, pan)
    ├─ Layer: ảnh drone
    ├─ Layer: đánh dấu người 🔴
    ├─ Layer: phương tiện 🔵
    ├─ Layer: vùng ngập 🟣
    └─ Layer: hạ tầng hư hỏng 🟡
```

---

## 📁 Cấu trúc thư mục

```
drone-map/
├── README.md                    # File này
├── LICENSE                      # MIT License
├── requirements.txt             # Python dependencies
├── config/
│   ├── pipeline.yaml            # Cấu hình pipeline
│   └── camera_profiles/         # Calibration data cho từng loại camera
│       ├── dji_mini4.yaml
│       └── gopro_hero12.yaml
├── src/
│   ├── pipeline.py              # Entry point — chạy toàn bộ pipeline
│   ├── video_parser.py          # Module 1: Parse video + extract frames
│   ├── gps_sync.py              # Module 2: Parse GPS/SRT + sync
│   ├── undistort.py             # Module 3: Camera calibration
│   ├── gcp_detector.py          # Module 4: Detect đèn LED GCP
│   ├── stitcher.py              # Module 5: Incremental stitching
│   ├── ai_detector.py           # Module 6: Person/vehicle/flood detect
│   ├── georeferencer.py         # Module 7: Pixel → GPS coordinates
│   ├── tile_generator.py        # Module 8: Mosaic → web tiles
│   └── web_server.py            # Module 9: Flask web server
├── web/
│   ├── templates/
│   │   └── index.html           # Giao diện bản đồ
│   └── static/
│       ├── css/style.css
│       └── js/map.js            # MapLibre GL JS logic
├── scripts/
│   ├── setup_jetson.sh          # Cài đặt tự động trên Jetson
│   └── download_models.sh       # Tải model AI
├── firmware/
│   └── gcp_led/
│       └── gcp_led.ino          # Arduino code cho 4 đèn GCP
├── models/                      # AI models (tải bằng script)
├── docs/
│   ├── HUONG_DAN_BAY.md         # Hướng dẫn bay drone
│   ├── HUONG_DAN_GCP.md         # Hướng dẫn đặt đèn GCP
│   └── TROUBLESHOOTING.md       # Xử lý sự cố
└── tests/
    └── test_pipeline.py         # Unit tests
```

---

## 🛡️ License

MIT License — Tự do sử dụng, sửa đổi, phân phối.

## 🤝 Đóng góp

Mọi đóng góp đều được chào đón. Xem [CONTRIBUTING.md](docs/CONTRIBUTING.md).

## 📞 Liên hệ

Dự án mã nguồn mở phục vụ cộng đồng.
Nếu bạn đang trong vùng thiên tai và cần hỗ trợ triển khai: [tạo issue trên GitHub].
