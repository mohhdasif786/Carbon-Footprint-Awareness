/* ═══════════════════════════════════════════════════════════════════
   EcoTrack — Carbon Footprint Awareness Platform
   Main Application JavaScript
   Map: Leaflet.js + OpenStreetMap (free, no API key)
   Charts: Google Charts
   AI: Gemini via backend
   ═══════════════════════════════════════════════════════════════════ */

'use strict';

// ─── App State ────────────────────────────────────────────────────────────────
const STATE = {
  sessionId:       null,
  carbonData:      null,
  insightsData:    null,
  map:             null,         // Leaflet map instance
  mapMarkers:      [],           // Leaflet markers/layers
  activeMapLayers: { ev: true, parks: false, transit: false, bike: false },
  tileLayer:       null,
  activityLog:     [],
  streak:          0,
  lastActivityDate: null,
  totalSaved:      0,
  chartsLoaded:    false,
  currentSection:  'calculator',
};

const API = '/api';

// ─── Initialization ───────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  initSession();
  loadLocalStorage();
  spawnParticles();
  initGoogleCharts();
  await loadPlatformStats();
  updateBadges();
  renderActivityLog();
  renderProgressChart();
  updateStreakDisplay();
  updateNavScore();
  updateChatContext();

  setTimeout(() => document.getElementById('loader').classList.add('hidden'), 1200);
  setTimeout(animateHeroCounters, 1500);

  trackEvent('page_view', { page: 'home' });
});

// ─── Session Management ───────────────────────────────────────────────────────
function initSession() {
  let sid = localStorage.getItem('eco_session_id');
  if (!sid) {
    sid = 'eco_' + Date.now() + '_' + Math.random().toString(36).slice(2, 9);
    localStorage.setItem('eco_session_id', sid);
  }
  STATE.sessionId = sid;
}

function loadLocalStorage() {
  const saved = localStorage.getItem('eco_carbon_data');
  if (saved) STATE.carbonData = JSON.parse(saved);

  const log = localStorage.getItem('eco_activity_log');
  if (log) STATE.activityLog = JSON.parse(log);

  const streak = localStorage.getItem('eco_streak');
  if (streak) STATE.streak = parseInt(streak, 10);

  const lastDate = localStorage.getItem('eco_last_activity');
  if (lastDate) STATE.lastActivityDate = lastDate;

  const savedTotal = localStorage.getItem('eco_total_saved');
  if (savedTotal) STATE.totalSaved = parseFloat(savedTotal);

  // Reset streak if not active yesterday/today
  if (STATE.lastActivityDate) {
    const today     = new Date().toDateString();
    const yesterday = new Date(Date.now() - 86400000).toDateString();
    const last      = new Date(STATE.lastActivityDate).toDateString();
    if (last !== today && last !== yesterday) {
      STATE.streak = 0;
      localStorage.setItem('eco_streak', '0');
    }
  }
}

// ─── Google Charts ────────────────────────────────────────────────────────────
function initGoogleCharts() {
  google.charts.load('current', { packages: ['corechart', 'geochart', 'bar'] });
  google.charts.setOnLoadCallback(() => {
    STATE.chartsLoaded = true;
    if (STATE.carbonData) renderBreakdownChart(STATE.carbonData);
    renderProgressChart();
    renderGeoChart();
  });
}

// ─── Navigation ───────────────────────────────────────────────────────────────
function showSection(name) {
  document.querySelectorAll('.app-section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.nav-link').forEach(l => { l.classList.remove('active'); l.removeAttribute('aria-current'); });

  const section = document.getElementById(`section-${name}`);
  const navBtn  = document.getElementById(`nav-${name}`);
  if (section) section.classList.add('active');
  if (navBtn)  { navBtn.classList.add('active'); navBtn.setAttribute('aria-current', 'page'); }

  STATE.currentSection = name;
  document.getElementById('main-content').scrollIntoView({ behavior: 'smooth', block: 'start' });

  // Lazy-init Leaflet map when Map section is first opened
  if (name === 'map' && !STATE.map) {
    setTimeout(initMap, 100); // small delay ensures div is visible
  }
  if (name === 'community') loadLeaderboard();
  if (name === 'tracker')   renderProgressChart();

  const navLinks = document.querySelector('.nav-links');
  if (navLinks) navLinks.classList.remove('mobile-open');

  trackEvent('section_view', { section: name });
}

function toggleMobileMenu() {
  const links = document.querySelector('.nav-links');
  const btn   = document.getElementById('hamburger-btn');
  const open  = links.classList.toggle('mobile-open');
  btn.setAttribute('aria-expanded', open.toString());
}

// ─── Particle System ─────────────────────────────────────────────────────────
function spawnParticles() {
  const container = document.getElementById('hero-particles');
  for (let i = 0; i < 20; i++) {
    const p    = document.createElement('div');
    p.className = 'particle';
    const size  = Math.random() * 6 + 2;
    p.style.cssText = `
      width:${size}px;height:${size}px;
      left:${Math.random()*100}%;top:${Math.random()*100}%;
      animation-duration:${Math.random()*20+15}s;
      animation-delay:${Math.random()*-20}s;
      --drift-x:${(Math.random()-0.5)*200}px;`;
    container.appendChild(p);
  }
}

