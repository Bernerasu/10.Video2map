"""
Video2Map - Georeferencing & Tile Generation
Gắn tọa độ GPS lên panorama → Cắt thành map tiles (z/x/y)

Pipeline:
  Panorama + GPS points → Tính bounding box → Georeferenced image
  → Cắt thành tiles 256x256 → Lưu theo cấu trúc {z}/{x}/{y}.png

Chuẩn tiles: Slippy Map (OpenStreetMap / Leaflet / Google Maps)
"""

import cv2
import numpy as np
import os
import math
import shutil
from dataclasses import dataclass, field
from typing import List, Tuple, Optional


# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class GeoBounds:
    """Bounding box địa lý của panorama"""
    lat_min: float = 0.0
    lat_max: float = 0.0
    lon_min: float = 0.0
    lon_max: float = 0.0
    
    @property
    def center_lat(self) -> float:
        return (self.lat_min + self.lat_max) / 2
    
    @property
    def center_lon(self) -> float:
        return (self.lon_min + self.lon_max) / 2
    
    @property
    def width_meters(self) -> float:
        """Chiều rộng (mét)"""
        return _haversine(self.center_lat, self.lon_min, self.center_lat, self.lon_max)
    
    @property
    def height_meters(self) -> float:
        """Chiều cao (mét)"""
        return _haversine(self.lat_min, self.center_lon, self.lat_max, self.center_lon)


@dataclass
class TileResult:
    """Kết quả tạo tiles"""
    output_dir: str = ""
    total_tiles: int = 0
    zoom_levels: List[int] = field(default_factory=list)
    bounds: Optional[GeoBounds] = None
    tile_size: int = 256


# ============================================================
# GEOREFERENCING
# ============================================================

class GeoReferencer:
    """
    Gắn tọa độ GPS lên panorama.
    
    Cách tính: Dùng GPS từ keyframes → bounding box → 
    mở rộng thêm margin (vì panorama rộng hơn vùng GPS trung tâm)
    """
    
    @staticmethod
    def calculate_bounds(
        gps_points: List[Tuple[float, float]],
        panorama_shape: Tuple[int, int],
        margin_percent: float = 30.0
    ) -> GeoBounds:
        """
        Tính bounding box địa lý từ GPS points.
        
        Args:
            gps_points: List[(lat, lon)] từ keyframes
            panorama_shape: (height, width) của panorama
            margin_percent: % mở rộng ra ngoài GPS range
                           (vì mỗi frame nhìn rộng hơn tâm GPS)
        
        Returns:
            GeoBounds
        """
        if not gps_points:
            raise ValueError("Không có GPS points")
        
        lats = [p[0] for p in gps_points]
        lons = [p[1] for p in gps_points]
        
        lat_min, lat_max = min(lats), max(lats)
        lon_min, lon_max = min(lons), max(lons)
        
        # Mở rộng margin (mỗi frame nhìn rộng hơn tâm GPS)
        lat_range = lat_max - lat_min
        lon_range = lon_max - lon_min
        
        # Đảm bảo range tối thiểu (drone đứng yên)
        if lat_range < 0.0001:
            lat_range = 0.001
        if lon_range < 0.0001:
            lon_range = 0.001
        
        margin = margin_percent / 100.0
        lat_margin = lat_range * margin
        lon_margin = lon_range * margin
        
        # Điều chỉnh theo tỷ lệ panorama
        pano_h, pano_w = panorama_shape[:2]
        aspect = pano_w / pano_h if pano_h > 0 else 1.0
        
        # Đảm bảo bounds có cùng aspect ratio với panorama
        geo_width = lon_range + 2 * lon_margin
        geo_height = lat_range + 2 * lat_margin
        
        geo_aspect = geo_width / (geo_height * math.cos(math.radians((lat_min+lat_max)/2)))
        
        if geo_aspect < aspect:
            # Cần mở rộng longitude
            needed_width = geo_height * aspect * math.cos(math.radians((lat_min+lat_max)/2))
            lon_margin = (needed_width - lon_range) / 2
        else:
            # Cần mở rộng latitude
            needed_height = geo_width / (aspect * math.cos(math.radians((lat_min+lat_max)/2)))
            lat_margin = (needed_height - lat_range) / 2
        
        bounds = GeoBounds(
            lat_min=lat_min - lat_margin,
            lat_max=lat_max + lat_margin,
            lon_min=lon_min - lon_margin,
            lon_max=lon_max + lon_margin,
        )
        
        print(f"[Georef] Bounds:")
        print(f"  Lat: {bounds.lat_min:.7f} → {bounds.lat_max:.7f}")
        print(f"  Lon: {bounds.lon_min:.7f} → {bounds.lon_max:.7f}")
        print(f"  Center: {bounds.center_lat:.7f}, {bounds.center_lon:.7f}")
        print(f"  Size: {bounds.width_meters:.0f}m × {bounds.height_meters:.0f}m")
        
        return bounds


