import asyncio
import math
import json
import os
from dotenv import load_dotenv
import winsound
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Security, HTTPException, status, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import APIKeyHeader
from contextlib import asynccontextmanager
from pydantic import BaseModel
from winrt.windows.devices.sensors import Accelerometer
from winrt.windows.devices.enumeration import DeviceInformation

load_dotenv()

# --- Configuration ---
CONFIG_FILE = os.getenv("CONFIG_FILE", "hinge_config.json")
ALPHA = 0.15 
PORT = os.getenv("PORT", 8000)
API_KEY = os.getenv("API_KEY")

class HingeEngine:
    def __init__(self):
        self.angle = 0.0
        self.mode = "Unknown"
        self.is_calibrating = False
        self.trigger_calibration = False
        self.restart_event = asyncio.Event()
        
        self.base_id = None
        self.lid_id = None
        self.slope = 1.0
        self.intercept = 0.0
        self.load_config()

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    data = json.load(f)
                    self.base_id = data.get("base_id")
                    self.lid_id = data.get("lid_id")
                    self.slope = data.get("slope", 1.0)
                    self.intercept = data.get("intercept", 0.0)
            except: pass

    def save_config(self):
        with open(CONFIG_FILE, "w") as f:
            json.dump({
                "base_id": self.base_id,
                "lid_id": self.lid_id,
                "slope": self.slope,
                "intercept": self.intercept
            }, f, indent=4)

engine = HingeEngine()

# --- Hardware Logic ---

async def list_accelerometers():
    selector = Accelerometer.get_device_selector(0)
    devices = await DeviceInformation.find_all_async_aqs_filter_and_additional_properties(selector, [])
    return [{"id": d.id, "name": d.name} for d in devices]

async def get_raw_reading(accel_base, accel_lid):
    rb, rl = accel_base.get_current_reading(), accel_lid.get_current_reading()
    if rb and rl:
        a_b = math.atan2(rb.acceleration_y, rb.acceleration_z)
        a_l = math.atan2(rl.acceleration_y, rl.acceleration_z)
        
        raw_diff = math.degrees(a_l - a_b)
        # Apply the 180 physical offset and invert the direction
        normalized = (360 - ((raw_diff + 180 + 360) % 360)) % 360
        return normalized
    return None

async def calibrate_task(accel_base, accel_lid):
    engine.is_calibrating = True
    print("\n[CALIBRATION STARTED via Web]")
    
    async def capture_step(target):
        winsound.Beep(1000, 500)
        for _ in range(5):
            winsound.Beep(1200, 100)
            await asyncio.sleep(1)
            
        winsound.Beep(2000, 200) # Measuring tone
        total, count = 0, 0
        for _ in range(50):
            val = await get_raw_reading(accel_base, accel_lid)
            if val is not None:
                total += val
                count += 1
            await asyncio.sleep(0.02)
            
        winsound.Beep(2500, 300) # Success tone
        return total / count if count > 0 else 0

    p1_raw = await capture_step(180) # Flat
    await asyncio.sleep(1)           
    p2_raw = await capture_step(360) # Tablet

    try:
        m = 180.0 / (p2_raw - p1_raw)
        c = 180.0 - (m * p1_raw)
        engine.slope, engine.intercept = m, c
        engine.save_config()
        winsound.Beep(2000, 100); winsound.Beep(2500, 400) 
        print(f"[!] Calibration Saved: Slope {m:.3f}, Int {c:.3f}")
    except ZeroDivisionError:
        print("[!] Calibration failed: No movement detected.")
    
    engine.is_calibrating = False

