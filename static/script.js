let eventSource = null;
let alerts = [];

// Load initial
async function loadInitial() {
    const res = await fetch('/alerts');
    alerts = await res.json().alerts;
    renderAlerts();
    updateStatus();
}

// SSE
function connectSSE() {
    eventSource = new EventSource('/events');
    eventSource.onmessage = (e) => {
        const data = JSON.parse(e.data);
        if (data.type !== 'heartbeat') {
            alerts.unshift(data);
            renderAlerts();
            updateStatus();
        }
    };
    eventSource.onerror = () => connectSSE(); // Reconnect
}

// Render table
function renderAlerts() {
    const tbody = document.getElementById('alerts-body');
    tbody.innerHTML = '';
    alerts.slice(0, 50).forEach(a => {
        const row = tbody.insertRow();
        row.innerHTML = `
            <td><strong>${a.symbol}</strong></td>
            <td>$${a.price}</td>
            <td class="positive">+${a.change_pct}%</td>
            <td>${a.vol_ratio}x</td>
            <td>${a.volume.toLocaleString()}</td>
            <td>${a.time}</td>
        `;
    });
}

// Status poll
async function updateStatus() {
    const res = await fetch('/status');
    const s = await res.json();
    document.getElementById('connected').textContent = `WS: ${s.connected ? 'Online' : 'Offline'}`;
    document.getElementById('connected').className = s.connected ? 'status-on' : 'status-off';
    document.getElementById('market').textContent = `Market: ${s.market_open ? 'Open' : 'Closed'}`;
    document.getElementById('market').className = s.market_open ? 'status-on' : 'status-off';
    document.getElementById('alerts-count').textContent = `Alerts: ${s.alerts_count}`;
    document.getElementById('active').textContent = `Active: ${s.active_symbols}`;
}

// Init
loadInitial();
connectSSE();
setInterval(updateStatus, 5000); // Poll status