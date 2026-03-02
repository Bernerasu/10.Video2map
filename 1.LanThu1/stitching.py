"""
Video2Map - Image Stitching Module
Ghép keyframes thành panorama (bản đồ 2D)

Hai chế độ:
  1. AUTO   — Dùng OpenCV Stitcher (đơn giản, ổn định)
  2. MANUAL — ORB CUDA + Homography chain (kiểm soát, nhanh hơn)

Pipeline:
  Keyframes (4K) → Resize → Feature detect (ORB CUDA)
  → Feature match (BFMatcher CUDA) → Homography → Warp → Blend → Panorama

Giới hạn phần cứng (Jetson 8GB):
  - 4K frame = ~24MB RAM
  - Resize xuống 1920px để stitch → ~6MB/frame
  - Tối đa ~30-40 frames trong RAM cùng lúc
"""

import cv2
import numpy as np
import os
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


# ============================================================
# CONFIG
# ============================================================

@dataclass
class StitchConfig:
    """Cấu hình stitching"""
    # Resize frame trước khi stitch (tiết kiệm RAM + nhanh hơn)
    max_width: int = 1920        # Resize xuống 1920px (từ 3840)
    
    # Feature detection
    n_features: int = 2000       # Số features ORB detect
    
    # Feature matching
    match_ratio: float = 0.7     # Lowe's ratio test threshold
    min_matches: int = 30        # Tối thiểu matches để ghép
    
    # Homography
    ransac_thresh: float = 5.0   # RANSAC threshold (pixels)
    
    # Blending
    blend_strength: float = 5.0  # MultiBand blender strength
    
    # Output
    output_quality: int = 95     # JPEG quality


# ============================================================
# STITCHING RESULT
# ============================================================

@dataclass
class StitchResult:
    """Kết quả ghép ảnh"""
    panorama: Optional[np.ndarray] = None  # Ảnh panorama output
    success: bool = False
    num_frames: int = 0
    num_stitched: int = 0
    num_failed: int = 0
    total_time_sec: float = 0.0
    panorama_size: Tuple[int, int] = (0, 0)  # (width, height)
    method: str = ""
    error_msg: str = ""


# ============================================================
# AUTO STITCHER — OpenCV built-in
# ============================================================

class AutoStitcher:
    """
    Dùng cv2.Stitcher built-in (mode SCANS cho ảnh top-down).
    Ưu điểm: Ổn định, xử lý exposure + blending tốt.
    Nhược điểm: Chậm hơn, ít kiểm soát.
    """
    
    STATUS_MESSAGES = {
        cv2.Stitcher_OK: "OK",
        cv2.Stitcher_ERR_NEED_MORE_IMGS: "Cần thêm ảnh (không đủ overlap)",
        cv2.Stitcher_ERR_HOMOGRAPHY_EST_FAIL: "Không tìm được homography",
        cv2.Stitcher_ERR_CAMERA_PARAMS_ADJUST_FAIL: "Lỗi camera params",
    }
    
    @staticmethod
    def stitch(frames: List[np.ndarray], config: StitchConfig = None) -> StitchResult:
        """
        Ghép ảnh bằng OpenCV Stitcher (SCANS mode).
        
        Args:
            frames: List ảnh BGR
            config: Cấu hình (optional)
        
        Returns:
            StitchResult
        """
        if config is None:
            config = StitchConfig()
        
        result = StitchResult(
            num_frames=len(frames),
            method="auto_stitcher"
        )
        
        if len(frames) < 2:
            result.error_msg = "Cần ít nhất 2 ảnh"
            return result
        
        start = time.time()
        
        # Resize frames
        resized = [_resize_frame(f, config.max_width) for f in frames]
        print(f"[AutoStitch] {len(resized)} frames, "
              f"size: {resized[0].shape[1]}x{resized[0].shape[0]}")
        
        # Tạo stitcher mode SCANS (cho ảnh chụp từ trên xuống)
        stitcher = cv2.Stitcher.create(cv2.Stitcher_SCANS)
        
        # Cấu hình
        stitcher.setPanoConfidenceThresh(0.5)
        
        # Thực hiện stitch
        print("[AutoStitch] Đang ghép...")
        status, panorama = stitcher.stitch(resized)
        
        result.total_time_sec = time.time() - start
        
        if status == cv2.Stitcher_OK:
            result.panorama = panorama
            result.success = True
            result.num_stitched = len(frames)
            result.panorama_size = (panorama.shape[1], panorama.shape[0])
            print(f"[AutoStitch] ✅ Thành công: {panorama.shape[1]}x{panorama.shape[0]} "
                  f"trong {result.total_time_sec:.1f}s")
        else:
            msg = AutoStitcher.STATUS_MESSAGES.get(status, f"Lỗi không xác định ({status})")
            result.error_msg = msg
            print(f"[AutoStitch] ❌ Thất bại: {msg}")
        
        return result


