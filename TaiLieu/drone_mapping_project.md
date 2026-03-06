# Dự án: Bản đồ 2D từ Flycam — Edge AI trên Jetson Orin Nano

> **Phiên bản:** v0.1 — Khởi tạo  
> **Ngày:** 23/02/2026  
> **Tác giả:** Nam  
> **Trạng thái:** Đang phát triển

---

## 1. Tổng quan dự án

### 1.1 Mục tiêu
Xây dựng hệ thống xử lý video flycam để tạo bản đồ 2D, chạy hoàn toàn trên thiết bị edge (Jetson Orin Nano Super 8GB), xuất ra bản đồ web với thời gian xử lý mục tiêu ~10 phút.

### 1.2 Pipeline tổng quát

```
[Flycam + Camera HD]
        ↓ video stream
[Bộ thu tín hiệu video]
        ↓ 
[Switch mạng]
        ↓
[Jetson Orin Nano Super 8GB]
    ├─ Decode video (NVDEC)
    ├─ Trích xuất keyframe
    ├─ Detect GCP (4 đèn mốc)
    ├─ Feature matching + Incremental stitching
    ├─ Scale & georeferencing từ GCP
    ├─ AI segmentation (tùy chọn)
    ├─ Tạo map tiles
    └─ Web tile server
        ↓
[Trình duyệt web — Leaflet/MapLibre]
```

---

## 2. Phần cứng

### 2.1 Thiết bị xử lý

| Thông số | Giá trị |
|---|---|
| **Thiết bị** | NVIDIA Jetson Orin Nano Super |
| **RAM** | 8GB LPDDR5 |
| **GPU** | 1024 CUDA cores |
| **AI Performance** | 67 TOPS (INT8) |
| **Video Decoder** | NVDEC: 1x 4K60 hoặc 2x 4K30 |
| **Storage** | NVMe SSD (khuyến nghị ≥256GB) |
| **Kết nối** | GbE LAN |

### 2.2 Tối ưu phần cứng (TODO)

- [ ] Benchmark RAM usage khi stitching N frames HD
- [ ] Xác định giới hạn số frame tối đa xử lý đồng thời
- [ ] Tối ưu swap/zram cho trường hợp vượt 8GB
- [ ] Test pipeline decode → stitch end-to-end trên Jetson
- [ ] Benchmark NVDEC decode throughput với video HD thực tế
- [ ] Đánh giá dùng GPU memory cho OpenCV CUDA vs TensorRT inference
- [ ] Tối ưu power mode: MaxN vs 15W vs custom

### 2.3 Flycam & Truyền hình

| Thành phần | Yêu cầu | Ghi chú |
|---|---|---|
| **Flycam** | Có khả năng truyền video realtime | Cần xác định model cụ thể |
| **Camera** | HD (1920x1080) trở lên | GoPro-style (GX010063.MP4 — barrel distortion cần calibrate) |
| **Bộ thu video** | Nhận video stream từ flycam | Analog/Digital receiver |
| **Switch** | Chuyển tiếp video stream đến Jetson | GbE switch |
| **Giao thức truyền** | UDP / RTSP | Cần xác định |

### 2.4 Tối ưu truyền hình (TODO)

- [ ] Xác định protocol truyền video: UDP raw, RTSP, hay RTP
- [ ] Đo latency truyền hình từ flycam → Jetson
- [ ] Test packet loss và cách xử lý frame bị mất
- [ ] Xác định bitrate tối ưu: chất lượng vs bandwidth

---

## 3. Hệ thống GCP (Ground Control Points) — Thay thế GPS

### 3.1 Thiết kế

Sử dụng **4 đèn đặt trên mặt đất** làm điểm mốc tham chiếu, thay thế GPS.

```
  Đèn A ──── d_AB ──── Đèn B
    │                     │
  d_AC                  d_BD
    │                     │
  Đèn C ──── d_CD ──── Đèn D
```

**Yêu cầu:**
- 4 đèn không thẳng hàng
- Đo chính xác tất cả **6 khoảng cách**: AB, AC, AD, BC, BD, CD
- Không yêu cầu hình vuông/chữ nhật — chỉ cần đo chính xác

### 3.2 Thông số kỹ thuật