// ─── Carbon Calculator ────────────────────────────────────────────────────────
async function calculateFootprint() {
  const btn     = document.getElementById('calculate-btn');
  const txt     = document.getElementById('calc-btn-text');
  const spinner = document.getElementById('calc-spinner');

  const payload = {
    session_id:              STATE.sessionId,
    car_km_per_week:         parseFloat(document.getElementById('car_km').value) || 0,
    car_type:                document.getElementById('car_type').value,
    flights_per_year:        parseInt(document.getElementById('flights').value) || 0,
    flight_type:             document.getElementById('flight_type').value,
    public_transport_km:     parseFloat(document.getElementById('public_transport').value) || 0,
    electricity_kwh:         parseFloat(document.getElementById('electricity').value) || 0,
    natural_gas_cubic_m:     parseFloat(document.getElementById('gas').value) || 0,
    renewable_energy_pct:    parseFloat(document.getElementById('renewable_pct').value) || 0,
    diet_type:               document.getElementById('diet_type').value,
    food_waste_kg:           parseFloat(document.getElementById('food_waste').value) || 0,
    new_clothes_per_year:    parseInt(document.getElementById('clothes').value) || 0,
    online_orders_per_month: parseInt(document.getElementById('orders').value) || 0,
    streaming_hours_per_day: parseFloat(document.getElementById('streaming').value) || 0,
  };

  btn.disabled = true;
  txt.textContent = 'Calculating...';
  spinner.classList.remove('hidden');

  try {
    const res  = await fetch(`${API}/calculate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    STATE.carbonData = data;
    localStorage.setItem('eco_carbon_data', JSON.stringify(data));
    renderResults(data);
    updateNavScore(data.eco_score);
    updateChatContext(data);
    showToast('✅ Carbon footprint calculated!', 'success');
    trackEvent('footprint_calculated', { total_kg: data.total_kg_per_year, score: data.eco_score });
  } catch (err) {
    showToast('❌ Calculation failed. Is the server running?', 'error');
    console.error(err);
  } finally {
    btn.disabled = false;
    txt.textContent = 'Calculate My Carbon Footprint 🌍';
    spinner.classList.add('hidden');
  }
}

function renderResults(data) {
  document.getElementById('results-placeholder').classList.add('hidden');
  document.getElementById('results-content').classList.remove('hidden');

  // Score ring
  const score = data.eco_score;
  const ring  = document.getElementById('score-ring-fill');
  const offset = 314 - (score / 100) * 314;
  setTimeout(() => { ring.style.strokeDashoffset = offset; }, 100);

  const color = score > 70 ? '#10b981' : score > 40 ? '#f59e0b' : '#ef4444';
  ring.style.stroke = color;
  document.getElementById('result-score').textContent = score;
  document.getElementById('result-score').style.color = color;

  animateNumber(document.getElementById('result-total'), data.total_kg_per_year, ' kg CO₂e/yr', 0);
  document.getElementById('result-trees').textContent = `🌳 ${data.trees_to_offset} trees to offset`;

  const vsG = data.comparisons.vs_global_avg_pct;
  const vsU = data.comparisons.vs_us_avg_pct;
  document.getElementById('result-comparison').innerHTML = `
    <span class="chip ${vsG > 0 ? 'chip-above' : 'chip-below'}">${vsG > 0 ? '▲' : '▼'} ${Math.abs(vsG)}% vs Global avg</span>
    <span class="chip ${vsU > 0 ? 'chip-above' : 'chip-below'}">${vsU > 0 ? '▲' : '▼'} ${Math.abs(vsU)}% vs US avg</span>`;

  if (STATE.chartsLoaded) renderBreakdownChart(data);
  renderCategoryBars(data.breakdown);
  document.getElementById('insights-prompt').classList.add('hidden');
}

function renderBreakdownChart(data) {
  const b = data.breakdown;
  const chartData = google.visualization.arrayToDataTable([
    ['Category', 'kg CO₂e', { role: 'style' }, { role: 'annotation' }],
    ['🚗 Transport', b.transport.total, '#3b82f6', `${b.transport.total} kg`],
    ['⚡ Energy',    b.energy.total,    '#f59e0b', `${b.energy.total} kg`],
    ['🥗 Food',      b.food.total,      '#10b981', `${b.food.total} kg`],
    ['🛍️ Lifestyle', b.lifestyle.total, '#8b5cf6', `${b.lifestyle.total} kg`],
  ]);
  const options = {
    backgroundColor: 'transparent',
    chartArea: { width: '85%', height: '75%' },
    hAxis: { textStyle: { color: '#94a3b8', fontSize: 11 }, gridlines: { color: 'rgba(255,255,255,0.06)' } },
    vAxis: { textStyle: { color: '#94a3b8', fontSize: 11 } },
    legend: { position: 'none' },
    bar: { groupWidth: '55%' },
    annotations: { textStyle: { color: '#f0fdf4', fontSize: 11, bold: true } },
    animation: { startup: true, duration: 1000, easing: 'out' },
  };
  new google.visualization.ColumnChart(document.getElementById('breakdown-chart')).draw(chartData, options);
}

function renderCategoryBars(breakdown) {
  const container = document.getElementById('category-bars');
  const total = Object.values(breakdown).reduce((s, c) => s + (c.total || 0), 0);
  const cats = [
    { key: 'transport', label: '🚗 Transport', cls: 'bar-transport' },
    { key: 'energy',    label: '⚡ Energy',    cls: 'bar-energy' },
    { key: 'food',      label: '🥗 Food',      cls: 'bar-food' },
    { key: 'lifestyle', label: '🛍️ Lifestyle', cls: 'bar-lifestyle' },
  ];
  container.innerHTML = cats.map(c => {
    const pct = total > 0 ? ((breakdown[c.key].total / total) * 100).toFixed(1) : 0;
    return `<div class="cat-bar-item" role="listitem">
      <div class="cat-bar-label">
        <span class="cat-bar-name">${c.label}</span>
        <span class="cat-bar-value">${breakdown[c.key].total} kg (${pct}%)</span>
      </div>
      <div class="cat-bar-track">
        <div class="cat-bar-fill ${c.cls}" style="width:0" data-width="${pct}%"
             role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"
             aria-label="${c.label}: ${pct}%"></div>
      </div>
    </div>`;
  }).join('');
  setTimeout(() => {
    document.querySelectorAll('.cat-bar-fill').forEach(b => { b.style.width = b.dataset.width; });
  }, 200);
}

// ─── Form Helpers ─────────────────────────────────────────────────────────────
function toggleCategory(name) {
  const fields   = document.getElementById(`${name}-fields`);
  const arrow    = document.getElementById(`arrow-${name}`);
  const header   = fields.previousElementSibling;
  const collapsed = fields.classList.toggle('collapsed');
  arrow.classList.toggle('collapsed', collapsed);
  header.setAttribute('aria-expanded', (!collapsed).toString());
}

function selectDiet(diet) {
  document.querySelectorAll('.diet-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.diet === diet);
    b.setAttribute('aria-pressed', (b.dataset.diet === diet).toString());
  });
  document.getElementById('diet_type').value = diet;
}

function updateSliderLabel(sliderId, labelId) {
  const slider = document.getElementById(sliderId);
  const label  = document.getElementById(labelId);
  const val    = parseFloat(slider.value);
  let pct;
  if (labelId === 'renewable_label') {
    label.textContent = `${val}%`;
    pct = val;
  } else if (labelId === 'streaming_label') {
    label.textContent = `${val}h`;
    pct = (val / 16) * 100;
  } else if (labelId === 'goal-reduction-label') {
    label.textContent = `${val}%`;
    pct = ((val - 5) / 75) * 100;
  }
  slider.style.background = `linear-gradient(to right,#10b981 ${pct}%,rgba(255,255,255,0.1) ${pct}%)`;
  slider.setAttribute('aria-valuenow', val);
}

// ─── AI Insights ──────────────────────────────────────────────────────────────
async function loadInsights() {
  if (!STATE.carbonData) {
    showToast('⚠️ Calculate your footprint first!', 'info');
    showSection('calculator');
    return;
  }
  const loading  = document.getElementById('insights-loading');
  const content  = document.getElementById('insights-content');
  const promptEl = document.getElementById('insights-prompt');

  promptEl.classList.add('hidden');
  loading.classList.remove('hidden');
  content.classList.add('hidden');

  try {
    const res      = await fetch(`${API}/insights`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(STATE.carbonData),
    });
    const insights = await res.json();
    STATE.insightsData = insights;
    renderInsights(insights);
    trackEvent('insights_viewed', { eco_score: STATE.carbonData.eco_score });
  } catch {
    showToast('❌ Failed to load insights', 'error');
  } finally {
    loading.classList.add('hidden');
  }
}

function renderInsights(data) {
  document.getElementById('insights-content').classList.remove('hidden');
  document.getElementById('ai-summary-text').textContent    = data.summary || '';
  document.getElementById('motivational-msg').innerHTML     = `💬 "${data.motivational_message || ''}"`;

  document.getElementById('top-actions-list').innerHTML = (data.top_actions || []).map(a => `
    <div class="action-item" role="listitem" tabindex="0"
         onclick="logActionFromInsight('${a.action.replace(/'/g,"\\'")}',${a.impact_kg})"
         aria-label="${a.action}, saves ${a.impact_kg} kg CO2">
      <div class="action-impact">-${a.impact_kg} kg</div>
      <div class="action-text">
        <div class="action-title">${a.action}</div>
        <div class="action-meta">⏱ ${a.timeframe}</div>
      </div>
      <span class="diff-badge diff-${a.difficulty}">${a.difficulty}</span>
    </div>`).join('');

  document.getElementById('quick-win-content').innerHTML   = `<strong>⚡ Quick Win:</strong><br/>${data.quick_win || ''}`;
  document.getElementById('biggest-win-content').innerHTML = `<strong>🏆 Biggest Impact:</strong><br/>${data.biggest_win || ''}`;
  updateGoalPreview();
}

function logActionFromInsight(action, impactKg) {
  showSection('tracker');
  document.getElementById('custom-activity-desc').value  = action;
  document.getElementById('custom-co2-saved').value      = (impactKg / 365).toFixed(2);
  showToast('📝 Action pre-filled in tracker!', 'success');
}

// ─── Goal Setting ─────────────────────────────────────────────────────────────
function updateGoalPreview() {
  const slider   = document.getElementById('goal-reduction');
  const label    = document.getElementById('goal-reduction-label');
  const timeline = document.getElementById('goal-timeline');
  const preview  = document.getElementById('goal-preview');
  const pct      = parseFloat(slider.value);
  const months   = parseInt(timeline.value);
  label.textContent = `${pct}%`;
  const gradPct = ((pct - 5) / 75) * 100;
  slider.style.background = `linear-gradient(to right,#10b981 ${gradPct}%,rgba(255,255,255,0.1) ${gradPct}%)`;

  if (!STATE.carbonData) { preview.innerHTML = '<em>Calculate your footprint first.</em>'; return; }
  const current   = STATE.carbonData.total_kg_per_year;
  const reduction = Math.round(current * pct / 100);
  const target    = Math.round(current - reduction);
  const monthly   = Math.round(reduction / months);
  const trees     = Math.round(reduction / 21);
  preview.innerHTML = `📊 <strong>Current:</strong> ${current.toLocaleString()} kg/yr &nbsp;→&nbsp; <strong>Target:</strong> ${target.toLocaleString()} kg/yr<br/>
    💪 Reduce by <strong>${reduction.toLocaleString()} kg</strong> over <strong>${months} months</strong> (~${monthly} kg/month)<br/>
    🌳 Equivalent to planting <strong>${trees} trees</strong>`;
}

async function commitToGoal() {
  if (!STATE.carbonData) { showToast('⚠️ Calculate your footprint first!', 'info'); return; }
  const pct       = parseFloat(document.getElementById('goal-reduction').value);
  const months    = parseInt(document.getElementById('goal-timeline').value);
  const reduction = Math.round(STATE.carbonData.total_kg_per_year * pct / 100);
  localStorage.setItem('eco_goal', JSON.stringify({ pct, months, reduction, date: new Date().toISOString() }));
  showToast(`🎯 Goal set! Reduce ${reduction} kg over ${months} months 🌱`, 'success');
  trackEvent('goal_set', { reduction_pct: pct, months });
}

// ─── Map (Leaflet.js + OpenStreetMap — free, no API key) ─────────────────────
function initMap() {
  if (STATE.map) return;

  const defaultCenter = [17.3850, 78.4867]; // Hyderabad, India fallback

  STATE.map = L.map('google-map', {
    center: defaultCenter,
    zoom: 13,
    zoomControl: true,
    attributionControl: true,
  });

  // CartoDB Dark Matter — free dark tile layer, no API key required
  STATE.tileLayer = L.tileLayer(
    'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
    {
      attribution: '© <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a> contributors © <a href="https://carto.com/" target="_blank">CARTO</a>',
      subdomains: 'abcd',
      maxZoom: 20,
    }
  ).addTo(STATE.map);

  // Try to get real user location
  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(
      pos => {
        const { latitude: lat, longitude: lng } = pos.coords;
        STATE.map.setView([lat, lng], 14);
        // User location marker (green dot)
        L.circleMarker([lat, lng], {
          radius: 10, fillColor: '#10b981', color: '#fff', weight: 2, fillOpacity: 1,
        }).addTo(STATE.map).bindPopup('<div style="color:#0a1020"><b>📍 You are here</b></div>');
        searchNearbyEV(lat, lng);
      },
      () => searchNearbyEV(...defaultCenter)
    );
  } else {
    searchNearbyEV(...defaultCenter);
  }
}

// Overpass API — free, no API key, real EV charging station data
async function searchNearbyEV(lat, lng) {
  const query = `[out:json][timeout:15];
node["amenity"="charging_station"](around:5000,${lat},${lng});
out body 12;`;
  try {
    showToast('🔍 Searching EV stations...', 'info');
    const res  = await fetch('https://overpass-api.de/api/interpreter', { method: 'POST', body: query });
    const data = await res.json();
    clearMarkers();
    const places = data.elements.slice(0, 12).map(p => ({
      name:     p.tags?.name || 'EV Charging Station',
      vicinity: p.tags?.operator || p.tags?.network || 'Charging Point',
      lat:      p.lat, lng: p.lon, rating: null,
    }));
    renderPlacesList(places, '⚡');
    places.forEach(p => addLeafletMarker(p.lat, p.lng, '⚡', '#3b82f6', p.name, p.vicinity));
    showToast(`⚡ Found ${places.length} EV stations nearby`, 'success');
  } catch (e) {
    console.warn('EV search failed:', e);
    showToast('⚠️ Could not load EV stations. Check your internet.', 'info');
  }
}

async function searchNearbyParks(lat, lng) {
  const center = STATE.map ? STATE.map.getCenter() : { lat, lng };
  const clat   = lat ?? center.lat;
  const clng   = lng ?? center.lng;
  const query  = `[out:json][timeout:15];
(node["leisure"="park"](around:5000,${clat},${clng});
 way["leisure"="park"](around:5000,${clat},${clng}););
out center body 10;`;
  try {
    const res  = await fetch('https://overpass-api.de/api/interpreter', { method: 'POST', body: query });
    const data = await res.json();
    clearMarkers();
    const places = data.elements
      .filter(e => e.lat || e.center)
      .slice(0, 10)
      .map(p => ({
        name:     p.tags?.name || 'Green Space',
        vicinity: p.tags?.['addr:city'] || 'Public Park',
        lat:      p.lat ?? p.center?.lat,
        lng:      p.lon ?? p.center?.lng,
        rating:   null,
      }));
    renderPlacesList(places, '🌳');
    places.forEach(p => { if (p.lat && p.lng) addLeafletMarker(p.lat, p.lng, '🌳', '#10b981', p.name, p.vicinity); });
  } catch (e) {
    console.warn('Parks search failed:', e);
  }
}

function addLeafletMarker(lat, lng, emoji, color, name, address) {
  const icon = L.divIcon({
    html: `<div style="background:${color};border-radius:50%;width:34px;height:34px;display:flex;align-items:center;justify-content:center;font-size:16px;border:2px solid #fff;box-shadow:0 2px 8px rgba(0,0,0,0.5)">${emoji}</div>`,
    className: '', iconSize: [34, 34], iconAnchor: [17, 17],
  });
  const marker = L.marker([lat, lng], { icon })
    .addTo(STATE.map)
    .bindPopup(`
      <div style="background:#0a1020;color:#f0fdf4;padding:10px;border-radius:8px;min-width:160px;font-family:Inter,sans-serif">
        <strong style="color:#10b981">${emoji} ${name}</strong><br/>
        <small style="color:#94a3b8">${address}</small>
      </div>`);
  STATE.mapMarkers.push(marker);
  return marker;
}

function clearMarkers() {
  STATE.mapMarkers.forEach(m => STATE.map?.removeLayer(m));
  STATE.mapMarkers = [];
}

function renderPlacesList(places, emoji) {
  const list = document.getElementById('nearby-places-list');
  list.innerHTML = places.length
    ? places.map(p => `
        <div class="place-item" role="listitem"
             onclick="panToPlace(${p.lat},${p.lng})"
             aria-label="${p.name}">
          <strong>${emoji} ${p.name}</strong><br/>
          <small style="color:#64748b">${p.vicinity || ''}</small>
        </div>`).join('')
    : '<p style="color:#64748b;font-size:0.8rem;padding:8px">No places found nearby.</p>';
}

function panToPlace(lat, lng) {
  STATE.map?.setView([lat, lng], 16);
}

function toggleMapLayer(layer) {
  STATE.activeMapLayers[layer] = !STATE.activeMapLayers[layer];
  const chip = document.getElementById(`chip-${layer}`);
  chip.classList.toggle('active', STATE.activeMapLayers[layer]);
  chip.setAttribute('aria-pressed', STATE.activeMapLayers[layer].toString());

  if (!STATE.map) return;
  const c = STATE.map.getCenter();
  if (layer === 'ev'    && STATE.activeMapLayers.ev)    searchNearbyEV(c.lat, c.lng);
  if (layer === 'parks' && STATE.activeMapLayers.parks) searchNearbyParks(c.lat, c.lng);
  if (layer === 'transit') showToast('🚌 Use your local transit app for real-time bus/metro data.', 'info');
  if (layer === 'bike')   showToast('🚲 Cycling routes: see OpenCycleMap or Google Maps for bike lanes.', 'info');
}

// Nominatim geocoder — free, OpenStreetMap
async function searchMapLocation() {
  const input = document.getElementById('map-search-input').value.trim();
  if (!input || !STATE.map) return;
  try {
    const res  = await fetch(
      `https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(input)}&limit=1`,
      { headers: { 'Accept-Language': 'en' } }
    );
    const data = await res.json();
    if (data[0]) {
      const lat = parseFloat(data[0].lat), lng = parseFloat(data[0].lon);
      STATE.map.setView([lat, lng], 14);
      searchNearbyEV(lat, lng);
    } else {
      showToast('❌ Location not found', 'error');
    }
  } catch { showToast('❌ Search failed. Check your connection.', 'error'); }
}

// Route carbon comparison — Nominatim for geocoding, haversine for distance
async function compareRoutes() {
  const from = document.getElementById('route-from').value.trim();
  const to   = document.getElementById('route-to').value.trim();
  if (!from || !to) { showToast('⚠️ Enter both From and To locations', 'info'); return; }

  const resultsDiv = document.getElementById('route-results');
  resultsDiv.innerHTML = '<p style="color:#64748b;font-size:0.82rem">Geocoding locations...</p>';

  try {
    const [fromData, toData] = await Promise.all([
      fetch(`https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(from)}&limit=1`).then(r=>r.json()),
      fetch(`https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(to)}&limit=1`).then(r=>r.json()),
    ]);
    if (!fromData[0] || !toData[0]) {
      resultsDiv.innerHTML = '<p style="color:#f87171">Could not find one or both locations.</p>'; return;
    }
    const fLat = parseFloat(fromData[0].lat), fLng = parseFloat(fromData[0].lon);
    const tLat = parseFloat(toData[0].lat),   tLng = parseFloat(toData[0].lon);
    const distKm = haversineKm(fLat, fLng, tLat, tLng);

    // Draw route line on Leaflet map
    if (STATE.map) {
      clearMarkers();
      const line = L.polyline([[fLat,fLng],[tLat,tLng]], {
        color: '#10b981', weight: 4, dashArray: '10,6', opacity: 0.85,
      }).addTo(STATE.map);
      STATE.mapMarkers.push(line);
      STATE.mapMarkers.push(
        L.circleMarker([fLat,fLng],{radius:8,fillColor:'#3b82f6',color:'#fff',weight:2,fillOpacity:1}).addTo(STATE.map).bindPopup(`<b>🚀 From:</b> ${fromData[0].display_name.split(',')[0]}`),
        L.circleMarker([tLat,tLng],{radius:8,fillColor:'#10b981',color:'#fff',weight:2,fillOpacity:1}).addTo(STATE.map).bindPopup(`<b>🏁 To:</b> ${toData[0].display_name.split(',')[0]}`)
      );
      STATE.map.fitBounds([[fLat,fLng],[tLat,tLng]], { padding: [40, 40] });
    }

    const modes = [
      { name:'🚗 Car (Petrol)',  co2: distKm * 0.21  },
      { name:'🔋 Electric Car',  co2: distKm * 0.05  },
      { name:'🚌 Bus',           co2: distKm * 0.089 },
      { name:'🚂 Train',         co2: distKm * 0.041 },
      { name:'🚲 Cycling',       co2: 0              },
    ].sort((a,b) => a.co2 - b.co2);

    resultsDiv.innerHTML = `
      <div style="font-size:0.8rem;color:#94a3b8;margin-bottom:8px">📍 ~${distKm.toFixed(1)} km distance</div>
      ${modes.map((m,i)=>`
        <div class="route-option ${i===0?'best':''}">
          ${m.name} — <strong style="color:${i===0?'#10b981':'#94a3b8'}">${m.co2.toFixed(2)} kg CO₂</strong>${i===0?' ✅ Best':''}
        </div>`).join('')}`;
    trackEvent('route_compared', { distance_km: distKm });

  } catch (e) {
    resultsDiv.innerHTML = '<p style="color:#f87171">Route comparison failed. Try again.</p>';
    console.error(e);
  }
}

// Haversine great-circle distance in km
function haversineKm(lat1, lng1, lat2, lng2) {
  const R    = 6371;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLng = (lng2 - lng1) * Math.PI / 180;
  const a    = Math.sin(dLat/2)**2 + Math.cos(lat1*Math.PI/180)*Math.cos(lat2*Math.PI/180)*Math.sin(dLng/2)**2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
}

// ─── Progress Tracker ─────────────────────────────────────────────────────────
function logPreset(description, type, co2Saved) { logActivity(description, type, co2Saved); }

function logCustomActivity() {
  const desc  = document.getElementById('custom-activity-desc').value.trim();
  const type  = document.getElementById('custom-activity-type').value;
  const saved = parseFloat(document.getElementById('custom-co2-saved').value) || 0;
  if (!desc)     { showToast('⚠️ Enter an activity description', 'info'); return; }
  if (saved <= 0){ showToast('⚠️ Enter CO₂ saved (> 0)', 'info'); return; }
  logActivity(desc, type, saved);
  document.getElementById('custom-activity-desc').value = '';
  document.getElementById('custom-co2-saved').value     = '';
}

async function logActivity(description, type, co2Saved) {
  const entry = {
    session_id: STATE.sessionId, activity_type: type,
    description, co2_saved_kg: co2Saved, date: new Date().toISOString(),
  };
  STATE.activityLog.unshift(entry);
  STATE.totalSaved = parseFloat((STATE.totalSaved + co2Saved).toFixed(2));
  localStorage.setItem('eco_activity_log', JSON.stringify(STATE.activityLog.slice(0, 100)));
  localStorage.setItem('eco_total_saved',  STATE.totalSaved.toString());

  const today = new Date().toDateString();
  if (STATE.lastActivityDate !== today) {
    STATE.streak++;
    STATE.lastActivityDate = today;
    localStorage.setItem('eco_streak',        STATE.streak.toString());
    localStorage.setItem('eco_last_activity', today);
  }

  renderActivityLog(); updateBadges(); updateStreakDisplay(); renderProgressChart();
  showToast(`🌱 Logged! Saved ${co2Saved} kg CO₂`, 'success');

  try {
    await fetch(`${API}/log-activity`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(entry),
    });
    // Debounced stats reload (5s delay to avoid hammering the server)
    clearTimeout(window._statsDebounce);
    window._statsDebounce = setTimeout(loadPlatformStats, 5000);
  } catch { /* offline — local state already updated */ }

  trackEvent('activity_logged', { type, co2_saved: co2Saved });
}

