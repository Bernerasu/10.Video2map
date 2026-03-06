"""
Video2Map - Video Input Module
Nhận video từ mọi nguồn → decode qua NVDEC (hardware)

Nguồn hỗ trợ:
- Local file (.MP4, .AVI, .MOV)
- Google Drive link
- HTTP/HTTPS URL
- RTSP stream (real-time)
"""

import cv2
import os
import re
import subprocess
import gdown
from pathlib import Path
from typing import Generator, Optional, Tuple

# Thư mục tạm chứa video download
TEMP_DIR = Path.home() / "Video2Map" / "temp_video"
TEMP_DIR.mkdir(parents=True, exist_ok=True)


class VideoSource:
    """Nhận diện và chuẩn hóa nguồn video"""
    
    LOCAL = "local"
    GOOGLE_DRIVE = "gdrive"
    HTTP = "http"
    RTSP = "rtsp"
    
    @staticmethod
    def detect(source: str) -> str:
        """Nhận diện loại nguồn video"""
        if os.path.isfile(source):
            return VideoSource.LOCAL
        if "drive.google.com" in source or "docs.google.com" in source:
            return VideoSource.GOOGLE_DRIVE
        if source.startswith("rtsp://"):
            return VideoSource.RTSP
        if source.startswith("http://") or source.startswith("https://"):
            return VideoSource.HTTP
        raise ValueError(f"Không nhận diện được nguồn video: {source}")


class VideoDownloader:
    """Download video từ các nguồn cloud về local"""
    
    @staticmethod
    def from_google_drive(url: str, output_dir: Path = TEMP_DIR) -> str:
        """Download video từ Google Drive"""
        print(f"[Download] Google Drive: {url}")
        
        # Trích file ID từ URL
        patterns = [
            r'/file/d/([a-zA-Z0-9_-]+)',
            r'id=([a-zA-Z0-9_-]+)',
            r'/d/([a-zA-Z0-9_-]+)',
        ]
        
        file_id = None
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                file_id = match.group(1)
                break
        
        if not file_id:
            raise ValueError(f"Không trích được file ID từ URL: {url}")
        
        output_path = str(output_dir / f"{file_id}.mp4")
        
        # Download qua gdown
        gdown.download(id=file_id, output=output_path, quiet=False)
        
        if not os.path.isfile(output_path):
            raise RuntimeError(f"Download thất bại: {url}")
        
        print(f"[Download] Hoàn thành: {output_path}")
        return output_path
    
    @staticmethod
    def from_http(url: str, output_dir: Path = TEMP_DIR) -> str:
        """Download video từ HTTP/HTTPS URL"""
        print(f"[Download] HTTP: {url}")
        
        filename = url.split("/")[-1].split("?")[0]
        if not filename.endswith(('.mp4', '.avi', '.mov', '.MP4', '.AVI', '.MOV')):
            filename = "download.mp4"
        
        output_path = str(output_dir / filename)
        
        subprocess.run([
            "wget", "-q", "--show-progress", "-O", output_path, url
        ], check=True)
        
        print(f"[Download] Hoàn thành: {output_path}")
        return output_path


class NVDECDecoder:
    """
    Decode video qua NVDEC (hardware) bằng GStreamer pipeline.
    Mọi video đều đi qua chip NVDEC → tận dụng tối đa phần cứng Jetson.
    """
    
    # GStreamer pipeline templates
    PIPELINES = {
        "h264_file": (
            "filesrc location={path} ! qtdemux ! h264parse ! nvv4l2decoder ! "
            "nvvidconv ! video/x-raw,format=BGRx ! videoconvert ! "
            "video/x-raw,format=BGR ! appsink"
        ),
        "h265_file": (
            "filesrc location={path} ! qtdemux ! h265parse ! nvv4l2decoder ! "
            "nvvidconv ! video/x-raw,format=BGRx ! videoconvert ! "
            "video/x-raw,format=BGR ! appsink"
        ),
        "rtsp": (
            "rtspsrc location={path} latency=0 ! rtph264depay ! h264parse ! "
            "nvv4l2decoder ! nvvidconv ! video/x-raw,format=BGRx ! "
            "videoconvert ! video/x-raw,format=BGR ! appsink"
        ),
    }
    
    @staticmethod
    def detect_codec(path: str) -> str:
        """Phát hiện codec video bằng ffprobe"""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
                capture_output=True, text=True, check=True
            )
            codec = result.stdout.strip().lower()
            print(f"[Codec] Phát hiện: {codec}")
            return codec
        except subprocess.CalledProcessError:
            print("[Codec] Không xác định được, mặc định h264")
            return "h264"
    
    @staticmethod
    def build_pipeline(path: str, source_type: str) -> str:
        """Tạo GStreamer pipeline phù hợp"""
        if source_type == VideoSource.RTSP:
            return NVDECDecoder.PIPELINES["rtsp"].format(path=path)
        
        codec = NVDECDecoder.detect_codec(path)
        
        if "hevc" in codec or "h265" in codec or "265" in codec:
            template = NVDECDecoder.PIPELINES["h265_file"]
        else:
            template = NVDECDecoder.PIPELINES["h264_file"]
        
        return template.format(path=path)
    
    @staticmethod
    def open(path: str, source_type: str) -> cv2.VideoCapture:
        """Mở video qua NVDEC GStreamer pipeline"""
        pipeline = NVDECDecoder.build_pipeline(path, source_type)
        print(f"[NVDEC] Pipeline: {pipeline[:80]}...")
        
        cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        
        if not cap.isOpened():
            print("[NVDEC] GStreamer failed, fallback FFMPEG (CPU decode)")
            cap = cv2.VideoCapture(path, cv2.CAP_FFMPEG)
        
        if not cap.isOpened():
            raise RuntimeError(f"Không thể mở video: {path}")
        
        return cap


