"""
Video2Map - Frame & GPS Extraction Module
Trích xuất keyframes + GPS từ video DJI → output: frame + tọa độ

Pipeline:
  Video DJI MP4
    ├── Track "DJI meta" (binary) → parse GPS (radians → degrees)
    └── Track video (H.264/H.265) → NVDEC decode → keyframes

Output:
  List[FrameData] = [(frame_id, image, lat, lon, alt, timestamp)]

Hỗ trợ: DJI Mini 4 Pro, DJI Air, DJI Mavic (format dvtm protobuf)
"""

import cv2
import os
import struct
import math
import subprocess
import numpy as np
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class GPSPoint:
    """Một điểm GPS từ DJI metadata"""
    latitude: float      # Degrees
    longitude: float     # Degrees
    offset: int          # Byte offset trong binary (debug)
    frame_index: int = 0 # Frame tương ứng


@dataclass
class FrameData:
    """Một keyframe + GPS tương ứng"""
    frame_id: int           # Số thứ tự frame trong video
    image: np.ndarray       # Ảnh BGR (OpenCV format)
    latitude: float         # Degrees
    longitude: float        # Degrees
    timestamp_sec: float    # Thời điểm trong video (giây)


@dataclass
class ExtractionResult:
    """Kết quả trích xuất"""
    frames: List[FrameData] = field(default_factory=list)
    total_gps_points: int = 0
    total_video_frames: int = 0
    video_duration: float = 0.0
    video_fps: float = 0.0
    video_width: int = 0
    video_height: int = 0
    gps_start: Tuple[float, float] = (0.0, 0.0)
    gps_end: Tuple[float, float] = (0.0, 0.0)
    distance_meters: float = 0.0


# ============================================================
# GPS EXTRACTION — từ DJI meta binary track
# ============================================================

