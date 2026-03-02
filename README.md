# Video2Map

**Hệ thống tạo bản đồ 2D real-time từ video drone — phục vụ cứu trợ thiên tai & crowdsource bản đồ Việt Nam.**

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Platform](https://img.shields.io/badge/Platform-Jetson_Orin_Nano-green.svg)](https://developer.nvidia.com/embedded/jetson-orin-nano)

---

## Vấn đề

Khi thiên tai xảy ra (lũ lụt, sạt lở), đội cứu hộ cần bản đồ **hiện tại** của địa hình — không phải ảnh vệ tinh cũ từ vài tháng trước. Nhưng:

- Không có internet tại hiện trường để tải bản đồ
- Ảnh vệ tinh không phản ánh thay đổi sau thiên tai
- Các giải pháp mapping chuyên nghiệp quá đắt và phức tạp

**Video2Map** giải quyết bằng cách biến video drone thành bản đồ 2D trong **≤ 10 phút**, chạy **offline** trên phần cứng edge AI giá rẻ.

## Cách hoạt động

```
Video DJI 4K ──→ NVDEC decode ──→ Trích frame ──→ Ghép ảnh panorama
                                                        │
Bản đồ trên browser ←── Tile server ←── Georeferencing ←┘
                                                │
                                        AI detection (YOLOv8)
                                        → người, xe, công trình
```

## Hai chế độ vận hành

| | Peacetime (ngày thường) | Emergency (thiên tai) |
|---|---|---|
| **Nguồn video** | Cộng đồng upload qua cloud | Drone bay tại hiện trường |
| **Xử lý** | Jetson nhận từ queue | Jetson tại chỗ, offline |
| **Kết quả** | Phủ dần bản đồ Việt Nam | Real-time map cho đội cứu hộ |
| **So sánh** | Tích lũy dữ liệu timeline | So sánh trước/sau → đánh giá thiệt hại |

## Kiến trúc Module

| # | Module | Mô tả |
|---|--------|-------|
| 1 | Video Decoder | GStreamer → NVDEC (hardware decode 4K H.264) |
| 2 | Frame Extraction | Trích frame theo interval hoặc GPS metadata |
| 3 | Image Stitching | Ghép ảnh panorama từ các frame liên tiếp |
| 4 | AI Detection | YOLOv8 — phát hiện người, xe, công trình |
| 5 | Georeferencing | Gắn tọa độ GPS từ metadata video DJI |
| 6 | Tile Generator | Xuất map tiles theo chuẩn z/x/y |
| 7 | Web Server | FastAPI + Leaflet.js hiển thị bản đồ |

## Yêu cầu phần cứng

| Thành phần | Thông số |
|---|---|
| **Edge AI** | NVIDIA Jetson Orin Nano Super 8GB |
| **OS** | JetPack 6 (L4T R36.4.7) |
| **Drone** | DJI (video 4K H.264 có GPS metadata) |
| **Kết nối** | LAN: Drone RC → Switch → Jetson |

## Cài đặt

```bash
# Clone
git clone https://github.com/<your-username>/Video2Map.git
cd Video2Map

# Tạo môi trường (trên Jetson)
python3 -m venv venv --system-site-packages
source venv/bin/activate

# Kiểm tra CUDA
python3 -c "import torch; print(torch.cuda.is_available())"  # True
```

> **Lưu ý:** PyTorch phải dùng bản build của NVIDIA cho Jetson, **không** cài từ PyPI. torchvision cần build từ source để tương thích.

## Sử dụng

```bash
# (Coming soon) Xử lý video → map tiles
python3 video2map.py --input video_dji.mp4 --output ./tiles

# (Coming soon) Khởi động web viewer
python3 server.py  # → http://<jetson-ip>:8000
```

## Roadmap

- **Phase 1** — Core Engine trên Jetson *(đang làm)*
- **Phase 2** — Web Portal + Queue System
- **Phase 3** — Hệ thống thành viên + Quản lý dữ liệu
- **Phase 4** — Bản đồ theo tỉnh thành + Timeline
- **Phase 5** — Emergency Mode + Đội ứng cứu

Chi tiết: xem [`roadmap.md`](./roadmap.md)

## Nguyên tắc thiết kế

1. **Mọi video → NVDEC** — tận dụng tối đa hardware Jetson
2. **Xử lý tại edge** — không phụ thuộc internet khi emergency
3. **Chỉ lưu tiles, không lưu video** — giảm ~98% dung lượng
4. **Dual mode** — cùng core engine cho cả peacetime và emergency
5. **Cộng đồng trước, kinh doanh sau** — core tốt → người dùng tự đến

## License

- **Community:** [GNU AGPLv3](LICENSE) — miễn phí cho mọi người
- **Enterprise:** Commercial license cho multi-Jetson cluster, API, cloud sync — liên hệ để biết thêm

---

*Video2Map — Bản đồ từ cộng đồng, cho cộng đồng.*