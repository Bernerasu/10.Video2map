"""
Video2Map - Tile Mapper Module
Frame + Telemetry → Georeferenced Slippy Map tiles + Web viewer

Pipeline:
  FrameData (image + GPS + altitude + heading + gimbal_pitch)
    ├── Camera model → ground footprint (4 corners lat/lon)
    ├── Perspective warp → top-down orthophoto
    ├── Geo-projection → pixel ↔ lat/lon mapping
    ├── Tile cutting → z/x/y PNG tiles
    └── HTML viewer → Leaflet map with tile overlay

Supports: DJI Mini 4 Pro (82.1° diagonal FOV)
"""

import cv2
import os
import math
import json
import numpy as np
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Tuple, Optional


# ============================================================
# CAMERA MODEL
# ============================================================

@dataclass
class CameraModel:
    """
    Camera intrinsics for ground footprint calculation.
    
    DJI Mini 4 Pro:
      FOV diagonal: 82.1°
      Equiv focal length: 24mm (35mm equiv)
      Video mode: 3840×2160 (16:9)
      HFOV ≈ 73.7°, VFOV ≈ 46.4°
    """
    hfov_deg: float = 73.7
    vfov_deg: float = 46.4
    image_w: int = 3840
    image_h: int = 2160
    
    @property
    def hfov_rad(self):
        return math.radians(self.hfov_deg)
    
    @property
    def vfov_rad(self):
        return math.radians(self.vfov_deg)
    
    @property
    def fx(self):
        """Focal length in pixels (horizontal)"""
        return self.image_w / (2 * math.tan(self.hfov_rad / 2))
    
    @property
    def fy(self):
        """Focal length in pixels (vertical)"""
        return self.image_h / (2 * math.tan(self.vfov_rad / 2))


# ============================================================
# GROUND FOOTPRINT
# ============================================================

@dataclass
class GroundFootprint:
    """4 corners of frame projected onto ground plane"""
    corners_latlon: List[Tuple[float, float]]  # [TL, TR, BR, BL] in (lat, lon)
    corners_meters: List[Tuple[float, float]]   # [TL, TR, BR, BL] in local (x, y) meters
    center_lat: float = 0.0
    center_lon: float = 0.0
    gsd_cm: float = 0.0  # Ground Sample Distance at center
    width_m: float = 0.0
    height_m: float = 0.0


