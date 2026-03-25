// ── Arc gauge helper (Victron SoC SVG only) ────
function setArc(arcId, value, min, max, arc270) {
  const pct  = Math.max(0, Math.min(1, (value - min) / (max - min)));
  const dash = (arc270 * pct).toFixed(2);
  document.getElementById(arcId)
    .setAttribute('stroke-dasharray', `${dash} 1000`);
}

// ── Canvas Gauges — shared classic style ───────
// circa-2000 Land Rover instrument cluster aesthetic.
const CLASSIC = {
  colorPlate:            '#111111',
  colorBorderOuter:      '#555',
  colorBorderOuterEnd:   '#333',
  colorBorderMiddle:     '#3a3a3a',
  colorBorderMiddleEnd:  '#222',
  colorBorderInner:      '#555',
  colorBorderInnerEnd:   '#333',
  borderOuterWidth:      3,
  borderMiddleWidth:     2,
  borderInnerWidth:      3,
  borderShadowWidth:     0,

  colorMajorTicks: '#e0e0e0',
  colorMinorTicks: '#666',
  colorNumbers:    '#e0e0e0',
  colorUnits:      '#999',
  colorTitle:      '#999',

  colorNeedle:               '#f0f0f0',
  colorNeedleEnd:            '#cccccc',
  needleType:                'arrow',
  needleWidth:               2,
  needleShadow:              false,
  needleCircleSize:          8,
  needleCircleOuter:         true,
  needleCircleInner:         false,
  colorNeedleCircleOuter:    '#555',
  colorNeedleCircleOuterEnd: '#333',
  colorNeedleCircleInner:    '#1a1a1a',
  colorNeedleCircleInnerEnd: '#111',

  strokeTicks:       false,
  valueBox:          false,
  animationDuration: 350,
  animationRule:     'linear',
};

// Three radial gauges share the available width equally.
// (1280 - 32px padding - 3×16px gaps - 140px stats) / 3 ≈ 353 → 350
const GAUGE_SIZE = 350;

// Font sizes scale proportionally from the 290px reference values.
const FONTS = {
  fontNumbersSize: Math.round(36 * GAUGE_SIZE / 290),  // 43
  fontUnitsSize:   Math.round(30 * GAUGE_SIZE / 290),  // 36
  fontTitleSize:   Math.round(22 * GAUGE_SIZE / 290),  // 27
  numbersMargin:   2,
};

// ── Engine gauges ──────────────────────────────

// RPM — 0-5000, scale labelled 1–4 (× 1000)
const rpmGauge = new RadialGauge(Object.assign({}, CLASSIC, FONTS, {
  renderTo:    'canvas-rpm',
  width:       GAUGE_SIZE,
  height:      GAUGE_SIZE,
  minValue:    0,
  maxValue:    5000,
  value:       850,
  majorTicks:  ['', '1', '2', '3', '4', ''],
  minorTicks:  9,
  units:       'RPM',
  title:       '× 1000',
  fontNumbersSize: Math.round(42 * GAUGE_SIZE / 290),
  fontUnitsSize:   Math.round(36 * GAUGE_SIZE / 290),
  numbersMargin:   4,
  highlights: [
    { from: 4500, to: 5000, color: 'rgba(255, 82, 82, 0.35)' },
  ],
  highlightsWidth: 10,
})).draw();

// Boost — 0-2.5 bar
const boostGauge = new RadialGauge(Object.assign({}, CLASSIC, FONTS, {
  renderTo:   'canvas-boost',
  width:      GAUGE_SIZE,
  height:     GAUGE_SIZE,
  minValue:   0,
  maxValue:   2.5,
  value:      0,
  majorTicks: ['0', '0.5', '1.0', '1.5', '2.0', '2.5'],
  minorTicks: 4,
  units:      'bar',
  title:      'Boost',
  highlights: [
    { from: 1.8, to: 2.5, color: 'rgba(255, 82, 82, 0.35)' },
  ],
  highlightsWidth: 10,
})).draw();

// Throttle — full size, 0-100%
const throttleGauge = new RadialGauge(Object.assign({}, CLASSIC, FONTS, {
  renderTo:   'canvas-throttle',
  width:      GAUGE_SIZE,
  height:     GAUGE_SIZE,
  minValue:   0,
  maxValue:   100,
  value:      0,
  majorTicks: ['0', '50', '100'],
  minorTicks: 9,
  units:      '%',
  title:      'Throttle',
  highlights: [
    { from: 85, to: 100, color: 'rgba(255, 171, 64, 0.3)' },
  ],
  highlightsWidth: 10,
})).draw();

// ── Engine stat dot colours ────────────────────
// Battery voltage: red < 12.0, amber 12.0–12.5 or > 14.8, green otherwise
function batteryColor(v) {
  if (v < 12.0) return 'red';
  if (v < 12.5 || v > 14.8) return 'warn';
  return 'on';
}

// Coolant °C (TD5 thermostat opens ~82°C, normal 85–95°C)
function coolantColor(c) {
  if (c < 60)  return 'blue';
  if (c < 95)  return 'on';
  if (c < 105) return 'warn';
  return 'red';
}

// Inlet air temp — high temps indicate intercooler stress
function airTempColor(c) {
  if (c < 5)  return 'blue';
  if (c < 40) return 'on';
  if (c < 60) return 'warn';
  return 'red';
}

// Fuel temp — elevated temps risk vapour lock on the TD5 high-pressure system
function fuelTempColor(c) {
  if (c < 15) return 'blue';
  if (c < 50) return 'on';
  if (c < 65) return 'warn';
  return 'red';
}

function setStatDot(id, colorCls) {
  document.getElementById(id).className = `status-dot ${colorCls}`;
}

// ── Coolant trend indicator ─────────────────────
// Replaces the static dot with a dynamic shape + colour.
const COOLANT_NORMAL_MAX     = 95;    // green up to this
const COOLANT_WARNING_THRESH = 105;   // amber above this
const COOLANT_TREND_SAMPLES  = 5;     // readings for trend calculation

const _coolantHistory = [];

function _coolantTrend(current) {
  _coolantHistory.push(current);
  if (_coolantHistory.length > COOLANT_TREND_SAMPLES) _coolantHistory.shift();

  // Trend direction
  let shape = 'dot';  // stable
  if (_coolantHistory.length >= 3) {
    const first = _coolantHistory[0];
    const last = _coolantHistory[_coolantHistory.length - 1];
    const delta = last - first;
    if (delta > 1.5) shape = 'up';
    else if (delta < -1.5) shape = 'down';
  }

  // Colour: green (normal), amber (warming/approaching), red (hot & rising)
  let colour = 'on';  // green
  if (current < 60) {
    colour = 'blue';
  } else if (current >= COOLANT_WARNING_THRESH) {
    colour = shape === 'up' ? 'red' : 'warn';
  } else if (current >= COOLANT_NORMAL_MAX) {
    colour = 'warn';
  }

  return { shape, colour };
}

function _setCoolantIndicator(temp) {
  const { shape, colour } = _coolantTrend(temp);
  const el = document.getElementById('dot-coolant');
  el.className = `status-dot ${colour}`;
  // Shape: modify innerHTML for arrows
  if (shape === 'up') {
    el.innerHTML = '<span class="trend-arrow">&#9650;</span>';
  } else if (shape === 'down') {
    el.innerHTML = '<span class="trend-arrow">&#9660;</span>';
  } else {
    el.innerHTML = '';
  }
}