async def sensor_worker():
    while True:
        if not engine.base_id or not engine.lid_id:
            devices = await list_accelerometers()
            if len(devices) >= 2:
                engine.base_id, engine.lid_id = devices[0]["id"], devices[1]["id"]
                engine.save_config()

        try:
            accel_base = await Accelerometer.from_id_async(engine.base_id)
            accel_lid = await Accelerometer.from_id_async(engine.lid_id)
            fb, fl = [0.0]*3, [0.0]*3
            print(f"\n[SYSTEM] Sensors connected. Web UI at http://localhost:{PORT}")
        except Exception as e:
            print(f"[!] Connection failed. Retrying... ({e})")
            await asyncio.sleep(2)
            continue

        while not engine.restart_event.is_set():
            if engine.trigger_calibration:
                engine.trigger_calibration = False
                await calibrate_task(accel_base, accel_lid)

            if not engine.is_calibrating:
                rb, rl = accel_base.get_current_reading(), accel_lid.get_current_reading()
                if rb and rl:
                    for i, c in enumerate('xyz'):
                        fb[i] = (ALPHA * getattr(rb, f"acceleration_{c}")) + ((1-ALPHA)*fb[i])
                        fl[i] = (ALPHA * getattr(rl, f"acceleration_{c}")) + ((1-ALPHA)*fl[i])

                    raw_diff = math.degrees(math.atan2(fl[1], fl[2]) - math.atan2(fb[1], fb[2]))
                    # 1. Base Hardware Normalization
                    normalized_raw = (360 - ((raw_diff + 180 + 360) % 360)) % 360
                    
                    # 2. Apply your custom 2-Point Calibration
                    final = ((engine.slope * normalized_raw) + engine.intercept) % 360
                    
                    if final < 4.0 or final > 356.0: final = 0.0 if final < 180 else 360.0
                    engine.angle = round(final, 2)
                    
                    if final == 0: engine.mode = "Closed"
                    elif 175 < final < 185: engine.mode = "Flat"
                    elif final > 280: engine.mode = "Tablet"
                    else: engine.mode = "Laptop"

            await asyncio.sleep(0.05)
            
        engine.restart_event.clear()

# --- FastAPI & REST Routes ---

templates = Jinja2Templates(directory=".")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)

async def verify_api_key(api_key: str = Security(api_key_header)):
    # Ignore API key if not set
    if API_KEY is None:
        return True
    
    if api_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API Key",
        )
    return api_key

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(sensor_worker())
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)

class ManualSensorAssign(BaseModel):
    base_id: str
    lid_id: str

@app.get("/api/sensors", dependencies=[Depends(verify_api_key)])
async def get_sensors():
    devices = await list_accelerometers()
    return {"sensors": devices, "current_base": engine.base_id, "current_lid": engine.lid_id}

@app.post("/api/swap", dependencies=[Depends(verify_api_key)])
async def swap_sensors():
    engine.base_id, engine.lid_id = engine.lid_id, engine.base_id
    engine.save_config()
    engine.restart_event.set()
    return {"status": "swapped"}

@app.post("/api/set_sensors", dependencies=[Depends(verify_api_key)])
async def set_sensors(data: ManualSensorAssign):
    engine.base_id, engine.lid_id = data.base_id, data.lid_id
    engine.save_config()
    engine.restart_event.set()
    return {"status": "updated"}

@app.post("/api/calibrate", dependencies=[Depends(verify_api_key)])
async def trigger_calibrate():
    engine.trigger_calibration = True
    return {"status": "started"}

@app.get("/api/angle")
async def get_current_angle():
    """One-time poll for the current hinge state."""
    return {
        "angle": engine.angle,
        "mode": engine.mode,
        "calibrating": engine.is_calibrating
    }

@app.websocket("/ws/hinge")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.send_json({
                "angle": engine.angle, 
                "mode": engine.mode,
                "calibrating": engine.is_calibrating
            })
            await asyncio.sleep(0.05)
    except WebSocketDisconnect: pass

@app.get("/", response_class=HTMLResponse)
async def get_dashboard(request: Request):
    # This injects the API_KEY variable into your index.html
    return templates.TemplateResponse(request, "index.html", {
        "api_key": API_KEY
    })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(PORT), log_level="warning", reload=True)