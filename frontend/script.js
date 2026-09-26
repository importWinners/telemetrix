const API_BASE = "/api";

let pollInterval;
let currentRole = null; // 'manager' or 'driver'
let driverVid = null;

document.addEventListener('DOMContentLoaded', () => {
    fetchConfig();
    setupNavigation();
    populateVehicleDropdown();
});

async function populateVehicleDropdown() {
    try {
        const res = await fetch(`${API_BASE}/vehicles`);
        const vehicles = await res.json();
        const select = document.getElementById('driver-vid-select');
        select.innerHTML = '';
        vehicles.forEach(v => {
            const opt = document.createElement('option');
            opt.value = v.vehicle_id;
            opt.innerText = v.vehicle_id;
            select.appendChild(opt);
        });
    } catch (e) { console.error("Failed to load vehicles for dropdown"); }
}

async function loginAs(role) {
    if (role === 'driver') {
        const did = document.getElementById('driver-id').value.trim().toUpperCase();
        const vid = document.getElementById('driver-vid-select').value;
        const vtype = 'Alto';
        
        if (!did) return alert('Enter Driver ID');
        if (!vid) return alert('Please select a Vehicle from the dropdown');
        
        // Log into vehicle on backend
        await fetch(`${API_BASE}/vehicles/${vid}/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ driver_id: did, vehicle_type: vtype })
        });
        
        driverVid = vid;
        currentRole = 'driver';
        document.getElementById('driver-nav').style.display = 'flex';
        document.getElementById('manager-nav').style.display = 'none';
        document.getElementById('role-display').innerText = 'Driver: ' + did + ' | ' + vid + ' (' + vtype + ')';
        document.getElementById('driver-my-vid').innerText = vid;
        switchTab('driver-dashboard', 'My Vehicle');
    } else {
        currentRole = 'manager';
        document.getElementById('driver-nav').style.display = 'none';
        document.getElementById('manager-nav').style.display = 'flex';
        document.getElementById('role-display').innerText = 'Fleet Manager';
        switchTab('dashboard', 'Dashboard');
    }
    
    document.getElementById('login-overlay').style.display = 'none';
    startPolling();
}

async function logout() {
    if (currentRole === 'driver' && driverVid) {
        try {
            await fetch(`${API_BASE}/vehicles/${driverVid}/logout`, { method: 'POST' });
        } catch (e) {
            console.error("Logout error", e);
        }
    }
    currentRole = null;
    driverVid = null;
    clearInterval(pollInterval);
    document.getElementById('login-overlay').style.display = 'flex';
}

function switchTab(target, title) {
    const sections = document.querySelectorAll('.view-section');
    sections.forEach(sec => sec.classList.remove('active'));
    document.getElementById(`view-${target}`).classList.add('active');
    document.getElementById('section-title').innerText = title;
}

async function fetchConfig() {
    try {
        const res = await fetch(`${API_BASE}/config`);
        const data = await res.json();
        document.querySelectorAll('.brand-text').forEach(e => e.innerText = data.app_name);
        document.getElementById('login-brand').innerText = data.app_name;
        document.getElementById('page-title').innerText = data.app_name;
    } catch (e) {
        console.error("Failed to load config");
    }
}

function setupNavigation() {
    const links = document.querySelectorAll('.nav-links li');

    links.forEach(link => {
        link.addEventListener('click', () => {
            links.forEach(l => l.classList.remove('active'));
            link.classList.add('active');
            switchTab(link.getAttribute('data-target'), link.innerText);
        });
    });

    document.querySelector('.close-btn').addEventListener('click', () => {
        document.getElementById('incident-modal').classList.add('hidden');
    });
}

function startPolling() {
    pollData();
    pollInterval = setInterval(pollData, 1000);
}

async function pollData() {
    try {
        if (currentRole === 'manager') {
            await Promise.all([
                updateStats(),
                updateVehicles(),
                updateEvents(),
                updateIncidents(),
                updateDrivers()
            ]);
        } else if (currentRole === 'driver') {
            await updateDriverView();
        }
    } catch (e) {
        console.error("Polling error", e);
    }
}

async function updateDriverView() {
    // Fetch vehicle state directly from simulator for driver view
    const res = await fetch(`${API_BASE}/driver/state/${driverVid}`);
    if (!res.ok) return;
    const me = await res.json();
    if (me) {
        document.getElementById('driver-my-status').innerText = me.status;
        document.getElementById('driver-my-status').className = `badge badge-${me.status === 'Normal' ? 'normal' : me.status === 'Warning' ? 'warning' : 'critical'}`;
        document.getElementById('driver-my-speed').innerText = me.last_speed ? me.last_speed.toFixed(1) : '0';
        document.getElementById('driver-my-accel').innerText = me.last_acceleration ? me.last_acceleration.toFixed(2) : '0.00';
        document.getElementById('driver-my-fuel').innerText = me.fuel_level ? me.fuel_level.toFixed(1) + '%' : '-';
        document.getElementById('driver-my-engine').innerText = me.engine_status || '-';
        document.getElementById('driver-my-location').innerText = `${me.last_latitude ? me.last_latitude.toFixed(4) : '-'}, ${me.last_longitude ? me.last_longitude.toFixed(4) : '-'}`;
    }

    // Fetch alerts
    const resE = await fetch(`${API_BASE}/events`);
    const events = await resE.json();
    const myEvents = events.filter(e => e.vehicle_id === driverVid);
    
    const list = document.getElementById('driver-alerts-list');
    list.innerHTML = '';
    if (myEvents.length === 0) {
        list.innerHTML = '<li>No alerts.</li>';
        return;
    }
    
    myEvents.forEach(e => {
        const li = document.createElement('li');
        li.className = e.severity === 'CRITICAL' ? 'evt-critical' : 'evt-warning';
        const time = new Date(e.timestamp).toLocaleTimeString();
        li.innerHTML = `<strong>${time}</strong><br>${e.description}`;
        list.appendChild(li);
    });
}

async function updateStats() {
    const res = await fetch(`${API_BASE}/dashboard/stats`);
    const data = await res.json();

    document.getElementById('stat-total').innerText = data.total_vehicles;
    document.getElementById('stat-active').innerText = data.active_vehicles;
    document.getElementById('stat-events').innerText = data.safety_events;
    document.getElementById('stat-incidents').innerText = data.incidents;

    document.getElementById('health-processed').innerText = data.health.processed;
    document.getElementById('health-duplicates').innerText = data.health.duplicates;
    document.getElementById('health-late').innerText = data.health.late;
    document.getElementById('health-invalid').innerText = data.health.invalid;
    document.getElementById('health-gaps').innerText = data.health.gaps;
}

async function updateVehicles() {
    const res = await fetch(`${API_BASE}/vehicles`);
    let vehicles = await res.json();
    
    const searchVal = document.getElementById('vehicle-search').value.toLowerCase();
    if (searchVal) {
        vehicles = vehicles.filter(v => 
            v.vehicle_id.toLowerCase().includes(searchVal) || 
            (v.current_driver_id && v.current_driver_id.toLowerCase().includes(searchVal))
        );
    }

    const tbody = document.getElementById('vehicle-table-body');
    tbody.innerHTML = '';

    vehicles.forEach(v => {
        let badgeClass = 'badge-normal';
        if (v.status === 'Warning') badgeClass = 'badge-warning';
        if (v.status === 'Critical') badgeClass = 'badge-critical';
        
        const driverDisplay = v.current_driver_id ? v.current_driver_id : '<span style="color:var(--text-muted)">No driver</span>';

        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><strong>${v.vehicle_id}</strong></td>
            <td>${driverDisplay}</td>
            <td><span class="badge ${badgeClass}">${v.status}</span></td>
            <td>${Math.round(v.safety_score)}</td>
            <td>${v.last_speed !== null && v.last_speed !== undefined ? v.last_speed.toFixed(1) : 0} km/h</td>
            <td>${v.last_acceleration !== null && v.last_acceleration !== undefined ? v.last_acceleration.toFixed(2) : '0.00'} m/s²</td>
            <td>${v.fuel_level !== null && v.fuel_level !== undefined ? v.fuel_level.toFixed(1) + '%' : '-'}</td>
            <td>${v.last_latitude !== null && v.last_latitude !== undefined ? v.last_latitude.toFixed(4) : '-'}, ${v.last_longitude !== null && v.last_longitude !== undefined ? v.last_longitude.toFixed(4) : '-'}</td>
        `;
        tbody.appendChild(tr);
    });
}