// ── Engine data handler ────────────────────────
function handleEngine(d) {
  rpmGauge.value      = d.rpm;
  boostGauge.value    = d.boost_bar;
  throttleGauge.value = d.throttle_pct;

  document.getElementById('txt-battery').textContent   = `${d.battery_v.toFixed(1)} V`;
  document.getElementById('txt-coolant').textContent   = `${d.coolant_temp_c} °C`;
  document.getElementById('txt-air-temp').textContent  = `${d.inlet_air_temp_c} °C`;

  // Faults tile
  const faultCount = (d.fault_codes || []).length;
  document.getElementById('txt-faults').textContent = faultCount;
  setStatDot('dot-faults', faultCount > 0 ? 'red' : 'on');

  setStatDot('dot-battery',  batteryColor(d.battery_v));
  _setCoolantIndicator(d.coolant_temp_c);
  setStatDot('dot-air-temp', airTempColor(d.inlet_air_temp_c));

  // Update DTC detail (Engine layer 1), Raw Data (Engine layer 3), trip, and calibration
  _updateDTCList(d.fault_codes);
  _updateRawData(d);
  _updateThrottleRaw(d.throttle_raw_pct);
  _trip.update(d);
  _trip.render();
}

// ── Spotify data handler ───────────────────────
const PAUSE_SVG = '<rect x="5" y="4" width="4" height="16" rx="1.5"/><rect x="15" y="4" width="4" height="16" rx="1.5"/>';
const PLAY_SVG  = '<polygon points="6,4 20,12 6,20"/>';

function formatTime(s) {
  const m = Math.floor(s / 60);
  return `${m}:${Math.floor(s % 60).toString().padStart(2, '0')}`;
}

// Progress interpolation — advances progress bar by 1 s between WS updates.
let _spPlaying   = false;
let _spProgress  = 0;
let _spDuration  = 0;
let _spTick      = null;
let _spTrackId   = '';

function _startProgressTick() {
  clearInterval(_spTick);
  _spTick = setInterval(() => {
    if (!_spPlaying || _spDuration <= 0) return;
    _spProgress = Math.min(_spProgress + 1, _spDuration);
    const pct = (_spProgress / _spDuration * 100).toFixed(1);
    document.getElementById('sp-progress-fill').style.width = `${pct}%`;
    document.getElementById('sp-time-current').textContent  = formatTime(_spProgress);
  }, 1000);
}

function handleSpotify(d) {
  const disconnected = document.getElementById('sp-disconnected');
  const player       = document.getElementById('sp-player');

  _spPlaying = d.connected && d.playing;
  VIS.setPlaying(_spPlaying);

  if (!d.connected) {
    clearInterval(_spTick);
    document.getElementById('sp-disc-title').textContent =
      d.error ? 'Spotify Unavailable' : 'No Active Device';
    document.getElementById('sp-disc-sub').textContent =
      d.error ? 'Check credentials or network connection'
              : 'Open Spotify on any device to begin';
    disconnected.style.display = '';
    player.style.display = 'none';
    return;
  }

  disconnected.style.display = 'none';
  player.style.display = '';

  // Album art
  const artImg         = document.getElementById('sp-art-img');
  const artPlaceholder = document.getElementById('sp-art-placeholder');
  if (d.album_art_url) {
    artImg.src = d.album_art_url;
    artImg.style.display = 'block';
    artPlaceholder.style.display = 'none';
  } else {
    artImg.style.display = 'none';
    artPlaceholder.style.display = 'flex';
  }

  // Track info
  document.getElementById('sp-track').textContent  = d.track  || '—';
  document.getElementById('sp-artist').textContent = d.artist || '—';

  // Progress — server value is authoritative; tick fills the gaps
  _spProgress = d.progress_s;
  _spDuration = d.duration_s;
  if ((d.track_id || '') !== _spTrackId) {
    _spTrackId = d.track_id || '';
  }
  document.getElementById('sp-like-btn').classList.toggle('sp-btn--liked', !!d.liked);
  const pct = _spDuration > 0 ? (_spProgress / _spDuration * 100).toFixed(1) : 0;
  document.getElementById('sp-progress-fill').style.width = `${pct}%`;
  document.getElementById('sp-time-current').textContent  = formatTime(_spProgress);
  document.getElementById('sp-time-total').textContent    = formatTime(_spDuration);

  // Play / pause icon
  document.getElementById('sp-play-icon').innerHTML = d.playing ? PAUSE_SVG : PLAY_SVG;

  // Active device label — shown when a device is reported
  const deviceEl = document.getElementById('sp-device');
  if (d.device_name) {
    deviceEl.textContent = `▶ ${d.device_name}`;
    deviceEl.style.display = '';
  } else {
    deviceEl.style.display = 'none';
  }

  // Keep or start the interpolation tick
  if (d.playing) {
    _startProgressTick();
  } else {
    clearInterval(_spTick);
  }
}

// ── Spectrum visualiser ────────────────────────
// Classic Winamp-style spectrum analyser.
// 64 bars, green → amber → red gradient, floating peak markers.
// Driven entirely by simulated data — no audio API needed.
// setPlaying(bool) switches between full animation and silent decay.

const VIS = (() => {
  const NUM_BARS  = 64;
  const BAR_W     = 14;    // px wide per bar
  const GAP       = 6;     // px gap between bars  →  (14+6)×64 = 1280 px total
  const BOTTOM    = 400;   // y-root of bars (canvas bottom)
  const MAX_H     = 340;   // maximum bar height in px
  const PEAK_HOLD = 22;    // frames a peak marker holds before it falls

  let canvas, ctx, grad;
  let bars, peaks, peakHold, peakVel, simBuf;
  let _playing = false;

  // Real audio — set when getUserMedia succeeds
  let _analyser  = null;
  let _freqData  = null;   // Uint8Array from AnalyserNode

  function init() {
    canvas        = document.getElementById('sp-visualizer');
    ctx           = canvas.getContext('2d');
    canvas.width  = 1280;
    canvas.height = 400;

    bars     = new Float32Array(NUM_BARS);
    peaks    = new Float32Array(NUM_BARS);
    peakHold = new Int32Array(NUM_BARS);
    peakVel  = new Float32Array(NUM_BARS);
    simBuf   = new Float32Array(NUM_BARS);

    grad = ctx.createLinearGradient(0, BOTTOM, 0, BOTTOM - MAX_H);
    grad.addColorStop(0.00, '#00e676');
    grad.addColorStop(0.55, '#ffab40');
    grad.addColorStop(1.00, '#ff5252');

    // Attempt real audio capture — silently fall back to simulation on failure.
    // On the Pi: PulseAudio loopback exposes td5_sink.monitor as default source.
    // In Docker / dev: no audio source available, simulation is used instead.
    if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
      navigator.mediaDevices.getUserMedia({ audio: true, video: false })
        .then(stream => {
          const audioCtx  = new (window.AudioContext || window.webkitAudioContext)();
          const source    = audioCtx.createMediaStreamSource(stream);
          const analyser  = audioCtx.createAnalyser();
          analyser.fftSize            = 2048;
          analyser.smoothingTimeConstant = 0.0;  // we do our own smoothing
          source.connect(analyser);
          _analyser = analyser;
          _freqData = new Uint8Array(analyser.frequencyBinCount);
        })
        .catch(() => { /* no audio source — simulation remains active */ });
    }

    requestAnimationFrame(_frame);
  }

  function setPlaying(playing) {
    _playing = playing;
  }

  // Map FFT bins (frequencyBinCount = 1024) onto NUM_BARS using logarithmic
  // spacing so bass bars are wider and high-freq bars are narrower — matches
  // how music energy is distributed perceptually.
  function _readReal() {
    _analyser.getByteFrequencyData(_freqData);
    const bins    = _freqData.length;
    const minLog  = Math.log10(1);
    const maxLog  = Math.log10(bins);
    for (let i = 0; i < NUM_BARS; i++) {
      const lo = Math.floor(Math.pow(10, minLog + (i / NUM_BARS) * (maxLog - minLog)));
      const hi = Math.floor(Math.pow(10, minLog + ((i + 1) / NUM_BARS) * (maxLog - minLog)));
      let   sum = 0, count = 0;
      for (let b = lo; b <= Math.min(hi, bins - 1); b++) { sum += _freqData[b]; count++; }
      simBuf[i] = count > 0 ? (sum / count) / 255 : 0;
    }
    return simBuf;
  }

  function _simulate(t) {
    const kick     = Math.pow(Math.max(0, Math.sin(t * Math.PI * 2.0)),  6);
    const halfbeat = Math.pow(Math.max(0, Math.sin(t * Math.PI * 1.0 + 1.2)), 4);
    for (let i = 0; i < NUM_BARS; i++) {
      const p    = i / (NUM_BARS - 1);
      const bass = Math.max(0, 1 - p * 3.4) * (0.52 + 0.48 * kick);
      const lmid = Math.sin(p * Math.PI * 0.9) * (0.22 + 0.22 * Math.sin(t * 2.7 + i * 0.18) + 0.14 * halfbeat);
      const mid  = Math.sin(p * Math.PI) * 0.36 * (0.32 + 0.68 * Math.abs(Math.sin(t * 1.31 + i * 0.24)));
      const pres = Math.max(0, p - 0.38) * 0.55 * (0.18 + 0.42 * Math.abs(Math.sin(t * 2.9 + i * 0.31)));
      const air  = Math.max(0, p - 0.68) * 0.45 * (0.10 + 0.38 * Math.abs(Math.sin(t * 4.1 + i * 0.45)));
      simBuf[i]  = Math.min(1, Math.max(0, bass + lmid + mid + pres + air));
    }
    return simBuf;
  }

  function _frame(ts) {
    requestAnimationFrame(_frame);

    let targets;
    if (_analyser && _playing) {
      targets = _readReal();
    } else if (_playing) {
      targets = _simulate(ts / 1000);
    } else {
      targets = simBuf.fill(0);
    }

    for (let i = 0; i < NUM_BARS; i++) {
      const d = targets[i] - bars[i];
      bars[i] += d * (d > 0 ? 0.28 : 0.055);
    }

    for (let i = 0; i < NUM_BARS; i++) {
      if (bars[i] >= peaks[i]) {
        peaks[i]    = bars[i];
        peakHold[i] = PEAK_HOLD;
        peakVel[i]  = 0;
      } else if (peakHold[i] > 0) {
        peakHold[i]--;
      } else {
        peakVel[i] += 0.0018;
        peaks[i]    = Math.max(0, peaks[i] - peakVel[i]);
      }
    }

    ctx.clearRect(0, 0, 1280, 400);

    ctx.fillStyle = grad;
    for (let i = 0; i < NUM_BARS; i++) {
      const h = Math.round(bars[i] * MAX_H);
      if (h > 0) ctx.fillRect(i * (BAR_W + GAP), BOTTOM - h, BAR_W, h);
    }

    ctx.fillStyle   = '#ffffff';
    ctx.globalAlpha = 0.80;
    for (let i = 0; i < NUM_BARS; i++) {
      const ph = Math.round(peaks[i] * MAX_H);
      if (ph > 4) ctx.fillRect(i * (BAR_W + GAP), BOTTOM - ph - 3, BAR_W, 2);
    }
    ctx.globalAlpha = 1;
  }

  return { init, setPlaying };
})();