class VideoInput:
    """
    Module chính — nhận video từ mọi nguồn, decode qua NVDEC.
    
    Sử dụng:
        vi = VideoInput("/path/to/video.mp4")
        vi = VideoInput("https://drive.google.com/file/d/xxx/view")
        vi = VideoInput("rtsp://192.168.1.100:8554/stream")
        
        for frame in vi.frames(interval=30):
            # xử lý frame
    """
    
    def __init__(self, source: str):
        self.source_raw = source
        self.source_type = VideoSource.detect(source)
        self.local_path = self._resolve_local_path()
        self.cap = NVDECDecoder.open(self.local_path, self.source_type)
        self._downloaded = self.source_type != VideoSource.LOCAL
        
        # Metadata
        self.width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.duration = self.total_frames / self.fps if self.fps > 0 else 0
        
        print(f"[VideoInput] {self.width}x{self.height} | {self.fps:.1f}fps | "
              f"{self.total_frames} frames | {self.duration:.1f}s")
    
    def _resolve_local_path(self) -> str:
        """Chuyển mọi nguồn về local path"""
        if self.source_type == VideoSource.LOCAL:
            return self.source_raw
        elif self.source_type == VideoSource.GOOGLE_DRIVE:
            return VideoDownloader.from_google_drive(self.source_raw)
        elif self.source_type == VideoSource.HTTP:
            return VideoDownloader.from_http(self.source_raw)
        elif self.source_type == VideoSource.RTSP:
            return self.source_raw  # RTSP đọc trực tiếp, không download
        else:
            raise ValueError(f"Nguồn không hỗ trợ: {self.source_type}")
    
    def frames(self, interval: int = 1) -> Generator[Tuple[int, object], None, None]:
        """
        Generator trả về frames.
        
        Args:
            interval: Trích 1 frame mỗi N frames. 
                      VD: interval=30 với video 30fps → 1 frame/giây
        
        Yields:
            (frame_number, frame_image)
        """
        frame_count = 0
        while True:
            ret, frame = self.cap.read()
            if not ret:
                break
            
            if frame_count % interval == 0:
                yield frame_count, frame
            
            frame_count += 1
        
        print(f"[VideoInput] Đã đọc {frame_count} frames")
    
    def get_info(self) -> dict:
        """Trả về thông tin video"""
        return {
            "source": self.source_raw,
            "source_type": self.source_type,
            "local_path": self.local_path,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "total_frames": self.total_frames,
            "duration_seconds": round(self.duration, 1),
        }
    
    def cleanup(self):
        """Giải phóng tài nguyên + xóa file tạm"""
        self.cap.release()
        if self._downloaded and os.path.isfile(self.local_path):
            os.remove(self.local_path)
            print(f"[Cleanup] Đã xóa file tạm: {self.local_path}")
    
    def __del__(self):
        if hasattr(self, 'cap') and self.cap.isOpened():
            self.cap.release()


# === TEST ===
if __name__ == "__main__":
    import sys
    
    # Mặc định test video demo
    source = sys.argv[1] if len(sys.argv) > 1 else \
        "/home/nam/Video2Map/Video_drone/DJI_20260226141901_0017_D_MINHTHIEN.MP4"
    
    print(f"=== Test VideoInput: {source} ===\n")
    
    vi = VideoInput(source)
    print(f"\nInfo: {vi.get_info()}\n")
    
    # Đọc 5 frame đầu (interval=30 → 1 frame/giây)
    count = 0
    for fnum, frame in vi.frames(interval=30):
        print(f"  Frame {fnum}: {frame.shape}")
        count += 1
        if count >= 5:
            break
    
    vi.cleanup()
    print("\n=== DONE ===")
