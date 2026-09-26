from sqlalchemy import create_engine, Column, String, Float, Integer, DateTime, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
from config import DATABASE_URL

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Vehicle(Base):
    __tablename__ = "vehicles"
    vehicle_id = Column(String, primary_key=True, index=True)
    status = Column(String, default="Normal")  # Normal, Warning, High Risk, Critical, Offline
    last_latitude = Column(Float, nullable=True)
    last_longitude = Column(Float, nullable=True)
    last_speed = Column(Float, nullable=True)
    engine_status = Column(String, nullable=True)
    fuel_level = Column(Float, nullable=True)
    odometer = Column(Float, nullable=True)
    last_timestamp = Column(DateTime, nullable=True)
    risk_level = Column(String, default="Low")
    safety_score = Column(Float, default=100.0)
    
    # Removed historical tracking from Vehicle (moved to DriverProfile)
    current_driver_id = Column(String, nullable=True)
    vehicle_type = Column(String, default="Standard")

class DriverProfile(Base):
    __tablename__ = "driver_profiles"
    profile_id = Column(String, primary_key=True, index=True) # e.g. driver123_Standard
    driver_id = Column(String, index=True)
    vehicle_type = Column(String)
    historical_mean_speed = Column(Float, default=0.0)
    historical_speed_variance = Column(Float, default=0.0)
    total_speed_readings = Column(Integer, default=0)

class Telemetry(Base):
    __tablename__ = "telemetry"
    message_id = Column(String, primary_key=True, index=True)
    vehicle_id = Column(String, index=True)
    latitude = Column(Float)
    longitude = Column(Float)
    engine_status = Column(String)
    fuel_level = Column(Float)
    speed = Column(Float)
    odometer = Column(Float)
    diagnostic_codes = Column(String) # JSON string
    vehicle_timestamp = Column(DateTime)
    ingestion_timestamp = Column(DateTime, default=datetime.utcnow)
    validation_status = Column(String, default="Valid")

class SafetyEvent(Base):
    __tablename__ = "safety_events"
    event_id = Column(String, primary_key=True, index=True)
    vehicle_id = Column(String, index=True)
    driver_id = Column(String, index=True, nullable=True)
    event_type = Column(String) # SPEEDING, HARSH_BRAKING, HARSH_ACCEL
    severity = Column(String)
    timestamp = Column(DateTime)
    location = Column(String) # JSON string or lat,lon
    description = Column(String)
    metadata_info = Column(String) # JSON string

class Incident(Base):
    __tablename__ = "incidents"
    incident_id = Column(String, primary_key=True, index=True)
    vehicle_id = Column(String, index=True)
    timestamp = Column(DateTime)
    latitude = Column(Float)
    longitude = Column(Float)
    severity = Column(String)
    status = Column(String, default="NEW") # NEW, ACKNOWLEDGED, RESOLVED
    reason = Column(String)
    confidence_score = Column(Float)

class TelemetryError(Base):
    __tablename__ = "telemetry_errors"
    error_id = Column(String, primary_key=True, index=True)
    vehicle_id = Column(String, index=True)
    message_id = Column(String, index=True)
    error_type = Column(String) # DUPLICATE, LATE, INVALID, GAP
    timestamp = Column(DateTime, default=datetime.utcnow)
    details = Column(String)

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