class DJIGPSExtractor:
    """
    Extract GPS từ DJI meta track (dvtm protobuf binary).
    
    DJI Mini 4 Pro lưu GPS dạng:
    - Format: double (8 bytes, little-endian)
    - Đơn vị: radians
    - Latitude offset, Longitude offset cách 9 bytes
    - 1 GPS sample mỗi ~294 bytes (= 1 frame)
    """
    
    # Giới hạn tọa độ Việt Nam + buffer
    LAT_RANGE = (5.0, 25.0)    # Degrees
    LON_RANGE = (100.0, 120.0) # Degrees
    
    # Radians tương ứng
    LAT_RAD_RANGE = (math.radians(5.0), math.radians(25.0))
    LON_RAD_RANGE = (math.radians(100.0), math.radians(120.0))
    
    # Khoảng cách tối thiểu giữa 2 GPS points (bytes) để loại duplicate
    MIN_OFFSET_GAP = 100
    
    @staticmethod
    def extract_meta_track(video_path: str, output_path: str = "/tmp/dji_meta.bin") -> Optional[str]:
        """
        Extract DJI meta track từ video MP4 → file binary.
        Dùng ffmpeg -map 0:d:0 để lấy data track đầu tiên.
        """
        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", video_path, 
                 "-map", "0:d:0", "-f", "data", "-c", "copy", output_path],
                capture_output=True, text=True, timeout=60
            )
            
            if os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
                size = os.path.getsize(output_path)
                print(f"[GPS] Extracted DJI meta: {size:,} bytes → {output_path}")
                return output_path
            else:
                print("[GPS] CẢNH BÁO: Không tìm thấy DJI meta track")
                return None
                
        except subprocess.TimeoutExpired:
            print("[GPS] LỖI: Timeout khi extract meta track")
            return None
        except FileNotFoundError:
            print("[GPS] LỖI: ffmpeg chưa cài đặt")
            return None
    
    @staticmethod
    def has_dji_meta_track(video_path: str) -> bool:
        """Kiểm tra video có chứa DJI meta track không"""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_streams", video_path],
                capture_output=True, text=True, timeout=10
            )
            return "DJI meta" in result.stdout or "djmd" in result.stdout
        except:
            return False
    
    @staticmethod
    def parse_gps_from_binary(binary_path: str) -> List[GPSPoint]:
        """
        Parse GPS từ file binary DJI meta.
        
        Thuật toán:
        1. Scan từng byte, đọc double (8 bytes little-endian)
        2. Nếu giá trị nằm trong range radians của latitude VN → candidate
        3. Tìm longitude trong 20 bytes tiếp theo
        4. Lọc duplicate theo khoảng cách offset
        """
        with open(binary_path, 'rb') as f:
            data = f.read()
        
        if len(data) < 16:
            print("[GPS] File binary quá nhỏ, không có GPS")
            return []
        
        # Scan tìm cặp LAT/LON
        raw_points = []
        lat_min_rad, lat_max_rad = DJIGPSExtractor.LAT_RAD_RANGE
        lon_min_rad, lon_max_rad = DJIGPSExtractor.LON_RAD_RANGE
        
        i = 0
        while i < len(data) - 16:
            lat_rad = struct.unpack('<d', data[i:i+8])[0]
            
            # Check nếu là latitude (radians)
            if lat_min_rad < lat_rad < lat_max_rad:
                lat_deg = math.degrees(lat_rad)
                
                # Tìm longitude trong 20 bytes tiếp theo
                found_lon = False
                for j in range(1, 20):
                    if i + j + 8 <= len(data):
                        lon_rad = struct.unpack('<d', data[i+j:i+j+8])[0]
                        
                        if lon_min_rad < lon_rad < lon_max_rad:
                            lon_deg = math.degrees(lon_rad)
                            raw_points.append(GPSPoint(
                                latitude=lat_deg,
                                longitude=lon_deg,
                                offset=i
                            ))
                            found_lon = True
                            break
                
                if found_lon:
                    i += DJIGPSExtractor.MIN_OFFSET_GAP  # Skip ahead
                    continue
            
            i += 1
        
        # Lọc duplicate (offset quá gần nhau)
        if not raw_points:
            print("[GPS] Không tìm thấy GPS points")
            return []
        
        filtered = [raw_points[0]]
        for p in raw_points[1:]:
            if p.offset - filtered[-1].offset >= DJIGPSExtractor.MIN_OFFSET_GAP:
                filtered.append(p)
        
        # Gán frame index
        for idx, point in enumerate(filtered):
            point.frame_index = idx
        
        print(f"[GPS] Tìm thấy {len(filtered)} GPS points")
        return filtered
    
    @staticmethod
    def extract(video_path: str) -> List[GPSPoint]:
        """
        Pipeline hoàn chỉnh: Video → GPS points
        """
        # Kiểm tra có DJI meta track không
        if not DJIGPSExtractor.has_dji_meta_track(video_path):
            print(f"[GPS] Video không có DJI meta track: {video_path}")
            return []
        
        # Extract binary
        meta_path = f"/tmp/dji_meta_{os.getpid()}.bin"
        meta_file = DJIGPSExtractor.extract_meta_track(video_path, meta_path)
        
        if not meta_file:
            return []
        
        # Parse GPS
        try:
            points = DJIGPSExtractor.parse_gps_from_binary(meta_file)
            return points
        finally:
            # Cleanup
            if os.path.isfile(meta_path):
                os.remove(meta_path)


# ============================================================
# FRAME EXTRACTION — từ video qua NVDEC
# ============================================================

