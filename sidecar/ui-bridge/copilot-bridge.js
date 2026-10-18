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
  const ESCORT_PREFIX = 'gev_escort_';

  function toRadians(degrees) {
    return (degrees * Math.PI) / 180.0;
  }

  function toDegrees(radians) {
    return (radians * 180.0) / Math.PI;
  }

  function resolveCesium(viewer) {
    if (window.Cesium && window.Cesium.Cartesian3) return window.Cesium;

    const Cartesian3 = viewer?.camera?.position?.constructor;
    const Color = viewer?.scene?.backgroundColor?.constructor;
    const Ellipsoid = viewer?.scene?.globe?.ellipsoid?.constructor;
    const HeadingPitchRange = viewer?.camera?.constructor?.DEFAULT_OFFSET?.constructor || function (h = 0, p = 0, r = 0) {
      this.heading = h;
      this.pitch = p;
      this.range = r;
    };
    const BoundingSphere = function (center, radius = 0) {
      this.center = center;
      this.radius = radius;
    };

    const shim = {
      Cartesian3,
      Color,
      Ellipsoid,
      HeadingPitchRange,
      BoundingSphere,
      HeightReference: {
        NONE: 0,
        CLAMP_TO_GROUND: 1,
        RELATIVE_TO_GROUND: 2,
      },
      ClassificationType: {
        TERRAIN: 0,
        CESIUM_3D_TILE: 1,
        BOTH: 2,
      },
      LabelStyle: {
        FILL: 0,
        OUTLINE: 1,
        FILL_AND_OUTLINE: 2,
      },
      VerticalOrigin: {
        CENTER: 0,
        BOTTOM: 1,
        BASELINE: 2,
        TOP: -1,
      },
      DistanceDisplayCondition: function (near, far) {
        this.near = near;
        this.far = far;
      },
      PolygonHierarchy: function (positions) {
        this.positions = positions;
        this.holes = [];
      },
      ColorMaterialProperty: function (color) {
        this.color = color;
      },
      Cartesian2: function (x, y) {
        this.x = x;
        this.y = y;
      },
      Math: {
        toRadians: (deg) => (deg * Math.PI) / 180.0,
        toDegrees: (rad) => (rad * 180.0) / Math.PI,
        PI_OVER_TWO: Math.PI / 2.0,
      },
    };

    try {
      window.Cesium = shim;
    } catch (e) {}

    return shim;
  }

  function getCesiumGlobals() {
    const gev = window.__godsEyeView;
    const viewer = gev?.viewer || window.viewer;
    const Cesium = resolveCesium(viewer);
    const Cartesian3 = Cesium?.Cartesian3;
    return { gev, viewer, Cesium, Cartesian3 };
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

  function computeCirclePositions(Cesium, centerLon, centerLat, radiusMeters, steps = 64) {
    const Cartesian3 = Cesium?.Cartesian3 || window.Cesium?.Cartesian3;
    const positions = [];
    const latRad = (centerLat * Math.PI) / 180.0;
    const metersPerLat = 111132.95;
    const metersPerLon = Math.max(1.0, 111132.95 * Math.cos(latRad));

    for (let i = 0; i <= steps; i++) {
      const theta = (i * 2.0 * Math.PI) / steps;
      const dEast = radiusMeters * Math.sin(theta);
      const dNorth = radiusMeters * Math.cos(theta);
      const lon = centerLon + dEast / metersPerLon;
      const lat = centerLat + dNorth / metersPerLat;
      if (Cartesian3?.fromDegrees) {
        positions.push(Cartesian3.fromDegrees(lon, lat));
      }
    }
    return positions;
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
          const alt = Number(params.altitude_m || params.range_m || 5000);
          const duration = Math.max(0.2, Number(params.duration_s || params.duration || 2.5));
          const headingDeg = Number(params.heading_deg || 0);
          const pitchDeg = Number(params.pitch_deg !== undefined ? params.pitch_deg : -89.9);
          const heading = toRadians(headingDeg);
          const pitch = toRadians(pitchDeg);
          const roll = 0.0;

          const targetCartesian = computeCartesian(viewer, lon, lat, 0);
          if (!targetCartesian) {
            throw new Error(`Failed to calculate 3D Cartesian coordinates for (${lat}, ${lon})`);
          }

          console.log(`[GodsEyeSidecar] Flying camera to target [lon=${lon}, lat=${lat}, alt=${alt}m, pitch=${pitchDeg}°] over ${duration}s`);

          let flightStarted = false;

          // Attempt 1: Native Cesium flyToBoundingSphere for exact focal targeting
          if (typeof viewer?.camera?.flyToBoundingSphere === 'function') {
            try {
              const boundingSphere = Cesium?.BoundingSphere
                ? new Cesium.BoundingSphere(targetCartesian, 0)
                : { center: targetCartesian, radius: 0 };
              const offset = Cesium?.HeadingPitchRange
                ? new Cesium.HeadingPitchRange(heading, pitch, alt)
                : { heading, pitch, range: alt };

              viewer.camera.flyToBoundingSphere(boundingSphere, {
                offset,
                duration,
                complete: () => {
                  console.log('[GodsEyeSidecar] Camera flight completed (flyToBoundingSphere)');
                  gev?.requestRender?.('copilot-flight-complete');
                  viewer.scene?.requestRender?.();
                },
                cancel: () => {
                  console.log('[GodsEyeSidecar] Camera flight cancelled');
                },
              });
              flightStarted = true;
            } catch (err) {
              console.warn('[GodsEyeSidecar] flyToBoundingSphere fallback to trigonometric flyTo:', err);
            }
          }

          // Attempt 2: Direct camera.flyTo with ground-offset pullback trigonometry
          if (!flightStarted) {
            let eyeLon = lon;
            let eyeLat = lat;
            let eyeAlt = alt;

            const absPitch = Math.abs(pitchDeg);
            // If oblique pitch (not straight down), pull back the camera eye so the look-at ray lands dead-center on (lat, lon)
            if (absPitch < 88.0 && absPitch > 1.0) {
              const pitchRad = toRadians(absPitch);
              const groundDist = alt / Math.tan(pitchRad);
              const dNorth = -groundDist * Math.cos(heading);
              const dEast = -groundDist * Math.sin(heading);
              const latRad = toRadians(lat);
              const metersPerLat = 111132.95;
              const metersPerLon = Math.max(1.0, 111132.95 * Math.cos(latRad));
              eyeLat = lat + dNorth / metersPerLat;
              eyeLon = lon + dEast / metersPerLon;
            }

            const destination = computeCartesian(viewer, eyeLon, eyeLat, eyeAlt);
            viewer.camera.flyTo({
              destination,
              orientation: { heading, pitch, roll },
              duration,
              complete: () => {
                console.log('[GodsEyeSidecar] Camera flight completed (trigonometric flyTo)');
                gev?.requestRender?.('copilot-flight-complete');
                viewer.scene?.requestRender?.();
              },
              cancel: () => {
                console.log('[GodsEyeSidecar] Camera flight cancelled');
              },
            });
          }

          // Kick render governor so frames animate smoothly
          gev?.requestRender?.('copilot-flight-start');
          viewer.scene?.requestRender?.();

          result = { lat, lon, alt, duration, pitch_deg: pitchDeg };
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
          const show = visible !== undefined ? Boolean(visible) : true;
          if (viewer?.entities) {
            viewer.entities.values
              .filter(e => typeof e.id === 'string' && e.id.startsWith(ESCORT_PREFIX))
              .forEach(e => { e.show = show; });
            gev?.requestRender?.('escort-visibility-toggle');
            viewer.scene?.requestRender?.();
            result = { visible: show };
          } else {
            result = { visible: false, message: 'Viewer entities not ready' };
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
            const rKm = Number(target.bubble_radius_km || 25);
            const altM = Math.max(15000, rKm * 2500);
            await handleCommand({
              action: 'fly_to_coordinate',
              params: {
                lat: target.lat,
                lon: target.lon,
                altitude_m: altM,
                pitch_deg: -89.9,
                heading_deg: 0,
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
      const ringPositions = computeCirclePositions(Cesium, aoi.lon, aoi.lat, radiusMeters, 64);

      // 1. Ground-clamped tactical perimeter polyline ring
      if (ringPositions.length > 2) {
        entityTarget.add({
          id: `aoi_ring_${aoi.id}`,
          name: `AOI: ${aoi.name}`,
          polyline: {
            positions: ringPositions,
            clampToGround: true,
            width: 3,
            material: new Cesium.ColorMaterialProperty(Cesium.Color.fromCssColorString('#00ffcc')),
          },
        });

        // Semi-transparent glowing tactical geofence fill
        entityTarget.add({
          id: `aoi_fill_${aoi.id}`,
          polygon: {
            hierarchy: new Cesium.PolygonHierarchy(ringPositions),
            material: new Cesium.ColorMaterialProperty(Cesium.Color.fromCssColorString('rgba(0, 255, 204, 0.08)')),
            classificationType: Cesium.ClassificationType ? Cesium.ClassificationType.BOTH : undefined,
          },
        });
      }

      // 2. Center Tactical Beacon Point & Label
      entityTarget.add({
        id: `aoi_beacon_${aoi.id}`,
        position: center,
        point: {
          pixelSize: 10,
          color: Cesium.Color.fromCssColorString('#00ffcc'),
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 2,
          heightReference: Cesium.HeightReference ? Cesium.HeightReference.CLAMP_TO_GROUND : undefined,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
        label: {
          text: `🛡️ ${aoi.name.toUpperCase()} AOI [${aoi.radius_km}km]`,
          font: 'bold 12px monospace',
          fillColor: Cesium.Color.fromCssColorString('#00ffcc'),
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 3,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          pixelOffset: new Cesium.Cartesian2(0, -14),
          heightReference: Cesium.HeightReference ? Cesium.HeightReference.CLAMP_TO_GROUND : undefined,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
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
      const host = window.location.hostname || 'localhost';
      const res = await fetch(`http://${host}:8000/api/aois`);
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
      const host = window.location.hostname || 'localhost';
      const res = await fetch(`http://${host}:8000/api/radar/stations`);
      if (res.ok) {
        const data = await res.json();
        if (data.stations) {
          renderRadarDomes(data.stations, true);
        }
      }
    } catch (e) {}
  }

  let cachedEscortDevices = [];

  async function renderEscortBubbles(devices) {
    if (devices && Array.isArray(devices)) {
      cachedEscortDevices = devices;
    }
    // 1. Immediately render on-screen HUD (zero Cesium dependency)
    renderEscortHud(cachedEscortDevices);

    const viewer = await waitForViewer(12000);
    const { gev, Cesium } = getCesiumGlobals();
    if (!viewer) return;

    // Clear existing escort entities directly on viewer.entities
    if (viewer.entities) {
      const toRemove = viewer.entities.values
        .filter(e => typeof e.id === 'string' && e.id.startsWith(ESCORT_PREFIX))
        .map(e => e.id);
      for (const id of toRemove) {
        viewer.entities.removeById(id);
      }
    }

    for (const dev of cachedEscortDevices) {
      if (dev.lat === undefined || dev.lon === undefined) continue;
      const radiusKm = dev.bubble_radius_km || 25.0;
      const radiusMeters = radiusKm * 1000.0;
      const center = Cesium.Cartesian3.fromDegrees(dev.lon, dev.lat, dev.altitude_m || 0);

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

      const outerPositions = computeCirclePositions(Cesium, dev.lon, dev.lat, radiusMeters, 72);

      // 1. Ground-clamped tactical perimeter polyline ring (Guaranteed to render on 3D tiles & terrain)
      if (outerPositions.length > 2) {
        viewer.entities.add({
          id: `${ESCORT_PREFIX}ring_${dev.device_id}`,
          name: `Guardian Boundary: ${dev.name}`,
          polyline: {
            positions: outerPositions,
            clampToGround: true,
            width: 4,
            material: Cesium.Color?.fromCssColorString ? Cesium.Color.fromCssColorString(statusColorHex) : statusColorHex,
          },
        });

        // 2. Translucent geofence fill
        viewer.entities.add({
          id: `${ESCORT_PREFIX}fill_${dev.device_id}`,
          name: `Guardian Fill: ${dev.name}`,
          polygon: {
            hierarchy: outerPositions,
            material: Cesium.Color?.fromCssColorString ? Cesium.Color.fromCssColorString(fillRgba) : fillRgba,
            classificationType: Cesium.ClassificationType?.BOTH ?? 2,
          },
        });
      }

      // 3. Tactical Inner Buffer Ring (5km proximity danger zone)
      const innerBufferMeters = Math.min(5000.0, radiusMeters * 0.35);
      const innerPositions = computeCirclePositions(Cesium, dev.lon, dev.lat, innerBufferMeters, 48);
      if (innerPositions.length > 2) {
        viewer.entities.add({
          id: `${ESCORT_PREFIX}inner_${dev.device_id}`,
          polyline: {
            positions: innerPositions,
            clampToGround: true,
            width: 2,
            material: Cesium.Color?.fromCssColorString ? Cesium.Color.fromCssColorString(statusColorHex).withAlpha(0.6) : statusColorHex,
          },
        });
      }

      // 4. Vertical 4,000m Tactical Light Pillar (visible from high orbit / any angle)
      viewer.entities.add({
        id: `${ESCORT_PREFIX}pillar_${dev.device_id}`,
        polyline: {
          positions: [
            Cesium.Cartesian3.fromDegrees(dev.lon, dev.lat, 0),
            Cesium.Cartesian3.fromDegrees(dev.lon, dev.lat, 4000),
          ],
          width: 3,
          material: Cesium.Color?.fromCssColorString ? Cesium.Color.fromCssColorString(statusColorHex).withAlpha(0.85) : statusColorHex,
        },
      });

      // 5. Center Tactical Point Marker
      viewer.entities.add({
        id: `${ESCORT_PREFIX}point_${dev.device_id}`,
        position: center,
        point: {
          pixelSize: 16,
          color: Cesium.Color?.fromCssColorString ? Cesium.Color.fromCssColorString(statusColorHex) : statusColorHex,
          outlineColor: Cesium.Color?.BLACK || '#000000',
          outlineWidth: 3,
          heightReference: Cesium.HeightReference?.CLAMP_TO_GROUND ?? 1,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
      });

      // 6. Center Tactical HUD Badge Label
      const threatCount = dev.threat_count || (dev.active_threats ? dev.active_threats.length : 0);
      viewer.entities.add({
        id: `${ESCORT_PREFIX}label_${dev.device_id}`,
        position: center,
        label: {
          text: `${icon} ${dev.name.toUpperCase()} [${radiusKm}km BUBBLE]\nStatus: ${statusBadge} | Threats: ${threatCount}\nPos: ${dev.lat.toFixed(4)}, ${dev.lon.toFixed(4)}`,
          font: 'bold 13px monospace',
          fillColor: Cesium.Color?.fromCssColorString ? Cesium.Color.fromCssColorString(statusColorHex) : statusColorHex,
          outlineColor: Cesium.Color?.BLACK || '#000000',
          outlineWidth: 4,
          style: Cesium.LabelStyle?.FILL_AND_OUTLINE ?? 2,
          verticalOrigin: Cesium.VerticalOrigin?.BOTTOM ?? 1,
          pixelOffset: new Cesium.Cartesian2(0, -22),
          heightReference: Cesium.HeightReference?.CLAMP_TO_GROUND ?? 1,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
          distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 10000000),
        },
      });
    }

    gev?.requestRender?.('escort-bubbles-rendered');
    viewer.scene?.requestRender?.();
    renderEscortHud(cachedEscortDevices);
    console.log(`%c[GodsEyeSidecar]%c Rendered ${cachedEscortDevices.length} Escort Guardian Bubble(s) directly on viewer.entities.`, 'color: #00ff88; font-weight: bold;', 'color: inherit;');
  }

  async function syncEscortVisuals() {
    try {
      const host = window.location.hostname || 'localhost';
      let res = await fetch('/api/escort/devices').catch(() => null);
      if (!res || !res.ok) {
        res = await fetch(`http://${host}:8000/api/escort/devices`).catch(() => null);
      }
      if (res && res.ok) {
        const data = await res.json();
        if (data.devices) {
          renderEscortBubbles(data.devices);
        }
      }
    } catch (e) {}
  }

  function renderEscortHud(devices) {
    let hud = document.getElementById('sidecar-escort-hud');
    if (!devices || devices.length === 0) {
      if (hud) hud.remove();
      return;
    }
    if (!hud) {
      hud = document.createElement('div');
      hud.id = 'sidecar-escort-hud';
      hud.style.cssText = `
        position: fixed;
        top: 76px;
        right: 20px;
        z-index: 99999;
        background: rgba(10, 18, 30, 0.94);
        backdrop-filter: blur(10px);
        border: 1px solid rgba(0, 255, 136, 0.6);
        border-radius: 8px;
        padding: 12px 14px;
        color: #fff;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace;
        box-shadow: 0 8px 32px rgba(0,0,0,0.85), 0 0 16px rgba(0,255,136,0.3);
        min-width: 290px;
        max-width: 360px;
        pointer-events: auto;
      `;
      document.body.appendChild(hud);
    }

    let html = `
      <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; border-bottom: 1px solid rgba(255,255,255,0.12); padding-bottom: 6px;">
        <div style="font-size: 12px; font-weight: bold; color: #00ff88; letter-spacing: 0.5px; display: flex; align-items: center; gap: 6px;">
          <span>🛡️</span> GUARDIAN BUBBLES (${devices.length})
        </div>
        <div style="font-size: 9px; background: rgba(0,255,136,0.15); color: #00ff88; padding: 2px 6px; border-radius: 3px; font-weight: bold;">
          LIVE
        </div>
      </div>
    `;

    for (const dev of devices) {
      const isAlert = dev.status === 'ALERT';
      const isAdv = dev.status === 'ADVISORY';
      const color = isAlert ? '#ff3366' : (isAdv ? '#ffaa00' : '#00ff88');
      const typeStr = (dev.device_type || '').toLowerCase();
      const nameStr = (dev.name || '').toLowerCase();
      const icon = (typeStr.includes('vehicle') || nameStr.includes('suv') || nameStr.includes('car') || nameStr.includes('convoy')) ? '🚙' : '👤';

      html += `
        <div style="margin-bottom: 8px; padding: 8px; background: rgba(255,255,255,0.05); border: 1px solid ${color}55; border-radius: 6px; display: flex; align-items: center; justify-content: space-between; gap: 8px;">
          <div style="flex: 1; min-width: 0;">
            <div style="font-size: 11px; font-weight: bold; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
              ${icon} ${dev.name}
            </div>
            <div style="font-size: 10px; color: ${color}; margin-top: 2px;">
              ● ${dev.status || 'SAFE'} • ${dev.bubble_radius_km || 25}km bubble
            </div>
          </div>
          <button class="sidecar-hud-fly-btn" data-lat="${dev.lat}" data-lon="${dev.lon}" data-r="${dev.bubble_radius_km || 25}" style="
            background: ${color};
            border: none;
            color: #000;
            font-weight: bold;
            font-size: 10px;
            padding: 5px 10px;
            border-radius: 4px;
            cursor: pointer;
            letter-spacing: 0.5px;
            white-space: nowrap;
          ">FLY TO</button>
        </div>
      `;
    }

    hud.innerHTML = html;

    const btns = hud.querySelectorAll('.sidecar-hud-fly-btn');
    btns.forEach(btn => {
      btn.onclick = () => {
        const lat = Number(btn.getAttribute('data-lat'));
        const lon = Number(btn.getAttribute('data-lon'));
        const rKm = Number(btn.getAttribute('data-r'));
        handleCommand({
          action: 'fly_to_coordinate',
          params: {
            lat,
            lon,
            altitude_m: Math.max(15000, rKm * 2500),
            pitch_deg: -89.9,
            heading_deg: 0,
            duration_s: 2.0
          }
        });
      };
    });
  }

  window.__godsEyeSidecarFlyTo = (lat, lon, alt = 45000, pitch_deg = -89.9) => {
    handleCommand({
      action: 'fly_to_coordinate',
      params: { lat, lon, altitude_m: alt, pitch_deg, duration_s: 2.0 }
    });
  };

  async function syncEscortVisuals() {
    try {
      const host = window.location.hostname || 'localhost';
      const res = await fetch(`http://${host}:8000/api/escort/devices`);
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