def compute_footprint(
    cam: CameraModel,
    lat: float, lon: float,
    alt_m: float,
    heading_deg: float,
    gimbal_pitch_deg: float,
) -> Optional[GroundFootprint]:
    """
    Calculate ground footprint of a camera frame.
    
    Args:
        lat, lon: Camera GPS position
        alt_m: Altitude AGL (meters)
        heading_deg: Direction of travel (0=North, 90=East)
        gimbal_pitch_deg: Camera pitch (-90=nadir, -48=tilted)
    
    Returns:
        GroundFootprint with 4 corners in lat/lon and meters
    """
    if alt_m <= 0:
        return None
    
    # Camera tilt from vertical (nadir)
    # gimbal_pitch = -90 → tilt_from_nadir = 0
    # gimbal_pitch = -48 → tilt_from_nadir = 42
    tilt_from_nadir = 90.0 + gimbal_pitch_deg  # degrees from straight down
    tilt_rad = math.radians(tilt_from_nadir)
    
    half_vfov = cam.vfov_rad / 2
    half_hfov = cam.hfov_rad / 2
    heading_rad = math.radians(heading_deg)
    
    # Ray-ground intersection for 4 corners
    # In camera-forward frame (before heading rotation):
    # Near edge: tilt - half_vfov from vertical
    # Far edge: tilt + half_vfov from vertical
    
    angle_near = tilt_rad - half_vfov  # angle from vertical to near edge
    angle_far = tilt_rad + half_vfov   # angle from vertical to far edge
    
    # Check if far edge sees above horizon (angle > 90° from vertical)
    if angle_far >= math.radians(85):
        # Clip far edge to avoid infinity
        angle_far = math.radians(85)
    
    if angle_near < 0:
        angle_near = 0  # Looking past nadir, clip
    
    # Distance along ground from nadir point (directly below drone)
    d_near = alt_m * math.tan(angle_near)
    d_far = alt_m * math.tan(angle_far)
    d_center = alt_m * math.tan(tilt_rad)
    
    # Width at near and far edges
    w_near = alt_m * math.tan(half_hfov) / math.cos(angle_near) if math.cos(angle_near) > 0.01 else 100
    w_far = alt_m * math.tan(half_hfov) / math.cos(angle_far) if math.cos(angle_far) > 0.01 else 200
    
    # Limit extreme values
    w_near = min(w_near, 500)
    w_far = min(w_far, 500)
    d_far = min(d_far, 500)
    
    # 4 corners in local frame (x=right, y=forward from nadir)
    # Camera looks in +y direction before heading rotation
    corners_local = [
        (-w_far,  d_far),    # TL (far-left)
        ( w_far,  d_far),    # TR (far-right)
        ( w_near, d_near),   # BR (near-right)
        (-w_near, d_near),   # BL (near-left)
    ]
    
    # Rotate by heading (heading=0 → camera looks North → +y = North)
    cos_h = math.cos(heading_rad)
    sin_h = math.sin(heading_rad)
    
    corners_rotated = []
    for x, y in corners_local:
        # Rotate: East = x*cos + y*sin, North = -x*sin + y*cos
        east = x * cos_h + y * sin_h
        north = -x * sin_h + y * cos_h
        corners_rotated.append((east, north))
    
    # Convert meters to lat/lon
    # 1 degree lat ≈ 111320 meters
    # 1 degree lon ≈ 111320 * cos(lat) meters
    m_per_deg_lat = 111320.0
    m_per_deg_lon = 111320.0 * math.cos(math.radians(lat))
    
    corners_latlon = []
    for east, north in corners_rotated:
        clat = lat + north / m_per_deg_lat
        clon = lon + east / m_per_deg_lon
        corners_latlon.append((clat, clon))
    
    # GSD at center
    center_ground_dist = max(d_center, 1.0)
    slant_range = math.sqrt(alt_m**2 + center_ground_dist**2)
    gsd = slant_range / cam.fy * 100  # cm/pixel
    
    # Bounding dimensions
    width_m = max(w_near, w_far) * 2
    height_m = d_far - d_near
    
    return GroundFootprint(
        corners_latlon=corners_latlon,
        corners_meters=corners_rotated,
        center_lat=lat + d_center * math.cos(heading_rad) / m_per_deg_lat,
        center_lon=lon + d_center * math.sin(heading_rad) / m_per_deg_lon,
        gsd_cm=gsd,
        width_m=width_m,
        height_m=height_m,
    )


# ============================================================
# PERSPECTIVE WARP — Tilted frame → Top-down orthophoto
# ============================================================

def warp_to_ortho(
    frame: np.ndarray,
    footprint: GroundFootprint,
    output_gsd_m: float = 0.05,  # 5cm per pixel output
    max_output_size: int = 4000,  # Max output dimension
) -> Tuple[Optional[np.ndarray], Optional[Tuple[float, float, float, float]]]:
    """
    Warp tilted frame to top-down orthophoto.
    
    Args:
        frame: BGR image
        footprint: GroundFootprint with corner coordinates
        output_gsd_m: Output Ground Sample Distance (meters/pixel)
        max_output_size: Maximum output dimension in pixels
    
    Returns:
        (warped_image, geo_bounds) where geo_bounds = (min_lat, min_lon, max_lat, max_lon)
    """
    h, w = frame.shape[:2]
    
    # Source points: 4 corners of original frame
    src_pts = np.float32([
        [0, 0],          # TL
        [w - 1, 0],      # TR
        [w - 1, h - 1],  # BR
        [0, h - 1],      # BL
    ])
    
    # Destination: corners in meters (local frame)
    corners_m = footprint.corners_meters
    
    # Convert to pixel coordinates in output image
    xs = [c[0] for c in corners_m]
    ys = [c[1] for c in corners_m]
    
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    
    range_x = max_x - min_x
    range_y = max_y - min_y
    
    if range_x <= 0 or range_y <= 0:
        return None, None
    
    # Calculate output size
    out_w = int(range_x / output_gsd_m)
    out_h = int(range_y / output_gsd_m)
    
    # Clamp to max size
    if out_w > max_output_size or out_h > max_output_size:
        scale = max_output_size / max(out_w, out_h)
        out_w = int(out_w * scale)
        out_h = int(out_h * scale)
        output_gsd_m = max(range_x / out_w, range_y / out_h)
    
    if out_w < 10 or out_h < 10:
        return None, None
    
    # Destination points in output pixels
    # Note: Y axis is flipped (North = up in map, but y increases down in image)
    dst_pts = np.float32([
        [(c[0] - min_x) / output_gsd_m, (max_y - c[1]) / output_gsd_m]
        for c in corners_m
    ])
    
    # Compute perspective transform
    M = cv2.getPerspectiveTransform(src_pts, dst_pts)
    
    # Warp
    warped = cv2.warpPerspective(
        frame, M, (out_w, out_h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0)
    )
    
    # Compute geo bounds of output image
    # Map pixel corners back to lat/lon
    lat = footprint.center_lat
    lon = footprint.center_lon
    m_per_deg_lat = 111320.0
    m_per_deg_lon = 111320.0 * math.cos(math.radians(lat))
    
    # Use footprint corners for geo bounds
    lats = [c[0] for c in footprint.corners_latlon]
    lons = [c[1] for c in footprint.corners_latlon]
    
    geo_bounds = (min(lats), min(lons), max(lats), max(lons))
    
    return warped, geo_bounds


