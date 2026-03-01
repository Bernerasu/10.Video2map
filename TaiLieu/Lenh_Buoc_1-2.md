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