function renderActivityLog() {
  const list  = document.getElementById('activity-log-list');
  const icons = { transport:'🚗', energy:'⚡', food:'🥗', lifestyle:'🛍️' };
  list.innerHTML = STATE.activityLog.length
    ? STATE.activityLog.slice(0, 20).map(a => `
        <div class="log-entry" role="listitem">
          <span class="log-entry-icon">${icons[a.activity_type] || '🌱'}</span>
          <div class="log-entry-text">
            <div>${a.description}</div>
            <small style="color:#475569">${new Date(a.date).toLocaleDateString()}</small>
          </div>
          <span class="log-entry-saved">-${a.co2_saved_kg} kg</span>
        </div>`).join('')
    : '<p style="color:#475569;font-size:0.85rem;padding:8px">No activities yet. Start with a preset above!</p>';
}

function renderProgressChart() {
  if (!STATE.chartsLoaded) return;
  const today = new Date();
  const days  = [], saved = [];
  for (let i = 13; i >= 0; i--) {
    const d      = new Date(today); d.setDate(today.getDate() - i);
    const label  = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    const total  = STATE.activityLog
      .filter(a => new Date(a.date).toDateString() === d.toDateString())
      .reduce((s, a) => s + a.co2_saved_kg, 0);
    days.push(label); saved.push(parseFloat(total.toFixed(2)));
  }
  const chartData = new google.visualization.DataTable();
  chartData.addColumn('string', 'Date');
  chartData.addColumn('number', 'CO₂ Saved (kg)');
  chartData.addColumn({ type: 'string', role: 'style' });
  chartData.addRows(days.map((d, i) => [d, saved[i], saved[i] > 0 ? 'color:#10b981' : 'color:#1e293b']));
  new google.visualization.ColumnChart(document.getElementById('progress-chart')).draw(chartData, {
    backgroundColor: 'transparent',
    chartArea: { width: '88%', height: '72%' },
    hAxis: { textStyle: { color: '#64748b', fontSize: 10 }, gridlines: { color: 'transparent' } },
    vAxis: { textStyle: { color: '#64748b', fontSize: 10 }, gridlines: { color: 'rgba(255,255,255,0.06)' }, minValue: 0 },
    legend: { position: 'none' },
    animation: { startup: true, duration: 800, easing: 'out' },
  });
}

