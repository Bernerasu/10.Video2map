# JetsonDrone_UDP_OpenCVMapping

Hệ thống điều khiển drone DJI Tello tự động theo dõi đối tượng bằng OpenCV, giao tiếp qua UDP trên nền tảng Windows.

## Tổng quan

Project nhận kết quả phát hiện đối tượng (object detection) từ một server bên ngoài qua UDP, hiển thị bounding box bằng OpenCV, và tự động điều khiển drone Tello di chuyển để bám theo mục tiêu.

```
[Object Detection Server] --UDP:54000--> [App này] --UDP:8889--> [DJI Tello]
```

## Kiến trúc

| Module | File | Chức năng |
|---|---|---|
| **DroneEngine** | `droneEngine.h/cpp` | Giao tiếp UDP với Tello (SDK command mode) |
| **UDPServer** | `udpServer.h/cpp` | Nhận dữ liệu detection từ server ngoài |
| **Main** | `main.cpp` | Xử lý message, vẽ OpenCV, ra lệnh drone |

## Luồng hoạt động

1. Kết nối Tello → chuyển sang SDK mode → bật stream → cất cánh
2. Lắng nghe UDP port `54000` nhận kết quả detection
3. Parse message theo format: `distanceX,distanceY|centerX,centerY|box1X,box1Y,box2X,box2Y|scale|className`
4. Vẽ bounding box + center point lên frame OpenCV
5. Nếu `className == "Ali"` → tự động điều khiển drone bám theo (left/right, up/down, forward/back)
6. Nhấn phím bất kỳ → thoát → drone hạ cánh

## Yêu cầu

- **OS:** Windows 10+
- **Compiler:** MSVC v142 (Visual Studio 2019+)
- **OpenCV:** 4.5.3 (đặt trong `thirdparty/include` và `thirdparty/lib`)
- **Hardware:** DJI Tello (kết nối WiFi trực tiếp, IP `192.168.10.1`)
- **Winsock2** (có sẵn trên Windows)

## Cài đặt & Build

```bash
# Clone repo
git clone https://github.com/<your-username>/JetsonDrone_UDP_OpenCVMapping.git

# Cấu trúc thư mục OpenCV
thirdparty/
├── include/    # OpenCV headers
└── lib/        # OpenCV .lib files (opencv_core453d.lib, ...)
```

1. Mở `OpenCV_Drone.vcxproj` bằng Visual Studio
2. Chọn cấu hình **Debug | x64** hoặc **Release | x64**
3. Build & Run

## Cấu hình

| Tham số | Giá trị mặc định | Vị trí |
|---|---|---|
| Tello IP | `192.168.10.1` | `droneEngine.h` |
| Tello command port | `8889` | `droneEngine.h` |
| UDP listen port (detection) | `54000` | `main.cpp` |
| Target class name | `"Ali"` | `main.cpp` |

## Protocol - Detection Message

Server gửi message dạng text qua UDP, phân tách bằng `|`:

```
distanceX,distanceY | centerX,centerY | box1X,box1Y,box2X,box2Y | scale | className
```

| Field | Mô tả |
|---|---|
| `distanceX,distanceY` | Khoảng cách từ center đến tâm frame |
| `centerX,centerY` | Tọa độ tâm bounding box |
| `box1X,box1Y,box2X,box2Y` | 2 điểm góc bounding box |
| `scale` | Tỷ lệ kích thước (>0: tiến, ≤0: lùi) |
| `className` | Tên class đối tượng |

## Hạn chế hiện tại

- Chỉ hỗ trợ Windows (Winsock2, `Sleep()`, `ZeroMemory`)
- Hardcode target class là `"Ali"`
- Không có timeout cho `recvfrom` → block vô hạn nếu không có data
- `std::async` mỗi frame nhưng gọi `.get()` ngay → thực tế chạy đồng bộ
- Frame OpenCV tạo mới mỗi lần (đen) thay vì dùng video stream thật từ Tello

## License

MIT