# ============================================================
# SLIPPY MAP TILE MATH
# ============================================================

def latlon_to_tile(lat: float, lon: float, zoom: int) -> Tuple[int, int]:
    """Convert lat/lon to Slippy Map tile coordinates"""
    lat_rad = math.radians(lat)
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def tile_bounds(x: int, y: int, zoom: int) -> Tuple[float, float, float, float]:
    """Get lat/lon bounds of a tile → (min_lat, min_lon, max_lat, max_lon)"""
    n = 2 ** zoom
    lon_min = x / n * 360.0 - 180.0
    lon_max = (x + 1) / n * 360.0 - 180.0
    lat_max = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lat_min = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return lat_min, lon_min, lat_max, lon_max


def get_tiles_for_bounds(
    min_lat: float, min_lon: float,
    max_lat: float, max_lon: float,
    zoom: int
) -> List[Tuple[int, int]]:
    """Get all tile (x, y) that intersect the given bounds"""
    x_min, y_min = latlon_to_tile(max_lat, min_lon, zoom)  # Note: y increases south
    x_max, y_max = latlon_to_tile(min_lat, max_lon, zoom)
    
    tiles = []
    for x in range(x_min, x_max + 1):
        for y in range(y_min, y_max + 1):
            tiles.append((x, y))
    return tiles


# ============================================================
# TILE GENERATOR
# ============================================================

TILE_SIZE = 256  # Standard Slippy Map tile size


