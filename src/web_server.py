"""
Video2Map - Web Map Server
FastAPI serve tiles + Leaflet.js hiển thị bản đồ

Chạy:
  python3 web_server.py
  → Mở browser: http://192.168.88.123:8000
"""

import os
import json
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse


# ============================================================
# CONFIG
# ============================================================

BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
TILES_DIR = os.path.join(BASE_DIR, "output", "tiles")
GPS_LOG = os.path.join(BASE_DIR, "output", "keyframes", "gps_log.csv")
PANORAMA_PATH = os.path.join(BASE_DIR, "output", "panorama.jpg")
HOST = "0.0.0.0"
PORT = 8000


# ============================================================
# HTML TEMPLATE (plain string — không phải f-string)
# ============================================================

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Video2Map — Bản đồ từ Drone</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Arial, sans-serif; }
        #map { width: 100%; height: 100vh; }
        
        .info-panel {
            position: absolute; top: 10px; right: 10px; z-index: 1000;
            background: rgba(0,0,0,0.85); color: #fff;
            padding: 15px 20px; border-radius: 10px;
            font-size: 13px; line-height: 1.6; min-width: 220px;
        }
        .info-panel h3 { color: #4CAF50; margin-bottom: 8px; font-size: 16px; }
        .info-panel .label { color: #aaa; }
        .info-panel .value { color: #fff; font-weight: 600; }
        
        .status {
            position: absolute; bottom: 30px; left: 50%; transform: translateX(-50%);
            z-index: 1000; background: rgba(0,0,0,0.75); color: #4CAF50;
            padding: 8px 20px; border-radius: 20px; font-size: 12px; font-weight: 600;
        }
        
        .layer-toggle {
            position: absolute; top: 10px; left: 55px; z-index: 1000;
            background: rgba(0,0,0,0.85); color: #fff;
            padding: 10px 15px; border-radius: 10px; font-size: 12px;
        }
        .layer-toggle label { display: block; margin: 4px 0; cursor: pointer; }
        .layer-toggle input { margin-right: 6px; }
    </style>
</head>
<body>
    <div id="map"></div>
    
    <div class="info-panel">
        <h3>&#x1F6F8; Video2Map</h3>
        <div><span class="label">Tọa độ:</span> <span class="value" id="coord">—</span></div>
        <div><span class="label">Zoom:</span> <span class="value" id="zoom-level">—</span></div>
        <div><span class="label">GPS points:</span> <span class="value" id="gps-count">—</span></div>
        <div><span class="label">Tiles:</span> <span class="value" id="tile-count">—</span></div>
    </div>
    
    <div class="layer-toggle">
        <strong>Layers</strong>
        <label><input type="checkbox" id="toggle-drone" checked> Drone Map</label>
        <label><input type="checkbox" id="toggle-osm" checked> OpenStreetMap</label>
        <label><input type="checkbox" id="toggle-track" checked> GPS Track</label>
    </div>
    
    <div class="status">Video2Map v0.1 — Jetson Orin Nano | Bản đồ từ drone video</div>
    
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script>
        // Config inject từ server
        const CONFIG = __CONFIG_JSON__;
        
        // Map
        const map = L.map('map', {
            center: CONFIG.center,
            zoom: CONFIG.zoom_max - 1,
            minZoom: 5,
            maxZoom: 22,
        });
        
        // OpenStreetMap base
        const osmLayer = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 22,
            attribution: '&copy; OpenStreetMap contributors'
        }).addTo(map);
        
        // Drone tiles overlay
        const droneTiles = L.tileLayer('/tiles/{z}/{x}/{y}.png', {
            minZoom: CONFIG.zoom_min,
            maxZoom: CONFIG.zoom_max,
            opacity: 0.9,
            attribution: 'Video2Map',
        }).addTo(map);
        
        // GPS Track
        const gpsTrack = CONFIG.gps_track;
        let trackLayer = null;
        
        if (gpsTrack.length > 0) {
            trackLayer = L.polyline(gpsTrack, {
                color: '#FF5722', weight: 3, opacity: 0.8, dashArray: '10, 5',
            }).addTo(map);
            
            L.circleMarker(gpsTrack[0], {
                radius: 6, color: '#4CAF50', fillColor: '#4CAF50', fillOpacity: 1
            }).addTo(map).bindPopup('Bắt đầu');
            
            L.circleMarker(gpsTrack[gpsTrack.length - 1], {
                radius: 6, color: '#f44336', fillColor: '#f44336', fillOpacity: 1
            }).addTo(map).bindPopup('Kết thúc');
        }
        
        // Bounds
        const bounds = L.latLngBounds(CONFIG.bounds[0], CONFIG.bounds[1]);
        L.rectangle(bounds, { color: '#2196F3', weight: 1, fillOpacity: 0, dashArray: '5, 5' }).addTo(map);
        map.fitBounds(bounds, { padding: [50, 50] });
        
        // UI
        document.getElementById('gps-count').textContent = gpsTrack.length;
        document.getElementById('tile-count').textContent = CONFIG.total_tiles;
        document.getElementById('zoom-level').textContent = map.getZoom();
        
        map.on('mousemove', function(e) {
            document.getElementById('coord').textContent = 
                e.latlng.lat.toFixed(6) + ', ' + e.latlng.lng.toFixed(6);
        });
        map.on('zoomend', function() {
            document.getElementById('zoom-level').textContent = map.getZoom();
        });
        
        // Layer toggles
        document.getElementById('toggle-drone').addEventListener('change', function() {
            this.checked ? map.addLayer(droneTiles) : map.removeLayer(droneTiles);
        });
        document.getElementById('toggle-osm').addEventListener('change', function() {
            this.checked ? map.addLayer(osmLayer) : map.removeLayer(osmLayer);
        });
        document.getElementById('toggle-track').addEventListener('change', function() {
            if (trackLayer) this.checked ? map.addLayer(trackLayer) : map.removeLayer(trackLayer);
        });
    </script>
</body>
</html>"""


# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(title="Video2Map", version="0.1.0")


def _load_config():
    """Load metadata + GPS cho HTML template"""
    meta_path = os.path.join(TILES_DIR, "metadata.json")
    if os.path.isfile(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)
    else:
        meta = {
            "center": [10.8320, 106.8195],
            "zoom_min": 15, "zoom_max": 19,
            "bounds": [[10.830, 106.817], [10.834, 106.822]],
            "total_tiles": 0,
        }
    
    gps_track = []
    if os.path.isfile(GPS_LOG):
        with open(GPS_LOG) as f:
            for line in f.readlines()[1:]:
                parts = line.strip().split(",")
                if len(parts) >= 3:
                    gps_track.append([float(parts[1]), float(parts[2])])
    
    meta["gps_track"] = gps_track
    return meta


@app.get("/", response_class=HTMLResponse)
async def index():
    config = _load_config()
    html = HTML_TEMPLATE.replace("__CONFIG_JSON__", json.dumps(config))
    return HTMLResponse(content=html)


@app.get("/tiles/{z}/{x}/{y}.png")
async def get_tile(z: int, x: int, y: int):
    tile_path = os.path.join(TILES_DIR, str(z), str(x), f"{y}.png")
    if os.path.isfile(tile_path):
        return FileResponse(tile_path, media_type="image/png")
    raise HTTPException(status_code=404)


@app.get("/api/metadata")
async def get_metadata():
    meta_path = os.path.join(TILES_DIR, "metadata.json")
    if os.path.isfile(meta_path):
        with open(meta_path) as f:
            return JSONResponse(json.load(f))
    return JSONResponse({"error": "No metadata"}, status_code=404)


@app.get("/panorama.jpg")
async def get_panorama():
    if os.path.isfile(PANORAMA_PATH):
        return FileResponse(PANORAMA_PATH, media_type="image/jpeg")
    raise HTTPException(status_code=404)


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Video2Map — Web Map Server")
    print("=" * 60)
    print(f"  Tiles:  {TILES_DIR}")
    print(f"  GPS:    {GPS_LOG}")
    print(f"  Server: http://{HOST}:{PORT}")
    print(f"  LAN:    http://192.168.88.123:{PORT}")
    print("=" * 60)
    
    if os.path.isdir(TILES_DIR):
        meta_path = os.path.join(TILES_DIR, "metadata.json")
        if os.path.isfile(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
            print(f"  Tiles: {meta.get('total_tiles', '?')}")
            print(f"  Center: {meta.get('center', '?')}")
    else:
        print("  Chưa có tiles! Chạy georef_tiles.py trước.")
    
    print()
    uvicorn.run(app, host=HOST, port=PORT)