VIS.init();

// ── Victron data handler ───────────────────────
const CHARGE_STATE_LABELS = {
  bulk:        { label: 'Bulk',        cls: 'charge-badge--amber' },
  absorption:  { label: 'Absorption',  cls: 'charge-badge--amber' },
  float:       { label: 'Float',       cls: '' },
  storage:     { label: 'Storage',     cls: '' },
  equalize:    { label: 'Equalise',    cls: 'charge-badge--amber' },
  off:         { label: 'Off',         cls: 'charge-badge--dim' },
  low_power:   { label: 'Low Power',   cls: 'charge-badge--dim' },
  fault:       { label: 'Fault',       cls: 'charge-badge--red' },
};

function applySocColor(soc) {
  const el = document.getElementById('arc-soc');
  el.className.baseVal = 'gauge-value';
  if (soc <= 20)      el.classList.add('gauge-value--red');
  else if (soc <= 50) el.classList.add('gauge-value--amber');
}

function handleVictron(d) {
  setArc('arc-soc', d.soc_pct, 0, 100, 377);
  document.getElementById('txt-soc').textContent = Math.round(d.soc_pct);
  applySocColor(d.soc_pct);

  document.getElementById('txt-voltage').textContent = `${d.voltage_v.toFixed(1)} V`;

  const currentSign = d.current_a > 0 ? '+' : '';
  document.getElementById('txt-current').textContent = `${currentSign}${d.current_a.toFixed(1)} A`;

  document.getElementById('txt-solar').textContent = `${d.solar_yield_wh} Wh`;

  const flow = d.current_a > 0.5 ? 'Charging' : d.current_a < -0.5 ? 'Discharging' : 'Idle';
  document.getElementById('txt-flow').textContent = flow;

  const badge = document.getElementById('txt-charge-state');
  const state = CHARGE_STATE_LABELS[d.charge_state] ?? { label: d.charge_state, cls: '' };
  badge.textContent = state.label;
  badge.className = `charge-badge ${state.cls}`;

  // Orion XS DC-DC charger
  const orion = CHARGE_STATE_LABELS[d.orion_state] ?? { label: d.orion_state ?? 'Off', cls: '' };
  document.getElementById('txt-orion-state').textContent = orion.label;
  document.getElementById('txt-orion-input').textContent =
    d.orion_input_v > 0 ? `${d.orion_input_v.toFixed(1)} V` : '— V';
}

// ── Weather data handler ───────────────────────
// WMO weather interpretation codes → {icon, desc}
const WMO_CODES = {
  0:  { icon: '☀️',  desc: 'Clear' },
  1:  { icon: '🌤️', desc: 'Mainly Clear' },
  2:  { icon: '⛅️', desc: 'Partly Cloudy' },
  3:  { icon: '☁️',  desc: 'Overcast' },
  45: { icon: '🌫️', desc: 'Fog' },
  48: { icon: '🌫️', desc: 'Icy Fog' },
  51: { icon: '🌦️', desc: 'Light Drizzle' },
  53: { icon: '🌦️', desc: 'Drizzle' },
  55: { icon: '🌧️', desc: 'Heavy Drizzle' },
  56: { icon: '🌨️', desc: 'Freezing Drizzle' },
  57: { icon: '🌨️', desc: 'Heavy Freezing Drizzle' },
  61: { icon: '🌧️', desc: 'Light Rain' },
  63: { icon: '🌧️', desc: 'Rain' },
  65: { icon: '🌧️', desc: 'Heavy Rain' },
  66: { icon: '🌨️', desc: 'Freezing Rain' },
  67: { icon: '🌨️', desc: 'Heavy Freezing Rain' },
  71: { icon: '🌨️', desc: 'Light Snow' },
  73: { icon: '❄️',  desc: 'Snow' },
  75: { icon: '❄️',  desc: 'Heavy Snow' },
  77: { icon: '❄️',  desc: 'Snow Grains' },
  80: { icon: '🌦️', desc: 'Light Showers' },
  81: { icon: '🌧️', desc: 'Showers' },
  82: { icon: '🌧️', desc: 'Heavy Showers' },
  85: { icon: '🌨️', desc: 'Snow Showers' },
  86: { icon: '🌨️', desc: 'Heavy Snow Showers' },
  95: { icon: '⛈️',  desc: 'Thunderstorm' },
  96: { icon: '⛈️',  desc: 'Thunderstorm + Hail' },
  99: { icon: '⛈️',  desc: 'Thunderstorm + Hail' },
};

function wmoLookup(code) {
  return WMO_CODES[code] ?? { icon: '?', desc: `Code ${code}` };
}