function updateStreakDisplay() {
  document.getElementById('streak-count').textContent = STATE.streak;
}

// ─── Badges System ────────────────────────────────────────────────────────────
const BADGES = [
  { id: 'first_step',  emoji: '🌱', name: 'First Step',   condition: () => STATE.activityLog.length >= 1 },
  { id: 'eco5',        emoji: '♻️', name: '5 Actions',    condition: () => STATE.activityLog.length >= 5 },
  { id: 'eco20',       emoji: '🌍', name: '20 Actions',   condition: () => STATE.activityLog.length >= 20 },
  { id: 'saver10',     emoji: '💚', name: '10 kg Saved',  condition: () => STATE.totalSaved >= 10 },
  { id: 'saver100',    emoji: '🏆', name: '100 kg Saved', condition: () => STATE.totalSaved >= 100 },
  { id: 'streak3',     emoji: '🔥', name: '3-Day Streak', condition: () => STATE.streak >= 3 },
  { id: 'streak7',     emoji: '⭐', name: '7-Day Streak', condition: () => STATE.streak >= 7 },
  { id: 'calculator',  emoji: '📊', name: 'Measured',     condition: () => !!STATE.carbonData },
  { id: 'eco_score80', emoji: '🌟', name: 'Eco Hero',     condition: () => STATE.carbonData?.eco_score >= 80 },
];

