"""
Video2Map - Frame & Telemetry Extraction Module v2
Trích xuất keyframes + FULL telemetry từ video DJI

Pipeline:
  Video DJI MP4
    ├── Track "DJI meta" (binary dvtm protobuf)
    │     ├── GPS (lat, lon) — double radians
    │     ├── Altitude AGL — float32
    │     ├── Gimbal pitch — float32
    │     └── Camera quaternion → camera yaw
    └── Track video (H.264/H.265) → NVDEC decode → keyframes

Output:
  List[FrameData] với đầy đủ: lat, lon, alt, heading, gimbal_pitch, camera_yaw

Supported: DJI Mini 4 Pro, DJI Air, DJI Mavic (format dvtm protobuf)
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
class TelemetryPoint:
    """Một điểm telemetry đầy đủ từ DJI metadata"""
    latitude: float = 0.0        # Degrees
    longitude: float = 0.0       # Degrees
    altitude_m: float = 0.0      # Meters AGL (above ground level)
    gimbal_pitch: float = -90.0  # Degrees (-90 = nadir, 0 = horizon)
    camera_yaw: float = 0.0     # Degrees (0-360, compass heading of camera)
    heading: float = 0.0         # Degrees (0-360, direction of travel from GPS)
    offset: int = 0              # Byte offset in binary (debug)
    index: int = 0               # Record index


@dataclass
class FrameData:
    """Một keyframe + telemetry đầy đủ"""
    frame_id: int                # Số thứ tự frame trong video
    image: np.ndarray            # Ảnh BGR (OpenCV format)
    latitude: float = 0.0        # Degrees
    longitude: float = 0.0       # Degrees
    altitude_m: float = 0.0      # Meters AGL
    heading: float = 0.0         # Degrees (travel direction from GPS)
    gimbal_pitch: float = -90.0  # Degrees
    camera_yaw: float = 0.0     # Degrees (camera compass heading)
    timestamp_sec: float = 0.0   # Thời điểm trong video (giây)


@dataclass
class VideoQuality:
    """Kết quả đánh giá chất lượng video"""
    has_gps: bool = False
    gps_count: int = 0
    has_altitude: bool = False
    altitude_stable: bool = False
    altitude_avg: float = 0.0
    altitude_range: float = 0.0
    gimbal_pitch_avg: float = 0.0
    is_nadir: bool = False           # pitch < -80
    is_acceptable: bool = False      # pitch < -45
    resolution: str = ""
    fps: float = 0.0
    duration_sec: float = 0.0
    coverage_km2: float = 0.0
    score: int = 0                   # 1-5 stars
    messages: List[str] = field(default_factory=list)


@dataclass
class ExtractionResult:
    """Kết quả trích xuất"""
    frames: List[FrameData] = field(default_factory=list)
    telemetry: List[TelemetryPoint] = field(default_factory=list)
    quality: Optional[VideoQuality] = None
    total_gps_points: int = 0
    total_video_frames: int = 0
    video_duration: float = 0.0
    video_fps: float = 0.0
    video_width: int = 0
    video_height: int = 0


# ============================================================
# DJI TELEMETRY EXTRACTION
# ============================================================

class DJITelemetryExtractor:
    """
    Extract full telemetry từ DJI meta track (dvtm protobuf binary).

    Reverse-engineered format:
    - GPS: double radians, lat then lon (9 bytes apart)
    - Altitude: float32 (20-60m range), scan nearby GPS offset
    - Gimbal pitch: float32 (-90 to 0), scan nearby GPS offset
    - Camera quaternion: 4 float32, 5-byte spacing, |q|≈1.0
    - Heading: calculated from GPS bearing (not in metadata)
    """

    # Giới hạn tọa độ (buffer rộng cho toàn cầu, thu hẹp nếu cần)
    LAT_RAD_RANGE = (math.radians(5.0), math.radians(25.0))    # VN
    LON_RAD_RANGE = (math.radians(100.0), math.radians(120.0))  # VN

    MIN_OFFSET_GAP = 100  # Bytes giữa 2 GPS records

    # ── Extract meta track ──

    @staticmethod
    def extract_meta_track(video_path: str, output_path: str = "/tmp/dji_meta.bin") -> Optional[str]:
        """Extract DJI meta track từ video MP4 → file binary."""
        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", video_path,
                 "-map", "0:d:0", "-f", "data", "-c", "copy", output_path],
                capture_output=True, text=True, timeout=60
            )
            if os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
                size = os.path.getsize(output_path)
                print("[Telemetry] Extracted DJI meta: {:,} bytes".format(size))
                return output_path
            else:
                print("[Telemetry] WARNING: No DJI meta track found")
                return None
        except subprocess.TimeoutExpired:
            print("[Telemetry] ERROR: Timeout extracting meta track")
            return None
        except FileNotFoundError:
            print("[Telemetry] ERROR: ffmpeg not installed")
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

    # ── GPS scanning ──

    @staticmethod
    def _find_gps_records(data: bytes) -> List[Tuple[int, float, float]]:
        """Scan binary, tìm tất cả cặp lat/lon (radians → degrees)"""
        lat_min, lat_max = DJITelemetryExtractor.LAT_RAD_RANGE
        lon_min, lon_max = DJITelemetryExtractor.LON_RAD_RANGE
        min_gap = DJITelemetryExtractor.MIN_OFFSET_GAP

        records = []
        i = 0
        while i < len(data) - 16:
            val = struct.unpack('<d', data[i:i+8])[0]
            if lat_min < val < lat_max:
                for j in range(1, 20):
                    if i + j + 8 <= len(data):
                        val2 = struct.unpack('<d', data[i+j:i+j+8])[0]
                        if lon_min < val2 < lon_max:
                            records.append((i, math.degrees(val), math.degrees(val2)))
                            i += min_gap
                            break
                else:
                    i += 1
                    continue
                continue
            i += 1
        return records

    # ── Float scanning helpers ──

    @staticmethod
    def _read_float(data: bytes, pos: int) -> Optional[float]:
        """Read float32 at position, return None if invalid"""
        if 0 <= pos <= len(data) - 4:
            f = struct.unpack('<f', data[pos:pos+4])[0]
            if not (math.isnan(f) or math.isinf(f)):
                return f
        return None

    @staticmethod
    def _scan_altitude(data: bytes, gps_offset: int, radius: int = 150) -> float:
        """
        Tìm altitude AGL (float 20-60m, loại 47.95 FPS).
        Scan trong vùng [gps_offset - radius, gps_offset + radius].
        """
        start = max(0, gps_offset - radius)
        end = min(len(data) - 4, gps_offset + radius)

        for pos in range(start, end):
            f = struct.unpack('<f', data[pos:pos+4])[0]
            if not (math.isnan(f) or math.isinf(f)):
                if 20 < f < 200 and abs(f - 47.95) > 0.5 and abs(f - 47.952) > 0.5:
                    return f
        return 0.0

    @staticmethod
    def _scan_gimbal_pitch(data: bytes, gps_offset: int, radius: int = 150) -> float:
        """
        Tìm gimbal pitch (float -90 to -1).
        """
        start = max(0, gps_offset - radius)
        end = min(len(data) - 4, gps_offset + radius)

        for pos in range(start, end):
            f = struct.unpack('<f', data[pos:pos+4])[0]
            if not (math.isnan(f) or math.isinf(f)):
                if -90 <= f < -1:
                    return f
        return -90.0  # Default nadir

    @staticmethod
    def _scan_quaternion(data: bytes, gps_offset: int, radius: int = 250) -> Optional[Tuple[float, float, float, float]]:
        """
        Tìm camera quaternion (4 float32, 5-byte spacing, |q|≈1.0, non-trivial).
        Returns (qx, qy, qz, qw) or None.
        """
        start = max(0, gps_offset - radius)
        end = min(len(data) - 4, gps_offset + radius)

        # Index all floats in [-1.1, 1.1]
        floats = {}
        for pos in range(start, end):
            f = struct.unpack('<f', data[pos:pos+4])[0]
            if not (math.isnan(f) or math.isinf(f)) and abs(f) <= 1.1:
                floats[pos] = f

        # Find 4 floats at 5-byte intervals with |q|≈1 and non-trivial
        for pos in sorted(floats.keys()):
            positions = [pos, pos + 5, pos + 10, pos + 15]
            if all(p in floats for p in positions):
                vals = [floats[p] for p in positions]
                mag = math.sqrt(sum(v * v for v in vals))
                if 0.95 < mag < 1.05:
                    # Reject trivial (e.g. (0,0,1,0)) — need >= 2 big components
                    big = sum(1 for v in vals if abs(v) > 0.05)
                    if big >= 2:
                        return tuple(vals)
        return None

    @staticmethod
    def _quaternion_to_camera_yaw(q: Tuple[float, float, float, float]) -> float:
        """
        DJI gimbal quaternion → camera compass heading.

        DJI convention (discovered via brute-force matching):
        The quaternion represents gimbal orientation.
        Camera yaw ≈ atan2(2*(qw*qz + qx*qy), 1 - 2*(qy² + qz²)) 
        with possible offset. This gives the compass direction the camera faces.

        Note: This is CAMERA heading, not drone body heading.
        Drone heading is calculated from GPS bearing.
        """
        qx, qy, qz, qw = q
        # Standard ZYX euler yaw
        yaw = math.degrees(math.atan2(
            2 * (qw * qz + qx * qy),
            1 - 2 * (qy * qy + qz * qz)
        ))
        if yaw < 0:
            yaw += 360
        return yaw

    # ── Main extraction ──

    @staticmethod
    def parse_telemetry(binary_path: str) -> List[TelemetryPoint]:
        """
        Parse full telemetry từ DJI meta binary.

        Returns list of TelemetryPoint, mỗi point ~ 1 video frame.
        """
        with open(binary_path, 'rb') as f:
            data = f.read()

        if len(data) < 100:
            print("[Telemetry] Binary file too small")
            return []

        # Step 1: Find all GPS records
        gps_records = DJITelemetryExtractor._find_gps_records(data)
        if not gps_records:
            print("[Telemetry] No GPS records found")
            return []

        print("[Telemetry] GPS records: {}".format(len(gps_records)))

        # Step 2: Extract telemetry for each GPS record
        points = []
        for idx, (offset, lat, lon) in enumerate(gps_records):
            alt = DJITelemetryExtractor._scan_altitude(data, offset)
            gimbal = DJITelemetryExtractor._scan_gimbal_pitch(data, offset)
            quat = DJITelemetryExtractor._scan_quaternion(data, offset)

            cam_yaw = 0.0
            if quat:
                cam_yaw = DJITelemetryExtractor._quaternion_to_camera_yaw(quat)

            points.append(TelemetryPoint(
                latitude=lat,
                longitude=lon,
                altitude_m=alt,
                gimbal_pitch=gimbal,
                camera_yaw=cam_yaw,
                heading=0.0,  # Calculated below
                offset=offset,
                index=idx,
            ))

        # Step 3: Calculate heading from GPS bearing
        for i in range(len(points)):
            if i == 0:
                # Use next point's bearing for first record
                if len(points) > 1:
                    points[0].heading = _calc_bearing(
                        points[0].latitude, points[0].longitude,
                        points[1].latitude, points[1].longitude
                    )
            else:
                dlat = points[i].latitude - points[i-1].latitude
                dlon = points[i].longitude - points[i-1].longitude
                dist = math.sqrt(dlat**2 + dlon**2)
                if dist > 0.000005:  # > ~0.5m movement
                    points[i].heading = _calc_bearing(
                        points[i-1].latitude, points[i-1].longitude,
                        points[i].latitude, points[i].longitude
                    )
                else:
                    # Hovering — inherit previous heading
                    points[i].heading = points[i-1].heading

        # Stats
        alts = [p.altitude_m for p in points if p.altitude_m > 0]
        gimbals = [p.gimbal_pitch for p in points if p.gimbal_pitch < 0]
        if alts:
            print("[Telemetry] Altitude: {:.1f}m avg ({:.1f}-{:.1f}m range)".format(
                sum(alts)/len(alts), min(alts), max(alts)))
        if gimbals:
            print("[Telemetry] Gimbal pitch: {:.1f}° avg".format(sum(gimbals)/len(gimbals)))

        quats_found = sum(1 for p in points if p.camera_yaw != 0.0)
        print("[Telemetry] Quaternion: found in {}/{} records".format(quats_found, len(points)))

        return points

    @staticmethod
    def extract(video_path: str) -> List[TelemetryPoint]:
        """Pipeline hoàn chỉnh: Video → List[TelemetryPoint]"""
        if not DJITelemetryExtractor.has_dji_meta_track(video_path):
            print("[Telemetry] No DJI meta track: {}".format(video_path))
            return []

        meta_path = "/tmp/dji_meta_{}.bin".format(os.getpid())
        meta_file = DJITelemetryExtractor.extract_meta_track(video_path, meta_path)
        if not meta_file:
            return []

        try:
            return DJITelemetryExtractor.parse_telemetry(meta_file)
        finally:
            if os.path.isfile(meta_path):
                os.remove(meta_path)


# ============================================================
# VIDEO QUALITY VALIDATOR (Tier 2 — crowdsource)
# ============================================================

class VideoQualityValidator:
    """
    Đánh giá chất lượng video cho mapping.
    Output: VideoQuality với score 1-5 + messages.
    """

    @staticmethod
    def validate(video_path: str, telemetry: List[TelemetryPoint]) -> VideoQuality:
        """Đánh giá video + telemetry"""
        q = VideoQuality()

        # --- GPS ---
        q.has_gps = len(telemetry) > 0
        q.gps_count = len(telemetry)
        if not q.has_gps:
            q.score = 0
            q.messages.append("❌ Không tìm thấy GPS — không thể tạo bản đồ")
            return q

        # --- Altitude ---
        alts = [p.altitude_m for p in telemetry if p.altitude_m > 0]
        if alts:
            q.has_altitude = True
            q.altitude_avg = sum(alts) / len(alts)
            q.altitude_range = max(alts) - min(alts)
            q.altitude_stable = q.altitude_range < q.altitude_avg * 0.2  # < 20% variation

        # --- Gimbal pitch ---
        pitches = [p.gimbal_pitch for p in telemetry if p.gimbal_pitch < 0]
        if pitches:
            q.gimbal_pitch_avg = sum(pitches) / len(pitches)
            q.is_nadir = q.gimbal_pitch_avg < -80
            q.is_acceptable = q.gimbal_pitch_avg < -45

        # --- Video info ---
        try:
            cap = cv2.VideoCapture(video_path)
            q.fps = cap.get(cv2.CAP_PROP_FPS) or 0
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            q.resolution = "{}x{}".format(w, h)
            q.duration_sec = total / q.fps if q.fps > 0 else 0
            cap.release()
        except:
            pass

        # --- Coverage estimate ---
        if len(telemetry) >= 2:
            lats = [p.latitude for p in telemetry]
            lons = [p.longitude for p in telemetry]
            lat_range = max(lats) - min(lats)
            lon_range = max(lons) - min(lons)
            # Rough km² (at equator: 1° ≈ 111km)
            km_lat = lat_range * 111.0
            km_lon = lon_range * 111.0 * math.cos(math.radians(sum(lats)/len(lats)))
            q.coverage_km2 = km_lat * km_lon

        # --- Score ---
        score = 5
        if not q.has_altitude:
            score -= 1
            q.messages.append("⚠️ Không xác định được độ cao")
        elif not q.altitude_stable:
            score -= 1
            q.messages.append("⚠️ Độ cao không ổn định (dao động {:.1f}m)".format(q.altitude_range))

        if q.is_nadir:
            q.messages.append("✅ Camera nadir ({:.0f}°) — lý tưởng cho bản đồ".format(q.gimbal_pitch_avg))
        elif q.is_acceptable:
            score -= 1
            q.messages.append("⚠️ Camera nghiêng {:.0f}° — bản đồ sẽ có biến dạng rìa".format(q.gimbal_pitch_avg))
        else:
            score -= 2
            q.messages.append("❌ Camera quá nghiêng ({:.0f}°) — khuyến nghị -90° cho mapping".format(q.gimbal_pitch_avg))

        if q.gps_count < 100:
            score -= 1
            q.messages.append("⚠️ Ít GPS points ({}) — video quá ngắn?".format(q.gps_count))
        else:
            q.messages.append("✅ GPS: {} points ({:.0f}s video)".format(q.gps_count, q.duration_sec))

        if q.has_altitude:
            q.messages.append("✅ Độ cao: {:.0f}m trung bình".format(q.altitude_avg))

        if q.coverage_km2 > 0:
            q.messages.append("✅ Phạm vi ước tính: {:.3f} km²".format(q.coverage_km2))

        q.score = max(1, min(5, score))
        return q

    @staticmethod
    def print_report(q: VideoQuality):
        """In báo cáo chất lượng cho người dùng"""
        stars = "⭐" * q.score + "☆" * (5 - q.score)
        labels = {1: "POOR", 2: "LOW", 3: "ACCEPTABLE", 4: "GOOD", 5: "EXCELLENT"}

        print()
        print("╔══════════════════════════════════════════════╗")
        print("║       VIDEO QUALITY REPORT                  ║")
        print("╠══════════════════════════════════════════════╣")
        for msg in q.messages:
            print("║  {}".format(msg))
        print("╠══════════════════════════════════════════════╣")
        print("║  Quality: {} {}".format(stars, labels.get(q.score, "")))
        print("╚══════════════════════════════════════════════╝")
        print()


# ============================================================
# FRAME EXTRACTION — từ video qua NVDEC
# ============================================================

class FrameExtractor:
    """Trích keyframes từ video DJI qua NVDEC hardware decode."""

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
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=codec_name", "-of", "csv=p=0", video_path],
                capture_output=True, text=True, check=True
            )
            return result.stdout.strip().lower()
        except:
            return "h264"

    @staticmethod
    def open_video(video_path: str) -> cv2.VideoCapture:
        codec = FrameExtractor.detect_codec(video_path)

        if "hevc" in codec or "h265" in codec or "265" in codec:
            template = FrameExtractor.GSTREAMER_PIPELINES["h265"]
        else:
            template = FrameExtractor.GSTREAMER_PIPELINES["h264"]

        pipeline = template.format(path=video_path)
        print("[Video] Codec: {} | NVDEC pipeline".format(codec))

        cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        if not cap.isOpened():
            print("[Video] WARNING: GStreamer failed, fallback FFMPEG (CPU decode)")
            cap = cv2.VideoCapture(video_path, cv2.CAP_FFMPEG)

        if not cap.isOpened():
            raise RuntimeError("Cannot open video: {}".format(video_path))

        return cap

    @staticmethod
    def extract_keyframes(
        video_path: str,
        telemetry: List[TelemetryPoint],
        interval_sec: float = 1.0,
        max_frames: Optional[int] = None,
    ) -> ExtractionResult:
        """
        Trích keyframes + ghép telemetry đầy đủ.

        Args:
            video_path: Đường dẫn video DJI
            telemetry: Danh sách TelemetryPoint từ DJITelemetryExtractor
            interval_sec: Lấy 1 frame mỗi N giây
            max_frames: Giới hạn số frame
        """
        cap = FrameExtractor.open_video(video_path)

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        duration = total_frames / fps if fps > 0 else 0

        frame_interval = max(1, int(fps * interval_sec))
        tele_ratio = len(telemetry) / total_frames if total_frames > 0 else 1.0

        print("[Video] {}x{} | {:.1f}fps | {} frames | {:.1f}s".format(
            width, height, fps, total_frames, duration))
        print("[Extract] Interval: {}s = every {} frames".format(interval_sec, frame_interval))
        print("[Telemetry] {} points for {} frames (ratio {:.3f})".format(
            len(telemetry), total_frames, tele_ratio))

        result = ExtractionResult(
            telemetry=telemetry,
            total_gps_points=len(telemetry),
            total_video_frames=total_frames,
            video_duration=duration,
            video_fps=fps,
            video_width=width,
            video_height=height,
        )

        frame_count = 0
        extracted = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_count % frame_interval == 0:
                # Map frame → telemetry
                tele_idx = min(int(frame_count * tele_ratio), len(telemetry) - 1)

                if telemetry and tele_idx >= 0:
                    tp = telemetry[tele_idx]
                    fd = FrameData(
                        frame_id=frame_count,
                        image=frame,
                        latitude=tp.latitude,
                        longitude=tp.longitude,
                        altitude_m=tp.altitude_m,
                        heading=tp.heading,
                        gimbal_pitch=tp.gimbal_pitch,
                        camera_yaw=tp.camera_yaw,
                        timestamp_sec=round(frame_count / fps, 3),
                    )
                else:
                    fd = FrameData(frame_id=frame_count, image=frame)

                result.frames.append(fd)
                extracted += 1

                if max_frames and extracted >= max_frames:
                    break

            frame_count += 1

        cap.release()
        print("[Extract] Done: {} keyframes from {} frames".format(extracted, frame_count))
        return result


# ============================================================
# UTILITIES
# ============================================================

def _calc_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Tính bearing (0-360°) từ point1 → point2"""
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    bearing = math.degrees(math.atan2(dlon, dlat))
    if bearing < 0:
        bearing += 360
    return bearing