function handleWeather(d) {
  const dot = document.getElementById('wx-dot');

  if (d.loading) {
    dot.className = 'status-dot';   // grey — no data yet
    document.getElementById('wx-loading').style.display = '';
    document.getElementById('wx-today').style.display   = 'none';
    document.getElementById('wx-location').textContent  = '';
    document.getElementById('wx-forecast').innerHTML    = '';
    return;
  }

  // We have data — swap to the today layout
  document.getElementById('wx-loading').style.display = 'none';
  document.getElementById('wx-today').style.display   = '';

  // Stale: last successful fetch >5 min ago — show last known data with red dot.
  // Fresh: green dot, normal location label.
  if (d.stale) {
    dot.className = 'status-dot red';
    document.getElementById('wx-location').textContent =
      (d.location ? d.location + ' · ' : '') + 'Signal lost';
  } else {
    dot.className = 'status-dot on';
    document.getElementById('wx-location').textContent = d.location || '';
  }

  const cur = d.current;
  const wx  = wmoLookup(cur.weather_code);

  document.getElementById('wx-icon').textContent     = wx.icon;
  document.getElementById('wx-temp').textContent     = `${Math.round(cur.temp_c)}°`;
  document.getElementById('wx-desc').textContent     = wx.desc;
  document.getElementById('wx-wind').textContent     = `${cur.wind_kph} km/h`;
  document.getElementById('wx-humidity').textContent = `${cur.humidity_pct}%`;

  document.getElementById('wx-forecast').innerHTML = d.forecast.map(day => {
    const w = wmoLookup(day.weather_code);
    return `<div class="wx-day">
      <div class="wx-day-name">${day.day}</div>
      <div class="wx-day-icon">${w.icon}</div>
      <div class="wx-day-hi">${Math.round(day.high_c)}°</div>
      <div class="wx-day-lo">${Math.round(day.low_c)}°</div>
    </div>`;
  }).join('');
}

// ── Starlink data handler ──────────────────────
const SL_STATES = {
  connected: { label: 'Connected',  cls: 'sl-state--connected' },
  searching: { label: 'Searching',  cls: 'sl-state--searching' },
  booting:   { label: 'Booting',    cls: 'sl-state--booting'   },
  sleeping:  { label: 'Sleeping',   cls: 'sl-state--sleeping'  },
  offline:   { label: 'Offline',    cls: 'sl-state--offline'   },
  unknown:   { label: 'Unknown',    cls: 'sl-state--unknown'   },
};

const SL_ALERT_LABELS = {
  alert_motors_stuck:                    'Motors stuck',
  alert_thermal_throttle:                'Thermal throttle',
  alert_thermal_shutdown:                'Thermal shutdown',
  alert_mast_not_near_vertical:          'Mast not vertical',
  alert_unexpected_location:             'Unexpected location',
  alert_slow_ethernet_speeds:            'Slow ethernet',
  alert_install_pending:                 'Install pending',
  alert_is_heating:                      'Heating active',
  alert_power_supply_thermal_throttle:   'PSU throttle',
  alert_is_power_save_idle:              'Power save idle',
};

function formatUptime(s) {
  if (s == null || s < 0) return '—';
  if (s < 60)   return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

function handleStarlink(d) {
  const state = SL_STATES[d.state] ?? { label: d.state, cls: 'sl-state--unknown' };

  document.getElementById('sl-roaming-badge').style.display =
    d.roaming ? '' : 'none';

  // Status tile
  const isActive = d.state === 'connected';
  document.getElementById('sl-status-txt').textContent = isActive ? 'Online' : 'Offline';
  document.getElementById('sl-status-dot').className   = `status-dot ${isActive ? 'on' : 'red'}`;

  const obsDot = document.getElementById('sl-obstruction-dot');
  const obsTxt = document.getElementById('sl-obstruction-txt');
  obsDot.className  = `status-dot ${d.obstructed ? 'warn' : 'on'}`;
  obsTxt.textContent = d.obstructed ? `Blocked` : `Clear`;

  // Stats grid
  const isLive = d.state === 'connected';
  document.getElementById('sl-down').textContent =
    isLive ? `${d.down_mbps} Mbps` : '—';
  document.getElementById('sl-up').textContent =
    isLive ? `${d.up_mbps} Mbps`   : '—';
  document.getElementById('sl-latency').textContent =
    isLive ? `${d.latency_ms} ms`  : '—';
  document.getElementById('sl-loss').textContent =
    isLive ? `${d.ping_drop_pct}%` : '—';
  document.getElementById('sl-obstruction-pct').textContent =
    `${d.obstruction_pct}%`;
  document.getElementById('sl-uptime').textContent =
    formatUptime(d.uptime_s);

  // Alerts panel
  const alertsEl = document.getElementById('sl-alerts');
  if (!d.alerts || d.alerts.length === 0) {
    alertsEl.innerHTML = '<div class="sl-alert-ok">✓ All clear</div>';
  } else {
    alertsEl.innerHTML = d.alerts.map(k => {
      const label = SL_ALERT_LABELS[k] ?? k;
      return `<div class="sl-alert-item">⚠ ${label}</div>`;
    }).join('');
  }

  // Settings view — connectivity dot
  const settingsDot = document.getElementById('dot-starlink');
  const settingsTxt = document.getElementById('txt-starlink');
  const dotCls = {
    connected: 'on', searching: 'warn', booting: 'warn',
    sleeping: 'off', offline: 'red',   unknown: 'red',
  };
  settingsDot.className   = `status-dot ${dotCls[d.state] ?? 'red'}`;
  settingsTxt.textContent = state.label;
}

function handleGps(d) {
  // Show a simple GPS Active indicator — coordinates are not useful on the display.
  const hasfix = d.lat !== 0 || d.lon !== 0;
  document.getElementById('sl-gps-dot').className =
    `status-dot ${hasfix ? 'on' : 'off'}`;
  document.getElementById('sl-gps').textContent = hasfix ? 'Active' : 'No Fix';
}

// ── System data handler ────────────────────────
function setDot(id, on) {
  const el = document.getElementById(id);
  el.className = `status-dot ${on ? 'on' : 'off'}`;
}

function _sysDot(id, val, warnAt, redAt) {
  document.getElementById(id).className =
    `status-dot ${val == null ? '' : val < warnAt ? 'on' : val < redAt ? 'warn' : 'red'}`;
}

function handleSystem(d) {
  // Connectivity
  setDot('dot-wifi',     d.wifi_connected);
  setDot('dot-bt',       d.bt_connected);
  setDot('dot-override', d.override_mode);
  document.getElementById('txt-wifi').textContent     = d.wifi_connected ? 'Connected' : 'Off';
  document.getElementById('txt-bt').textContent       = d.bt_connected   ? 'Connected' : 'Off';
  document.getElementById('txt-override').textContent = d.override_mode  ? 'Active'    : 'Off';

  // CPU temp — dot goes red if throttled regardless of temperature
  const t = d.cpu_temp_c;
  document.getElementById('txt-cpu-temp').textContent = t != null ? `${t} °C` : '—';
  document.getElementById('dot-cpu-temp').className   =
    `status-dot ${d.throttled ? 'red' : t == null ? '' : t < 60 ? 'on' : t < 75 ? 'warn' : 'red'}`;

  // CPU load
  const load = d.cpu_load_pct;
  document.getElementById('txt-cpu-load').textContent = load != null ? `${load} %` : '—';
  _sysDot('dot-cpu-load', load, 60, 85);

  // RAM
  const ram = d.ram_usage_pct;
  document.getElementById('txt-ram').textContent = ram != null ? `${ram} %` : '—';
  _sysDot('dot-ram', ram, 75, 90);

  // Disk
  const disk = d.disk_usage_pct;
  document.getElementById('txt-disk').textContent = disk != null ? `${disk} %` : '—';
  _sysDot('dot-disk', disk, 70, 90);

  // Uptime
  document.getElementById('txt-uptime').textContent = formatUptime(d.uptime_s);

  // Throttle
  const thr = d.throttled;
  document.getElementById('txt-throttle').textContent =
    thr == null ? 'N/A' : thr ? 'Throttled' : 'OK';
  document.getElementById('dot-throttle').className =
    `status-dot ${thr == null ? '' : thr ? 'red' : 'on'}`;

  // Sidelights → auto day/night mode switch
  // d.sidelights is null until the GPIO hardware is wired; ignore until then.
  if (d.sidelights != null && d.sidelights !== _prevSidelights) {
    _prevSidelights = d.sidelights;
    setBrightMode(d.sidelights ? 'day' : 'night');
  }
}

// ── Settings: brightness & relay controls ──────
// Brightness values (0–255) are stored per mode in localStorage and applied
// immediately to the Pi backlight via POST /system/brightness.
// Mode ('day'/'night') switches automatically when the sidelights signal
// changes (system payload: d.sidelights = true → Day, false → Night).
// The user can still override the mode manually via the Settings buttons.

// Tracks the last known sidelights state so we only react to changes.
let _prevSidelights = null;

const _BRIGHT_DEF = { day: 180, night: 80 };

function _loadBrightPrefs() {
  return {
    day:  Math.min(255, Math.max(0, parseInt(localStorage.getItem('td5_bright_day')   ?? _BRIGHT_DEF.day))),
    night:Math.min(255, Math.max(0, parseInt(localStorage.getItem('td5_bright_night') ?? _BRIGHT_DEF.night))),
    mode: localStorage.getItem('td5_lights_mode') || 'day',
  };
}

function _saveBrightPrefs(p) {
  localStorage.setItem('td5_bright_day',   p.day);
  localStorage.setItem('td5_bright_night', p.night);
  localStorage.setItem('td5_lights_mode',  p.mode);
}

function _applyBrightUI(p) {
  document.getElementById('bar-bright-day').style.width   = `${(p.day   / 255 * 100).toFixed(1)}%`;
  document.getElementById('bar-bright-night').style.width = `${(p.night / 255 * 100).toFixed(1)}%`;
  document.getElementById('bright-mode-day').classList.toggle('bright-mode-btn--active',   p.mode === 'day');
  document.getElementById('bright-mode-night').classList.toggle('bright-mode-btn--active', p.mode === 'night');
}

function _postBrightness(value) {
  fetch('/system/brightness', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ value }),
  }).catch(() => {});
}

