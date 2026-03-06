# VIDEO2MAP — ROADMAP TỔNG THỂ
**Cập nhật:** 01/03/2026  
**Tầm nhìn:** Hệ thống bản đồ cộng đồng từ drone video — phục vụ cứu trợ thiên tai & crowdsource bản đồ Việt Nam

---

## PHASE 1: Core Engine trên Jetson ← ĐANG LÀM
**Mục tiêu:** Video đầu vào → Map tiles đầu ra, chạy trên Jetson Orin Nano  
**Thời gian:** Tuần 1-2

### Đã hoàn thành ✅
- [x] Setup Jetson: dọn dẹp, tắt service cũ
- [x] Tạo môi trường Video2Map (venv + system-site-packages)
- [x] Cài PyTorch NVIDIA (CUDA: True)
- [x] Build torchvision từ source
- [x] Ultralytics/YOLOv8 sẵn sàng
- [x] Kết nối LAN: Jetson ↔ Switch ↔ Laptop
- [x] SSH + VS Code Remote hoạt động

### Đang làm 🔄
- [ ] Build OpenCV + GStreamer + CUDA (đang compile)
- [ ] Test NVDEC decode video DJI (4K H.264)

### Cần làm tiếp
- [ ] Module 1: Video decoder (GStreamer → NVDEC)
- [ ] Module 2: Frame extraction (trích frame theo interval/GPS)
- [ ] Module 3: Image stitching (ghép ảnh panorama)
- [ ] Module 4: AI detection (YOLOv8 — người, xe, công trình)
- [ ] Module 5: Georeferencing (gắn tọa độ GPS từ metadata DJI)
- [ ] Module 6: Tile generator (xuất map tiles z/x/y)
- [ ] Module 7: Web server (FastAPI + Leaflet.js hiển thị)
- [ ] Test end-to-end: Video DJI → Map trên browser

### Deliverables Phase 1
| Output | Tiêu chí hoàn thành |
|--------|---------------------|
| Core engine | 1 video DJI 4K → map tiles trong ≤ 10 phút |
| Web viewer | Laptop mở browser → xem bản đồ từ Jetson |
| AI overlay | Hiển thị vị trí người/xe trên bản đồ |

---

## PHASE 2: Web Portal + Queue System
**Mục tiêu:** Giao diện web điều khiển Jetson, nhận video từ cloud  
**Thời gian:** Tuần 3-4

- [ ] Web portal: giao diện upload / paste link video
- [ ] Hỗ trợ nguồn video: Google Drive, DJI Cloud, upload trực tiếp
- [ ] Queue manager: hàng đợi xử lý video
- [ ] Jetson worker: tự động lấy video từ queue → xử lý → trả tiles
- [ ] Validate đầu vào: check GPS metadata, độ cao, chất lượng
- [ ] Dashboard: số video pending, đang xử lý, hoàn thành
- [ ] Xóa video gốc tự động sau khi xử lý xong

---

## PHASE 3: Hệ thống thành viên + Quản lý dữ liệu
**Mục tiêu:** Cộng tác viên đăng ký, upload, xem kết quả  
**Thời gian:** Tuần 5-6

- [ ] Đăng ký / đăng nhập thành viên
- [ ] Profile cộng tác viên: lịch sử upload, vùng đã bay
- [ ] Quản lý dữ liệu: thành viên xem được những gì họ đưa vào
- [ ] Chứng nhận cộng tác viên (contribution badge)
- [ ] Thông báo khi video đã xử lý xong

---

## PHASE 4: Bản đồ theo tỉnh thành + Timeline
**Mục tiêu:** Bản đồ Việt Nam được phủ dần — vết dầu loang  
**Thời gian:** Tuần 7-10

- [ ] Merge map tiles theo tỉnh thành
- [ ] Map viewer công khai: ai cũng xem được
- [ ] Hiển thị vùng trống → tạo động lực cộng đồng lấp đầy
- [ ] Lưu dữ liệu theo timeline (version: 2026-Q1, Q2...)
- [ ] So sánh bản đồ theo thời gian (trước/sau)
- [ ] Thống kê: % phủ sóng theo tỉnh, top cộng tác viên

---

## PHASE 5: Emergency Mode + Đội ứng cứu
**Mục tiêu:** Khi thiên tai xảy ra — triển khai ngay  
**Thời gian:** Liên tục sau Phase 4

- [ ] Chế độ Emergency: Jetson tại hiện trường, offline, real-time
- [ ] So sánh bản đồ peacetime vs emergency → đánh giá thiệt hại
- [ ] Offline map export cho các tổ công tác (điện thoại/tablet)
- [ ] Mạng Wi-Fi nội bộ tại trung tâm chỉ huy
- [ ] Multi-Jetson cluster (7 Jetson + 1 Server)
- [ ] Đội ứng cứu: kỹ thuật viên + Jetson + drone sẵn sàng

---

## HAI CHẾ ĐỘ HOẠT ĐỘNG

```
┌─────────────────────────────────────────────────┐
│  PEACETIME (ngày thường)                        │
│                                                 │
│  Cộng đồng bay → Upload cloud → Jetson xử lý   │
│  → Map tiles → Phủ dần bản đồ Việt Nam          │
│  → Dữ liệu tích lũy theo thời gian             │
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│  EMERGENCY (thiên tai)                          │
│                                                 │
│  Drone tại chỗ → Jetson hiện trường → Real-time │
│  → So sánh với bản đồ nền (peacetime)           │
│  → Đánh giá thiệt hại → Cứu trợ chính xác     │
└─────────────────────────────────────────────────┘
```

---

## MÔ HÌNH KINH DOANH

```
Community (AGPLv3 - miễn phí)     Enterprise (Commercial License)
├── Core engine                    ├── Multi-Jetson cluster
├── Web viewer                     ├── API cho bên thứ 3
├── Upload + xử lý                ├── Analytics, báo cáo
├── AI detection                   ├── Cloud sync
└── Offline export                 └── Hỗ trợ kỹ thuật dedicated
```

**Doanh thu enterprise → Nuôi hạ tầng + nhân lực → Phục vụ cộng đồng**

---

## PHÂN BỔ DOANH THU (khi có)
| Hạng mục | Tỷ lệ | Mục đích |
|----------|--------|----------|
| Hạ tầng | 40% | VPS, server, domain, CDN |
| Nhân lực core | 30% | Dev, QA, documentation |
| Cộng đồng | 20% | Phí triển khai cứu trợ, phần cứng cho đội địa phương |
| Dự phòng | 10% | Emergency fund |

---

## NGUYÊN TẮC THIẾT KẾ
1. **Mọi video → NVDEC**: Tận dụng tối đa phần cứng Jetson
2. **Xử lý tại edge**: Không phụ thuộc internet khi emergency
3. **Chỉ lưu tiles, không lưu video**: Giảm 98% dung lượng
4. **Dual mode**: Cùng core engine, peacetime + emergency
5. **Cộng đồng trước, kinh doanh sau**: Core tốt → người dùng tự đến
6. **Dữ liệu theo thời gian**: Tài sản quý nhất của hệ thống