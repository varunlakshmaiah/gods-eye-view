/**
 * Gods-Eye-View Sidecar Co-Pilot Bridge
 * Zero-touch client script auto-injected by proxy-tee.
 * Bridges Cesium 3D Globe with Headless AI Assistant / MCP Gateway.
 */
(() => {
  console.log('%c[GodsEyeSidecar]%c Co-Pilot UI Bridge initializing...', 'color: #00ffcc; font-weight: bold;', 'color: inherit;');

  const GATEWAY_WS_PORT = 8000;
  let socket = null;
  let reconnectAttempts = 0;
  const MAX_RECONNECT_DELAY = 15000;

  function toRadians(degrees) {
    return (degrees * Math.PI) / 180.0;
  }

  function toDegrees(radians) {
    return (radians * 180.0) / Math.PI;
  }

  function getCesiumGlobals() {
    const gev = window.__godsEyeView;
    const viewer = gev?.viewer || window.viewer;
    const Cartesian3 = window.Cesium?.Cartesian3 || viewer?.camera?.position?.constructor;
    return { gev, viewer, Cartesian3 };
  }

  async function waitForViewer(timeoutMs = 15000) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      const { viewer } = getCesiumGlobals();
      if (viewer?.camera) return viewer;
      await new Promise((r) => setTimeout(r, 250));
    }
    return getCesiumGlobals().viewer;
  }

  function computeCartesian(viewer, lon, lat, alt = 5000) {
    const Cartesian3 = window.Cesium?.Cartesian3 || viewer?.camera?.position?.constructor;
    if (Cartesian3?.fromDegrees) {
      return Cartesian3.fromDegrees(lon, lat, alt);
    }
    const ellipsoid = viewer?.scene?.globe?.ellipsoid;
    if (ellipsoid?.cartographicToCartesian) {
      return ellipsoid.cartographicToCartesian({
        longitude: toRadians(lon),
        latitude: toRadians(lat),
        height: alt,
      });
    }
    // WGS84 Ellipsoid closed-form fallback
    const radLat = toRadians(lat);
    const radLon = toRadians(lon);
    const a = 6378137.0;
    const f = 1.0 / 298.257223563;
    const e2 = 2 * f - f * f;
    const sinLat = Math.sin(radLat);
    const cosLat = Math.cos(radLat);
    const N = a / Math.sqrt(1.0 - e2 * sinLat * sinLat);
    const x = (N + alt) * cosLat * Math.cos(radLon);
    const y = (N + alt) * cosLat * Math.sin(radLon);
    const z = (N * (1.0 - e2) + alt) * sinLat;
    return Cartesian3 ? new Cartesian3(x, y, z) : { x, y, z };
  }

  function getCameraViewport() {
    const { viewer } = getCesiumGlobals();
    if (!viewer?.camera) return null;

    try {
      const camera = viewer.camera;
      const carto = camera.positionCartographic;
      if (!carto) return null;

      return {
        lat: toDegrees(carto.latitude),
        lon: toDegrees(carto.longitude),
        alt: carto.height,
        heading: toDegrees(camera.heading),
        pitch: toDegrees(camera.pitch),
        roll: toDegrees(camera.roll),
        timestamp: new Date().toISOString(),
      };
    } catch (e) {
      return null;
    }
  }

  function connect() {
    const host = window.location.hostname || 'localhost';
    const wsUrl = `ws://${host}:${GATEWAY_WS_PORT}/ws/copilot`;

    try {
      socket = new WebSocket(wsUrl);

      socket.onopen = () => {
        reconnectAttempts = 0;
        console.log('%c[GodsEyeSidecar]%c Connected to MCP Gateway Co-Pilot Hub', 'color: #00ffcc; font-weight: bold;', 'color: inherit;');
        sendViewportUpdate();
      };

      socket.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'command') {
            handleCommand(msg);
          } else if (msg.type === 'tactical_alert') {
            handleTacticalAlert(msg.alert);
          } else if (msg.type === 'aoi_sync') {
            renderAois(msg.aois);
          } else if (msg.type === 'escort_sync') {
            renderEscortBubbles(msg.devices);
          }
        } catch (err) {
          console.warn('[GodsEyeSidecar] Error parsing message:', err);
        }
      };

      socket.onclose = () => {
        scheduleReconnect();
      };

      socket.onerror = () => {
        socket.close();
      };
    } catch (e) {
      scheduleReconnect();
    }
  }

  function scheduleReconnect() {
    const delay = Math.min(1000 * Math.pow(1.5, reconnectAttempts), MAX_RECONNECT_DELAY);
    reconnectAttempts++;
    setTimeout(connect, delay);
  }

  function sendViewportUpdate() {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    const vp = getCameraViewport();
    if (vp) {
      socket.send(JSON.stringify({
        type: 'viewport_update',
        payload: vp,
      }));
    }
  }

  // Handle AI Agent commands sent to the browser
  async function handleCommand(msg) {
    const { req_id, action, params = {} } = msg;
    const { gev } = getCesiumGlobals();
    let result = { ok: true };

    console.log(`%c[GodsEyeSidecar] Executing Action:%c ${action}`, 'color: #00ffcc; font-weight: bold;', 'color: inherit;', params);

    try {
      const viewer = await waitForViewer(5000);
      if (!viewer) {
        throw new Error('Cesium viewer not ready in window.__godsEyeView after 5s timeout');
      }

      switch (action) {
        case 'fly_to_coordinate': {
          const lat = Number(params.lat);
          const lon = Number(params.lon);
          const alt = Number(params.altitude_m || 5000);
          const duration = Math.max(0.2, Number(params.duration_s || 2.5));
          const heading = toRadians(Number(params.heading_deg || 0));
          const pitch = toRadians(Number(params.pitch_deg || -45));
          const roll = 0.0;

          const destination = computeCartesian(viewer, lon, lat, alt);
          if (!destination) {
            throw new Error(`Failed to calculate 3D Cartesian coordinates for (${lat}, ${lon}, ${alt})`);
          }

          console.log(`[GodsEyeSidecar] Flying camera to [lon=${lon}, lat=${lat}, alt=${alt}m] over ${duration}s`);

          viewer.camera.flyTo({
            destination,
            orientation: { heading, pitch, roll },
            duration,
            complete: () => {
              console.log('[GodsEyeSidecar] Camera flight completed');
              gev?.requestRender?.('copilot-flight-complete');
              viewer.scene?.requestRender?.();
            },
            cancel: () => {
              console.log('[GodsEyeSidecar] Camera flight cancelled');
            },
          });

          // Kick render governor so frames animate smoothly
          gev?.requestRender?.('copilot-flight-start');
          viewer.scene?.requestRender?.();

          result = { lat, lon, alt, duration };
          break;
        }

        case 'toggle_layer': {
          const targetId = params.layer_id || params.layer_name;
          const isEnabled = params.enabled !== undefined ? Boolean(params.enabled) : (params.visible !== undefined ? Boolean(params.visible) : true);
          const dataManager = gev?.dataManager;

          if (dataManager) {
            if (isEnabled) {
              dataManager.enableLayer?.(targetId);
            } else {
              dataManager.disableLayer?.(targetId);
            }
            gev?.requestRender?.('copilot-layer-toggle');
            viewer.scene?.requestRender?.();
            result = { layer_id: targetId, enabled: isEnabled };
          } else {
            throw new Error('dataManager not found on window.__godsEyeView');
          }
          break;
        }

        case 'select_entity': {
          const { entity_id, layer } = params;
          window.dispatchEvent(new CustomEvent('sidecar:select-entity', { detail: { entity_id, layer } }));
          window.dispatchEvent(new CustomEvent('gev:world-request-focus', { detail: { id: entity_id, layer } }));
          gev?.requestRender?.('copilot-select-entity');
          viewer.scene?.requestRender?.();
          result = { entity_id, layer, selected: true };
          break;
        }

        case 'set_time_replay_mode': {
          const { timestamp, live, mode: paramMode } = params;
          const mode = paramMode || (live ? 'live' : 'replay');
          await fetch('/__sidecar/playback', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode, timestamp }),
          });

          window.dispatchEvent(new CustomEvent('sidecar:playback-changed', { detail: { mode, timestamp } }));
          gev?.requestRender?.('copilot-playback');
          viewer.scene?.requestRender?.();
          result = { mode, timestamp };
          break;
        }

        case 'get_active_viewport': {
          result = getCameraViewport() || { error: 'Viewer camera unavailable' };
          break;
        }

        case 'toggle_aoi_visuals': {
          const { visible } = params;
          if (aoiDataSource) {
            aoiDataSource.show = visible !== undefined ? Boolean(visible) : !aoiDataSource.show;
            gev?.requestRender?.('aoi-visibility-toggle');
            viewer.scene?.requestRender?.();
            result = { visible: aoiDataSource.show };
          } else {
            result = { visible: false, message: 'AOI data source not initialized yet' };
          }
          break;
        }

        case 'refresh_aois': {
          await syncAoiVisuals();
          result = { refreshed: true, aoi_count: cachedAois.length };
          break;
        }

        case 'render_radar_domes': {
          const { enabled, stations, station_id } = params;
          await renderRadarDomes(stations, enabled !== undefined ? enabled : true, station_id);
          result = { rendered: true, enabled: enabled !== false };
          break;
        }

        case 'toggle_radar_domes': {
          const { visible } = params;
          if (radarDataSource) {
            radarDataSource.show = visible !== undefined ? Boolean(visible) : !radarDataSource.show;
            gev?.requestRender?.('radar-visibility-toggle');
            viewer.scene?.requestRender?.();
            result = { visible: radarDataSource.show };
          } else {
            result = { visible: false, message: 'Radar data source not initialized yet' };
          }
          break;
        }

        case 'toggle_escort_visuals': {
          const { visible } = params;
          if (escortDataSource) {
            escortDataSource.show = visible !== undefined ? Boolean(visible) : !escortDataSource.show;
            gev?.requestRender?.('escort-visibility-toggle');
            viewer.scene?.requestRender?.();
            result = { visible: escortDataSource.show };
          } else {
            result = { visible: false, message: 'Escort data source not initialized yet' };
          }
          break;
        }

        case 'refresh_escorts': {
          await syncEscortVisuals();
          result = { refreshed: true, device_count: cachedEscortDevices.length };
          break;
        }

        case 'fly_to_escort': {
          const { device_id } = params;
          const target = cachedEscortDevices.find(d => 
            d.device_id === device_id || 
            (d.name && d.name.toLowerCase().includes((device_id || '').toLowerCase()))
          );
          if (target && target.lat !== undefined && target.lon !== undefined) {
            const altM = Math.max(6000, (target.bubble_radius_km || 25) * 1500);
            await handleCommand({
              action: 'fly_to_coordinate',
              params: {
                lat: target.lat,
                lon: target.lon,
                altitude_m: altM,
                duration_s: 2.5
              }
            });
            result = { flying_to: target.device_id, name: target.name, lat: target.lat, lon: target.lon };
          } else {
            result = { error: `Escort device '${device_id}' not found in active cache` };
          }
          break;
        }

        default:
          throw new Error(`Unknown action: ${action}`);
      }
    } catch (err) {
      console.error(`%c[GodsEyeSidecar] Action '${action}' failed:%c`, 'color: #ff3366; font-weight: bold;', 'color: inherit;', err);
      result = { ok: false, error: err.message };
    }

    // Send response back
    if (socket && socket.readyState === WebSocket.OPEN && req_id) {
      socket.send(JSON.stringify({
        type: 'response',
        req_id,
        result,
      }));
    }

    // Update viewport after camera action
    setTimeout(sendViewportUpdate, 500);
  }

  function handleTacticalAlert(alert) {
    if (!alert) return;
    console.warn('%c[TACTICAL ALERT]%c ' + alert.title, 'color: #ff0055; font-weight: bold; background: #220011; padding: 2px 6px;', 'color: inherit;', alert);

    // 1. Request Notification permission if needed and trigger native Desktop Notification
    if ('Notification' in window) {
      if (Notification.permission === 'default') {
        Notification.requestPermission();
      }
      if (Notification.permission === 'granted') {
        try {
          new Notification(`🚨 ${alert.title}`, {
            body: alert.message,
            icon: '/favicon.ico'
          });
        } catch (e) {}
      }
    }

    // 2. Render Tactical HUD Banner on Cesium Canvas
    renderAlertBanner(alert);
  }

  function renderAlertBanner(alert) {
    const existing = document.getElementById('sidecar-tactical-banner');
    if (existing) existing.remove();

    const banner = document.createElement('div');
    banner.id = 'sidecar-tactical-banner';
    const isCritical = alert.severity === 'CRITICAL';
    const borderColor = isCritical ? '#ff0055' : '#ffaa00';
    const bgColor = isCritical ? 'rgba(30, 0, 15, 0.94)' : 'rgba(35, 25, 0, 0.94)';

    banner.style.cssText = `
      position: fixed;
      top: 16px;
      left: 50%;
      transform: translateX(-50%);
      z-index: 99999;
      background: ${bgColor};
      border: 2px solid ${borderColor};
      border-radius: 8px;
      padding: 12px 20px;
      color: #fff;
      font-family: monospace, sans-serif;
      box-shadow: 0 4px 24px rgba(0,0,0,0.8), 0 0 16px ${borderColor};
      display: flex;
      align-items: center;
      gap: 16px;
      max-width: 650px;
    `;

    banner.innerHTML = `
      <div style="font-size: 24px;">🚨</div>
      <div style="flex: 1;">
        <div style="font-size: 11px; font-weight: bold; color: ${borderColor}; text-transform: uppercase; letter-spacing: 1px;">
          ${alert.severity} • ${alert.category}
        </div>
        <div style="font-size: 14px; font-weight: bold; margin: 2px 0;">${alert.title}</div>
        <div style="font-size: 12px; opacity: 0.85;">${alert.message}</div>
      </div>
      ${alert.lat && alert.lon ? `
      <button id="sidecar-fly-btn" style="
        background: ${borderColor};
        border: none;
        color: #000;
        font-weight: bold;
        padding: 6px 12px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 11px;
        text-transform: uppercase;
      ">FLY TO TARGET</button>` : ''}
      <button id="sidecar-close-btn" style="
        background: transparent;
        border: none;
        color: #fff;
        font-size: 16px;
        cursor: pointer;
        opacity: 0.7;
      ">✕</button>
    `;

    document.body.appendChild(banner);

    const closeBtn = document.getElementById('sidecar-close-btn');
    if (closeBtn) closeBtn.onclick = () => banner.remove();

    const flyBtn = document.getElementById('sidecar-fly-btn');
    if (flyBtn) {
      flyBtn.onclick = () => {
        handleCommand({
          action: 'fly_to_coordinate',
          params: { lat: alert.lat, lon: alert.lon, altitude_m: 12000, duration: 2.5 }
        });
        banner.remove();
      };
    }

    // Auto-dismiss after 15 seconds
    setTimeout(() => {
      if (banner.parentElement) banner.remove();
    }, 15000);
  }

  let aoiDataSource = null;
  let cachedAois = [];

  async function renderAois(aois) {
    if (aois && Array.isArray(aois)) {
      cachedAois = aois;
    }
    const viewer = await waitForViewer(12000);
    const { gev } = getCesiumGlobals();
    const Cesium = window.Cesium;
    if (!viewer || !Cesium) return;

    if (!aoiDataSource) {
      if (Cesium.CustomDataSource) {
        aoiDataSource = new Cesium.CustomDataSource('sidecar-aois');
        viewer.dataSources.add(aoiDataSource);
      }
    }

    const entityTarget = aoiDataSource ? aoiDataSource.entities : viewer.entities;
    if (aoiDataSource) {
      aoiDataSource.entities.removeAll();
    }

    for (const aoi of cachedAois) {
      if (!aoi.enabled) continue;
      const radiusMeters = (aoi.radius_km || 50.0) * 1000.0;
      const center = Cesium.Cartesian3.fromDegrees(aoi.lon, aoi.lat);

      // 1. Semi-transparent glowing tactical geofence fill + outline ring
      entityTarget.add({
        id: `aoi_ring_${aoi.id}`,
        name: `AOI: ${aoi.name}`,
        position: center,
        ellipse: {
          semiMinorAxis: radiusMeters,
          semiMajorAxis: radiusMeters,
          material: Cesium.Color.fromCssColorString('rgba(0, 255, 204, 0.08)'),
          outline: true,
          outlineColor: Cesium.Color.fromCssColorString('#00ffcc'),
          outlineWidth: 2,
          height: 0,
        },
      });

      // 2. Center Tactical Beacon Point & Label
      entityTarget.add({
        id: `aoi_beacon_${aoi.id}`,
        position: center,
        point: {
          pixelSize: 8,
          color: Cesium.Color.fromCssColorString('#00ffcc'),
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 2,
        },
        label: {
          text: `🛡️ ${aoi.name.toUpperCase()} AOI [${aoi.radius_km}km]`,
          font: 'bold 12px monospace',
          fillColor: Cesium.Color.fromCssColorString('#00ffcc'),
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 3,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          pixelOffset: new Cesium.Cartesian2(0, -12),
          distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 8000000),
        },
      });
    }

    gev?.requestRender?.('aoi-rings-rendered');
    viewer.scene?.requestRender?.();
    console.log(`%c[GodsEyeSidecar]%c Rendered ${cachedAois.length} AOI perimeter ring(s) in 3D Cesium.`, 'color: #00ffcc; font-weight: bold;', 'color: inherit;');
  }

  async function syncAoiVisuals() {
    try {
      const res = await fetch('http://localhost:8000/api/aois');
      if (res.ok) {
        const data = await res.json();
        if (data.aois) {
          renderAois(data.aois);
        }
      }
    } catch (e) {}
  }

  let radarDataSource = null;
  let cachedRadarStations = [];

  async function renderRadarDomes(stations, enabled = true, stationFilter = null) {
    if (stations && Array.isArray(stations)) {
      cachedRadarStations = stations;
    }
    const viewer = await waitForViewer(12000);
    const { gev } = getCesiumGlobals();
    const Cesium = window.Cesium;
    if (!viewer || !Cesium) return;

    if (!radarDataSource) {
      if (Cesium.CustomDataSource) {
        radarDataSource = new Cesium.CustomDataSource('sidecar-radar-domes');
        viewer.dataSources.add(radarDataSource);
      }
    }

    const entityTarget = radarDataSource ? radarDataSource.entities : viewer.entities;
    if (radarDataSource) {
      radarDataSource.entities.removeAll();
      radarDataSource.show = enabled;
    }

    if (!enabled) {
      gev?.requestRender?.('radar-domes-cleared');
      viewer.scene?.requestRender?.();
      return;
    }

    for (const st of cachedRadarStations) {
      if (!st.enabled) continue;
      if (stationFilter && st.id.toLowerCase() !== stationFilter.toLowerCase()) continue;

      const center = Cesium.Cartesian3.fromDegrees(st.lon, st.lat, st.antenna_height_m || 0);
      const groundCenter = Cesium.Cartesian3.fromDegrees(st.lon, st.lat, 0);
      const maxRangeMeters = (st.max_range_km || 300.0) * 1000.0;
      const altEnvelopeMeters = 20000.0; // 20km upper radar detection ceiling

      // 1. 3D Translucent Hemispherical Envelope (Cesium Ellipsoid)
      entityTarget.add({
        id: `radar_dome_${st.id}`,
        name: `Radar Coverage Envelope: ${st.name}`,
        position: center,
        ellipsoid: {
          radii: new Cesium.Cartesian3(maxRangeMeters, maxRangeMeters, altEnvelopeMeters),
          maximumCone: Cesium.Math.PI_OVER_TWO, // Upper hemisphere
          material: Cesium.Color.fromCssColorString('rgba(0, 160, 255, 0.07)'),
          outline: true,
          outlineColor: Cesium.Color.fromCssColorString('rgba(0, 200, 255, 0.35)'),
          outlineWidth: 1,
        },
      });

      // 2. Tactical Concentric Range Rings (50km, 100km, Horizon, Max Range)
      const ringIntervalsKm = [50.0, 100.0, st.horizon_med_alt_3000m_km || 200.0, st.max_range_km];
      for (const rKm of ringIntervalsKm) {
        if (!rKm || rKm > st.max_range_km) continue;
        const rMeters = rKm * 1000.0;
        const isMax = Math.abs(rKm - st.max_range_km) < 1.0;
        const ringColor = isMax ? '#00b4d8' : 'rgba(0, 180, 255, 0.4)';

        entityTarget.add({
          id: `radar_ring_${st.id}_${Math.round(rKm)}`,
          position: groundCenter,
          ellipse: {
            semiMinorAxis: rMeters,
            semiMajorAxis: rMeters,
            material: Cesium.Color.TRANSPARENT,
            outline: true,
            outlineColor: Cesium.Color.fromCssColorString(ringColor),
            outlineWidth: isMax ? 2 : 1,
            height: 0,
          },
        });
      }

      // 3. Azimuth Spoke Radials (8 cardinal/intercardinal radials to max range)
      const azimuthAngles = [0, 45, 90, 135, 180, 225, 270, 315];
      for (const az of azimuthAngles) {
        const rad = toRadians(az);
        const dLat = (maxRangeMeters / 6371000.0) * Math.cos(rad);
        const dLon = (maxRangeMeters / (6371000.0 * Math.cos(toRadians(st.lat)))) * Math.sin(rad);
        const endPt = Cesium.Cartesian3.fromDegrees(st.lon + toDegrees(dLon), st.lat + toDegrees(dLat), 0);

        entityTarget.add({
          id: `radar_radial_${st.id}_${az}`,
          polyline: {
            positions: [groundCenter, endPt],
            width: 1,
            material: Cesium.Color.fromCssColorString('rgba(0, 180, 255, 0.2)'),
          },
        });
      }

      // 4. Center Radar Antenna Mast & Beacon
      entityTarget.add({
        id: `radar_mast_${st.id}`,
        polyline: {
          positions: [groundCenter, center],
          width: 3,
          material: Cesium.Color.fromCssColorString('#00d2ff'),
        },
      });

      entityTarget.add({
        id: `radar_beacon_${st.id}`,
        position: center,
        point: {
          pixelSize: 10,
          color: Cesium.Color.fromCssColorString('#00ffff'),
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 2,
        },
        label: {
          text: `📡 ${st.name} [${st.band || 'Radar'}]\nRange: ${st.max_range_km}km | FL330 Horizon: ${st.horizon_high_alt_10000m_km || 'N/A'}km`,
          font: 'bold 11px monospace',
          fillColor: Cesium.Color.fromCssColorString('#00f0ff'),
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 3,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          pixelOffset: new Cesium.Cartesian2(0, -14),
          distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 6000000),
        },
      });
    }

    gev?.requestRender?.('radar-domes-rendered');
    viewer.scene?.requestRender?.();
    console.log(`%c[GodsEyeSidecar]%c Rendered ${cachedRadarStations.length} 3D Radar Coverage Dome(s) in Cesium.`, 'color: #00d2ff; font-weight: bold;', 'color: inherit;');
  }

  async function syncRadarVisuals() {
    try {
      const res = await fetch('http://localhost:8000/api/radar/stations');
      if (res.ok) {
        const data = await res.json();
        if (data.stations) {
          renderRadarDomes(data.stations, true);
        }
      }
    } catch (e) {}
  }

  let escortDataSource = null;
  let cachedEscortDevices = [];

  async function renderEscortBubbles(devices) {
    if (devices && Array.isArray(devices)) {
      cachedEscortDevices = devices;
    }
    const viewer = await waitForViewer(12000);
    const { gev } = getCesiumGlobals();
    const Cesium = window.Cesium;
    if (!viewer || !Cesium) return;

    if (!escortDataSource) {
      if (Cesium.CustomDataSource) {
        escortDataSource = new Cesium.CustomDataSource('sidecar-escort-bubbles');
        viewer.dataSources.add(escortDataSource);
      }
    }

    const entityTarget = escortDataSource ? escortDataSource.entities : viewer.entities;
    if (escortDataSource) {
      escortDataSource.entities.removeAll();
    }

    for (const dev of cachedEscortDevices) {
      if (dev.lat === undefined || dev.lon === undefined) continue;
      const radiusKm = dev.bubble_radius_km || 25.0;
      const radiusMeters = radiusKm * 1000.0;
      const center = Cesium.Cartesian3.fromDegrees(dev.lon, dev.lat, dev.altitude_m || 0);
      const groundCenter = Cesium.Cartesian3.fromDegrees(dev.lon, dev.lat, 0);

      // Status color scheme: SAFE (emerald neon), ADVISORY (tactical amber), ALERT (crimson red)
      const status = (dev.status || 'SAFE').toUpperCase();
      let statusColorHex = '#00ff88';
      let fillRgba = 'rgba(0, 255, 136, 0.08)';
      let statusBadge = '🛡️ SAFE';

      if (status === 'ALERT') {
        statusColorHex = '#ff3366';
        fillRgba = 'rgba(255, 51, 102, 0.15)';
        statusBadge = '🚨 CRITICAL ALERT';
      } else if (status === 'ADVISORY') {
        statusColorHex = '#ffaa00';
        fillRgba = 'rgba(255, 170, 0, 0.12)';
        statusBadge = '⚠️ ADVISORY';
      }

      // Tactical icon
      const typeStr = (dev.device_type || '').toLowerCase();
      const nameStr = (dev.name || '').toLowerCase();
      let icon = '👤';
      if (typeStr.includes('vehicle') || nameStr.includes('car') || nameStr.includes('convoy') || nameStr.includes('suv') || nameStr.includes('truck')) {
        icon = '🚙';
      } else if (typeStr.includes('drone') || typeStr.includes('uav')) {
        icon = '🛸';
      } else if (typeStr.includes('vessel') || typeStr.includes('boat')) {
        icon = '🚤';
      } else if (typeStr.includes('vip')) {
        icon = '👑';
      }

      // 1. Outer Dynamic Guardian Bubble (Translucent glowing geosphere boundary)
      entityTarget.add({
        id: `escort_bubble_${dev.device_id}`,
        name: `Guardian Bubble: ${dev.name}`,
        position: groundCenter,
        ellipse: {
          semiMinorAxis: radiusMeters,
          semiMajorAxis: radiusMeters,
          material: Cesium.Color.fromCssColorString(fillRgba),
          outline: true,
          outlineColor: Cesium.Color.fromCssColorString(statusColorHex),
          outlineWidth: 2,
          height: 0,
        },
      });

      // 2. Tactical Inner Threat Buffer Ring (5km proximity zone)
      const innerBufferMeters = Math.min(5000.0, radiusMeters * 0.35);
      entityTarget.add({
        id: `escort_inner_${dev.device_id}`,
        position: groundCenter,
        ellipse: {
          semiMinorAxis: innerBufferMeters,
          semiMajorAxis: innerBufferMeters,
          material: Cesium.Color.TRANSPARENT,
          outline: true,
          outlineColor: Cesium.Color.fromCssColorString(statusColorHex).withAlpha(0.35),
          outlineWidth: 1,
          height: 0,
        },
      });

      // 3. Center Entity Point Marker
      entityTarget.add({
        id: `escort_point_${dev.device_id}`,
        position: center,
        point: {
          pixelSize: 12,
          color: Cesium.Color.fromCssColorString(statusColorHex),
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 2,
        },
      });

      // 4. Center Tactical HUD Badge Label
      const threatCount = dev.threat_count || (dev.active_threats ? dev.active_threats.length : 0);
      entityTarget.add({
        id: `escort_label_${dev.device_id}`,
        position: center,
        label: {
          text: `${icon} ${dev.name.toUpperCase()} [${radiusKm}km BUBBLE]\nStatus: ${statusBadge} | Threats: ${threatCount}\nPos: ${dev.lat.toFixed(4)}, ${dev.lon.toFixed(4)}`,
          font: 'bold 11px monospace',
          fillColor: Cesium.Color.fromCssColorString(statusColorHex),
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 3,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          pixelOffset: new Cesium.Cartesian2(0, -16),
          distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 6000000),
        },
      });
    }

    gev?.requestRender?.('escort-bubbles-rendered');
    viewer.scene?.requestRender?.();
    console.log(`%c[GodsEyeSidecar]%c Rendered ${cachedEscortDevices.length} Escort Guardian Bubble(s) in 3D Cesium.`, 'color: #00ff88; font-weight: bold;', 'color: inherit;');
  }

  async function syncEscortVisuals() {
    try {
      const res = await fetch('http://localhost:8000/api/escort/devices');
      if (res.ok) {
        const data = await res.json();
        if (data.devices) {
          renderEscortBubbles(data.devices);
        }
      }
    } catch (e) {}
  }

  // Periodic polling for dynamic moving bubbles
  setInterval(() => {
    syncEscortVisuals();
  }, 5000);

  // Periodic heartbeat reporting camera changes
  setInterval(() => {
    sendViewportUpdate();
  }, 4000);

  // Connect when DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      connect();
      syncAoiVisuals();
      syncRadarVisuals();
      syncEscortVisuals();
    });
  } else {
    connect();
    syncAoiVisuals();
    syncRadarVisuals();
    syncEscortVisuals();
  }
})();
