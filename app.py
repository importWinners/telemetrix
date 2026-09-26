from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import uvicorn
from database import init_db, get_db, Vehicle, Telemetry, SafetyEvent, Incident, TelemetryError, SessionLocal, DriverProfile
from processor import process_telemetry, log_error
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
        "speed": 0.0,
        "target_speed": 0.0,
        "lat": 12.9716 + random.uniform(-0.05, 0.05),
        "lon": 80.2450 + random.uniform(-0.05, 0.05),
        "fuel": random.uniform(20, 100),
        "odometer": random.uniform(10000, 50000),
        "engine": "OFF"
    } for i in range(5)
}
inject_queue = []
gap_buffer = {}

def generate_telemetry(veh_id):
    state = sim_vehicles[veh_id]
    
    if state["engine"] == "OFF":
        state["speed"] = 0.0
        state["target_speed"] = 0.0
        state["accel"] = 0.0
    else:
        # Smooth, realistic speed changes instead of random jumps
        target_speed = state.get("target_speed", random.uniform(40, 75))
        if abs(state["speed"] - target_speed) < 2 and target_speed > 0:
            state["target_speed"] = random.uniform(40, 75)
        
        # Accelerate/Decelerate towards target
        if state["speed"] < target_speed:
            state["speed"] += random.uniform(2.0, 5.0)
        else:
            if target_speed == 0.0:
                state["speed"] -= random.uniform(10.0, 15.0) # Stop faster
            else:
                state["speed"] -= random.uniform(2.0, 5.0)
            
        state["speed"] = round(max(0, state["speed"]), 1)
        
        # Turn off engine automatically when fully stopped
        if target_speed == 0.0 and state["speed"] == 0.0:
            state["engine"] = "OFF"
    
    # Realistic GPS movement (increased multiplier so it's visible on UI)
    state["lat"] = round(state["lat"] + (state["speed"] * 0.00002), 6)
    state["lon"] = round(state["lon"] + (state["speed"] * 0.00002), 6)
    
    # Realistic Odometer and Fuel drop
    state["odometer"] = round(state["odometer"] + state["speed"] * (1 / 3600.0), 1)
    if state["engine"] == "ON":
        state["fuel"] = round(max(0, state["fuel"] - 0.005), 1)
    
    if "prev_speed" in state:
        state["accel"] = round((state["speed"] - state["prev_speed"]) / 3.6, 2)
    else:
        state["accel"] = 0.0
    state["prev_speed"] = state["speed"]
    
    if "local_alerts" not in state:
        state["local_alerts"] = []
        
    speed = state["speed"]
    if speed > 100:
        state["local_alerts"].insert(0, {"description": f"Extreme Speeding at {speed} km/h", "severity": "CRITICAL", "timestamp": (datetime.utcnow() + timedelta(hours=5, minutes=30)).isoformat()})
    elif speed > 80:
        state["local_alerts"].insert(0, {"description": f"Speeding at {speed} km/h", "severity": "WARNING", "timestamp": (datetime.utcnow() + timedelta(hours=5, minutes=30)).isoformat()})
        
    if state["accel"] < -5.0:
        state["local_alerts"].insert(0, {"description": f"Harsh braking: {state['accel']} m/s²", "severity": "WARNING", "timestamp": (datetime.utcnow() + timedelta(hours=5, minutes=30)).isoformat()})
    elif state["accel"] > 3.0:
        state["local_alerts"].insert(0, {"description": f"Harsh acceleration: {state['accel']} m/s²", "severity": "WARNING", "timestamp": (datetime.utcnow() + timedelta(hours=5, minutes=30)).isoformat()})
        
    state["local_alerts"] = state["local_alerts"][:10]
    
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
        "vehicle_timestamp": (datetime.utcnow() + timedelta(hours=5, minutes=30)).isoformat()
    }