async function updateDrivers() {
    try {
        const res = await fetch(`${API_BASE}/drivers`);
        const drivers = await res.json();
        
        const tbody = document.getElementById('driver-table-body');
        tbody.innerHTML = '';
        
        drivers.forEach(d => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><strong>${d.driver_id}</strong></td>
                <td><span class="badge badge-normal">${d.vehicle_id}</span></td>
                <td>${d.vehicle_type}</td>
                <td>${d.historical_mean_speed ? d.historical_mean_speed.toFixed(1) : '0.0'} km/h</td>
                <td>${Math.round(d.safety_score)}</td>
            `;
            tbody.appendChild(tr);
        });
    } catch (e) {
        console.error("Failed to load drivers", e);
    }
}

async function updateEvents() {
    const res = await fetch(`${API_BASE}/events`);
    const events = await res.json();

    const list = document.getElementById('recent-events-list');
    list.innerHTML = '';

    if (events.length === 0) {
        list.innerHTML = '<li>No events yet.</li>';
        return;
    }

    events.forEach(e => {
        const li = document.createElement('li');
        li.className = e.severity === 'CRITICAL' ? 'evt-critical' : 'evt-warning';
        const time = new Date(e.timestamp).toLocaleTimeString();
        li.innerHTML = `<strong>${time}</strong> - ${e.vehicle_id} - ${e.description}`;
        list.appendChild(li);
    });
}

async function updateIncidents() {
    const res = await fetch(`${API_BASE}/incidents`);
    const incidents = await res.json();

    const list = document.getElementById('incident-list');
    
    // Only redraw if length changed (simple hack for hackathon, better would be diffing)
    if (list.children.length === incidents.length && incidents.length !== 0) return;
    
    list.innerHTML = '';

    if (incidents.length === 0) {
        list.innerHTML = '<p style="color: var(--text-muted)">No active incidents.</p>';
        return;
    }

    incidents.forEach(i => {
        const div = document.createElement('div');
        div.className = 'incident-card';
        const time = new Date(i.timestamp).toLocaleTimeString();
        div.innerHTML = `
            <div>
                <h4>🚨 ${i.vehicle_id} - Possible Crash Detected</h4>
                <p style="color: var(--text-muted); font-size: 14px; margin-top: 4px;">Time: ${time} | Status: ${i.status}</p>
                <p style="font-size: 14px; margin-top: 4px;">${i.reason}</p>
            </div>
            <button class="btn btn-secondary" onclick="viewIncident('${i.incident_id}')">View Timeline</button>
        `;
        list.appendChild(div);
    });
}

async function viewIncident(id) {
    const res = await fetch(`${API_BASE}/incidents/${id}/timeline`);
    const timeline = await res.json();

    document.getElementById('incident-modal').classList.remove('hidden');
    
    const tlList = document.getElementById('incident-timeline');
    tlList.innerHTML = '';

    let prevSpeed = null;
    timeline.forEach(t => {
        const time = new Date(t.vehicle_timestamp).toLocaleTimeString();
        const li = document.createElement('li');
        
        let text = `${time} - Speed: ${t.speed.toFixed(1)} km/h`;
        
        if (prevSpeed !== null && prevSpeed > 20 && t.speed === 0) {
            li.classList.add('timeline-critical');
            text += ` ⬅️ SUDDEN STOP DETECTED`;
        }
        
        if (t.diagnostic_codes && t.diagnostic_codes !== '[]') {
            text += ` | Diag: ${t.diagnostic_codes}`;
            li.classList.add('timeline-critical');
        }

        li.innerText = text;
        tlList.appendChild(li);
        prevSpeed = t.speed;
    });
}

async function injectScenario(action) {
    try {
        let url = `${API_BASE}/inject/${action}`;
        if (driverVid) {
            url += `?veh_id=${driverVid}`;
        }
        const res = await fetch(url, { method: 'POST' });
        const data = await res.json();
        // Silently succeed
    } catch (e) {
        console.error("Failed to inject scenario.", e);
    }
}

async function promptAddVehicle() {
    const vid = prompt("Enter new Vehicle ID (e.g. V109):");
    if (!vid) return;
    try {
        await fetch(`${API_BASE}/vehicles`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ vehicle_id: vid })
        });
        alert(`Vehicle ${vid} added! Telemetry simulator will now pick it up.`);
        updateVehicles();
        populateVehicleDropdown();
    } catch (e) {
        console.error("Failed to add vehicle", e);
    }
}

async function promptAddDriver() {
    const did = prompt("Enter new Driver ID (e.g. D99):");
    if (!did) return;
    try {
        await fetch(`${API_BASE}/drivers`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ driver_id: did })
        });
        alert(`Driver ${did} added successfully!`);
        updateDrivers();
    } catch (e) {
        console.error("Failed to add driver", e);
    }
}