function updateBadges() {
  const grid   = document.getElementById('badges-grid');
  const earned = JSON.parse(localStorage.getItem('eco_badges') || '[]');
  BADGES.forEach(b => {
    if (b.condition() && !earned.includes(b.id)) {
      earned.push(b.id);
      showToast(`🏅 Badge earned: ${b.name}!`, 'success');
    }
  });
  localStorage.setItem('eco_badges', JSON.stringify(earned));
  grid.innerHTML = BADGES.map(b => `
    <div class="badge-item ${earned.includes(b.id)?'earned':'locked'}" role="listitem"
         title="${b.name}" aria-label="${b.name}: ${earned.includes(b.id)?'earned':'locked'}">
      <span class="badge-emoji" aria-hidden="true">${b.emoji}</span>
      <span class="badge-name">${b.name}</span>
    </div>`).join('');
}

// ─── Community Leaderboard ────────────────────────────────────────────────────
async function loadLeaderboard() {
  const list = document.getElementById('leaderboard-list');
  list.innerHTML = '<p style="color:#64748b;padding:16px;text-align:center">Loading...</p>';
  try {
    const res  = await fetch(`${API}/leaderboard`);
    const data = await res.json();
    const lb   = data.leaderboard;
    document.getElementById('comm-users').textContent = data.total_users || '--';
    list.innerHTML = lb.length
      ? lb.map((e, i) => `
          <div class="lb-entry ${i<3?'top-3':''}" role="listitem">
            <span class="lb-rank">${e.rank}</span>
            <span class="lb-medal" aria-hidden="true">${e.medal||''}</span>
            <span class="lb-name">${e.user}</span>
            <div style="text-align:right">
              <div class="lb-saved">${e.co2_saved_kg.toLocaleString()} kg saved</div>
              <div class="lb-actions">${e.actions} action${e.actions!==1?'s':''}</div>
            </div>
          </div>`).join('')
      : '<p style="color:#64748b;padding:16px">No data yet. Be the first! 🌱</p>';
  } catch {
    list.innerHTML = '<p style="color:#f87171;padding:16px">Failed to load leaderboard.</p>';
  }
}