def _calc_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance (meters)"""
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon/2)**2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c


def save_keyframes(result: ExtractionResult, output_dir: str = "output/keyframes"):
    """Lưu keyframes + CSV telemetry đầy đủ"""
    os.makedirs(output_dir, exist_ok=True)

    rows = []
    for fd in result.frames:
        filename = "frame_{:06d}.jpg".format(fd.frame_id)
        filepath = os.path.join(output_dir, filename)
        cv2.imwrite(filepath, fd.image, [cv2.IMWRITE_JPEG_QUALITY, 95])

        rows.append("{},{:.7f},{:.7f},{:.1f},{:.1f},{:.1f},{:.1f},{:.3f}".format(
            filename, fd.latitude, fd.longitude,
            fd.altitude_m, fd.heading, fd.gimbal_pitch, fd.camera_yaw,
            fd.timestamp_sec))

    csv_path = os.path.join(output_dir, "telemetry.csv")
    with open(csv_path, 'w') as f:
        f.write("filename,latitude,longitude,altitude_m,heading,gimbal_pitch,camera_yaw,timestamp_sec\n")
        f.write("\n".join(rows))

    print("[Save] {} keyframes → {}".format(len(result.frames), output_dir))
    print("[Save] Telemetry CSV → {}".format(csv_path))


# ============================================================
# MAIN — TEST
# ============================================================

if __name__ == "__main__":
    import sys

    video = sys.argv[1] if len(sys.argv) > 1 else \
        "/home/nam/Video2Map/Video_drone/DJI_20260226142532_0019_D_MINHTHIEN.MP4"

    print("=" * 70)
    print("Video2Map — Frame & Telemetry Extraction v2")
    print("Video: {}".format(video))
    print("=" * 70)
    print()

    # ── Step 1: Extract telemetry ──
    print("── Step 1: Telemetry extraction ──")
    telemetry = DJITelemetryExtractor.extract(video)

    if telemetry:
        print("  First: {:.7f}, {:.7f} | alt={:.0f}m | heading={:.0f}° | gimbal={:.0f}°".format(
            telemetry[0].latitude, telemetry[0].longitude,
            telemetry[0].altitude_m, telemetry[0].heading, telemetry[0].gimbal_pitch))
        print("  Last:  {:.7f}, {:.7f} | alt={:.0f}m | heading={:.0f}° | gimbal={:.0f}°".format(
            telemetry[-1].latitude, telemetry[-1].longitude,
            telemetry[-1].altitude_m, telemetry[-1].heading, telemetry[-1].gimbal_pitch))
    print()

    # ── Step 2: Video quality check ──
    print("── Step 2: Quality validation ──")
    quality = VideoQualityValidator.validate(video, telemetry)
    VideoQualityValidator.print_report(quality)

    # ── Step 3: Extract keyframes ──
    print("── Step 3: Keyframe extraction ──")
    result = FrameExtractor.extract_keyframes(
        video_path=video,
        telemetry=telemetry,
        interval_sec=2.0,
        max_frames=10,
    )
    result.quality = quality
    print()

    # ── Results ──
    print("── Results ──")
    print("  Video: {}x{} | {:.1f}fps | {:.1f}s".format(
        result.video_width, result.video_height,
        result.video_fps, result.video_duration))
    print("  Telemetry points: {}".format(result.total_gps_points))
    print("  Keyframes: {}".format(len(result.frames)))
    print()

    print("── Keyframe details ──")
    print("{:>7} {:>8} {:>12} {:>13} {:>7} {:>8} {:>8} {:>8}".format(
        "Frame", "Time(s)", "Lat", "Lon", "Alt(m)", "Heading", "Gimbal", "CamYaw"))
    print("-" * 90)

    for fd in result.frames:
        print("{:7d} {:8.1f} {:12.7f} {:13.7f} {:7.1f} {:8.1f} {:8.1f} {:8.1f}".format(
            fd.frame_id, fd.timestamp_sec,
            fd.latitude, fd.longitude,
            fd.altitude_m, fd.heading, fd.gimbal_pitch, fd.camera_yaw))

    # ── Save ──
    print()
    save_yn = input("Save keyframes to disk? (y/n): ").strip().lower()
    if save_yn == 'y':
        save_keyframes(result, "output/keyframes_v2")

    print()
    print("=" * 70)
    print("DONE")
    print("=" * 70)