def generate_tiles_from_warped(
    warped: np.ndarray,
    geo_bounds: Tuple[float, float, float, float],
    zoom: int,
    tiles_dir: str,
    alpha_blend: bool = True,
) -> List[Tuple[int, int, int]]:
    """
    Cut a warped orthophoto into Slippy Map tiles.
    
    Args:
        warped: Top-down orthophoto (BGR)
        geo_bounds: (min_lat, min_lon, max_lat, max_lon)
        zoom: Zoom level
        tiles_dir: Output directory (tiles_dir/z/x/y.png)
        alpha_blend: Blend with existing tiles if present
    
    Returns:
        List of (z, x, y) tiles generated
    """
    min_lat, min_lon, max_lat, max_lon = geo_bounds
    h, w = warped.shape[:2]
    
    if h == 0 or w == 0:
        return []
    
    # Get tiles that cover this area
    tiles = get_tiles_for_bounds(min_lat, min_lon, max_lat, max_lon, zoom)
    
    generated = []
    
    for tx, ty in tiles:
        # Get this tile's geo bounds
        t_min_lat, t_min_lon, t_max_lat, t_max_lon = tile_bounds(tx, ty, zoom)
        
        # Map tile bounds to pixel coordinates in warped image
        # Warped image: x → longitude, y → latitude (top=max_lat, bottom=min_lat)
        
        lat_range = max_lat - min_lat
        lon_range = max_lon - min_lon
        
        if lat_range <= 0 or lon_range <= 0:
            continue
        
        # Pixel coordinates in warped image for this tile's corners
        px_left = (t_min_lon - min_lon) / lon_range * w
        px_right = (t_max_lon - min_lon) / lon_range * w
        px_top = (max_lat - t_max_lat) / lat_range * h
        px_bottom = (max_lat - t_min_lat) / lat_range * h
        
        # Clamp to image bounds
        src_x1 = max(0, int(px_left))
        src_x2 = min(w, int(px_right))
        src_y1 = max(0, int(px_top))
        src_y2 = min(h, int(px_bottom))
        
        if src_x2 <= src_x1 or src_y2 <= src_y1:
            continue
        
        # Extract region from warped image
        region = warped[src_y1:src_y2, src_x1:src_x2]
        
        if region.size == 0:
            continue
        
        # Check if region has actual content (not all black)
        if region.mean() < 2:
            continue
        
        # Resize to tile size, accounting for partial coverage
        # Calculate where in the tile this region falls
        tile_px_left = (src_x1 / w * lon_range + min_lon - t_min_lon) / (t_max_lon - t_min_lon) * TILE_SIZE
        tile_px_right = (src_x2 / w * lon_range + min_lon - t_min_lon) / (t_max_lon - t_min_lon) * TILE_SIZE
        tile_px_top = (1 - (max_lat - src_y1 / h * lat_range - t_min_lat) / (t_max_lat - t_min_lat)) * TILE_SIZE
        tile_px_bottom = (1 - (max_lat - src_y2 / h * lat_range - t_min_lat) / (t_max_lat - t_min_lat)) * TILE_SIZE
        
        dst_x1 = max(0, int(tile_px_left))
        dst_x2 = min(TILE_SIZE, int(tile_px_right))
        dst_y1 = max(0, int(tile_px_top))
        dst_y2 = min(TILE_SIZE, int(tile_px_bottom))
        
        dst_w = dst_x2 - dst_x1
        dst_h = dst_y2 - dst_y1
        
        if dst_w <= 0 or dst_h <= 0:
            continue
        
        # Resize region to fit tile portion
        resized = cv2.resize(region, (dst_w, dst_h), interpolation=cv2.INTER_LINEAR)
        
        # Create or load existing tile
        tile_path = os.path.join(tiles_dir, str(zoom), str(tx), "{}.png".format(ty))
        os.makedirs(os.path.dirname(tile_path), exist_ok=True)
        
        if alpha_blend and os.path.isfile(tile_path):
            tile_img = cv2.imread(tile_path, cv2.IMREAD_UNCHANGED)
            if tile_img is None:
                tile_img = np.zeros((TILE_SIZE, TILE_SIZE, 4), dtype=np.uint8)
        else:
            tile_img = np.zeros((TILE_SIZE, TILE_SIZE, 4), dtype=np.uint8)
        
        # Create alpha mask for the new region (non-black pixels)
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY) if len(resized.shape) == 3 else resized
        mask = (gray > 5).astype(np.uint8) * 255
        
        # Place on tile with alpha
        roi = tile_img[dst_y1:dst_y2, dst_x1:dst_x2]
        
        for c in range(3):
            if len(resized.shape) == 3:
                new_pixels = resized[:, :, c]
            else:
                new_pixels = resized
            
            existing = roi[:, :, c]
            existing_alpha = roi[:, :, 3]
            
            # Where we have new content, blend or overwrite
            has_new = mask > 0
            has_old = existing_alpha > 0
            
            # Simple overwrite for now (later: proper blending)
            result = existing.copy()
            result[has_new] = new_pixels[has_new]
            roi[:, :, c] = result
        
        # Update alpha
        new_alpha = roi[:, :, 3].copy()
        new_alpha[mask > 0] = 255
        roi[:, :, 3] = new_alpha
        
        tile_img[dst_y1:dst_y2, dst_x1:dst_x2] = roi
        
        cv2.imwrite(tile_path, tile_img)
        generated.append((zoom, tx, ty))
    
    return generated


# ============================================================
# HTML VIEWER GENERATOR
# ============================================================

