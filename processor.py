import uuid
import json
from datetime import datetime
from config import SPEED_LIMIT_KMH, HARSH_BRAKING_THRESHOLD_MS2, HARSH_ACCEL_THRESHOLD_MS2, TELEMETRY_TIMEOUT_SECONDS, MAX_VALID_SPEED_KMH, INCIDENT_SPEED_DROP_KMH
from database import Vehicle, Telemetry, SafetyEvent, Incident, TelemetryError, DriverProfile

def process_telemetry(db, data: dict):
    msg_id = data.get("message_id")
    veh_id = data.get("vehicle_id")
    try:
        v_time = datetime.fromisoformat(data.get("vehicle_timestamp"))
    except:
        v_time = datetime.utcnow()
        
    driver_id = data.get("driver_id")
    veh_type = data.get("vehicle_type", "Standard")
    
    # 1. Duplicate Detection
    existing_tel = db.query(Telemetry).filter(Telemetry.message_id == msg_id).first()
    if existing_tel:
        log_error(db, veh_id, msg_id, "DUPLICATE", "Duplicate message received")
        return {"status": "rejected", "reason": "duplicate"}
    
    vehicle = db.query(Vehicle).filter(Vehicle.vehicle_id == veh_id).first()
    if not vehicle:
        vehicle = Vehicle(vehicle_id=veh_id, vehicle_type=veh_type, current_driver_id=driver_id)
        db.add(vehicle)
        db.commit()
    else:
        if driver_id:
            vehicle.current_driver_id = driver_id
            vehicle.vehicle_type = veh_type
            
    # Load driver profile if driver is assigned
    driver_profile = None
    if vehicle.current_driver_id:
        profile_id = f"{vehicle.current_driver_id}_{vehicle.vehicle_type}"
        driver_profile = db.query(DriverProfile).filter(DriverProfile.profile_id == profile_id).first()
        if not driver_profile:
            driver_profile = DriverProfile(profile_id=profile_id, driver_id=vehicle.current_driver_id, vehicle_type=vehicle.vehicle_type)
            db.add(driver_profile)
            db.commit()

    # 2. Invalid Data Validation
    speed = data.get("speed", 0)
    if speed < 0 or speed > MAX_VALID_SPEED_KMH:
        log_error(db, veh_id, msg_id, "INVALID", f"Invalid speed value: {speed}")
        return {"status": "rejected", "reason": "invalid_value"}
    
    # 3. Out-of-order / Late Data
    if vehicle.last_timestamp and v_time < vehicle.last_timestamp:
        log_error(db, veh_id, msg_id, "LATE", "Out-of-order packet received")
        # Save telemetry but do not update vehicle state
        save_telemetry(db, data, v_time, "LATE")
        return {"status": "processed", "note": "late_data"}
    
    # 4. Missing Data Gap Detection (handled partially by background job, but can check here too)
    if vehicle.last_timestamp:
        gap = (v_time - vehicle.last_timestamp).total_seconds()
        if gap > TELEMETRY_TIMEOUT_SECONDS:
            log_error(db, veh_id, msg_id, "GAP", f"Telemetry gap of {gap} seconds")

    # Proceed to update vehicle state and detect events
    prev_speed = vehicle.last_speed if vehicle.last_speed is not None else speed
    prev_time = vehicle.last_timestamp
    
    vehicle.last_speed = speed
    vehicle.last_latitude = data.get("latitude")
    vehicle.last_longitude = data.get("longitude")
    vehicle.engine_status = data.get("engine_status")
    vehicle.fuel_level = data.get("fuel_level")
    vehicle.odometer = data.get("odometer")
    vehicle.last_timestamp = v_time
    vehicle.status = "Normal"

    # Save telemetry
    save_telemetry(db, data, v_time, "Valid")
    
    # 5. Safety Event Detection
    detect_safety_events(db, vehicle, driver_profile, data, prev_speed, prev_time, v_time)
    
    db.commit()
    return {"status": "processed"}

def log_error(db, veh_id, msg_id, err_type, details):
    err = TelemetryError(
        error_id=str(uuid.uuid4()),
        vehicle_id=veh_id,
        message_id=msg_id,
        error_type=err_type,
        details=details
    )
    db.add(err)
    db.commit()

def save_telemetry(db, data, v_time, val_status):
    tel = Telemetry(
        message_id=data["message_id"],
        vehicle_id=data["vehicle_id"],
        latitude=data.get("latitude"),
        longitude=data.get("longitude"),
        engine_status=data.get("engine_status"),
        fuel_level=data.get("fuel_level"),
        speed=data.get("speed"),
        odometer=data.get("odometer"),
        diagnostic_codes=json.dumps(data.get("diagnostic_codes", [])),
        vehicle_timestamp=v_time,
        validation_status=val_status
    )
    db.add(tel)
    db.commit()