// ─── Platform Stats ───────────────────────────────────────────────────────────
async function loadPlatformStats() {
  try {
    const res  = await fetch(`${API}/stats`);
    const data = await res.json();
    animateNumber(document.getElementById('hero-users'), data.total_users, '');
    animateNumber(document.getElementById('hero-saved'), data.total_co2_saved_kg, '');
    animateNumber(document.getElementById('hero-trees'), data.total_trees_equivalent, '');
    animateNumber(document.getElementById('footer-saved'), data.total_co2_saved_kg, '');
    setText('comm-saved', data.total_co2_saved_kg);
    setText('comm-trees', data.total_trees_equivalent);
    setText('comm-calcs', data.calculations_done);
    renderGeoChart();
  } catch { /* Server may not be running yet */ }
}

// ─── Google Charts Geo Chart ─────────────────────────────────────────────────
function renderGeoChart() {
  if (!STATE.chartsLoaded || !document.getElementById('geo-chart')) return;
  const data = google.visualization.arrayToDataTable([
    ['Country', 'CO₂ Reduction Engagement'],
    ['India', 85], ['United States', 70], ['China', 60], ['Germany', 90],
    ['Brazil', 72], ['United Kingdom', 88], ['Canada', 65], ['Australia', 74],
    ['France', 91], ['Japan', 68], ['South Africa', 55], ['Mexico', 62],
  ]);
  new google.visualization.GeoChart(document.getElementById('geo-chart')).draw(data, {
    backgroundColor: 'transparent',
    colorAxis: { colors: ['#0d9488', '#10b981', '#34d399'] },
    datalessRegionColor: '#1e293b', defaultColor: '#1e293b',
  });
}