# ============================================================
# TILE GENERATION
# ============================================================

class TileGenerator:
    """
    Cắt panorama thành map tiles theo chuẩn Slippy Map.
    
    Chuẩn: z/x/y.png
    - z = zoom level (0-20)
    - x = tile column (0 → 2^z - 1)
    - y = tile row (0 → 2^z - 1)
    - Mỗi tile = 256×256 pixels
    """
    
    TILE_SIZE = 256
    
    @staticmethod
    def generate(
        panorama: np.ndarray,
        bounds: GeoBounds,
        output_dir: str = "output/tiles",
        zoom_range: Optional[Tuple[int, int]] = None,
    ) -> TileResult:
        """
        Tạo map tiles từ panorama.
        
        Args:
            panorama: Ảnh panorama BGR
            bounds: Bounding box địa lý
            output_dir: Thư mục output
            zoom_range: (min_zoom, max_zoom), None = tự tính
        
        Returns:
            TileResult
        """
        result = TileResult(
            output_dir=output_dir,
            bounds=bounds,
            tile_size=TileGenerator.TILE_SIZE,
        )
        
        # Xóa tiles cũ
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir)
        os.makedirs(output_dir, exist_ok=True)
        
        pano_h, pano_w = panorama.shape[:2]
        
        # Tính zoom range phù hợp
        if zoom_range is None:
            zoom_range = TileGenerator._calc_zoom_range(bounds, pano_w)
        
        min_zoom, max_zoom = zoom_range
        print(f"[Tiles] Panorama: {pano_w}x{pano_h}")
        print(f"[Tiles] Zoom levels: {min_zoom} → {max_zoom}")
        
        total_tiles = 0
        
        for z in range(min_zoom, max_zoom + 1):
            # Tính tile range cho zoom level này
            x_min, y_min = TileGenerator._latlon_to_tile(bounds.lat_max, bounds.lon_min, z)
            x_max, y_max = TileGenerator._latlon_to_tile(bounds.lat_min, bounds.lon_max, z)
            
            tiles_this_zoom = 0
            
            for x in range(x_min, x_max + 1):
                for y in range(y_min, y_max + 1):
                    tile = TileGenerator._render_tile(panorama, bounds, z, x, y)
                    
                    if tile is not None:
                        # Lưu tile
                        tile_dir = os.path.join(output_dir, str(z), str(x))
                        os.makedirs(tile_dir, exist_ok=True)
                        tile_path = os.path.join(tile_dir, f"{y}.png")
                        cv2.imwrite(tile_path, tile)
                        tiles_this_zoom += 1
            
            total_tiles += tiles_this_zoom
            result.zoom_levels.append(z)
            print(f"  Zoom {z}: {tiles_this_zoom} tiles "
                  f"(x: {x_min}-{x_max}, y: {y_min}-{y_max})")
        
        result.total_tiles = total_tiles
        print(f"[Tiles] Tổng: {total_tiles} tiles → {output_dir}")
        
        # Tạo metadata file cho web viewer
        TileGenerator._save_metadata(result, output_dir)
        
        return result
    
    @staticmethod
    def _calc_zoom_range(bounds: GeoBounds, pano_width: int) -> Tuple[int, int]:
        """Tính zoom range phù hợp dựa trên kích thước panorama"""
        # Tính resolution (meters/pixel) của panorama
        meters_per_pixel = bounds.width_meters / pano_width if pano_width > 0 else 1.0
        
        # Tính max zoom: 1 tile pixel ≈ panorama pixel
        # Tại zoom z, 1 pixel ≈ 156543.03 * cos(lat) / 2^z mét
        cos_lat = math.cos(math.radians(bounds.center_lat))
        
        max_zoom = 15  # Default
        for z in range(20, 10, -1):
            tile_mpp = 156543.03 * cos_lat / (2**z)
            if tile_mpp <= meters_per_pixel * 2:
                max_zoom = z
                break
        
        # Min zoom: panorama chiếm ít nhất 1 tile
        min_zoom = max(max_zoom - 4, 10)
        
        return (min_zoom, max_zoom)
    
    @staticmethod
    def _latlon_to_tile(lat: float, lon: float, zoom: int) -> Tuple[int, int]:
        """Chuyển lat/lon → tile x/y tại zoom level"""
        lat_rad = math.radians(lat)
        n = 2.0 ** zoom
        x = int((lon + 180.0) / 360.0 * n)
        y = int((1.0 - math.log(math.tan(lat_rad) + 1.0/math.cos(lat_rad)) / math.pi) / 2.0 * n)
        return (x, y)
    
    @staticmethod
    def _tile_to_latlon(x: int, y: int, zoom: int) -> Tuple[float, float]:
        """Chuyển tile x/y → lat/lon (góc trên-trái của tile)"""
        n = 2.0 ** zoom
        lon = x / n * 360.0 - 180.0
        lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
        lat = math.degrees(lat_rad)
        return (lat, lon)
    
    @staticmethod
    def _render_tile(
        panorama: np.ndarray,
        bounds: GeoBounds,
        z: int, x: int, y: int
    ) -> Optional[np.ndarray]:
        """
        Render 1 tile từ panorama.
        
        Cắt vùng tương ứng từ panorama, resize thành 256x256.
        """
        tile_size = TileGenerator.TILE_SIZE
        pano_h, pano_w = panorama.shape[:2]
        
        # Tọa độ góc của tile
        tile_lat_max, tile_lon_min = TileGenerator._tile_to_latlon(x, y, z)
        tile_lat_min, tile_lon_max = TileGenerator._tile_to_latlon(x+1, y+1, z)
        
        # Chuyển tọa độ tile → pixel trên panorama
        px_left = (tile_lon_min - bounds.lon_min) / (bounds.lon_max - bounds.lon_min) * pano_w
        px_right = (tile_lon_max - bounds.lon_min) / (bounds.lon_max - bounds.lon_min) * pano_w
        px_top = (bounds.lat_max - tile_lat_max) / (bounds.lat_max - bounds.lat_min) * pano_h
        px_bottom = (bounds.lat_max - tile_lat_min) / (bounds.lat_max - bounds.lat_min) * pano_h
        
        # Làm tròn
        px_left = int(px_left)
        px_right = int(px_right)
        px_top = int(px_top)
        px_bottom = int(px_bottom)
        
        # Kiểm tra tile có nằm trong panorama không
        if px_right <= 0 or px_left >= pano_w or px_bottom <= 0 or px_top >= pano_h:
            return None
        
        # Clamp vào bounds panorama
        src_left = max(0, px_left)
        src_right = min(pano_w, px_right)
        src_top = max(0, px_top)
        src_bottom = min(pano_h, px_bottom)
        
        # Cắt vùng từ panorama
        crop = panorama[src_top:src_bottom, src_left:src_right]
        
        if crop.size == 0:
            return None
        
        # Tạo tile với background trong suốt (PNG)
        tile = np.zeros((tile_size, tile_size, 4), dtype=np.uint8)  # BGRA
        
        # Tính vị trí paste trên tile
        dst_left = int((src_left - px_left) / (px_right - px_left) * tile_size) if px_right != px_left else 0
        dst_top = int((src_top - px_top) / (px_bottom - px_top) * tile_size) if px_bottom != px_top else 0
        dst_right = int((src_right - px_left) / (px_right - px_left) * tile_size) if px_right != px_left else tile_size
        dst_bottom = int((src_top - px_top + (src_bottom - src_top)) / (px_bottom - px_top) * tile_size) if px_bottom != px_top else tile_size
        
        # Clamp
        dst_left = max(0, min(tile_size, dst_left))
        dst_right = max(0, min(tile_size, dst_right))
        dst_top = max(0, min(tile_size, dst_top))
        dst_bottom = max(0, min(tile_size, dst_bottom))
        
        dst_w = dst_right - dst_left
        dst_h = dst_bottom - dst_top
        
        if dst_w <= 0 or dst_h <= 0:
            return None
        
        # Resize crop vào vùng destination
        resized = cv2.resize(crop, (dst_w, dst_h), interpolation=cv2.INTER_AREA)
        
        # Paste vào tile (BGR → BGRA)
        if resized.shape[2] == 3:
            # Tạo alpha channel: pixel đen (từ panorama border) → transparent
            gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
            alpha = np.where(gray > 5, 255, 0).astype(np.uint8)
            
            tile[dst_top:dst_bottom, dst_left:dst_right, :3] = resized
            tile[dst_top:dst_bottom, dst_left:dst_right, 3] = alpha
        
        # Kiểm tra tile có nội dung không (không phải toàn transparent)
        if tile[:, :, 3].sum() < 100:
            return None
        
        return tile
    
    @staticmethod
    def _save_metadata(result: TileResult, output_dir: str):
        """Lưu metadata cho web viewer"""
        if result.bounds is None:
            return
        
        b = result.bounds
        metadata = {
            "bounds": [[b.lat_min, b.lon_min], [b.lat_max, b.lon_max]],
            "center": [b.center_lat, b.center_lon],
            "zoom_min": min(result.zoom_levels) if result.zoom_levels else 15,
            "zoom_max": max(result.zoom_levels) if result.zoom_levels else 18,
            "total_tiles": result.total_tiles,
            "tile_size": result.tile_size,
        }
        
        import json
        meta_path = os.path.join(output_dir, "metadata.json")
        with open(meta_path, 'w') as f:
            json.dump(metadata, f, indent=2)
        
        print(f"[Tiles] Metadata → {meta_path}")