function setBrightMode(mode) {
  const p = _loadBrightPrefs();
  p.mode  = mode;
  _saveBrightPrefs(p);
  _applyBrightUI(p);
  _postBrightness(p[mode]);
}

function adjustBrightness(which, delta) {
  const p   = _loadBrightPrefs();
  p[which]  = Math.min(255, Math.max(0, p[which] + delta));
  _saveBrightPrefs(p);
  _applyBrightUI(p);
  // Only send to Pi if this mode is currently active
  if (p.mode === which) _postBrightness(p[which]);
}

// Relay (amplifier) — state persisted in localStorage
// GPIO wiring pending hardware build.
const _relayState = {};

function _loadRelayState(name) {
  return localStorage.getItem(`td5_relay_${name}`) === 'true';
}

function _applyRelayUI(name) {
  const on  = _relayState[name];
  const btn = document.getElementById(`btn-${name}`);
  const lbl = document.getElementById(`lbl-${name}`);
  if (!btn || !lbl) return;
  lbl.textContent = on ? 'Amp On' : 'Amp Off';
  btn.classList.toggle('relay-btn--on', on);
}

function toggleRelay(name) {
  _relayState[name] = !_relayState[name];
  localStorage.setItem(`td5_relay_${name}`, _relayState[name]);
  _applyRelayUI(name);
  fetch('/system/relay', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ name, state: _relayState[name] }),
  }).catch(() => {});
}

// Initialise settings controls on load
(function _initSettings() {
  _applyBrightUI(_loadBrightPrefs());
  _relayState['amp'] = _loadRelayState('amp');
  _applyRelayUI('amp');
}());

// ── OTA update ─────────────────────────────────
async function triggerUpdate() {
  const btn    = document.getElementById('btn-update');
  const lbl    = document.getElementById('lbl-update');
  const status = document.getElementById('update-status');

  btn.disabled = true;
  lbl.textContent = 'Checking…';
  status.textContent = '';
  status.className = 'update-status';

  try {
    const r    = await fetch('/system/update', { method: 'POST' });
    const data = await r.json();
    lbl.textContent     = 'Restarting…';
    status.textContent  = data.output;
    status.className    = 'update-status update-status--ok';
  } catch (_) {
    // Expected — service restarted before response completed
    lbl.textContent    = 'Restarting…';
    status.textContent = 'Reconnecting…';
    status.className   = 'update-status update-status--ok';
  }

  // Re-enable once the WS reconnects (service is back up)
  const _resetBtn = () => {
    btn.disabled    = false;
    lbl.textContent = 'Check for Updates';
    // Leave status text visible so user can see what changed
  };
  document.addEventListener('td5-ws-connected', _resetBtn, { once: true });
  // Fallback: reset after 30 s if reconnect event never fires
  setTimeout(_resetBtn, 30_000);
}

// ── 2D Navigation System ──────────────────────────
// Horizontal (views): swipe left/right, wraps around
// Vertical (layers): swipe up/down within a view
// Axis locked after ~15px of movement; no diagonal navigation.