def detect_safety_events(db, vehicle, driver_profile, data, prev_speed, prev_time, curr_time):
    speed = data.get("speed", 0)
    veh_id = vehicle.vehicle_id
    lat_lon = f"{data.get('latitude')},{data.get('longitude')}"
    d_id = vehicle.current_driver_id
    # Speed > 100 check
    if speed > 100:
        evt = SafetyEvent(
            event_id=str(uuid.uuid4()),
            vehicle_id=veh_id,
            driver_id=d_id,
            event_type="SPEEDING_EXTREME",
            severity="CRITICAL",
            timestamp=curr_time,
            location=lat_lon,
            description=f"Extreme Speeding at {speed} km/h"
        )
        db.add(evt)
        vehicle.risk_level = "Critical"
        vehicle.status = "Critical"
        vehicle.safety_score = max(0, vehicle.safety_score - 3)
    # Normal Speeding check
    elif speed > SPEED_LIMIT_KMH:
        evt = SafetyEvent(
            event_id=str(uuid.uuid4()),
            vehicle_id=veh_id,
            driver_id=d_id,
            event_type="SPEEDING",
            severity="WARNING",
            timestamp=curr_time,
            location=lat_lon,
            description=f"Speeding at {speed} km/h"
        )
        db.add(evt)
        vehicle.risk_level = "High Risk"
        vehicle.status = "Warning"
        vehicle.safety_score = max(0, vehicle.safety_score - 1)

    # Anomaly Detection (Behavioural difference) using DriverProfile
    if driver_profile and driver_profile.total_speed_readings > 10:
        mean = driver_profile.historical_mean_speed
        var = driver_profile.historical_speed_variance
        std_dev = max(var ** 0.5, 5.0) # minimum std dev of 5km/h to avoid micro anomalies
        
        # If speed is more than 3 std deviations away from mean (and speed > 30 to ignore just stopping)
        if abs(speed - mean) > (3 * std_dev) and speed > 30:
            confidence = min(99, int(abs(speed - mean) / std_dev * 10) + 50)
            evt = SafetyEvent(
                event_id=str(uuid.uuid4()),
                vehicle_id=veh_id,
                driver_id=d_id,
                event_type="BEHAVIOUR_ANOMALY",
                severity="WARNING",
                timestamp=curr_time,
                location=lat_lon,
                description=f"Behaviour anomaly detected for {d_id} in {driver_profile.vehicle_type} (Confidence: {confidence}%). Reason: Current driving pattern differs significantly from historical behaviour. (Mean: {mean:.1f}, Current: {speed})"
            )
            db.add(evt)
            vehicle.status = "Warning"
            
    # Update historical data for next time (Welford's online algorithm)
    if driver_profile:
        driver_profile.total_speed_readings += 1
        n = driver_profile.total_speed_readings
        delta = speed - driver_profile.historical_mean_speed
        driver_profile.historical_mean_speed += delta / n
        delta2 = speed - driver_profile.historical_mean_speed
        driver_profile.historical_speed_variance = ((driver_profile.historical_speed_variance * (n - 1)) + delta * delta2) / n if n > 1 else 0.0

    # Acceleration/Deceleration
    if prev_time and prev_time != curr_time:
        dt = (curr_time - prev_time).total_seconds()
        if dt > 0:
            # speed in km/h, convert to m/s
            dv_ms = (speed - prev_speed) / 3.6
            accel = dv_ms / dt
            
            if accel < HARSH_BRAKING_THRESHOLD_MS2:
                evt = SafetyEvent(
                    event_id=str(uuid.uuid4()),
                    vehicle_id=veh_id,
                    driver_id=d_id,
                    event_type="HARSH_BRAKING",
                    severity="WARNING",
                    timestamp=curr_time,
                    location=lat_lon,
                    description=f"Harsh braking: {accel:.1f} m/s²"
                )
                db.add(evt)
                vehicle.safety_score = max(0, vehicle.safety_score - 2)

            elif accel > HARSH_ACCEL_THRESHOLD_MS2:
                evt = SafetyEvent(
                    event_id=str(uuid.uuid4()),
                    vehicle_id=veh_id,
                    driver_id=d_id,
                    event_type="HARSH_ACCEL",
                    severity="WARNING",
                    timestamp=curr_time,
                    location=lat_lon,
                    description=f"Harsh acceleration: {accel:.1f} m/s²"
                )
                db.add(evt)
                vehicle.safety_score = max(0, vehicle.safety_score - 1.5)
            
            # Possible Crash Detection
            # high speed -> sudden decel -> stopped + possible diag
            if prev_speed > 50 and speed == 0 and accel < (HARSH_BRAKING_THRESHOLD_MS2 * 2):
                diag = data.get("diagnostic_codes", [])
                score = 60 # base for sudden stop
                if prev_speed > 70: score += 20
                if diag: score += 20
                
                inc = Incident(
                    incident_id=str(uuid.uuid4()),
                    vehicle_id=veh_id,
                    timestamp=curr_time,
                    latitude=data.get("latitude"),
                    longitude=data.get("longitude"),
                    severity="CRITICAL",
                    reason=f"Sudden stop from {prev_speed}km/h",
                    confidence_score=score
                )
                db.add(inc)
                vehicle.status = "Critical"
                vehicle.risk_level = "Critical"