# ============================================================
# MANUAL STITCHER — ORB CUDA + Homography chain
# ============================================================

class ManualStitcher:
    """
    Ghép ảnh thủ công với ORB CUDA + BFMatcher CUDA.
    
    Pipeline:
      Frame 0 (base) → Frame 1 → Frame 2 → ...
      Mỗi frame mới được warp vào canvas chung bằng homography.
    
    Ưu điểm: Nhanh (CUDA), kiểm soát được, incremental.
    Nhược điểm: Drift tích lũy theo số frame.
    """
    
    @staticmethod
    def stitch(frames: List[np.ndarray], config: StitchConfig = None) -> StitchResult:
        """
        Ghép ảnh bằng ORB CUDA + Homography chain.
        """
        if config is None:
            config = StitchConfig()
        
        result = StitchResult(
            num_frames=len(frames),
            method="manual_orb_cuda"
        )
        
        if len(frames) < 2:
            result.error_msg = "Cần ít nhất 2 ảnh"
            return result
        
        start = time.time()
        
        # Resize frames
        resized = [_resize_frame(f, config.max_width) for f in frames]
        h, w = resized[0].shape[:2]
        print(f"[ManualStitch] {len(resized)} frames, size: {w}x{h}")
        
        # Tạo ORB detector trên CUDA
        try:
            orb_cuda = cv2.cuda.ORB.create(nfeatures=config.n_features)
            bf_cuda = cv2.cuda.DescriptorMatcher.createBFMatcher(cv2.NORM_HAMMING)
            use_cuda = True
            print("[ManualStitch] Feature matching: ORB CUDA")
        except Exception as e:
            print(f"[ManualStitch] CUDA không khả dụng ({e}), fallback CPU")
            orb_cuda = None
            use_cuda = False
        
        # Canvas lớn (3x kích thước frame để chứa panorama)
        canvas_h = h * 3
        canvas_w = w * 3
        
        # Offset: đặt frame đầu tiên ở giữa canvas
        offset_x = w
        offset_y = h
        
        # Khởi tạo canvas
        canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
        mask = np.zeros((canvas_h, canvas_w), dtype=np.uint8)
        
        # Đặt frame đầu tiên vào giữa canvas
        canvas[offset_y:offset_y+h, offset_x:offset_x+w] = resized[0]
        mask[offset_y:offset_y+h, offset_x:offset_x+w] = 255
        
        # Homography tích lũy (bắt đầu = identity + offset)
        H_accumulated = np.float64([
            [1, 0, offset_x],
            [0, 1, offset_y],
            [0, 0, 1]
        ])
        
        stitched_count = 1
        failed_count = 0
        
        # Ghép từng frame
        for i in range(1, len(resized)):
            print(f"  Frame {i}/{len(resized)-1}...", end=" ")
            
            # Tìm homography giữa frame[i] và frame[i-1]
            H = _find_homography(
                resized[i-1], resized[i],
                orb_cuda, bf_cuda, use_cuda, config
            )
            
            if H is None:
                print("❌ Không đủ matches")
                failed_count += 1
                continue
            
            # Tích lũy homography
            H_accumulated = H_accumulated @ H
            
            # Warp frame vào canvas
            try:
                warped = cv2.warpPerspective(
                    resized[i], H_accumulated,
                    (canvas_w, canvas_h),
                    flags=cv2.INTER_LINEAR
                )
                
                # Tạo mask cho vùng warped
                warped_mask = cv2.warpPerspective(
                    np.ones((h, w), dtype=np.uint8) * 255,
                    H_accumulated,
                    (canvas_w, canvas_h)
                )
                
                # Blend: vùng mới ghi đè vùng cũ (simple overlay)
                # Nâng cấp sau: multiband blending
                blend_region = (warped_mask > 0) & (mask == 0)
                overlap_region = (warped_mask > 0) & (mask > 0)
                
                # Vùng mới hoàn toàn → copy
                canvas[blend_region] = warped[blend_region]
                
                # Vùng overlap → trộn 50/50
                if np.any(overlap_region):
                    canvas[overlap_region] = (
                        canvas[overlap_region].astype(np.float32) * 0.5 +
                        warped[overlap_region].astype(np.float32) * 0.5
                    ).astype(np.uint8)
                
                mask = mask | warped_mask
                
                stitched_count += 1
                print("✅")
                
            except Exception as e:
                print(f"❌ Warp lỗi: {e}")
                failed_count += 1
        
        # Crop canvas — bỏ vùng đen
        panorama = _crop_black_border(canvas, mask)
        
        result.total_time_sec = time.time() - start
        result.num_stitched = stitched_count
        result.num_failed = failed_count
        
        if panorama is not None and panorama.size > 0:
            result.panorama = panorama
            result.success = True
            result.panorama_size = (panorama.shape[1], panorama.shape[0])
            print(f"\n[ManualStitch] ✅ Hoàn thành: {panorama.shape[1]}x{panorama.shape[0]} "
                  f"| {stitched_count}/{len(frames)} frames "
                  f"| {result.total_time_sec:.1f}s")
        else:
            result.error_msg = "Canvas trống sau khi ghép"
            print(f"\n[ManualStitch] ❌ Thất bại")
        
        return result


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def _resize_frame(frame: np.ndarray, max_width: int) -> np.ndarray:
    """Resize frame giữ tỷ lệ, max_width pixels"""
    h, w = frame.shape[:2]
    if w <= max_width:
        return frame
    
    scale = max_width / w
    new_w = max_width
    new_h = int(h * scale)
    return cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)


