import http.server
import json
import os
import threading
import time
import socketserver

# ==========================================
# 1. CAMPUS CONFIGURATION & METADATA
# ==========================================
CAMPUS_CONFIG = {
    "campus_name": "Dayananda Sagar University - Harohalli Main Campus",
    "location": "Devarakaggalahalli, Kanakapura Road, Ramanagara, Karnataka",
    "crs": "EPSG:4326",
    "reference_origin": {
        "latitude": 12.558200,
        "longitude": 77.491500,
        "altitude_meters": 650.0
    },
    "zones": [
        { "zone_id": "Z-01", "name": "Entry & Perimeters", "type": "security", "status": "nominal" },
        { "zone_id": "Z-02", "name": "School of Engineering", "type": "academic", "status": "active" },
        { "zone_id": "Z-03", "name": "CDSIMER Hospital", "type": "healthcare", "status": "nominal" },
        { "zone_id": "Z-04", "name": "Central Library & Auditorium", "type": "amenity", "status": "nominal" },
        { "zone_id": "Z-05", "name": "Sports Complex", "type": "recreation", "status": "active" },
        { "zone_id": "Z-06", "name": "S' Residences & Mess", "type": "residential", "status": "nominal" }
    ]
}

# In-memory store for real-time telemetry updates
latest_telemetry = {
    "Z-01": {"power_kw": 12.5, "occupancy_count": 45},
    "Z-02": {"power_kw": 142.0, "occupancy_count": 850},
    "Z-03": {"power_kw": 210.5, "occupancy_count": 420},
    "Z-04": {"power_kw": 35.0, "occupancy_count": 180},
    "Z-05": {"power_kw": 8.0, "occupancy_count": 90},
    "Z-06": {"power_kw": 95.0, "occupancy_count": 600}
}

# ==========================================
# 2. BACKGROUND TELEMETRY SIMULATION THREAD
# ==========================================
def simulate_iot_gateway():
    """Simulates real-time IoT sensor data streaming from Harohalli campus zones."""
    while True:
        time.sleep(5)
        for zone_id in latest_telemetry:
            # Fluctuate live telemetry slightly to mimic real sensor feeds
            latest_telemetry[zone_id]["power_kw"] = round(latest_telemetry[zone_id]["power_kw"] * (0.98 + (0.04 * time.time() % 1)), 2)
        print("[IoT Gateway] Telemetry state updated across campus zones.")

