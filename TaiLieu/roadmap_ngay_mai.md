# ROADMAP - Setup DroneMap System
**Thời gian:** Cả ngày (sáng → tối)

---

## BUỔI SÁNG — Nền tảng

### 1. Setup hạ tầng mạng (1h)
- [ ] Kết nối Switch → Jetson → Laptop
- [ ] Đặt IP tĩnh cho tất cả thiết bị (VD: 192.168.1.x)
- [ ] Ping test giữa các thiết bị
- [ ] Xác nhận Laptop truy cập được Jetson qua SSH

### 2. Setup môi trường Jetson (1.5h)
- [ ] Tạo thư mục `~/droneMap` + venv (`--system-site-packages`)
- [ ] Cài dependencies cơ bản (OpenCV, Flask/FastAPI, NumPy)
- [ ] Verify CUDA + NVDEC hoạt động
- [ ] Test YOLOv8 chạy được trên Jetson

### 3. Pipeline nhận video (1.5h)
- [ ] Copy video demo vào Jetson
- [ ] Viết module đọc video (OpenCV VideoCapture)
- [ ] Decode qua NVDEC (GStreamer pipeline)
- [ ] Hiển thị frame test → xác nhận pipeline hoạt động

---

## BUỔI CHIỀU — Xử lý & Stitching

### 4. Module xử lý ảnh (2h)
- [ ] Trích frame từ video theo interval
- [ ] Camera undistortion (nếu có calibration data)
- [ ] Feature detection + matching giữa các frame
- [ ] Stitching cơ bản 2-3 frame → xác nhận thuật toán OK

### 5. Module AI Detection (1h)
- [ ] Load YOLOv8 model (người + phương tiện)
- [ ] Chạy detect trên frame → vẽ bounding box
- [ ] Test tốc độ inference trên Jetson
- [ ] Lưu kết quả detect kèm tọa độ

### 6. Georeferencing cơ bản (1h)
- [ ] Gắn tọa độ GPS giả lập cho frame (demo)
- [ ] Convert ảnh stitched → map tiles
- [ ] Xuất ra folder tiles theo chuẩn z/x/y

---

## BUỔI TỐI — Web & Kết nối

### 7. Web server hiển thị (1.5h)
- [ ] Dựng FastAPI/Flask trên Jetson
- [ ] Serve map tiles qua HTTP
- [ ] Frontend: Leaflet.js load tiles → hiển thị bản đồ
- [ ] Laptop mở browser → xem bản đồ từ Jetson

### 8. Test end-to-end (1h)
- [ ] Chạy full pipeline: Video → Xử lý → Tiles → Web
- [ ] Laptop xem được bản đồ real-time
- [ ] Ghi nhận bottleneck, tốc độ, lỗi
- [ ] Liệt kê TODO cho ngày tiếp theo

---

## Deliverables cuối ngày
| # | Output | Trạng thái |
|---|--------|------------|
| 1 | Jetson nhận + decode video | ⬜ |
| 2 | Stitching cơ bản hoạt động | ⬜ |
| 3 | YOLOv8 detect trên Jetson | ⬜ |
| 4 | Web map hiển thị trên Laptop | ⬜ |
| 5 | Full pipeline chạy end-to-end | ⬜ |

---

## Chuẩn bị tối nay
- [ ] Jetson Orin Nano — sạc đầy, cắm SSD
- [ ] Switch — đủ cáp LAN
- [ ] Laptop — cài SSH client, trình duyệt
- [ ] Video demo — copy sẵn vào USB
- [ ] Đọc sơ tài liệu JetPack/NVDEC nếu cần