const NAV = (() => {
  const SLIDE_MS = 300;
  const EASE = `transform ${SLIDE_MS}ms cubic-bezier(0.4, 0, 0.2, 1)`;
  const SWIPE_MIN = 40;
  const LOCK_PX = 15;

  let viewCount = 0;
  let curView = 0;
  let curLayer = [];       // per-view current layer index (into enabledLayers)
  let enabledLayers = [];  // per-view: sorted array of enabled layer DOM indices
  let animating = false;

  let viewEls = [];
  let layerEls = [];       // layerEls[viewIdx] = array of layer elements

  // Touch state
  let tx0 = 0, ty0 = 0;
  let axis = null;

  function init(pageFlags) {
    viewEls = Array.from(document.querySelectorAll('.view'));
    viewCount = viewEls.length;

    layerEls = viewEls.map(v => Array.from(v.querySelectorAll('.layer')));

    // Build enabled layer arrays per view
    enabledLayers = layerEls.map(layers => {
      const enabled = [];
      layers.forEach((el, idx) => {
        const page = el.dataset.page;
        if (!page || pageFlags[page]) {
          enabled.push(idx);
        }
      });
      return enabled;
    });

    curLayer = enabledLayers.map(() => 0);

    // Position views and layers
    viewEls.forEach((v, vi) => {
      v.style.transition = 'none';
      v.style.transform = vi === 0 ? 'translateX(0)' : 'translateX(9999px)';
    });
    layerEls.forEach(layers => {
      layers.forEach((l, li) => {
        l.style.transition = 'none';
        l.style.transform = li === 0 ? 'translateY(0)' : 'translateY(9999px)';
      });
    });

    // Touch handlers
    const el = document.getElementById('carousel');
    el.addEventListener('touchstart', _onStart, { passive: true });
    el.addEventListener('touchmove', _onMove, { passive: true });
    el.addEventListener('touchend', _onEnd, { passive: true });

    _updateIndicator();
  }

  function _onStart(e) {
    tx0 = e.touches[0].clientX;
    ty0 = e.touches[0].clientY;
    axis = null;
  }

  function _onMove(e) {
    if (axis) return;
    const dx = Math.abs(e.touches[0].clientX - tx0);
    const dy = Math.abs(e.touches[0].clientY - ty0);
    if (dx > LOCK_PX || dy > LOCK_PX) {
      axis = dx > dy ? 'x' : 'y';
    }
  }

  function _onEnd(e) {
    if (_browseOpen) return;
    const dx = tx0 - e.changedTouches[0].clientX;
    const dy = ty0 - e.changedTouches[0].clientY;

    if (!axis) {
      // No axis locked during move — determine from final delta
      if (Math.abs(dx) < SWIPE_MIN && Math.abs(dy) < SWIPE_MIN) return;
      axis = Math.abs(dx) > Math.abs(dy) ? 'x' : 'y';
    }

    if (axis === 'x' && Math.abs(dx) > SWIPE_MIN) {
      goView(dx > 0 ? curView + 1 : curView - 1);
    } else if (axis === 'y' && Math.abs(dy) > SWIPE_MIN) {
      _stepLayer(dy > 0 ? 1 : -1);
    }
    axis = null;
  }

  // ── Horizontal navigation (views) ─────────────
  function goView(n) {
    if (animating) return;
    const next = ((n % viewCount) + viewCount) % viewCount;
    if (next === curView) return;

    animating = true;
    const dir = n > curView ? 1 : -1;

    // Reset current view to layer 0 before leaving
    const curLayerIdx = enabledLayers[curView][curLayer[curView]];
    if (curLayerIdx !== 0) {
      layerEls[curView].forEach((l, li) => {
        l.style.transition = 'none';
        l.style.transform = li === 0 ? 'translateY(0)' : 'translateY(9999px)';
      });
      curLayer[curView] = 0;
    }

    const currEl = viewEls[curView];
    const nextEl = viewEls[next];

    nextEl.style.transition = 'none';
    nextEl.style.transform = `translateX(${dir * 1280}px)`;
    nextEl.getBoundingClientRect();

    nextEl.style.transition = EASE;
    currEl.style.transition = EASE;
    nextEl.style.transform = 'translateX(0)';
    currEl.style.transform = `translateX(${-dir * 1280}px)`;

    curView = next;
    _updateIndicator();

    const leaving = currEl;
    setTimeout(() => {
      leaving.style.transition = 'none';
      leaving.style.transform = 'translateX(9999px)';
      animating = false;
    }, SLIDE_MS);
  }

  // ── Vertical navigation (layers) ──────────────
  function _stepLayer(delta) {
    const enabled = enabledLayers[curView];
    if (enabled.length <= 1) return;

    const nextPos = curLayer[curView] + delta;
    if (nextPos < 0 || nextPos >= enabled.length) return;

    _goToLayerIdx(enabled[nextPos], delta);
  }

  function _goToLayerIdx(targetLayerDomIdx, dir) {
    if (animating) return;
    const currentLayerDomIdx = enabledLayers[curView][curLayer[curView]];
    if (targetLayerDomIdx === currentLayerDomIdx) return;

    if (!dir) dir = targetLayerDomIdx > currentLayerDomIdx ? 1 : -1;

    animating = true;

    const currEl = layerEls[curView][currentLayerDomIdx];
    const nextEl = layerEls[curView][targetLayerDomIdx];

    nextEl.style.transition = 'none';
    nextEl.style.transform = `translateY(${dir * 400}px)`;
    nextEl.getBoundingClientRect();

    nextEl.style.transition = EASE;
    currEl.style.transition = EASE;
    nextEl.style.transform = 'translateY(0)';
    currEl.style.transform = `translateY(${-dir * 400}px)`;

    // Update curLayer to the position in enabled array
    const enabled = enabledLayers[curView];
    curLayer[curView] = enabled.indexOf(targetLayerDomIdx);
    _updateIndicator();

    const leaving = currEl;
    setTimeout(() => {
      leaving.style.transition = 'none';
      leaving.style.transform = 'translateY(9999px)';
      animating = false;
    }, SLIDE_MS);
  }

  // ── Imperative navigation API ─────────────────
  function navigateTo(viewIdx, layerDomIdx) {
    layerDomIdx = layerDomIdx || 0;

    if (viewIdx !== curView) {
      goView(viewIdx);
      if (layerDomIdx > 0) {
        setTimeout(() => {
          const enabled = enabledLayers[viewIdx];
          if (enabled.includes(layerDomIdx)) {
            _goToLayerIdx(layerDomIdx);
          }
        }, SLIDE_MS + 50);
      }
    } else {
      const enabled = enabledLayers[viewIdx];
      if (enabled.includes(layerDomIdx)) {
        _goToLayerIdx(layerDomIdx);
      }
    }
  }

  function _updateIndicator() {
    // No-op — nav dots removed per user preference
  }

  return {
    init,
    goView,
    navigateTo,
    get currentView() { return curView; },
    get currentLayerDomIdx() {
      return enabledLayers[curView]?.[curLayer[curView]] ?? 0;
    },
  };
})();

// ── Spotify playlist browser ───────────────────
// State: 'playlists' shows the playlist grid; 'tracks' shows the track list
// for a selected playlist.  _browseOpen gates the carousel swipe handler so
// horizontal swipes inside the panel don't navigate to another view.

let _browseOpen     = false;
let _browseState    = 'playlists';
let _browsePlaylist = null;   // {id, uri, name} of selected playlist

function _esc(s) {
  return (s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function _openBrowse() {
  _browseOpen = true;
  document.getElementById('sp-browse').style.display = 'flex';
  _showPlaylists();
}

function _closeBrowse() {
  _browseOpen = false;
  document.getElementById('sp-browse').style.display = 'none';
}

function _showPlaylists() {
  _browseState = 'playlists';
  document.getElementById('sp-browse-title').textContent = 'Playlists';
  document.getElementById('sp-browse-back').style.visibility = 'hidden';
  _setBrowseBody('<div class="sp-browse-msg">Loading…</div>');

  fetch('/spotify/playlists')
    .then(r => r.ok ? r.json() : Promise.reject(r.status))
    .then(data => _renderPlaylists(data.playlists))
    .catch(() => _setBrowseBody(
      '<div class="sp-browse-msg">Could not load playlists</div>'
    ));
}

function _showTracks(playlist) {
  _browseState    = 'tracks';
  _browsePlaylist = playlist;
  document.getElementById('sp-browse-title').textContent = playlist.name;
  document.getElementById('sp-browse-back').style.visibility = 'visible';
  _setBrowseBody('<div class="sp-browse-msg">Loading…</div>');

  fetch(`/spotify/playlist/${playlist.id}/tracks`)
    .then(r => r.ok ? r.json() : Promise.reject(r.status))
    .then(data => _renderTracks(data.tracks, playlist.uri))
    .catch(() => _setBrowseBody(
      '<div class="sp-browse-msg">Could not load tracks</div>'
    ));
}

function _setBrowseBody(html) {
  document.getElementById('sp-browse-body').innerHTML = html;
}

function _renderPlaylists(playlists) {
  const list = document.createElement('div');
  list.className = 'sp-pl-list';

  playlists.forEach(pl => {
    const card = document.createElement('div');
    card.className = 'sp-pl-card';
    const artHtml = pl.image_url
      ? `<img class="sp-pl-art" src="${_esc(pl.image_url)}" alt="" loading="lazy">`
      : `<div class="sp-pl-art-ph"><svg viewBox="0 0 24 24">
           <path d="M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z"/>
         </svg></div>`;
    card.innerHTML = `
      ${artHtml}
      <div class="sp-pl-name">${_esc(pl.name)}</div>`;
    card.addEventListener('click', () => _showTracks(pl));
    list.appendChild(card);
  });

  const body = document.getElementById('sp-browse-body');
  body.innerHTML = '';
  body.appendChild(list);
}

function _renderTracks(tracks, contextUri) {
  if (!tracks || tracks.length === 0) {
    const body = document.getElementById('sp-browse-body');
    body.innerHTML = '<div class="sp-browse-msg">Track listing not available for this playlist</div>';
    const btn = document.createElement('button');
    btn.className = 'sp-play-pl-btn';
    btn.textContent = 'Play Playlist';
    btn.addEventListener('click', () => { _playTrack(contextUri, null); _closeBrowse(); });
    body.appendChild(btn);
    return;
  }

  const list = document.createElement('div');
  list.className = 'sp-tr-list';

  tracks.forEach((tr, i) => {
    const row = document.createElement('div');
    row.className = 'sp-tr-row';
    row.innerHTML = `
      <span class="sp-tr-num">${i + 1}</span>
      <div class="sp-tr-info">
        <div class="sp-tr-name">${_esc(tr.name)}</div>
        <div class="sp-tr-artist">${_esc(tr.artist)}</div>
      </div>
      <span class="sp-tr-dur">${formatTime(tr.duration_s)}</span>`;
    row.addEventListener('click', () => _playTrack(contextUri, tr.uri));
    list.appendChild(row);
  });

  const body = document.getElementById('sp-browse-body');
  body.innerHTML = '';
  body.appendChild(list);
}

async function _playTrack(contextUri, trackUri) {
  try {
    await fetch('/spotify/play', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ context_uri: contextUri, track_uri: trackUri }),
    });
  } catch (_) { /* ignore — WS polling will reflect the outcome */ }
  _closeBrowse();
}