class FrameExtractor:
    """
    Trích keyframes từ video DJI qua NVDEC hardware decode.
    Ghép mỗi keyframe với GPS tương ứng.
    """
    
    # GStreamer pipelines cho NVDEC
    GSTREAMER_PIPELINES = {
        "h264": (
            "filesrc location={path} ! qtdemux ! h264parse ! nvv4l2decoder ! "
            "nvvidconv ! video/x-raw,format=BGRx ! videoconvert ! "
            "video/x-raw,format=BGR ! appsink"
        ),
        "h265": (
            "filesrc location={path} ! qtdemux ! h265parse ! nvv4l2decoder ! "
            "nvvidconv ! video/x-raw,format=BGRx ! videoconvert ! "
            "video/x-raw,format=BGR ! appsink"
        ),
    }
    
    @staticmethod
    def detect_codec(video_path: str) -> str:
        """Phát hiện codec video"""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=codec_name", "-of", "csv=p=0", video_path],
                capture_output=True, text=True, check=True
            )
            codec = result.stdout.strip().lower()
            return codec
        except:
            return "h264"
    
    @staticmethod
    def open_video(video_path: str) -> cv2.VideoCapture:
        """Mở video qua NVDEC GStreamer pipeline"""
        codec = FrameExtractor.detect_codec(video_path)
        
        # Chọn pipeline
        if "hevc" in codec or "h265" in codec or "265" in codec:
            template = FrameExtractor.GSTREAMER_PIPELINES["h265"]
        else:
            template = FrameExtractor.GSTREAMER_PIPELINES["h264"]
        
        pipeline = template.format(path=video_path)
        print(f"[Video] Codec: {codec} | NVDEC pipeline")
        
        cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        
        if not cap.isOpened():
            print("[Video] CẢNH BÁO: GStreamer failed, fallback FFMPEG (CPU decode)")
            cap = cv2.VideoCapture(video_path, cv2.CAP_FFMPEG)
        
        if not cap.isOpened():
            raise RuntimeError(f"Không thể mở video: {video_path}")
        
        return cap
    
    @staticmethod
    def extract_keyframes(
        video_path: str,
        gps_points: List[GPSPoint],
        interval_sec: float = 1.0,
        max_frames: Optional[int] = None,
    ) -> ExtractionResult:
        """
        Trích keyframes + ghép GPS.
        
        Args:
            video_path: Đường dẫn video DJI
            gps_points: Danh sách GPS từ DJIGPSExtractor
            interval_sec: Lấy 1 frame mỗi N giây (mặc định 1s)
            max_frames: Giới hạn số frame (None = lấy hết)
        
        Returns:
            ExtractionResult chứa frames + metadata
        """
        cap = FrameExtractor.open_video(video_path)
        
        # Metadata video
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        duration = total_frames / fps if fps > 0 else 0
        
        # Tính interval theo frames
        frame_interval = max(1, int(fps * interval_sec))
        
        print(f"[Video] {width}x{height} | {fps:.1f}fps | {total_frames} frames | {duration:.1f}s")
        print(f"[Extract] Interval: {interval_sec}s = mỗi {frame_interval} frames")
        print(f"[GPS] {len(gps_points)} points cho {total_frames} frames")
        
        # Tính tỷ lệ GPS points / video frames
        # Thường 1:1 nhưng có thể khác nếu GPS sample rate ≠ video fps
        gps_ratio = len(gps_points) / total_frames if total_frames > 0 else 1.0
        
        result = ExtractionResult(
            total_gps_points=len(gps_points),
            total_video_frames=total_frames,
            video_duration=duration,
            video_fps=fps,
            video_width=width,
            video_height=height,
        )
        
        # Extract frames
        frame_count = 0
        extracted = 0
        
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            
            if frame_count % frame_interval == 0:
                # Tìm GPS tương ứng
                gps_idx = min(int(frame_count * gps_ratio), len(gps_points) - 1)
                
                if gps_points and gps_idx >= 0:
                    gps = gps_points[gps_idx]
                    lat, lon = gps.latitude, gps.longitude
                else:
                    lat, lon = 0.0, 0.0
                
                frame_data = FrameData(
                    frame_id=frame_count,
                    image=frame,
                    latitude=lat,
                    longitude=lon,
                    timestamp_sec=round(frame_count / fps, 3),
                )
                result.frames.append(frame_data)
                extracted += 1
                
                if max_frames and extracted >= max_frames:
                    break
            
            frame_count += 1
        
        cap.release()
        
        # Tính thống kê
        if result.frames:
            result.gps_start = (result.frames[0].latitude, result.frames[0].longitude)
            result.gps_end = (result.frames[-1].latitude, result.frames[-1].longitude)
            result.distance_meters = _calc_distance(
                result.gps_start[0], result.gps_start[1],
                result.gps_end[0], result.gps_end[1]
            )
        
        print(f"[Extract] Hoàn thành: {extracted} keyframes từ {frame_count} frames")
        
        return result


# ============================================================
# UTILITIES
# ============================================================

