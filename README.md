# {{ app_name }} — Connected Vehicle Safety & Incident Response

## Problem

Fleet operators receive massive amounts of telemetry. This raw data is noisy and difficult to interpret without dedicated engineering teams.

## Solution

{{ app_name }} converts noisy telemetry into actionable safety intelligence. 
It supports two roles:
- **Fleet Manager:** Real-time overview of the entire fleet, crash detection, and system health.
- **Driver:** A focused view for the assigned vehicle that displays real-time driving alerts.

**PREVENT → DETECT → RESPOND**

## Features

- **Role-Based Views:** Drivers see personal alerts; Fleet Managers see everything.
- **Dynamic Anomaly Detection:** Calculates safety baselines per driver-vehicle combination. Speeds deviating significantly from the norm trigger behavioural anomaly alerts.
- **Data Reliability Pipeline:** Automatically handles duplicate packets, out-of-order data, missing gaps, and invalid sensor readings.
- **Crash Detection:** Recognizes severe incidents based on rapid deceleration patterns.
- **Built-In Simulator:** Automatically generates live fleet data with a built-in control panel for judges to inject faults on demand.

## Quick Setup

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Start the API & Dashboard:**
   ```bash
   python app.py
   ```

3. **Start the Simulator (New Terminal):**
   ```bash
   python simulator.py
   ```

4. **View the Dashboard:** Open `http://localhost:8000` in your browser.

## Demo Instructions (Dual-Laptop Setup)

1. Open `http://localhost:8000` on **Laptop 1** (Driver). Select "Login as Driver", enter Driver ID `D1`. (The application simulates an IP-bound system, so this laptop is automatically tied to vehicle `TN14-4289`, an Alto).
2. The python simulator will automatically begin generating normal telemetry data for this vehicle in the background.
3. Open `http://localhost:8000` on **Laptop 2** (Fleet Manager). Select "Login as Fleet Manager".
4. To test the behavioural anomaly engine, **go back to Laptop 1 (Driver)** and use the Driver's "Simulator Control" panel to inject faults (like "Inject Speeding").
5. Watch the alerts appear instantly on the Driver's personal feed and the Manager's global feed.
6. Demonstrate pipeline resilience by injecting duplicate packets or out-of-order data via the Driver's panel and observing the server handle them without crashing.

## Deployment (Render)

This application is ready to deploy on Render.com:
- Connect this repository to a new Render **Web Service**.
- Build Command: `pip install -r requirements.txt`
- Start Command: `python app.py` (The app dynamically binds to Render's `$PORT`).
- *Note: Run the `simulator.py` script locally to feed data into your deployed Render URL by modifying `API_URL` in `simulator.py`.*
