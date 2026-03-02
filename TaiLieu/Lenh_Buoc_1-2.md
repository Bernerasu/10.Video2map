# Video2Map - Setup Log (Bước 1 & 2)
**Ngày:** 01/03/2026  
**Thiết bị:** Jetson Orin Nano Super 8GB | JetPack 6 (L4T R36.4.7)  
**IP Jetson:** 192.168.88.123

---

## Bước 1: Dọn dẹp Jetson

### 1.1 Tắt service dự án cũ
```bash
sudo systemctl stop tkbt-nhinquanh.service
sudo systemctl disable tkbt-nhinquanh.service
```

### 1.2 Tắt service không cần (máy in)
```bash
sudo systemctl stop cups.service cups-browsed.service snap.cups.cups-browsed.service snap.cups.cupsd.service
sudo systemctl disable cups.service cups-browsed.service snap.cups.cups-browsed.service snap.cups.cupsd.service
```

### 1.3 Xóa thư mục build OpenCV (tiết kiệm ~2GB)
```bash
rm -rf ~/opencv ~/opencv_contrib
```

### Kết quả sau dọn dẹp
- Ổ đĩa: 227GB tổng | ~155GB trống
- RAM: 7.4GB tổng | ~5.7GB available
- Giữ nguyên: TeamViewer, ttyd, dự án cũ (TimKiemBauTroi, ThuVienTimkiem)

---

## Bước 2: Setup môi trường Video2Map

### 2.1 Tạo thư mục + venv
```bash
mkdir ~/Video2Map && cd ~/Video2Map
python3 -m venv venv --system-site-packages
source venv/bin/activate
```

### 2.2 Cài PyTorch (bản NVIDIA cho Jetson)
```bash
pip install --no-cache https://developer.download.nvidia.com/compute/redist/jp/v61/pytorch/torch-2.5.0a0+872d972e41.nv24.08.17622132-cp310-cp310-linux_aarch64.whl
```

### 2.3 Build torchvision từ source (QUAN TRỌNG: không dùng pip install torchvision)
```bash
sudo apt install -y libjpeg-dev libpng-dev
cd /tmp
git clone --branch v0.20.0 --depth 1 https://github.com/pytorch/vision.git
cd vision
pip install --no-build-isolation --no-deps .
cd ~/Video2Map
```

> ⚠️ **KHÔNG dùng** `pip install torchvision` — sẽ kéo torch từ PyPI, ghi đè bản NVIDIA, mất CUDA!

### 2.4 .gitignore
```
TaiLieu/
Video_drone/
venv/
__pycache__/
*.pyc
```

### Verify môi trường
```bash
python3 -c "
import cv2; print('OpenCV:', cv2.__version__)
import torch; print('PyTorch:', torch.__version__, '| CUDA:', torch.cuda.is_available())
import torchvision; print('torchvision:', torchvision.__version__)
from ultralytics import YOLO; print('Ultralytics: OK')
"
```

### Kết quả xác nhận ✅
| Package | Version | Status |
|---------|---------|--------|
| OpenCV | 4.11.0 | ✅ |
| PyTorch | 2.5.0a0+872d972e41.nv24.08 | ✅ CUDA: True |
| torchvision | 0.20.0a0+afc54f7 | ✅ |
| Ultralytics | OK | ✅ |

# Video2Map - Tìm GPS metadata từ video DJI Mini 4 Pro

**Ngày:** 01/03/2026  
**Video test:** DJI_20260226141901_0017_D_MINHTHIEN.MP4

---

## Bước 1: Kiểm tra metadata cơ bản (exiftool)

```bash
# Cài exiftool
sudo apt install -y libimage-exiftool-perl

# Tìm GPS trong metadata thông thường → KHÔNG CÓ
exiftool -ee -G3 Video_drone/DJI_20260226141901_0017_D_MINHTHIEN.MP4 | grep -i "gps\|lat\|lon\|alt\|gimbal"

# Xem metadata tổng quát
exiftool Video_drone/DJI_20260226141901_0017_D_MINHTHIEN.MP4 | head -40
```

**Kết quả:** Không có GPS trong exif metadata thông thường.

---

## Bước 2: Kiểm tra video tracks (ffprobe)

```bash
ffprobe -v error -show_streams Video_drone/DJI_20260226141901_0017_D_MINHTHIEN.MP4 | grep -E "codec_type|codec_name|TAG"
```

**Kết quả:** Phát hiện 4 tracks:
| Track | Loại | Nội dung |
|-------|------|----------|
| Stream 0:0 | Video | H.264 4K 3840x2160 48fps |
| Stream 0:1 | Data | **DJI meta** (djmd) ← GPS ở đây |
| Stream 0:2 | Data | DJI dbgi (debug info) |
| Stream 0:3 | Video | MJPEG 1280x720 (thumbnail) |

---

## Bước 3: Extract DJI meta track

```bash
# Extract raw binary từ DJI meta track
ffmpeg -i Video_drone/DJI_20260226141901_0017_D_MINHTHIEN.MP4 -map 0:d:0 -f data -c copy /tmp/dji_meta.bin

# Xem hex dump
xxd /tmp/dji_meta.bin | head -30
```

**Kết quả:** DJI Mini 4 Pro lưu metadata ở format **Protobuf binary** (file header: `dvtm_Mini4_Pro.proto`).

---

## Bước 4: Parse GPS từ binary (đang làm)

```bash
pip install protobuf

python3 -c "
import struct

with open('/tmp/dji_meta.bin', 'rb') as f:
    data = f.read()

print(f'File size: {len(data)} bytes')

# Tìm tọa độ GPS (double) trong vùng Việt Nam
# Latitude: 8-23, Longitude: 100-115
results = []
for i in range(len(data) - 8):
    val = struct.unpack('<d', data[i:i+8])[0]
    if 8.0 < val < 23.0:
        results.append(('lat?', i, val))
    elif 100.0 < val < 115.0:
        results.append(('lon?', i, val))

for tag, offset, val in results:
    print(f'  {tag} offset={offset}: {val:.8f}')
"
```

---

## Ghi chú kỹ thuật

- DJI Mini 4 Pro **KHÔNG** lưu GPS vào exif metadata thông thường
- GPS nằm trong track riêng "DJI meta" (codec: djmd)
- Format: Protobuf binary (dvtm_Mini4_Pro.proto)
- Track khác "DJI dbgi" chứa debug info, không cần
- File LRF (Low Resolution File) cũng chứa cùng metadata structure