// ─── AI Chat ──────────────────────────────────────────────────────────────────
async function sendChatMessage() {
  const input   = document.getElementById('chat-input');
  const msg     = input.value.trim();
  if (!msg) return;
  input.value   = '';
  input.style.height = 'auto';
  addChatMessage(msg, 'user');
  hideSuggestions();

  const sendBtn  = document.getElementById('chat-send-btn');
  const sendIcon = document.getElementById('send-icon');
  const spinner  = document.getElementById('chat-spinner');
  sendBtn.disabled = true;
  sendIcon.classList.add('hidden');
  spinner.classList.remove('hidden');
  addTypingIndicator();

  try {
    const res  = await fetch(`${API}/chat`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: STATE.sessionId, message: msg, carbon_context: STATE.carbonData }),
    });
    const data = await res.json();
    removeTypingIndicator();
    addChatMessage(data.response, 'bot');
    trackEvent('chat_message', {});
  } catch {
    removeTypingIndicator();
    addChatMessage('Sorry, I\'m having trouble connecting. Please ensure the server is running.', 'bot');
  } finally {
    sendBtn.disabled = false;
    sendIcon.classList.remove('hidden');
    spinner.classList.add('hidden');
  }
}

function addChatMessage(text, role) {
  const messages = document.getElementById('chat-messages');
  const div      = document.createElement('div');
  div.className  = `message ${role==='bot'?'bot-message':'user-message'}`;
  div.setAttribute('role', 'article');
  const formatted = text
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g,     '<em>$1</em>')
    .replace(/\n\n/g,          '</p><p>')
    .replace(/\n/g,            '<br/>');
  div.innerHTML = `
    <div class="message-avatar" aria-hidden="true">${role==='bot'?'🌱':'👤'}</div>
    <div class="message-content"><p>${formatted}</p></div>`;
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
}