document.getElementById('sp-like-btn').addEventListener('click', () => {
  if (!_spTrackId) return;
  const btn = document.getElementById('sp-like-btn');
  btn.classList.add('sp-btn--liked');
  fetch('/spotify/like', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ track_id: _spTrackId }),
  }).catch(() => btn.classList.remove('sp-btn--liked'));
});

document.getElementById('sp-browse-btn').addEventListener('click', _openBrowse);
document.getElementById('sp-browse-close').addEventListener('click', _closeBrowse);
document.getElementById('sp-browse-back').addEventListener('click', () => {
  if (_browseState === 'tracks') _showPlaylists();
});

// ── Spotify controls ───────────────────────────
async function _spotifyCmd(action) {
  try {
    await fetch('/spotify/command', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ action }),
    });
  } catch (_) { /* ignore — WS update will reflect the outcome */ }
}

document.getElementById('sp-prev').addEventListener('click', () => _spotifyCmd('prev'));
document.getElementById('sp-next').addEventListener('click', () => _spotifyCmd('next'));
document.getElementById('sp-play').addEventListener('click', () =>
  _spotifyCmd(_spPlaying ? 'pause' : 'play')
);

// ── Faults tile navigation ──────────────────────
document.getElementById('tile-faults').addEventListener('click', () => {
  NAV.navigateTo(0, 1);  // Engine view, layer 1 (Engine Detail)
});

// ── DTC detail (Engine layer 1) ─────────────────
// Known TD5 DTC descriptions (subset — unknown codes show hex)
const _DTC_TABLE = {
  0x0263: 'Injector 1 open', 0x0266: 'Injector 2 open', 0x0269: 'Injector 3 open',
  0x026C: 'Injector 4 open', 0x026F: 'Injector 5 open',
  0x0380: 'Glow plug 1', 0x0381: 'Glow plug 2', 0x0382: 'Glow plug 3',
  0x0383: 'Glow plug 4', 0x0384: 'Glow plug 5',
  0x0100: 'MAF sensor', 0x0105: 'Inlet air temp', 0x0110: 'Coolant temp high',
  0x0115: 'Coolant temp low', 0x0120: 'Throttle position', 0x0235: 'Boost sensor',
  0x0400: 'EGR fault', 0x0500: 'Speed sensor', 0x0560: 'System voltage',
  0x0340: 'Crank sensor', 0x0341: 'Cam sensor',
};

function _dtcDesc(code) {
  return _DTC_TABLE[code] || `Unknown (0x${code.toString(16).toUpperCase().padStart(4, '0')})`;
}

function _updateDTCList(faultCodes) {
  const list = document.getElementById('dtc-list');
  if (!faultCodes || faultCodes.length === 0) {
    list.innerHTML = '<div class="dtc-none">No faults stored</div>';
    return;
  }
  list.innerHTML = faultCodes.map(code => {
    const hex = code.toString(16).toUpperCase().padStart(4, '0');
    return `<div class="dtc-row">
      <span class="dtc-code">${hex}</span>
      <span class="dtc-desc">${_dtcDesc(code)}</span>
    </div>`;
  }).join('');
}

function clearDTC() {
  document.getElementById('dtc-clear-confirm').style.display = 'flex';
}

function cancelClearDTC() {
  document.getElementById('dtc-clear-confirm').style.display = 'none';
}

async function confirmClearDTC() {
  document.getElementById('dtc-clear-confirm').style.display = 'none';
  const lbl = document.getElementById('lbl-clear-dtc');
  lbl.textContent = 'Clearing...';
  try {
    const r = await fetch('/obd/clear-dtc', { method: 'POST' });
    const d = await r.json();
    lbl.textContent = d.ok ? 'Cleared' : (d.detail || 'Failed');
  } catch (_) {
    lbl.textContent = 'Error';
  }
  setTimeout(() => { lbl.textContent = 'Clear Faults'; }, 3000);
}

// ── Trip computer (in-memory session data) ──────
const _trip = {
  peakRpm: 0,
  peakBoost: 0,
  peakCoolant: 0,
  speedSum: 0,
  speedCount: 0,
  startTime: Date.now(),

  update(d) {
    if (d.rpm > this.peakRpm) this.peakRpm = d.rpm;
    if (d.boost_bar > this.peakBoost) this.peakBoost = d.boost_bar;
    if (d.coolant_temp_c > this.peakCoolant) this.peakCoolant = d.coolant_temp_c;
    if (d.road_speed_kph > 0) {
      this.speedSum += d.road_speed_kph;
      this.speedCount++;
    }
  },

  render() {
    const s = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    s('txt-trip-peak-rpm', this.peakRpm > 0 ? `${Math.round(this.peakRpm)} RPM` : '—');
    s('txt-trip-peak-boost', this.peakBoost > 0 ? `${this.peakBoost.toFixed(2)} bar` : '—');
    s('txt-trip-peak-coolant', this.peakCoolant > 0 ? `${Math.round(this.peakCoolant)} °C` : '—');
    s('txt-trip-avg-speed', this.speedCount > 0
      ? `${Math.round(this.speedSum / this.speedCount)} kph` : '—');
    const elapsed = Math.floor((Date.now() - this.startTime) / 1000);
    s('txt-trip-time', formatUptime(elapsed));
  },

  reset() {
    this.peakRpm = 0;
    this.peakBoost = 0;
    this.peakCoolant = 0;
    this.speedSum = 0;
    this.speedCount = 0;
    this.startTime = Date.now();
    this.render();
  },
};

function resetTrip() {
  _trip.reset();
}

// ── Throttle calibration (Setup wizard) ─────────
let _lastThrottleRaw = null;

function _updateThrottleRaw(raw) {
  _lastThrottleRaw = raw;
  const el = document.getElementById('throttle-raw-val');
  if (el) el.textContent = raw != null ? `${raw.toFixed(1)} %` : '— %';
}

async function setThrottleIdle() {
  if (_lastThrottleRaw == null) return;
  await fetch('/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ throttle_idle: _lastThrottleRaw.toString() }),
  });
  const el = document.getElementById('throttle-cal-idle');
  if (el) el.textContent = _lastThrottleRaw.toFixed(1);
}

async function setThrottleWOT() {
  if (_lastThrottleRaw == null) return;
  await fetch('/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ throttle_wot: _lastThrottleRaw.toString() }),
  });
  const el = document.getElementById('throttle-cal-wot');
  if (el) el.textContent = _lastThrottleRaw.toFixed(1);
}

// ── Raw data handler (Engine layer 3) ───────────
function _updateRawData(d) {
  const s = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
  s('raw-rpm',      d.rpm != null ? `${d.rpm} RPM` : '—');
  s('raw-battery',  d.battery_v != null ? `${d.battery_v.toFixed(2)} V` : '—');
  s('raw-speed',    d.road_speed_kph != null ? `${d.road_speed_kph} kph` : '—');
  s('raw-coolant',  d.coolant_temp_c != null ? `${d.coolant_temp_c} °C` : '—');
  s('raw-air',      d.inlet_air_temp_c != null ? `${d.inlet_air_temp_c} °C` : '—');
  s('raw-ext',      d.external_temp_c != null ? `${d.external_temp_c} °C` : '—');
  s('raw-fuel',     d.fuel_temp_c != null ? `${d.fuel_temp_c} °C` : '—');
  s('raw-boost',    d.boost_bar != null ? `${d.boost_bar.toFixed(3)} bar` : '—');
  s('raw-throttle', d.throttle_pct != null ? `${d.throttle_pct.toFixed(1)} %` : '—');
  s('raw-faults',   (d.fault_codes || []).length.toString());
}

