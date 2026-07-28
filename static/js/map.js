// ── Mapa de ubicación (Leaflet + OpenStreetMap) ──
let locationMap = null;
let locationMarker = null;

function setLocationPin(lat, lon, recenter) {
    document.getElementById('input-latitude').value = lat.toFixed(4);
    document.getElementById('input-longitude').value = lon.toFixed(4);
    if (locationMarker) {
        locationMarker.setLatLng([lat, lon]);
    }
    if (recenter && locationMap) {
        locationMap.setView([lat, lon], 12);
    }
}

function toggleLocationMap() {
    const panel = document.getElementById('location-map-panel');
    const wasHidden = panel.classList.contains('hidden');
    panel.classList.toggle('hidden');
    if (!wasHidden) return; // se estaba cerrando, nada más que hacer

    const lat = parseFloat(document.getElementById('input-latitude').value) || -32.99;
    const lon = parseFloat(document.getElementById('input-longitude').value) || -71.27;

    if (!locationMap) {
        locationMap = L.map('location-map').setView([lat, lon], 11);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; OpenStreetMap',
            maxZoom: 18
        }).addTo(locationMap);
        locationMarker = L.marker([lat, lon], { draggable: true }).addTo(locationMap);
        locationMarker.on('dragend', () => {
            const p = locationMarker.getLatLng();
            setLocationPin(p.lat, p.lng, false);
        });
        locationMap.on('click', (e) => {
            setLocationPin(e.latlng.lat, e.latlng.lng, false);
        });
    }
    // El mapa se inicializa mientras el panel estaba oculto (0 alto) — hay que forzar el recálculo de tamaño
    setTimeout(() => { locationMap.invalidateSize(); locationMap.setView([lat, lon], locationMap.getZoom()); }, 150);
}

function searchMapLocation() {
    const query = document.getElementById('map-search-input').value.trim();
    if (!query) return;
    fetch(`https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(query)}`)
        .then(res => res.json())
        .then(results => {
            if (!results || results.length === 0) {
                showToast('No se encontró esa dirección');
                return;
            }
            const { lat, lon } = results[0];
            setLocationPin(parseFloat(lat), parseFloat(lon), true);
        })
        .catch(() => showToast('Error buscando la dirección'));
}
