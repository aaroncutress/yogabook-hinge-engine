import asyncio
import math
import json
import os
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Security, HTTPException, status, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import APIKeyHeader
from contextlib import asynccontextmanager
from pydantic import BaseModel
from winrt.windows.devices.sensors import Accelerometer
from winrt.windows.devices.enumeration import DeviceInformation

from calibration import run_calibration

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
        self.cancel_event = asyncio.Event()
        
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
                engine.is_calibrating = True
                engine.cancel_event.clear() # Reset the abort flag
                
                # Execute the external calibration script with the kill switch
                new_slope, new_intercept = await run_calibration(accel_base, accel_lid, engine.cancel_event)
                
                if new_slope is not None:
                    engine.slope, engine.intercept = new_slope, new_intercept
                    engine.save_config()
                    print(f"[!] Calibration Saved: Slope {new_slope:.3f}, Int {new_intercept:.3f}")
                elif engine.cancel_event.is_set():
                    print("[!] Calibration Aborted by User.")
                else:
                    print("[!] Calibration failed: No movement detected.")
                    
                engine.is_calibrating = False

            if not engine.is_calibrating:
                rb, rl = accel_base.get_current_reading(), accel_lid.get_current_reading()
                if rb and rl:
                    for i, c in enumerate('xyz'):
                        fb[i] = (ALPHA * getattr(rb, f"acceleration_{c}")) + ((1-ALPHA)*fb[i])
                        fl[i] = (ALPHA * getattr(rl, f"acceleration_{c}")) + ((1-ALPHA)*fl[i])

                    raw_diff = math.degrees(math.atan2(fl[1], fl[2]) - math.atan2(fb[1], fb[2]))
                    # Base Hardware Normalization
                    normalized_raw = (360 - ((raw_diff + 180 + 360) % 360)) % 360
                    
                    # Apply your custom 2-Point Calibration
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

@app.post("/api/reset_sensors", dependencies=[Depends(verify_api_key)])
async def reset_sensors():
    """Clears manual sensor assignments to trigger auto-discovery."""
    engine.base_id = None
    engine.lid_id = None
    engine.save_config()
    engine.restart_event.set()
    return {"status": "reset"}

@app.post("/api/calibrate", dependencies=[Depends(verify_api_key)])
async def trigger_calibrate():
    engine.trigger_calibration = True
    return {"status": "started"}

@app.post("/api/cancel_calibration", dependencies=[Depends(verify_api_key)])
async def cancel_calibration():
    """Fires the abort event to stop an active calibration."""
    engine.cancel_event.set()
    return {"status": "aborted"}

@app.post("/api/reset_calibration", dependencies=[Depends(verify_api_key)])
async def reset_calibration():
    """Resets the math to standard 1:1 scaling (Factory Default)."""
    engine.slope = 1.0
    engine.intercept = 0.0
    engine.save_config()
    return {"status": "reset"}

@app.get("/api/angle")
async def get_current_angle():
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
    return templates.TemplateResponse(request, "index.html", {
        "api_key": API_KEY
    })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(PORT), log_level="warning", reload=True)