def _calc_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Tính khoảng cách giữa 2 tọa độ (mét) — Haversine formula"""
    R = 6371000  # Bán kính Trái Đất (mét)
    
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    
    a = (math.sin(dlat/2)**2 + 
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * 
         math.sin(dlon/2)**2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    
    return R * c


def save_keyframes(result: ExtractionResult, output_dir: str = "output/keyframes"):
    """Lưu keyframes ra ổ đĩa kèm GPS info"""
    os.makedirs(output_dir, exist_ok=True)
    
    gps_log = []
    
    for fd in result.frames:
        # Lưu ảnh
        filename = f"frame_{fd.frame_id:06d}.jpg"
        filepath = os.path.join(output_dir, filename)
        cv2.imwrite(filepath, fd.image, [cv2.IMWRITE_JPEG_QUALITY, 95])
        
        # Log GPS
        gps_log.append(f"{filename},{fd.latitude:.7f},{fd.longitude:.7f},{fd.timestamp_sec:.3f}")
    
    # Lưu GPS log
    log_path = os.path.join(output_dir, "gps_log.csv")
    with open(log_path, 'w') as f:
        f.write("filename,latitude,longitude,timestamp_sec\n")
        f.write("\n".join(gps_log))
    
    print(f"[Save] {len(result.frames)} keyframes → {output_dir}")
    print(f"[Save] GPS log → {log_path}")


# ============================================================
# MAIN — TEST
# ============================================================

if __name__ == "__main__":
    import sys
    
    # Video test
    video = sys.argv[1] if len(sys.argv) > 1 else \
        "/home/nam/Video2Map/Video_drone/DJI_20260226142532_0019_D_MINHTHIEN.MP4"
    
    print(f"{'='*60}")
    print(f"Video2Map — Frame & GPS Extraction")
    print(f"Video: {video}")
    print(f"{'='*60}\n")
    
    # Bước 1: Extract GPS
    print("── Bước 1: Trích xuất GPS ──")
    gps_points = DJIGPSExtractor.extract(video)
    
    if gps_points:
        print(f"  Đầu:  {gps_points[0].latitude:.7f}, {gps_points[0].longitude:.7f}")
        print(f"  Cuối: {gps_points[-1].latitude:.7f}, {gps_points[-1].longitude:.7f}")
        dist = _calc_distance(
            gps_points[0].latitude, gps_points[0].longitude,
            gps_points[-1].latitude, gps_points[-1].longitude
        )
        print(f"  Khoảng cách: {dist:.0f}m")
    print()
    
    # Bước 2: Extract keyframes + ghép GPS
    print("── Bước 2: Trích xuất keyframes ──")
    result = FrameExtractor.extract_keyframes(
        video_path=video,
        gps_points=gps_points,
        interval_sec=2.0,   # 1 frame mỗi 2 giây
        max_frames=10,       # Chỉ lấy 10 frame để test
    )
    print()
    
    # Hiển thị kết quả
    print("── Kết quả ──")
    print(f"  Video: {result.video_width}x{result.video_height} | "
          f"{result.video_fps:.1f}fps | {result.video_duration:.1f}s")
    print(f"  GPS points: {result.total_gps_points}")
    print(f"  Keyframes: {len(result.frames)}")
    print(f"  GPS start: {result.gps_start[0]:.7f}, {result.gps_start[1]:.7f}")
    print(f"  GPS end:   {result.gps_end[0]:.7f}, {result.gps_end[1]:.7f}")
    print(f"  Khoảng cách: {result.distance_meters:.0f}m")
    print()
    
    # Chi tiết từng frame
    print("── Chi tiết keyframes ──")
    for fd in result.frames:
        print(f"  Frame {fd.frame_id:5d} | t={fd.timestamp_sec:6.1f}s | "
              f"GPS: {fd.latitude:.7f}, {fd.longitude:.7f} | "
              f"Shape: {fd.image.shape}")
    
    # Lưu keyframes (tùy chọn)
    save = input("\nLưu keyframes ra ổ đĩa? (y/n): ").strip().lower()
    if save == 'y':
        save_keyframes(result, "output/keyframes")
    
    print(f"\n{'='*60}")
    print("DONE")
    print(f"{'='*60}")