| Thông số | Giá trị | Ghi chú |
|---|---|---|
| **Số điểm** | 4 | Tối thiểu để xác định đầy đủ scale + rotation + perspective |
| **Khoảng cách tối thiểu** | 10-15m (bay ở 30-50m) | ≥ 1/10 chiều rộng vùng nhìn thấy |
| **Loại đèn** | LED sáng, nhìn rõ từ trên cao | Khuyến nghị: đèn LED trắng hoặc đỏ, công suất cao |
| **Hình dạng** | Tứ giác bất kỳ | Vuông/chữ nhật dễ đo hơn nhưng không bắt buộc |

### 3.3 Pipeline xử lý GCP

```
1. Frame đầu video → Detect 4 đèn (blob detection / threshold)
2. Xác định tọa độ pixel của 4 đèn
3. Tính homography: pixel ↔ tọa độ thực (mét)
4. Đặt hệ tọa độ: Đèn A = gốc (0,0)
5. Các frame tiếp theo:
   a. Nếu thấy ≥2 đèn → recalibrate (giảm drift)
   b. Nếu không thấy đèn → feature matching thuần (sẽ drift dần)
```

### 3.4 Tối ưu GCP (TODO)

- [ ] Chọn loại đèn cụ thể (LED, kích thước, công suất)
- [ ] Test nhận dạng đèn ở các độ cao khác nhau
- [ ] Xác định thuật toán detect tối ưu: threshold vs blob vs AI
- [ ] Xử lý trường hợp đèn bị che khuất
- [ ] Thêm đặc điểm phân biệt mỗi đèn (màu khác nhau? nhấp nháy?)

---

## 4. Phương pháp bay

### 4.1 Pattern bay cơ bản

**Mục tiêu:** Bao phủ khu vực cần vẽ bản đồ với overlap đủ cho stitching.

```
Pattern "Cắt cỏ" (Lawnmower):

    ──────────────→
    ←──────────────
    ──────────────→
    ←──────────────

Overlap ngang: ≥60%
Overlap dọc: ≥30%
```

### 4.2 Yêu cầu kỹ thuật khi bay

| Thông số | Giá trị khuyến nghị | Lý do |
|---|---|---|
| **Độ cao** | 30-50m | Cân bằng giữa coverage và chi tiết |
| **Tốc độ** | 3-5 m/s | Tránh motion blur ở HD |
| **Góc camera** | 90° (nhìn thẳng xuống) | Tối ưu cho orthomosaic |
| **Overlap ngang** | ≥60% | Đủ feature matching |
| **Overlap dọc** | ≥30% | Đủ cho liên kết giữa các pass |
| **Bay qua GCP** | Bắt buộc ở đầu và cuối chuyến bay | Để calibrate + close loop |

### 4.3 Quy trình bay

```
1. Đặt 4 đèn GCP, đo 6 khoảng cách, ghi lại
2. Cất cánh, bay lên độ cao target
3. Bay qua vùng GCP (bắt buộc — frame tham chiếu)
4. Bắt đầu pattern cắt cỏ
5. Kết thúc: bay lại qua vùng GCP (close loop)
6. Hạ cánh
```

### 4.4 Tối ưu phương pháp bay (TODO)

- [ ] Tính toán diện tích bao phủ tối đa với 1 lần bay (phụ thuộc pin)
- [ ] Xác định overlap tối ưu: đủ cho stitch nhưng giảm số frame
- [ ] Test ảnh hưởng của gió đến chất lượng stitch
- [ ] Đánh giá bay nghiêng camera (oblique) để tăng coverage
- [ ] Hướng dẫn xử lý khi mất tín hiệu video giữa chừng
- [ ] Thử nghiệm bay tự động (waypoint) vs bay tay

---

## 5. Xử lý ảnh & Stitching

### 5.1 Pipeline xử lý trên Jetson

```
Video HD (input)
    ↓
[Stage 1] Decode — GStreamer + NVDEC
    ↓ raw frames
[Stage 2] Keyframe extraction — mỗi 1-2 giây
    ↓ keyframes
[Stage 3] Lens undistortion — OpenCV (calibration matrix)
    ↓ corrected frames
[Stage 4] GCP detection — blob detection / threshold
    ↓ GCP pixel coordinates
[Stage 5] Feature matching — ORB + CUDA (hoặc SIFT)
    ↓ matched features
[Stage 6] Incremental stitching — homography chain
    ↓ mosaic image
[Stage 7] Scale + referencing — từ GCP data
    ↓ georeferenced mosaic
[Stage 8] Tile generation — cắt thành 256x256 tiles
    ↓ tile pyramid
[Stage 9] Serve — HTTP tile server
    ↓
Web viewer (output)
```

