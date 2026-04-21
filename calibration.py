import asyncio
import math
import winsound

async def get_raw_reading(accel_base, accel_lid):
    rb, rl = accel_base.get_current_reading(), accel_lid.get_current_reading()
    if rb and rl:
        a_b = math.atan2(rb.acceleration_y, rb.acceleration_z)
        a_l = math.atan2(rl.acceleration_y, rl.acceleration_z)
        
        raw_diff = math.degrees(a_l - a_b)
        normalized = (360 - ((raw_diff + 180 + 360) % 360)) % 360
        return normalized
    return None

# --- Audio Motifs ---

def play_start(): winsound.Beep(523, 150); winsound.Beep(659, 150); winsound.Beep(784, 200)
def play_countdown(): winsound.Beep(880, 100)
def play_measure_start(): winsound.Beep(1046, 300)
def play_step_success(): winsound.Beep(784, 150); winsound.Beep(1046, 250)
def play_final_success(): winsound.Beep(523, 150); winsound.Beep(659, 150); winsound.Beep(784, 150); winsound.Beep(1046, 400)
def play_error(): winsound.Beep(300, 300); winsound.Beep(250, 400)
def play_cancelled(): winsound.Beep(400, 200); winsound.Beep(300, 400) # NEW: Descending abort tone

# --- Process Logic ---

async def capture_step(target_name, accel_base, accel_lid, cancel_event):
    print(f"\n[CALIBRATION] Preparing for {target_name}...")
    play_start()
    
    for i in range(5, 0, -1):
        if cancel_event.is_set(): 
            play_cancelled()
            return None
        print(f"Sampling in {i}... ", end="\r")
        play_countdown()
        await asyncio.sleep(0.9)
        
    print("\n[MEASURING] Averaging 50 gravitational vector samples. Hold steady...")
    play_measure_start()
    
    total, count = 0, 0
    for _ in range(50):
        if cancel_event.is_set(): 
            play_cancelled()
            return None
        val = await get_raw_reading(accel_base, accel_lid)
        if val is not None:
            total += val
            count += 1
        await asyncio.sleep(0.02)
        
    play_step_success()
    return total / count if count > 0 else 0

async def run_calibration(accel_base, accel_lid, cancel_event):
    print("\n" + "="*40 + "\nCALIBRATION INITIATED\n" + "="*40)
    
    p1_raw = await capture_step("FLAT (180°)", accel_base, accel_lid, cancel_event)
    if p1_raw is None: return None, None 

    for _ in range(10):
        if cancel_event.is_set(): 
            play_cancelled()
            return None, None
        await asyncio.sleep(0.1)
        
    p2_raw = await capture_step("TABLET (360°)", accel_base, accel_lid, cancel_event)
    if p2_raw is None: return None, None 

    try:
        m = 180.0 / (p2_raw - p1_raw)
        c = 180.0 - (m * p1_raw)
        play_final_success()
        return m, c
    except ZeroDivisionError:
        play_error()
        return None, None