async def sim_loop():
    while True:
        try:
            db = SessionLocal()
            for veh_id in list(sim_vehicles.keys()):
                if veh_id not in sim_vehicles: continue
                injections = [i for i in inject_queue if i['veh_id'] == veh_id or i['veh_id'] == 'ALL']
                
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
                        sim_vehicles[veh_id]['target_speed'] = 0
                        sim_vehicles[veh_id]['engine'] = 'OFF'
                        # Promote to step 2 for next tick
                        inj['type'] = 'CRASH_STEP_2'
                    elif inj['type'] == 'CRASH_STEP_2':
                        payload['speed'] = 0
                        payload['diagnostic_codes'] = ['AIRBAG_DEPLOYED']
                        inj['type'] = 'CRASH_DONE'
                    elif inj['type'] == 'INVALID':
                        payload['speed'] = 500
                    elif inj['type'] == 'OUT_OF_ORDER':
                        payload['vehicle_timestamp'] = (datetime.utcnow() + timedelta(hours=5, minutes=30) - timedelta(minutes=5)).isoformat()
                
                is_gap = any(i['type'] == 'GAP' for i in injections)
                if is_gap:
                    if veh_id not in gap_buffer:
                        gap_buffer[veh_id] = []
                    gap_buffer[veh_id].append(payload)
                else:
                    if veh_id in gap_buffer and gap_buffer[veh_id]:
                        gap_duration = len(gap_buffer[veh_id])
                        if gap_duration > 0:
                            log_error(db, veh_id, gap_buffer[veh_id][-1]["message_id"], "GAP", f"Telemetry gap of {gap_duration} seconds resolved")
                        for buffered_payload in gap_buffer[veh_id]:
                            process_telemetry(db, buffered_payload)
                        gap_buffer[veh_id] = []
                        
                    process_telemetry(db, payload)
                    
                    if any(i['type'] == 'DUPLICATE' for i in injections):
                        process_telemetry(db, payload)
                        
            # Decrement ticks
            for inj in inject_queue:
                if 'ticks' in inj:
                    inj['ticks'] -= 1
                    
            inject_queue[:] = [i for i in inject_queue if i.get('ticks', 1) > 0 and i['type'] not in ['CRASH_DONE', 'CRASH_STOP']]


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
    elif action == "START":
        if veh_id in sim_vehicles:
            sim_vehicles[veh_id]["engine"] = "ON"
            sim_vehicles[veh_id]["target_speed"] = 50.0
    elif action == "STOP":
        if veh_id in sim_vehicles:
            sim_vehicles[veh_id]["target_speed"] = 0.0
    else:
        inject_queue.append({"veh_id": veh_id, "type": action.upper(), "ticks": 3})
    
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

@app.get("/api/drivers")
def get_drivers(db: Session = Depends(get_db)):
    profiles = db.query(DriverProfile).all()
    drivers = []
    for p in profiles:
        v = db.query(Vehicle).filter(Vehicle.current_driver_id == p.driver_id).first()
        drivers.append({
            "driver_id": p.driver_id,
            "vehicle_id": v.vehicle_id if v else "Not Assigned",
            "vehicle_type": p.vehicle_type,
            "historical_mean_speed": p.historical_mean_speed,
            "safety_score": p.safety_score
        })
    return drivers

@app.get("/api/driver/state/{veh_id}")
def get_driver_state(veh_id: str):
    if veh_id not in sim_vehicles:
        raise HTTPException(404, "Not found")
    state = sim_vehicles[veh_id]
    return {
        "status": "Normal",
        "last_speed": state["speed"],
        "last_acceleration": state.get("accel", 0.0),
        "fuel_level": state["fuel"],
        "engine_status": state["engine"],
        "last_latitude": state["lat"],
        "last_longitude": state["lon"],
        "local_alerts": state.get("local_alerts", [])
    }

from pydantic import BaseModel
class NewVehicle(BaseModel):
    vehicle_id: str
    vehicle_type: str = "Standard"

class NewDriver(BaseModel):
    driver_id: str
    vehicle_type: str = "Standard"