// ── Pages screen ────────────────────────────────
// Single screen with all toggles. Restart button appears on any change.

const _PAGES_TOGGLES = [
  { key: 'engine_detail', label: 'Engine Detail', desc: 'DTC fault list & trip computer' },
  { key: 'engine_stats',  label: 'Engine Stats',  desc: 'Session history charts' },
  { key: 'engine_raw',    label: 'Raw Data',       desc: 'All PID values' },
  { key: 'settings_diagnostics', label: 'Diagnostics', desc: 'Service health & OBD tests' },
];

let _pagesOriginal = {};
let _pagesCurrent = {};

function _pagesInit(pageFlags) {
  _pagesOriginal = { ...pageFlags };
  _pagesCurrent = { ...pageFlags };
  _pagesRender();
}

function _pagesRender() {
  const container = document.getElementById('pages-toggles');
  container.innerHTML = _PAGES_TOGGLES.map(t => {
    const checked = _pagesCurrent[t.key] ? 'checked' : '';
    return `<div class="pages-toggle-row" data-page-key="${t.key}">
      <div class="pages-toggle-info">
        <div class="pages-toggle-label">${t.label}</div>
        <div class="pages-toggle-desc">${t.desc}</div>
      </div>
      <label class="toggle-switch">
        <input type="checkbox" id="pages-cb-${t.key}" ${checked}>
        <span class="toggle-slider"></span>
      </label>
    </div>`;
  }).join('');

  // Tap anywhere in the row toggles the checkbox
  container.querySelectorAll('.pages-toggle-row').forEach(row => {
    row.addEventListener('click', e => {
      if (e.target.closest('.toggle-switch')) return; // let the checkbox handle itself
      const key = row.dataset.pageKey;
      const cb = document.getElementById('pages-cb-' + key);
      cb.checked = !cb.checked;
      _pagesToggle(key, cb.checked);
    });
    // Also wire up the checkbox change event
    const key = row.dataset.pageKey;
    document.getElementById('pages-cb-' + key).addEventListener('change', function() {
      _pagesToggle(key, this.checked);
    });
  });
}

function _pagesToggle(key, enabled) {
  _pagesCurrent[key] = enabled ? 1 : 0;
  fetch('/pages', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ [key]: _pagesCurrent[key] }),
  }).catch(() => {});
  const changed = _PAGES_TOGGLES.some(t => _pagesCurrent[t.key] !== _pagesOriginal[t.key]);
  document.getElementById('pages-changed-msg').style.visibility = changed ? 'visible' : 'hidden';
  document.getElementById('pages-restart-btn').disabled = !changed;
}

function pagesRestart() {
  _doReboot();
}

// ── Setup wizard ────────────────────────────────

function confirmRestart() {
  document.getElementById('restart-confirm').style.display = 'flex';
}

function cancelRestart() {
  document.getElementById('restart-confirm').style.display = 'none';
}

function doRestart() {
  _doReboot();
}

function _doReboot() {
  fetch('/system/restart', { method: 'POST' }).catch(() => {});
}

function reimportWeatherLocation() {
  // This will be fully implemented when the backend supports reading .env
  // For now, just show a brief confirmation
  fetch('/settings').then(r => r.json()).then(settings => {
    alert('Weather location: ' + (settings.weather_location || 'unknown'));
  }).catch(() => {});
}

// ── Engine Stats charts ─────────────────────────
function _drawLineChart(canvasId, data, color) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || !data.length) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width = canvas.clientWidth;
  const h = canvas.height = canvas.clientHeight;
  ctx.clearRect(0, 0, w, h);

  const vals = data.filter(v => v != null);
  if (vals.length < 2) return;

  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const range = max - min || 1;
  const pad = 4;

  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  for (let i = 0; i < vals.length; i++) {
    const x = pad + (i / (vals.length - 1)) * (w - pad * 2);
    const y = h - pad - ((vals[i] - min) / range) * (h - pad * 2);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.stroke();

  // Min/max labels
  ctx.fillStyle = '#666';
  ctx.font = '11px system-ui';
  ctx.fillText(max.toFixed(0), 4, 14);
  ctx.fillText(min.toFixed(0), 4, h - 4);
}

async function loadHistory(range) {
  // Update active button
  document.querySelectorAll('.stats-range-btn').forEach(b => {
    b.classList.toggle('stats-range-btn--active', b.dataset.range === range);
  });

  try {
    const r = await fetch(`/history?time_range=${range}`);
    const d = await r.json();
    const rows = d.rows || [];
    _drawLineChart('chart-speed', rows.map(r => r.speed), '#40c4ff');
    _drawLineChart('chart-rpm', rows.map(r => r.rpm), '#00e676');
    _drawLineChart('chart-coolant', rows.map(r => r.coolant), '#ffab40');
  } catch (_) {}
}

// ── Diagnostics screen ──────────────────────────
async function refreshDiagnostics() {
  try {
    const r = await fetch('/health');
    const h = await r.json();
    const grid = document.getElementById('diag-services');
    grid.innerHTML = Object.entries(h.services).map(([name, status]) =>
      `<div class="diag-svc">
        <span class="diag-svc-name">${name}</span>
        <span class="diag-svc-status ${status}">${status}</span>
      </div>`
    ).join('');
    document.getElementById('diag-ws-count').textContent = h.ws_clients;
  } catch (_) {}
}

async function runOBDTest(test) {
  const result = document.getElementById('diag-test-result');
  result.textContent = `Running ${test} test...`;
  result.textContent = `${test} test: not yet implemented (requires live OBD connection)`;
}

// ── WebSocket ──────────────────────────────────
const connDot  = document.getElementById('conn-dot');
const connTxt  = document.getElementById('txt-conn');

function setConnState(cls, label) {
  connDot.className = `status-dot ${cls}`;
  connTxt.textContent = label;
}

function connect() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws    = new WebSocket(`${proto}//${location.host}/ws`);

  ws.onopen = () => {
    setConnState('connected', 'Online');
    document.dispatchEvent(new CustomEvent('td5-ws-connected'));
  };

  ws.onmessage = e => {
    const { type, data } = JSON.parse(e.data);
    switch (type) {
      case 'engine':  handleEngine(data);  break;
      case 'spotify': handleSpotify(data); break;
      case 'victron': handleVictron(data); break;
      case 'system':   handleSystem(data);   break;
      case 'starlink': handleStarlink(data); break;
      case 'gps':      handleGps(data);      break;
      case 'weather':  handleWeather(data);  break;
    }
  };

  ws.onclose = () => {
    setConnState('error', 'Offline');
    setTimeout(connect, 3000);
  };

  ws.onerror = () => ws.close();
}

// ── Startup: fetch pages, init nav, connect WS ──
(async function _startup() {
  let pageFlags = {};
  try {
    const r = await fetch('/pages');
    pageFlags = await r.json();
  } catch (_) {
    // Defaults: all enabled
    pageFlags = { engine_detail: 1, engine_stats: 1, engine_raw: 1, settings_diagnostics: 0 };
  }

  NAV.init(pageFlags);
  _pagesInit(pageFlags);

  // Load throttle calibration display values
  try {
    const sr = await fetch('/settings');
    const settings = await sr.json();
    const idleEl = document.getElementById('throttle-cal-idle');
    const wotEl = document.getElementById('throttle-cal-wot');
    if (idleEl && settings.throttle_idle) idleEl.textContent = parseFloat(settings.throttle_idle).toFixed(1);
    if (wotEl && settings.throttle_wot) wotEl.textContent = parseFloat(settings.throttle_wot).toFixed(1);
  } catch (_) {}

  connect();
})();