# ==========================================
# 3. EMBEDDED FRONTEND DASHBOARD (HTML + JS)
# ==========================================
HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>DSU Harohalli - Digital Twin Dashboard</title>
    <style>
        body { margin: 0; font-family: Arial, sans-serif; background: #121212; color: #fff; overflow: hidden; }
        #header { position: absolute; top: 0; left: 0; width: 100%; background: rgba(0,0,0,0.8); padding: 15px 30px; display: flex; justify-content: space-between; align-items: center; z-index: 10; border-bottom: 1px solid #333; }
        #header h1 { margin: 0; font-size: 20px; color: #4CAF50; }
        #sidebar { position: absolute; top: 70px; right: 0; width: 320px; height: calc(100vh - 70px); background: rgba(20,20,20,0.95); padding: 20px; box-sizing: border-box; overflow-y: auto; border-left: 1px solid #333; z-index: 10; }
        .zone-card { background: #222; padding: 12px; margin-bottom: 10px; border-radius: 6px; border-left: 4px solid #4CAF50; }
        .zone-card h3 { margin: 0 0 5px 0; font-size: 15px; }
        .zone-card p { margin: 2px 0; font-size: 12px; color: #aaa; }
        #canvas-container { width: 100vw; height: 100vh; }
    </style>
</head>
<body>
    <div id="header">
        <h1>DSU Harohalli Campus Digital Twin</h1>
        <span id="system-status">Status: Connected (Live Telemetry Active)</span>
    </div>
    
    <div id="sidebar">
        <h2>Campus Zones</h2>
        <div id="zones-list">Loading spatial telemetry...</div>
    </div>

    <div id="canvas-container"></div>

    <!-- Three.js CDN -->
    <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
    <script>
        // 3D Scene Initialization
        const container = document.getElementById('canvas-container');
        const scene = new THREE.Scene();
        scene.background = new THREE.Color(0x111111);

        const camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.1, 1000);
        camera.position.set(0, 40, 60);
        camera.lookAt(0, 0, 0);

        const renderer = new THREE.WebGLRenderer({ antialias: true });
        renderer.setSize(window.innerWidth, window.innerHeight);
        container.appendChild(renderer.domElement);

        // Lighting
        const ambientLight = new THREE.AmbientLight(0xffffff, 0.7);
        scene.add(ambientLight);
        const directionalLight = new THREE.DirectionalLight(0xffffff, 0.8);
        directionalLight.position.set(20, 40, 20);
        scene.add(directionalLight);

        // Ground Plane representing Campus Footprint
        const groundGeo = new THREE.PlaneGeometry(60, 60);
        const groundMat = new THREE.MeshStandardMaterial({ color: 0x1e1e1e, roughness: 0.9 });
        const ground = new THREE.Mesh(groundGeo, groundMat);
        ground.rotation.x = -Math.PI / 2;
        scene.add(ground);

        // Procedural Zone Meshes representing campus blocks
        const zoneMeshes = {};
        const positions = [
            {-15, 0, -15, "Z-01", "Entry & Perimeters", 0x607D8B},
            {-10, 0, 5, "Z-02", "School of Engineering", 0x2196F3},
            {10, 0, 5, "Z-03", "CDSIMER Hospital", 0xE91E63},
            {0, 0, -10, "Z-04", "Central Library", 0xFF9800},
            {15, 0, -15, "Z-05", "Sports Complex", 0x4CAF50},
            {-15, 0, 20, "Z-06", "S' Residences", 0x9C27B0}
        ];

        positions.forEach(pos => {
            const geometry = new THREE.BoxGeometry(6, pos[3] === "Z-02" ? 12 : 8, 6);
            const material = new THREE.MeshStandardMaterial({ color: pos[5], roughness: 0.4 });
            const mesh = new THREE.Mesh(geometry, material);
            mesh.position.set(pos[0], (pos[3] === "Z-02" ? 12 : 8)/2, pos[1]);
            scene.add(mesh);
            zoneMeshes[pos[3]] = mesh;
        });

        // Animation Loop
        function animate() {
            requestAnimationFrame(animate);
            renderer.render(scene, camera);
        }
        animate();

        // Fetch Config & Live Telemetry Loop
        async function fetchTelemetry() {
            try {
                const response = await fetch('/api/data');
                const data = await response.json();
                
                let html = '';
                data.zones.forEach(zone => {
                    const tel = data.telemetry[zone.zone_id] || {power_kw: 0, occupancy_count: 0};
                    html += `
                        <div class="zone-card">
                            <h3>${zone.name}</h3>
                            <p><b>Type:</b> ${zone.type.toUpperCase()}</p>
                            <p><b>Power Draw:</b> ${tel.power_kw} kW</p>
                            <p><b>Est. Occupancy:</b> ${tel.occupancy_count} persons</p>
                        </div>
                    `;
                });
                document.getElementById('zones-list').innerHTML = html;
            } catch (err) {
                console.error("Failed to fetch telemetry", err);
            }
        }

        setInterval(fetchTelemetry, 3000);
        fetchTelemetry();

        // Window Resizing
        window.addEventListener('resize', () => {
            camera.aspect = window.innerWidth / window.innerHeight;
            camera.updateProjectionMatrix();
            renderer.setSize(window.innerWidth, window.innerHeight);
        });
    </script>
</body>
</html>
"""

# ==========================================
# 4. HTTP SERVER HANDLER
# ==========================================
class TwinServerHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(HTML_CONTENT.encode('utf-8'))
        elif self.path == '/api/data':
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            payload = {
                "config": CAMPUS_CONFIG,
                "zones": CAMPUS_CONFIG["zones"],
                "telemetry": latest_telemetry
            }
            self.wfile.write(json.dumps(payload).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Suppress standard http server access logs for clean console output
        return

if __name__ == '__main__':
    PORT = int(os.environ.get("TWIN_PORT", "8080"))
    
    # Start the IoT simulation thread in the background
    iot_thread = threading.Thread(target=simulate_iot_gateway, daemon=True)
    iot_thread.start()
    
    # Start local web server
    with socketserver.TCPServer(("", PORT), TwinServerHandler) as httpd:
        print(f"=======================================================")
        print(f" DSU Harohalli Digital Twin Server running successfully!")
        print(f" Access your interactive 3D Twin at: http://localhost:{PORT}")
        print(f"=======================================================")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down digital twin server...")
            httpd.server_close()