def generate_viewer_html(
    tiles_dir: str,
    center_lat: float,
    center_lon: float,
    zoom: int = 18,
    footprints: Optional[List[GroundFootprint]] = None,
    output_path: str = "viewer.html",
):
    """Generate Leaflet HTML viewer for the generated tiles"""
    
    # Build footprint polygons for visualization
    footprint_js = "[]"
    if footprints:
        polys = []
        for fp in footprints:
            coords = [[c[0], c[1]] for c in fp.corners_latlon]
            coords.append(coords[0])  # Close polygon
            polys.append(coords)
        footprint_js = json.dumps(polys)
    
    html = """<!DOCTYPE html>
<html>
<head>
<title>Video2Map - Tile Viewer</title>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  body {{ margin: 0; padding: 0; }}
  #map {{ position: absolute; top: 0; bottom: 0; width: 100%; }}
  .info-panel {{
    position: absolute; top: 10px; right: 10px; z-index: 1000;
    background: rgba(0,0,0,0.8); color: white; padding: 12px 16px;
    border-radius: 8px; font-family: monospace; font-size: 13px;
    max-width: 300px;
  }}
  .info-panel h3 {{ margin: 0 0 8px 0; color: #4CAF50; }}
  .legend {{ margin-top: 8px; }}
  .legend span {{ display: inline-block; width: 16px; height: 3px; margin-right: 6px; vertical-align: middle; }}
</style>
</head>
<body>
<div id="map"></div>
<div class="info-panel">
  <h3>Video2Map</h3>
  <div id="info">Tiles: loading...</div>
  <div class="legend">
    <span style="background:#ff4444"></span>Frame footprint<br>
    <span style="background:#44ff44"></span>Drone path
  </div>
</div>
<script>
var map = L.map('map').setView([{center_lat}, {center_lon}], {zoom});

// Base map: OpenStreetMap
L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  maxZoom: 22,
  attribution: '&copy; OpenStreetMap'
}}).addTo(map);

// Our drone tiles overlay
var droneLayer = L.tileLayer('./tiles/{{z}}/{{x}}/{{y}}.png', {{
  maxZoom: 22,
  minZoom: 15,
  opacity: 0.85,
  errorTileUrl: '',
  tms: false,
}}).addTo(map);

// Footprint polygons
var footprints = {footprints_json};
var fpGroup = L.layerGroup().addTo(map);
footprints.forEach(function(coords) {{
  L.polygon(coords, {{
    color: '#ff4444', weight: 1, fillOpacity: 0.05, dashArray: '4,4'
  }}).addTo(fpGroup);
}});

// Drone path (center points)
if (footprints.length > 1) {{
  var pathPoints = footprints.map(function(coords) {{
    var lat = coords.reduce(function(s,c){{return s+c[0]}}, 0) / coords.length;
    var lon = coords.reduce(function(s,c){{return s+c[1]}}, 0) / coords.length;
    return [lat, lon];
  }});
  L.polyline(pathPoints, {{color: '#44ff44', weight: 2}}).addTo(map);
  
  // Start/end markers
  L.circleMarker(pathPoints[0], {{radius: 6, color: '#44ff44', fillOpacity: 1}})
    .bindPopup('START').addTo(map);
  L.circleMarker(pathPoints[pathPoints.length-1], {{radius: 6, color: '#ff4444', fillOpacity: 1}})
    .bindPopup('END').addTo(map);
}}

// Layer control
L.control.layers({{'OSM': L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png')}},
  {{'Drone Tiles': droneLayer, 'Footprints': fpGroup}}).addTo(map);

document.getElementById('info').innerHTML = 'Frames: ' + footprints.length + '<br>Zoom: ' + {zoom};
</script>
</body>
</html>""".format(
        center_lat=center_lat,
        center_lon=center_lon,
        zoom=zoom,
        footprints_json=footprint_js,
    )
    
    with open(output_path, 'w') as f:
        f.write(html)
    
    print("[Viewer] Generated: {}".format(output_path))


# ============================================================
# MAIN PIPELINE
# ============================================================