def _find_homography(
    img1: np.ndarray, img2: np.ndarray,
    orb_cuda, bf_cuda, use_cuda: bool,
    config: StitchConfig
) -> Optional[np.ndarray]:
    """
    Tìm homography giữa 2 ảnh bằng ORB + BFMatcher.
    
    Returns:
        Ma trận Homography 3x3, hoặc None nếu thất bại
    """
    # Chuyển sang grayscale
    gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
    gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)
    
    if use_cuda:
        kp1, des1, kp2, des2 = _detect_features_cuda(gray1, gray2, orb_cuda)
    else:
        kp1, des1, kp2, des2 = _detect_features_cpu(gray1, gray2, config)
    
    if des1 is None or des2 is None:
        return None
    
    if len(kp1) < config.min_matches or len(kp2) < config.min_matches:
        return None
    
    # Match features
    if use_cuda:
        matches = _match_features_cuda(des1, des2, bf_cuda, config)
    else:
        matches = _match_features_cpu(des1, des2, config)
    
    if len(matches) < config.min_matches:
        return None
    
    # Extract matched keypoint coordinates
    src_pts = np.float32([kp1[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
    dst_pts = np.float32([kp2[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
    
    # Tính Homography (img2 → img1)
    H, mask = cv2.findHomography(dst_pts, src_pts, cv2.RANSAC, config.ransac_thresh)
    
    if H is None:
        return None
    
    # Kiểm tra homography hợp lệ (không biến dạng quá mức)
    if not _is_valid_homography(H):
        return None
    
    inliers = int(mask.sum()) if mask is not None else 0
    print(f"matches={len(matches)}, inliers={inliers}", end=" ")
    
    return H


def _detect_features_cuda(gray1, gray2, orb_cuda):
    """Detect features bằng ORB CUDA"""
    # Upload lên GPU
    gpu1 = cv2.cuda_GpuMat()
    gpu2 = cv2.cuda_GpuMat()
    gpu1.upload(gray1)
    gpu2.upload(gray2)
    
    # Detect + compute
    kp1_gpu, des1_gpu = orb_cuda.detectAndComputeAsync(gpu1, None)
    kp2_gpu, des2_gpu = orb_cuda.detectAndComputeAsync(gpu2, None)
    
    # Download về CPU
    kp1 = orb_cuda.convert(kp1_gpu)
    kp2 = orb_cuda.convert(kp2_gpu)
    des1 = des1_gpu.download() if des1_gpu is not None else None
    des2 = des2_gpu.download() if des2_gpu is not None else None
    
    return kp1, des1, kp2, des2


def _detect_features_cpu(gray1, gray2, config):
    """Detect features bằng ORB CPU (fallback)"""
    orb = cv2.ORB.create(nfeatures=config.n_features)
    kp1, des1 = orb.detectAndCompute(gray1, None)
    kp2, des2 = orb.detectAndCompute(gray2, None)
    return kp1, des1, kp2, des2


def _match_features_cuda(des1, des2, bf_cuda, config):
    """Match features bằng BFMatcher CUDA"""
    gpu_des1 = cv2.cuda_GpuMat()
    gpu_des2 = cv2.cuda_GpuMat()
    gpu_des1.upload(des1)
    gpu_des2.upload(des2)
    
    # KNN match (k=2 cho Lowe's ratio test)
    matches_gpu = bf_cuda.knnMatch(gpu_des1, gpu_des2, k=2)
    
    # Lowe's ratio test
    good = []
    for m_pair in matches_gpu:
        if len(m_pair) == 2:
            m, n = m_pair
            if m.distance < config.match_ratio * n.distance:
                good.append(m)
    
    return good


def _match_features_cpu(des1, des2, config):
    """Match features bằng BFMatcher CPU (fallback)"""
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    matches = bf.knnMatch(des1, des2, k=2)
    
    good = []
    for m_pair in matches:
        if len(m_pair) == 2:
            m, n = m_pair
            if m.distance < config.match_ratio * n.distance:
                good.append(m)
    
    return good


def _is_valid_homography(H: np.ndarray) -> bool:
    """
    Kiểm tra homography có hợp lệ không.
    Loại bỏ homography biến dạng quá mức (flip, scale cực đại...).
    """
    # Determinant phải dương và gần 1
    det = np.linalg.det(H[:2, :2])
    if det < 0.1 or det > 10.0:
        return False
    
    # Translation không quá lớn (< 50% kích thước ảnh)
    tx, ty = abs(H[0, 2]), abs(H[1, 2])
    if tx > 2000 or ty > 2000:
        return False
    
    # Perspective distortion không quá mạnh
    if abs(H[2, 0]) > 0.005 or abs(H[2, 1]) > 0.005:
        return False
    
    return True


def _crop_black_border(canvas: np.ndarray, mask: np.ndarray) -> Optional[np.ndarray]:
    """Crop bỏ viền đen xung quanh panorama"""
    # Tìm bounding box của vùng có nội dung
    coords = cv2.findNonZero(mask)
    if coords is None:
        return None
    
    x, y, w, h = cv2.boundingRect(coords)
    
    # Thêm padding nhỏ
    pad = 5
    x = max(0, x - pad)
    y = max(0, y - pad)
    w = min(canvas.shape[1] - x, w + 2*pad)
    h = min(canvas.shape[0] - y, h + 2*pad)
    
    return canvas[y:y+h, x:x+w]


# ============================================================
# CONVENIENCE FUNCTION
# ============================================================

def stitch_frames(
    frames: List[np.ndarray],
    method: str = "auto",
    config: StitchConfig = None,
) -> StitchResult:
    """
    Hàm chính — ghép list ảnh thành panorama.
    
    Args:
        frames: List ảnh BGR (từ frame_extract.py)
        method: "auto" (OpenCV Stitcher) hoặc "manual" (ORB CUDA)
        config: Cấu hình tùy chỉnh
    
    Returns:
        StitchResult chứa panorama + metadata
    """
    if config is None:
        config = StitchConfig()
    
    print(f"\n{'='*50}")
    print(f"Stitching: {len(frames)} frames | Method: {method}")
    print(f"{'='*50}\n")
    
    if method == "auto":
        return AutoStitcher.stitch(frames, config)
    elif method == "manual":
        return ManualStitcher.stitch(frames, config)
    else:
        # Thử auto trước, nếu fail thì dùng manual
        print("[Stitch] Thử AUTO trước...")
        result = AutoStitcher.stitch(frames, config)
        if result.success:
            return result
        
        print("[Stitch] AUTO thất bại, thử MANUAL...")
        return ManualStitcher.stitch(frames, config)


def save_panorama(
    result: StitchResult,
    output_path: str = "output/panorama.jpg",
    config: StitchConfig = None
) -> Optional[str]:
    """Lưu panorama ra file"""
    if config is None:
        config = StitchConfig()
    
    if not result.success or result.panorama is None:
        print("[Save] Không có panorama để lưu")
        return None
    
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    cv2.imwrite(output_path, result.panorama, [cv2.IMWRITE_JPEG_QUALITY, config.output_quality])
    
    file_size = os.path.getsize(output_path) / (1024*1024)
    print(f"[Save] Panorama → {output_path} ({file_size:.1f}MB)")
    
    return output_path


# ============================================================
# MAIN — TEST
# ============================================================

if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from frame_extract import DJIGPSExtractor, FrameExtractor
    
    # Video test
    video = sys.argv[1] if len(sys.argv) > 1 else \
        "/home/nam/Video2Map/Video_drone/DJI_20260226142532_0019_D_MINHTHIEN.MP4"
    
    print(f"{'='*60}")
    print(f"Video2Map — Stitching Test")
    print(f"Video: {os.path.basename(video)}")
    print(f"{'='*60}\n")
    
    # === Bước 1: Extract GPS ===
    print("── Bước 1: GPS ──")
    gps_points = DJIGPSExtractor.extract(video)
    
    # === Bước 2: Extract frames ===
    print("\n── Bước 2: Keyframes ──")
    result = FrameExtractor.extract_keyframes(
        video_path=video,
        gps_points=gps_points,
        interval_sec=2.0,      # 1 frame mỗi 2 giây
        max_frames=20,          # Lấy 20 frames để test
    )
    
    frames = [fd.image for fd in result.frames]
    print(f"\n  Có {len(frames)} keyframes để ghép")
    
    # === Bước 3: Stitching ===
    # Thử AUTO trước
    print("\n── Bước 3a: Thử AUTO Stitcher ──")
    stitch_result = stitch_frames(frames, method="auto")
    
    # Nếu AUTO thất bại → thử MANUAL
    if not stitch_result.success:
        print("\n── Bước 3b: Thử MANUAL Stitcher ──")
        stitch_result = stitch_frames(frames, method="manual")
    
    # === Kết quả ===
    print(f"\n{'='*60}")
    print(f"KẾT QUẢ STITCHING")
    print(f"{'='*60}")
    print(f"  Method:      {stitch_result.method}")
    print(f"  Success:     {'✅' if stitch_result.success else '❌'}")
    print(f"  Frames:      {stitch_result.num_stitched}/{stitch_result.num_frames}")
    print(f"  Failed:      {stitch_result.num_failed}")
    print(f"  Panorama:    {stitch_result.panorama_size[0]}x{stitch_result.panorama_size[1]}")
    print(f"  Time:        {stitch_result.total_time_sec:.1f}s")
    if stitch_result.error_msg:
        print(f"  Error:       {stitch_result.error_msg}")
    
    # Lưu panorama
    if stitch_result.success:
        path = save_panorama(stitch_result, "output/panorama.jpg")
        if path:
            print(f"\n  📸 Mở xem: output/panorama.jpg")
    
    print(f"\n{'='*60}")
    print("DONE")
    print(f"{'='*60}")