### 5.2 Giới hạn bộ nhớ

| Thao tác | RAM ước tính | Ghi chú |
|---|---|---|
| 1 frame HD (1920x1080 RGB) | ~6MB | |
| 50 keyframes trong RAM | ~300MB | |
| Feature matching buffer | ~200-500MB | Tùy thuật toán |
| Mosaic đang ghép | ~500MB-2GB | Phụ thuộc kích thước khu vực |
| TensorRT model (nếu dùng AI) | ~200-500MB | |
| **Tổng ước tính** | **1.5-3.5GB** | Còn headroom cho OS + tile server |

### 5.3 Tối ưu xử lý ảnh (TODO)

- [ ] Benchmark ORB vs SIFT vs SuperPoint trên Jetson
- [ ] Test incremental stitch: max bao nhiêu frame trước khi drift
- [ ] Camera calibration cho GoPro (barrel distortion correction)
- [ ] Xử lý exposure khác nhau giữa các frame (exposure compensation)
- [ ] Blending strategy: simple overlap vs multiband blending
- [ ] Đánh giá NVIDIA VPI (Vision Programming Interface) thay OpenCV

---

## 6. Hiển thị Web

### 6.1 Kiến trúc

```
[Jetson Orin Nano]
    ├─ Tile Server (nginx hoặc custom Python)
    │   └─ Serve tiles: /tiles/{z}/{x}/{y}.png
    └─ WebSocket server (cập nhật realtime)
        └─ Thông báo client khi có tile mới

[Trình duyệt — Client]
    ├─ MapLibre GL JS / Leaflet
    ├─ Custom tile layer → load từ Jetson
    ├─ WebSocket listener → auto-refresh tiles
    └─ UI: zoom, pan, measure
```

### 6.2 Tính năng hiển thị

| Tính năng | Mức độ | Ghi chú |
|---|---|---|
| Xem bản đồ 2D | **Bắt buộc** | Zoom, pan |
| Auto-refresh khi có dữ liệu mới | **Bắt buộc** | WebSocket hoặc polling |
| Đo khoảng cách | Nên có | Dựa trên scale từ GCP |
| So sánh trước/sau | Tùy chọn | Side-by-side hoặc slider |
| Overlay AI segmentation | Tùy chọn | Hiển thị đường, nhà, cây... |
| Xuất ảnh bản đồ | Nên có | Download full mosaic |
| Hiển thị vị trí GCP | Nên có | Marker trên bản đồ |
| Progress indicator | Nên có | Hiện tiến trình xử lý |

### 6.3 Tối ưu hiển thị (TODO)

- [ ] Chọn thư viện map: Leaflet vs MapLibre GL JS
- [ ] Tile format: PNG vs JPEG vs WebP (trade-off chất lượng/kích thước)
- [ ] Tile size: 256x256 vs 512x512
- [ ] Caching strategy trên client
- [ ] Progressive loading: hiện low-res trước, high-res sau
- [ ] Mobile responsive
- [ ] Benchmark tile server trên Jetson (concurrent requests)

---

## 7. AI / Deep Learning (Tùy chọn — Phase sau)

### 7.1 Ứng dụng tiềm năng

| Ứng dụng | Model gợi ý | Chạy trên Jetson? |
|---|---|---|
| Phát hiện đường | DeepLabV3 / SegFormer | ✅ TensorRT |
| Phát hiện nhà/công trình | YOLOv8-seg | ✅ TensorRT |
| Phân loại đất/cây/nước | SegFormer | ✅ TensorRT |
| Super-resolution | Real-ESRGAN | ⚠️ Nặng, cần test |
| Detect GCP (đèn) | YOLOv8-detect (custom train) | ✅ TensorRT |

### 7.2 Tối ưu AI (TODO)