function addTypingIndicator() {
  const messages = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.className = 'message bot-message'; div.id = 'typing-indicator';
  div.innerHTML = `
    <div class="message-avatar" aria-hidden="true">🌱</div>
    <div class="message-content">
      <div class="typing-indicator" aria-label="EcoGuide is typing">
        <div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>
      </div>
    </div>`;
  messages.appendChild(div);
  messages.scrollTop = messages.scrollHeight;
}

function removeTypingIndicator() { document.getElementById('typing-indicator')?.remove(); }
function hideSuggestions()       { document.getElementById('chat-suggestions').style.display = 'none'; }
function sendSuggestion(text)    { document.getElementById('chat-input').value = text; sendChatMessage(); }
function handleChatKey(e)        { if (e.key==='Enter' && !e.shiftKey) { e.preventDefault(); sendChatMessage(); } }
function autoResizeTextarea(el)  { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 120) + 'px'; }

// ─── Chat Context Sidebar ─────────────────────────────────────────────────────
function updateChatContext(data) {
  data = data || STATE.carbonData;
  const div = document.getElementById('chat-context-display');
  if (!data) { div.innerHTML = '<p class="context-empty">Calculate your footprint first!</p>'; return; }
  div.innerHTML = [
    ['Annual footprint', `${data.total_kg_per_year} kg`],
    ['Eco Score',        `${data.eco_score}/100`],
    ['Trees to offset',  `${data.trees_to_offset} 🌳`],
    ['Transport',        `${data.breakdown.transport.total} kg`],
    ['Energy',           `${data.breakdown.energy.total} kg`],
    ['Food',             `${data.breakdown.food.total} kg`],
  ].map(([l,v])=>`<div class="context-stat"><span class="context-stat-label">${l}</span><span class="context-stat-value">${v}</span></div>`).join('');
}

// ─── Nav Score ────────────────────────────────────────────────────────────────
function updateNavScore(score) {
  score = score ?? STATE.carbonData?.eco_score ?? null;
  const el = document.getElementById('nav-eco-score');
  if (score !== null) {
    el.textContent = score;
    el.style.color = score > 70 ? '#10b981' : score > 40 ? '#f59e0b' : '#ef4444';
  }
}

// ─── Toast ────────────────────────────────────────────────────────────────────
let _toastTimer = null;
function showToast(message, type = 'info') {
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.className   = `toast show ${type}`;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => toast.classList.remove('show'), 3500);
}

// ─── Number Animation ─────────────────────────────────────────────────────────
function animateNumber(el, target, suffix = '', decimals = 0) {
  if (!el) return;
  const dur   = 1500;
  const start = performance.now();
  const step  = now => {
    const p = Math.min((now - start) / dur, 1);
    const v = target * (1 - Math.pow(1 - p, 3));
    el.textContent = (decimals > 0 ? v.toFixed(decimals) : Math.round(v)).toLocaleString() + suffix;
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function animateHeroCounters() { loadPlatformStats(); }
function setText(id, value)    { const el = document.getElementById(id); if (el) el.textContent = (value||'--').toLocaleString(); }

// ─── Google Analytics Event Tracking ─────────────────────────────────────────
function trackEvent(name, params) {
  if (typeof gtag === 'function') gtag('event', name, params);
  if (window.dataLayer) window.dataLayer.push({ event: `ecotrack_${name}`, ...params });
}

// ─── Keyboard Shortcuts ───────────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (!e.altKey) return;
  const map = { c:'calculator', i:'insights', m:'map', t:'tracker', l:'community', h:'chat' };
  if (map[e.key?.toLowerCase()]) { e.preventDefault(); showSection(map[e.key.toLowerCase()]); }
});

// ─── Nav links mobile ID fix ──────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const links = document.querySelector('.nav-links');
  if (links && !links.id) links.id = 'nav-links';
});
