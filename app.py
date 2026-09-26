from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import uvicorn
from database import init_db, get_db, Vehicle, Telemetry, SafetyEvent, Incident, TelemetryError
from processor import process_telemetry
from config import app_name

app = FastAPI(title=app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    init_db()

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
    active_vehicles = db.query(Vehicle).filter(Vehicle.speed > 0).count()
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