def process_frames(
    frames_dir: str,
    telemetry_csv: str,
    output_dir: str = "output/map",
    zoom_levels: List[int] = [18, 19],
    altitude_override: float = 0,  # Override if telemetry altitude is wrong
    skip_dark_frames: bool = True,
):
    """
    Main pipeline: keyframes + telemetry → tiles + viewer
    
    Args:
        frames_dir: Directory containing frame_NNNNNN.jpg files
        telemetry_csv: Path to telemetry.csv
        output_dir: Output directory for tiles and viewer
        zoom_levels: Slippy Map zoom levels to generate
        altitude_override: Override altitude (0 = use telemetry)
        skip_dark_frames: Skip frames with mean pixel < 5
    """
    cam = CameraModel()
    
    # ── Read telemetry ──
    print("── Reading telemetry ──")
    
    import csv
    frames_data = []
    
    with open(telemetry_csv, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            frames_data.append(row)
    
    print("  {} records from CSV".format(len(frames_data)))
    
    # ── Process each frame ──
    print("\n── Processing frames ──")
    
    tiles_dir = os.path.join(output_dir, "tiles")
    os.makedirs(tiles_dir, exist_ok=True)
    
    all_footprints = []
    total_tiles = 0
    all_lats = []
    all_lons = []
    
    for i, row in enumerate(frames_data):
        filename = row['filename']
        frame_path = os.path.join(frames_dir, filename)
        
        if not os.path.isfile(frame_path):
            print("  [SKIP] {} — file not found".format(filename))
            continue
        
        # Read frame
        frame = cv2.imread(frame_path)
        if frame is None:
            print("  [SKIP] {} — cannot read".format(filename))
            continue
        
        # Skip dark frames
        if skip_dark_frames and frame.mean() < 5:
            print("  [SKIP] {} — too dark (mean={:.1f})".format(filename, frame.mean()))
            continue
        
        # Parse telemetry
        lat = float(row['latitude'])
        lon = float(row['longitude'])
        alt = altitude_override if altitude_override > 0 else float(row['altitude_m'])
        heading = float(row['heading'])
        gimbal = float(row['gimbal_pitch'])
        
        if alt <= 0:
            alt = 44.0  # Default fallback
            print("  [WARN] {} — altitude=0, using default 44m".format(filename))
        
        all_lats.append(lat)
        all_lons.append(lon)
        
        # ── Compute footprint ──
        fp = compute_footprint(cam, lat, lon, alt, heading, gimbal)
        if fp is None:
            print("  [SKIP] {} — cannot compute footprint".format(filename))
            continue
        
        all_footprints.append(fp)
        
        print("  [{}/{}] {} | GPS: {:.6f},{:.6f} | alt={:.0f}m h={:.0f}° g={:.0f}° | GSD={:.1f}cm | {:.0f}×{:.0f}m".format(
            i + 1, len(frames_data), filename,
            lat, lon, alt, heading, gimbal,
            fp.gsd_cm, fp.width_m, fp.height_m))
        
        # ── Warp to orthophoto ──
        warped, geo_bounds = warp_to_ortho(frame, fp)
        
        if warped is None:
            print("    [WARN] Warp failed")
            continue
        
        print("    Warped: {}×{} | bounds: {:.6f},{:.6f} → {:.6f},{:.6f}".format(
            warped.shape[1], warped.shape[0],
            geo_bounds[0], geo_bounds[1], geo_bounds[2], geo_bounds[3]))
        
        # ── Generate tiles for each zoom level ──
        for z in zoom_levels:
            tiles = generate_tiles_from_warped(warped, geo_bounds, z, tiles_dir)
            total_tiles += len(tiles)
            if tiles:
                print("    z={}: {} tiles".format(z, len(tiles)))
    
    # ── Generate viewer ──
    print("\n── Generating viewer ──")
    
    if all_lats:
        center_lat = sum(all_lats) / len(all_lats)
        center_lon = sum(all_lons) / len(all_lons)
    else:
        center_lat, center_lon = 10.832, 106.819  # Default
    
    viewer_path = os.path.join(output_dir, "viewer.html")
    generate_viewer_html(
        tiles_dir=tiles_dir,
        center_lat=center_lat,
        center_lon=center_lon,
        zoom=max(zoom_levels),
        footprints=all_footprints,
        output_path=viewer_path,
    )
    
    # ── Summary ──
    print("\n" + "=" * 60)
    print("TILE GENERATION COMPLETE")
    print("=" * 60)
    print("  Frames processed: {} / {}".format(len(all_footprints), len(frames_data)))
    print("  Total tiles: {}".format(total_tiles))
    print("  Zoom levels: {}".format(zoom_levels))
    print("  Output: {}".format(output_dir))
    print("  Viewer: {}".format(viewer_path))
    print()
    print("  To view: open {} in browser".format(viewer_path))
    print("  Or serve: cd {} && python3 -m http.server 8000".format(output_dir))
    print("=" * 60)


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import sys
    
    # Default paths (Jetson)
    frames_dir = "output/keyframes_v2"
    telemetry_csv = "output/keyframes_v2/telemetry.csv"
    output_dir = "output/map"
    zoom_levels = [18, 19]
    altitude_override = 44.0  # Override buggy 100m altitude
    
    # Parse simple args
    if len(sys.argv) > 1:
        frames_dir = sys.argv[1]
    if len(sys.argv) > 2:
        telemetry_csv = sys.argv[2]
    if len(sys.argv) > 3:
        output_dir = sys.argv[3]
    
    print("=" * 60)
    print("Video2Map — Tile Mapper")
    print("=" * 60)
    print("  Frames:    {}".format(frames_dir))
    print("  Telemetry: {}".format(telemetry_csv))
    print("  Output:    {}".format(output_dir))
    print("  Zoom:      {}".format(zoom_levels))
    print("  Alt override: {}m".format(altitude_override))
    print("=" * 60)
    print()
    
    process_frames(
        frames_dir=frames_dir,
        telemetry_csv=telemetry_csv,
        output_dir=output_dir,
        zoom_levels=zoom_levels,
        altitude_override=altitude_override,
    )