@app.post("/api/drivers")
def create_driver(new_driver: NewDriver, db: Session = Depends(get_db)):
    profile_id = f"{new_driver.driver_id}_{new_driver.vehicle_type}"
    profile = db.query(DriverProfile).filter(DriverProfile.profile_id == profile_id).first()
    if not profile:
        profile = DriverProfile(profile_id=profile_id, driver_id=new_driver.driver_id, vehicle_type=new_driver.vehicle_type)
        db.add(profile)
        db.commit()
    return {"status": "success", "driver_id": new_driver.driver_id}

@app.post("/api/vehicles")
def create_vehicle(new_veh: NewVehicle, db: Session = Depends(get_db)):
    vehicle = db.query(Vehicle).filter(Vehicle.vehicle_id == new_veh.vehicle_id).first()
    if not vehicle:
        vehicle = Vehicle(vehicle_id=new_veh.vehicle_id, vehicle_type=new_veh.vehicle_type)
        db.add(vehicle)
        db.commit()
    
    # Add to simulator so it starts sending data
    if new_veh.vehicle_id not in sim_vehicles:
        sim_vehicles[new_veh.vehicle_id] = {
            "speed": 0.0,
            "target_speed": 0.0,
            "lat": 12.9716 + random.uniform(-0.05, 0.05),
            "lon": 80.2450 + random.uniform(-0.05, 0.05),
            "fuel": 100.0,
            "odometer": 0.0,
            "engine": "OFF"
        }
        
    return {"status": "success", "vehicle_id": new_veh.vehicle_id}

@app.delete("/api/vehicles/{vehicle_id}")
def delete_vehicle(vehicle_id: str, db: Session = Depends(get_db)):
    vehicle = db.query(Vehicle).filter(Vehicle.vehicle_id == vehicle_id).first()
    if not vehicle:
        raise HTTPException(404, "Vehicle not found")
    db.delete(vehicle)
    db.commit()
    if vehicle_id in sim_vehicles:
        del sim_vehicles[vehicle_id]
    return {"status": "success"}

@app.delete("/api/drivers/{driver_id}")
def delete_driver(driver_id: str, db: Session = Depends(get_db)):
    profile = db.query(DriverProfile).filter(DriverProfile.driver_id == driver_id).first()
    if not profile:
        raise HTTPException(404, "Driver not found")
    
    # Also log them out of any vehicle
    vehicle = db.query(Vehicle).filter(Vehicle.current_driver_id == driver_id).first()
    if vehicle:
        vehicle.current_driver_id = None
        vehicle.safety_score = 100.0
        vehicle.status = ""
        
    db.delete(profile)
    db.commit()
    return {"status": "success"}

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
    vehicle.safety_score = 100.0
    vehicle.status = "Normal"
    db.commit()
    
    if vehicle_id in sim_vehicles:
        sim_vehicles[vehicle_id]["engine"] = "OFF"
        sim_vehicles[vehicle_id]["target_speed"] = 0.0
        sim_vehicles[vehicle_id]["speed"] = 0.0
        sim_vehicles[vehicle_id]["accel"] = 0.0
        
    return {"status": "success", "message": f"Driver {driver_id} logged into {vehicle_id}"}

@app.post("/api/vehicles/{vehicle_id}/logout")
def driver_logout(vehicle_id: str, db: Session = Depends(get_db)):
    vehicle = db.query(Vehicle).filter(Vehicle.vehicle_id == vehicle_id).first()
    if vehicle:
        vehicle.current_driver_id = None
        vehicle.safety_score = 100.0
        vehicle.status = ""
        db.commit()
        
    if vehicle_id in sim_vehicles:
        sim_vehicles[vehicle_id]["engine"] = "OFF"
        sim_vehicles[vehicle_id]["target_speed"] = 0.0
        sim_vehicles[vehicle_id]["speed"] = 0.0
        sim_vehicles[vehicle_id]["accel"] = 0.0
        
    return {"status": "success"}

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
