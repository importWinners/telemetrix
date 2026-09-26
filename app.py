from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import uvicorn
from database import init_db, get_db, Vehicle, Telemetry, SafetyEvent, Incident, TelemetryError, SessionLocal
from processor import process_telemetry
from config import app_name
import asyncio
import random
import uuid

app = FastAPI(title=app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simulator State
sim_vehicles = {
    f"{'TN14-4289' if i == 0 else 'V10'+str(i)}": {
        "speed": random.uniform(40, 70),
        "lat": 12.9716 + random.uniform(-0.05, 0.05),
        "lon": 80.2450 + random.uniform(-0.05, 0.05),
        "fuel": random.uniform(20, 100),
        "odometer": random.uniform(10000, 50000),
        "engine": "ON"
    } for i in range(5)
}
inject_queue = []

def generate_telemetry(veh_id):
    state = sim_vehicles[veh_id]
    
    # Smooth, realistic speed changes instead of random jumps
    target_speed = state.get("target_speed", random.uniform(40, 75))
    if abs(state["speed"] - target_speed) < 2:
        state["target_speed"] = random.uniform(40, 75)
    
    # Accelerate/Decelerate towards target
    if state["speed"] < target_speed:
        state["speed"] += random.uniform(0.5, 1.5)
    else:
        state["speed"] -= random.uniform(0.5, 1.5)
        
    state["speed"] = round(max(0, state["speed"]), 1)
    
    # Realistic GPS movement
    state["lat"] = round(state["lat"] + (state["speed"] * 0.0000005), 6)
    state["lon"] = round(state["lon"] + (state["speed"] * 0.0000005), 6)
    
    # Realistic Odometer and Fuel drop
    state["odometer"] = round(state["odometer"] + state["speed"] * (1 / 3600.0), 1)
    state["fuel"] = round(max(0, state["fuel"] - 0.005), 1)
    
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

async def sim_loop():
    while True:
        try:
            db = SessionLocal()
            for veh_id in sim_vehicles:
                injections = [i for i in inject_queue if i['veh_id'] == veh_id or i['veh_id'] == 'ALL']
                if any(i['type'] == 'GAP' for i in injections):
                    continue
                    
                payload = generate_telemetry(veh_id)
                
                for inj in injections:
                    if inj['type'] == 'SPEEDING':
                        payload['speed'] = 95
                        sim_vehicles[veh_id]['speed'] = 95
                    elif inj['type'] == 'HARSH_BRAKING':
                        payload['speed'] = max(0, sim_vehicles[veh_id]['speed'] - 20)
                        sim_vehicles[veh_id]['speed'] = payload['speed']
                    elif inj['type'] == 'HARSH_ACCEL':
                        payload['speed'] = min(150, sim_vehicles[veh_id]['speed'] + 15)
                        sim_vehicles[veh_id]['speed'] = payload['speed']
                    elif inj['type'] == 'CRASH':
                        payload['speed'] = 80
                        sim_vehicles[veh_id]['speed'] = 0
                    elif inj['type'] == 'CRASH_STOP':
                        payload['speed'] = 0
                        payload['diagnostic_codes'] = ['AIRBAG_DEPLOYED']
                    elif inj['type'] == 'INVALID':
                        payload['speed'] = 500
                    elif inj['type'] == 'OUT_OF_ORDER':
                        payload['vehicle_timestamp'] = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
                
                process_telemetry(db, payload)
                
                if any(i['type'] == 'DUPLICATE' for i in injections):
                    process_telemetry(db, payload)
                    
            inject_queue[:] = [i for i in inject_queue if i['type'] == 'GAP']
        except Exception as e:
            print(f"Sim loop error: {e}")
        finally:
            db.close()
            
        await asyncio.sleep(1)

@app.on_event("startup")
async def on_startup():
    init_db()
    asyncio.create_task(sim_loop())

@app.post("/api/inject/{action}")
def inject(action: str, veh_id: str = "TN14-4289"):
    if action == "GAP_START":
        inject_queue.append({"veh_id": veh_id, "type": "GAP"})
    elif action == "GAP_END":
        inject_queue[:] = [i for i in inject_queue if i['type'] != 'GAP' or i['veh_id'] != veh_id]
    elif action == "CRASH":
        inject_queue.append({"veh_id": veh_id, "type": "CRASH"})
        async def delayed_stop():
            await asyncio.sleep(1.2)
            inject_queue.append({"veh_id": veh_id, "type": "CRASH_STOP"})
        asyncio.create_task(delayed_stop())
    else:
        inject_queue.append({"veh_id": veh_id, "type": action.upper()})
    
    return {"status": "injected", "action": action, "veh_id": veh_id}

@app.get("/api/config")
def get_config():
    return {"app_name": app_name}

@app.post("/api/telemetry")
async def receive_telemetry(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    result = process_telemetry(db, data)
    return result

@app.get("/api/dashboard/stats")
def get_dashboard_stats(db: Session = Depends(get_db)):
    total_vehicles = db.query(Vehicle).count()
    active_vehicles = db.query(Vehicle).filter(Vehicle.last_speed > 0).count()
    safety_events = db.query(SafetyEvent).count()
    incidents = db.query(Incident).count()
    
    # Telemetry health
    received = db.query(Telemetry).count()
    duplicates = db.query(TelemetryError).filter(TelemetryError.error_type == 'DUPLICATE').count()
    late = db.query(TelemetryError).filter(TelemetryError.error_type == 'LATE').count()
    invalid = db.query(TelemetryError).filter(TelemetryError.error_type == 'INVALID').count()
    gaps = db.query(TelemetryError).filter(TelemetryError.error_type == 'GAP').count()

    return {
        "total_vehicles": total_vehicles,
        "active_vehicles": active_vehicles,
        "stationary_vehicles": total_vehicles - active_vehicles,
        "safety_events": safety_events,
        "incidents": incidents,
        "health": {
            "received": received,
            "processed": received - invalid,
            "duplicates": duplicates,
            "late": late,
            "invalid": invalid,
            "gaps": gaps
        }
    }

@app.get("/api/vehicles")
def get_vehicles(db: Session = Depends(get_db)):
    return db.query(Vehicle).all()

@app.post("/api/vehicles/{vehicle_id}/login")
async def driver_login(vehicle_id: str, request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    driver_id = data.get("driver_id")
    vehicle_type = data.get("vehicle_type", "Standard")
    
    vehicle = db.query(Vehicle).filter(Vehicle.vehicle_id == vehicle_id).first()
    if not vehicle:
        vehicle = Vehicle(vehicle_id=vehicle_id, vehicle_type=vehicle_type)
        db.add(vehicle)
        
    vehicle.current_driver_id = driver_id
    vehicle.vehicle_type = vehicle_type
    db.commit()
    return {"status": "success", "message": f"Driver {driver_id} logged into {vehicle_id}"}

@app.get("/api/events")
def get_events(db: Session = Depends(get_db)):
    return db.query(SafetyEvent).order_by(SafetyEvent.timestamp.desc()).limit(50).all()

@app.get("/api/incidents")
def get_incidents(db: Session = Depends(get_db)):
    return db.query(Incident).order_by(Incident.timestamp.desc()).limit(50).all()

@app.get("/api/incidents/{incident_id}/timeline")
def get_incident_timeline(incident_id: str, db: Session = Depends(get_db)):
    inc = db.query(Incident).filter(Incident.incident_id == incident_id).first()
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")
    
    # Get telemetry 10 seconds before and 5 seconds after incident timestamp
    start_time = inc.timestamp - timedelta(seconds=10)
    end_time = inc.timestamp + timedelta(seconds=5)
    
    tels = db.query(Telemetry).filter(
        Telemetry.vehicle_id == inc.vehicle_id,
        Telemetry.vehicle_timestamp >= start_time,
        Telemetry.vehicle_timestamp <= end_time
    ).order_by(Telemetry.vehicle_timestamp.asc()).all()
    
    return tels

app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")

import os

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port)