- [ ] Đánh giá có cần AI hay chỉ cần CV truyền thống
- [ ] Benchmark inference time các model trên Jetson
- [ ] Đánh giá TensorRT quantization (FP16 vs INT8)
- [ ] Custom dataset cho GCP detection (nếu cần)

---

## 8. Roadmap phát triển

### Phase 1 — Proof of Concept (Tuần 1-4)
**Mục tiêu:** Chứng minh pipeline chạy được end-to-end

- [ ] Setup Jetson Orin Nano (OS, CUDA, OpenCV, GStreamer)
- [ ] Nhận video stream từ flycam qua switch
- [ ] Decode + extract keyframes
- [ ] Stitching cơ bản (OpenCV, không GCP)
- [ ] Hiển thị panorama đơn giản trên web
- [ ] Đo thời gian xử lý end-to-end

### Phase 2 — GCP Integration (Tuần 5-8)
**Mục tiêu:** Thêm hệ thống đèn mốc, có scale chính xác

- [ ] Thiết kế và chế tạo hệ thống 4 đèn GCP
- [ ] Thuật toán detect đèn trên frame
- [ ] Tính toán homography từ GCP
- [ ] Scale/reference bản đồ theo mét thực tế
- [ ] Đo khoảng cách trên web viewer

### Phase 3 — Tối ưu & Nâng cao (Tuần 9-12)
**Mục tiêu:** Cải thiện chất lượng và hiệu năng

- [ ] Tối ưu RAM usage
- [ ] Camera calibration (lens correction)
- [ ] Exposure compensation
- [ ] Progressive web tile loading
- [ ] AI segmentation (tùy chọn)
- [ ] Hướng dẫn quy trình bay chuẩn

### Phase 4 — Production Ready (Tuần 13+)
**Mục tiêu:** Ổn định, tài liệu hóa, có thể demo

- [ ] Stress test toàn pipeline
- [ ] Error handling & recovery
- [ ] Tài liệu hướng dẫn sử dụng
- [ ] Package deploy (Docker / script)
- [ ] Demo với khu vực thực tế

---

## 9. Rủi ro & Giải pháp

| Rủi ro | Mức độ | Giải pháp |
|---|---|---|
| RAM không đủ khi stitch nhiều frame | Cao | Incremental stitch, giải phóng frame cũ, giảm resolution |
| Drift tích lũy khi bay xa GCP | Trung bình | Bay lại qua GCP định kỳ (close loop) |
| Barrel distortion từ GoPro | Trung bình | Camera calibration trước khi stitch |
| Mất tín hiệu video giữa chừng | Thấp | Buffer + retry, xử lý frame gap |
| Gió mạnh → blur, overlap không đều | Trung bình | Bay chậm hơn, tăng overlap margin |
| Đèn GCP bị che khuất | Thấp | Đặt đèn ở vị trí trống, dùng đèn đủ sáng |

---

## 10. Công nghệ & Thư viện

| Thành phần | Công nghệ | Ghi chú |
|---|---|---|
| OS | JetPack 6.x (Ubuntu 22.04) | NVIDIA official |
| Video decode | GStreamer + NVDEC | Hardware accelerated |
| Computer Vision | OpenCV 4.x + CUDA | Feature matching, stitching |
| AI Inference | TensorRT | Tối ưu cho Jetson |
| Tile Server | Nginx / Python (Flask/FastAPI) | Lightweight |
| Web Frontend | MapLibre GL JS hoặc Leaflet | Mã nguồn mở |
| Realtime Update | WebSocket | Thông báo tile mới |
| Ngôn ngữ chính | C++ (processing) + Python (tile server) + JS (web) | |

---

## Phụ lục A: Repo hiện tại

**GitHub:** `alir14/JetsonDrone_UDP_OpenCVMapping`

Codebase hiện tại: UDP communication + OpenCV + Tello drone control (Windows/C++).  
Cần refactor cho Linux (Jetson) và thêm pipeline stitching + web output.

## Phụ lục B: Bảng đo GCP

Sử dụng bảng sau khi đặt đèn:

| Cặp đèn | Khoảng cách (m) |
|---|---|
| A → B | ___ |
| A → C | ___ |
| A → D | ___ |
| B → C | ___ |
| B → D | ___ |
| C → D | ___ |

**Ghi chú:** Đo bằng thước dây, ghi chính xác đến 0.1m.