# ============================================================
# UTILITIES
# ============================================================

def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Khoảng cách giữa 2 tọa độ (mét)"""
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2 + 
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * 
         math.sin(dlon/2)**2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


# ============================================================
# MAIN — TEST
# ============================================================

if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from frame_extract import DJIGPSExtractor, FrameExtractor
    from stitching import stitch_frames, save_panorama
    
    video = sys.argv[1] if len(sys.argv) > 1 else \
        "/home/nam/Video2Map/Video_drone/DJI_20260226142532_0019_D_MINHTHIEN.MP4"
    
    print(f"{'='*60}")
    print(f"Video2Map — Georef & Tile Generation")
    print(f"{'='*60}\n")
    
    # Bước 1: GPS
    print("── Bước 1: GPS ──")
    gps_points = DJIGPSExtractor.extract(video)
    
    # Bước 2: Keyframes
    print("\n── Bước 2: Keyframes ──")
    extract_result = FrameExtractor.extract_keyframes(
        video_path=video,
        gps_points=gps_points,
        interval_sec=2.0,
        max_frames=20,
    )
    
    # Bước 3: Stitch
    print("\n── Bước 3: Stitching ──")
    frames = [fd.image for fd in extract_result.frames]
    stitch_result = stitch_frames(frames, method="auto")
    
    if not stitch_result.success:
        print("❌ Stitching thất bại!")
        sys.exit(1)
    
    save_panorama(stitch_result, "output/panorama.jpg")
    
    # Bước 4: Georeferencing
    print("\n── Bước 4: Georeferencing ──")
    gps_for_georef = [(fd.latitude, fd.longitude) for fd in extract_result.frames]
    bounds = GeoReferencer.calculate_bounds(
        gps_for_georef, stitch_result.panorama.shape
    )
    
    # Bước 5: Tile Generation
    print("\n── Bước 5: Tile Generation ──")
    tile_result = TileGenerator.generate(
        panorama=stitch_result.panorama,
        bounds=bounds,
        output_dir="output/tiles",
    )
    
    # Kết quả
    print(f"\n{'='*60}")
    print(f"KẾT QUẢ")
    print(f"{'='*60}")
    print(f"  Panorama: {stitch_result.panorama_size[0]}x{stitch_result.panorama_size[1]}")
    print(f"  Bounds: {bounds.lat_min:.5f},{bounds.lon_min:.5f} → {bounds.lat_max:.5f},{bounds.lon_max:.5f}")
    print(f"  Center: {bounds.center_lat:.7f}, {bounds.center_lon:.7f}")
    print(f"  Tiles: {tile_result.total_tiles} (zoom {tile_result.zoom_levels})")
    print(f"  Output: {tile_result.output_dir}/")
    print(f"\n  → Tiếp: chạy web_server.py để xem trên browser")
    print(f"{'='*60}")
