import os

# 1. APP NAME MUST BE CONFIGURABLE
app_name = os.getenv("APP_NAME", "VehicleHub")

# General Configuration
DATABASE_URL = "sqlite:///./fleet.db"
SIMULATOR_INTERVAL_SECONDS = 1.0

# Telemetry Processing Thresholds
SPEED_LIMIT_KMH = 80
HARSH_BRAKING_THRESHOLD_MS2 = -5.0
HARSH_ACCEL_THRESHOLD_MS2 = 3.0
TELEMETRY_TIMEOUT_SECONDS = 3  # Gap detection
MAX_VALID_SPEED_KMH = 200

# Incident Detection Thresholds
INCIDENT_SPEED_DROP_KMH = 40
INCIDENT_TIME_WINDOW_SECONDS = 5

