import time
import uuid
import random
import asyncio
import httpx
from datetime import datetime, timedelta
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI(title="Telemetry Simulator")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_URL = "http://localhost:8000/api/telemetry"

vehicles = {
    f"{'TN14-4289' if i == 0 else 'V10'+str(i)}": {
        "speed": random.uniform(40, 70),
        "lat": 12.9716 + random.uniform(-0.05, 0.05),
        "lon": 80.2450 + random.uniform(-0.05, 0.05),
        "fuel": random.uniform(20, 100),
        "odometer": random.uniform(10000, 50000),
        "engine": "ON"
    } for i in range(5)
}

simulator_task = None
is_running = True
inject_queue = []

def generate_telemetry(veh_id):
    state = vehicles[veh_id]
    state["speed"] = max(0, state["speed"] + random.uniform(-2, 2))
    state["lat"] += random.uniform(-0.0001, 0.0001)
    state["lon"] += random.uniform(-0.0001, 0.0001)
    state["odometer"] += state["speed"] * (1 / 3600.0)
    
    return {
        "message_id": f"MSG_{uuid.uuid4().hex[:8]}",
        "vehicle_id": veh_id,
        "latitude": state["lat"],
        "longitude": state["lon"],
        "engine_status": state["engine"],
        "fuel_level": state["fuel"],
        "speed": state["speed"],
        "odometer": state["odometer"],
        "diagnostic_codes": [],
        "vehicle_timestamp": datetime.utcnow().isoformat()
    }

async def send_telemetry(payload):
    async with httpx.AsyncClient() as client:
        try:
            await client.post(API_URL, json=payload)
        except:
            pass

async def sim_loop():
    global is_running
    while True:
        if is_running:
            for veh_id in vehicles:
                # check if there's a specific injection for this vehicle
                injections = [i for i in inject_queue if i['veh_id'] == veh_id or i['veh_id'] == 'ALL']
                
                if any(i['type'] == 'GAP' for i in injections):
                    continue # Skip sending to simulate gap
                    
                payload = generate_telemetry(veh_id)
                
                # Apply injections
                for inj in injections:
                    if inj['type'] == 'SPEEDING':
                        payload['speed'] = 95
                        vehicles[veh_id]['speed'] = 95
                    elif inj['type'] == 'HARSH_BRAKING':
                        payload['speed'] = max(0, vehicles[veh_id]['speed'] - 20)
                        vehicles[veh_id]['speed'] = payload['speed']
                    elif inj['type'] == 'HARSH_ACCEL':
                        payload['speed'] = min(150, vehicles[veh_id]['speed'] + 15)
                        vehicles[veh_id]['speed'] = payload['speed']
                    elif inj['type'] == 'CRASH':
                        # Send high speed first, then stop
                        payload['speed'] = 80
                        vehicles[veh_id]['speed'] = 0 # Next tick will be 0
                    elif inj['type'] == 'CRASH_STOP':
                        payload['speed'] = 0
                        payload['diagnostic_codes'] = ['AIRBAG_DEPLOYED']
                    elif inj['type'] == 'INVALID':
                        payload['speed'] = 500
                    elif inj['type'] == 'OUT_OF_ORDER':
                        payload['vehicle_timestamp'] = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
                
                # Send normal
                await send_telemetry(payload)
                
                # Send duplicate
                if any(i['type'] == 'DUPLICATE' for i in injections):
                    await send_telemetry(payload)
                
            # Clean up one-time injections
            inject_queue[:] = [i for i in inject_queue if i['type'] == 'GAP'] 
            
        await asyncio.sleep(1)

@app.on_event("startup")
async def startup_event():
    global simulator_task
    simulator_task = asyncio.create_task(sim_loop())

@app.post("/inject/{action}")
def inject(action: str, veh_id: str = "TN14-4289"):
    if action == "GAP_START":
        inject_queue.append({"veh_id": veh_id, "type": "GAP"})
    elif action == "GAP_END":
        inject_queue[:] = [i for i in inject_queue if i['type'] != 'GAP' or i['veh_id'] != veh_id]
    elif action == "CRASH":
        inject_queue.append({"veh_id": veh_id, "type": "CRASH"})
        # Queue the stop for next tick
        async def delayed_stop():
            await asyncio.sleep(1.2)
            inject_queue.append({"veh_id": veh_id, "type": "CRASH_STOP"})
        asyncio.create_task(delayed_stop())
    else:
        inject_queue.append({"veh_id": veh_id, "type": action.upper()})
    
    return {"status": "injected", "action": action, "veh_id": veh_id}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
