# Yoga Book 9i Hinge Engine

A lightweight, real-time local microservice that calculates the physical hinge angle of the Lenovo Yoga Book 9i using its dual built-in accelerometers. 

This utility bypasses proprietary drivers to provide a clean Web UI, a WebSocket stream, and a REST API, allowing developers to build dual-screen aware applications or custom OS automations.

## Features
* **Real-Time Tracking:** Fuses data from the Base and Lid accelerometers at 20Hz.
* **Audio-Assisted Calibration:** A guided 2-point linear calibration process (Flat & Tablet) to map out hardware-specific mounting biases.
* **Web Dashboard:** A clean, local interface to view the live angle and manage sensor configurations.
* **Developer APIs:** Exposes both a WebSocket stream for real-time reactivity and a REST endpoint for quick polling.
* **Persistent Hardware Mapping:** Automatically saves device IDs so the script survives reboots and driver updates without flipping the angle backwards.

## Prerequisites
* **OS:** Windows 10/11 (Relies on the Windows Runtime `winrt` Sensor API)
* **Python:** 3.10+

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/aaroncutress/yogabook-hinge-engine.git
   cd yogabook-hinge-engine
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Create a `.env` file in the root directory to configure the engine (optional):
   ```env
   API_KEY=your_super_secret_key_here
   CONFIG_FILE=hinge_config.json
   PORT=8000
   ```
   * **`API_KEY`**: Secures your configuration endpoints via the `X-API-Key` header. **If omitted, the API runs in open mode** and all endpoints allow unauthenticated access.
   * **`CONFIG_FILE`**: The file path used to store your device ID mappings and calibration math. (Defaults to `hinge_config.json`).
   * **`PORT`**: The port for the web dashboard and API. (Defaults to `8000`).

## Usage

Start the engine:
```bash
python main.py
```

Open your browser and navigate to `http://localhost:8000` (or your custom configured port) to access the dashboard. 

**First Run Setup:**
1. Click **Start 2-Point Calibration**.
2. Follow the audio cues to place the laptop flat (180°) and then folded back (360°).
3. If the angle tracks backwards or fails to update, open the **Advanced Sensor Configuration** menu to manually swap the Base and Lid sensor IDs.

## API Reference

### WebSocket Stream (Public)
`ws://localhost:8000/ws/hinge`  
Pushes live hinge data at 20Hz.
```json
{
  "angle": 92.4,
  "mode": "Laptop",
  "calibrating": false
}
```

### REST Endpoints
* **`GET /api/angle`** (Public): One-time poll of the current state.
* **`GET /api/sensors`** (Protected): Lists available accelerometer hardware IDs.
* **`POST /api/calibrate`** (Protected): Triggers the audio-assisted calibration sequence.
* **`POST /api/swap`** (Protected): Flips the active Base and Lid sensors.
* **`POST /api/set_sensors`** (Protected): Manually assigns the Base and Lid sensor IDs.

*Protected routes require the `X-API-Key` header matching your `.env` file. If no `API_KEY` is set in the environment, these routes become publicly accessible.*