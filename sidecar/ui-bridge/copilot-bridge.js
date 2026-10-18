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
      CallbackProperty: function (callback, isConstant = false) {
        this._callback = callback;
        this._isConstant = isConstant;
        this.getValue = function (time, result) {
          return typeof this._callback === 'function' ? this._callback(time, result) : this._callback;
        };
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
          const { entity_id, layer = 'flights' } = params;
          const cleanId = String(entity_id || '').toLowerCase().trim();
          let tracked = false;

          // 1. Try layer-specific native tracking on dataManager
          const targetLayer = gev?.dataManager?.layers?.get
            ? gev.dataManager.layers.get(layer)
            : gev?.dataManager?.layers?.[layer];
          const module = targetLayer?.module || targetLayer;

          if (typeof module?.trackById === 'function') {
            try {
              tracked = Boolean(module.trackById(cleanId, { origin: 'user' }));
              console.log(`[GodsEyeSidecar] layer '${layer}' trackById('${cleanId}'):`, tracked);
            } catch (err) {
              console.warn('[GodsEyeSidecar] Error calling module.trackById:', err);
            }
          }

          // 2. Also check if Cesium entities collection has an entity with this ID
          if (!tracked && viewer?.entities) {
            try {
              const ent = viewer.entities.getById(entity_id) || viewer.entities.getById(cleanId);
              if (ent) {
                viewer.selectedEntity = ent;
                viewer.trackedEntity = ent;
                tracked = true;
              }
            } catch (e) {}
          }

          // 3. Dispatch standard custom events for HUD and reactive panels
          window.dispatchEvent(new CustomEvent('sidecar:select-entity', { detail: { entity_id: cleanId, layer } }));
          window.dispatchEvent(new CustomEvent('gev:world-request-focus', { detail: { id: cleanId, layer } }));

          gev?.requestRender?.('copilot-select-entity');
          viewer.scene?.requestRender?.();
          result = { entity_id: cleanId, layer, tracked, selected: true };
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
          if (target) {
            await trackEscortVehicle(target.device_id);
            result = { tracking: target.device_id, name: target.name, lat: target.lat, lon: target.lon };
          } else {
            result = { error: `Escort device '${device_id}' not found in active cache` };
          }
          break;
        }

        case 'tag_traffic_vehicle': {
          const vehicleId = params.vehicle_id;
          const radiusKm = Number(params.bubble_radius_km || 15.0);
          const name = params.name || `Traffic ${vehicleId}`;
          result = await tagTrafficVehicle(vehicleId, radiusKm, name);
          break;
        }

        case 'untag_escort_device': {
          const deviceId = params.device_id;
          result = await untagEscortDevice(deviceId);
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
      ">${(alert.entity_id || alert.metadata?.contact_id) ? `FLY TO THREAT (${String(alert.entity_id || alert.metadata?.contact_id).toUpperCase()})` : 'FLY TO TARGET'}</button>` : ''}
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
      flyBtn.onclick = async () => {
        const contactId = alert.entity_id || alert.metadata?.contact_id;
        const layerId = alert.metadata?.layer || 'flights';
        const contactAlt = alert.metadata?.contact_alt ? Number(alert.metadata.contact_alt) : 2500;
        const targetAlt = Math.max(3500, contactAlt * 2.5);

        // 1. Ensure the layer (e.g. flights) is visible
        if (layerId) {
          try {
            await handleCommand({
              action: 'toggle_layer',
              params: { layer_id: layerId, enabled: true }
            });
          } catch (e) {}
        }

        // 2. Fly camera directly to target threat coordinate with dead-center nadir pitch
        await handleCommand({
          action: 'fly_to_coordinate',
          params: {
            lat: alert.lat,
            lon: alert.lon,
            altitude_m: targetAlt,
            pitch_deg: -89.9,
            heading_deg: 0,
            duration_s: 2.0
          }
        });

        // 3. Select and track the threat entity natively in Cesium
        if (contactId) {
          await handleCommand({
            action: 'select_entity',
            params: { entity_id: contactId, layer: layerId }
          });
        }

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
  const trackedTrafficVehicles = new Map();
  let activeFollowVehicleId = null;
  let lastFollowCartesian = null;
  let isCameraFlying = false;

  function normalizeVehicleId(rawId) {
    if (!rawId) return '';
    const trimmed = String(rawId).trim().replace(/\s+/g, '');
    const m = trimmed.match(/^(?:VEH-?)?(\d+)$/i);
    if (m) {
      return `VEH-${m[1].padStart(4, '0')}`;
    }
    return trimmed;
  }

  function getNumericVehicleId(rawId) {
    if (!rawId) return null;
    const num = String(rawId).replace(/[^0-9]/g, '');
    return num ? Number(num) : null;
  }

  function findEscortDevice(rawId) {
    if (!rawId) return null;
    const raw = String(rawId).trim();
    const norm = normalizeVehicleId(raw);
    const num = getNumericVehicleId(raw);
    return cachedEscortDevices.find(d => {
      if (!d) return false;
      if (d.device_id === raw || d.device_id === norm) return true;
      if (normalizeVehicleId(d.device_id) === norm) return true;
      if (num !== null && getNumericVehicleId(d.device_id) === num) return true;
      return false;
    }) || null;
  }

  function cartesianToLatLon(pos) {
    if (!pos) return null;
    const { viewer } = getCesiumGlobals();
    if (!viewer) return null;

    try {
      const ellipsoid = viewer.scene?.globe?.ellipsoid || viewer.scene?.mapProjection?.ellipsoid;
      if (ellipsoid?.cartesianToCartographic) {
        const carto = ellipsoid.cartesianToCartographic(pos);
        if (carto) {
          return {
            lat: toDegrees(carto.latitude),
            lon: toDegrees(carto.longitude),
            alt: carto.height || 0,
          };
        }
      }
    } catch (e) {}

    // Fallback: WGS84 closed-form Cartesian to Geodetic
    try {
      const x = pos.x, y = pos.y, z = pos.z;
      const a = 6378137.0;
      const f = 1.0 / 298.257223563;
      const b = a * (1.0 - f);
      const e2 = (a * a - b * b) / (a * a);
      const ep2 = (a * a - b * b) / (b * b);
      const p = Math.hypot(x, y);
      const theta = Math.atan2(z * a, p * b);
      const lon = Math.atan2(y, x);
      const lat = Math.atan2(z + ep2 * b * Math.pow(Math.sin(theta), 3), p - e2 * a * Math.pow(Math.cos(theta), 3));
      return {
        lat: toDegrees(lat),
        lon: toDegrees(lon),
        alt: 0,
      };
    } catch (e) {
      return null;
    }
  }

  function findTrafficDot(targetId) {
    if (!targetId) return null;
    const { gev } = getCesiumGlobals();
    const trafficLayer = gev?.dataManager?.layers?.get ? gev.dataManager.layers.get('traffic') : gev?.dataManager?.layers?.traffic;
    const module = trafficLayer?.module || trafficLayer;
    if (!module) return null;

    const numTarget = getNumericVehicleId(targetId);
    const normTarget = normalizeVehicleId(targetId).toUpperCase();
    const strTarget = String(targetId).trim().toUpperCase();
    let found = null;

    if (typeof module.visitMotionDots === 'function') {
      module.visitMotionDots((id, roadKey, point, road) => {
        if (found) return;
        const num = Number(id);
        const strId = String(id).trim().toUpperCase();
        const vehId = `VEH-${strId}`.toUpperCase();
        const vehPadded = `VEH-${String(id).padStart(4, '0')}`.toUpperCase();

        if (
          (numTarget !== null && num === numTarget) ||
          strId === strTarget ||
          strId === normTarget ||
          vehId === strTarget ||
          vehId === normTarget ||
          vehPadded === strTarget ||
          vehPadded === normTarget
        ) {
          found = { id, roadKey, point, road };
        }
      });
    }
    return found;
  }

  function getLiveVehicleCartesian(rawDeviceId) {
    if (!rawDeviceId) return null;
    const { viewer, Cesium } = getCesiumGlobals();
    const deviceId = normalizeVehicleId(rawDeviceId);
    const numId = getNumericVehicleId(deviceId);
    const tracked = trackedTrafficVehicles.get(deviceId) ||
                    (numId ? trackedTrafficVehicles.get(numId) : null);

    // 1. Direct fast-path: read cached dot reference (updated at 60 FPS by traffic layer)
    if (tracked?.dotRef && tracked.dotRef.point?.position && !tracked.dotRef.recycle) {
      const pos = tracked.dotRef.point.position;
      if (Cesium?.Cartesian3?.clone) {
        tracked.lastCartesian = Cesium.Cartesian3.clone(pos, tracked.lastCartesian || new Cesium.Cartesian3());
      } else {
        tracked.lastCartesian = pos;
      }
      return tracked.lastCartesian;
    }

    // 2. Search traffic layer for matching dot
    const dot = findTrafficDot(deviceId) || findTrafficDot(rawDeviceId);
    if (dot && dot.point?.position) {
      if (tracked) {
        tracked.dotRef = dot;
        if (Cesium?.Cartesian3?.clone) {
          tracked.lastCartesian = Cesium.Cartesian3.clone(dot.point.position, tracked.lastCartesian || new Cesium.Cartesian3());
        } else {
          tracked.lastCartesian = dot.point.position;
        }
      }
      return dot.point.position;
    }

    // 3. Fallback to last known moving position
    if (tracked?.lastCartesian) {
      return tracked.lastCartesian;
    }

    // 4. Fallback to device coordinates
    const dev = findEscortDevice(rawDeviceId);
    if (dev && dev.lat !== undefined && dev.lon !== undefined && Cesium?.Cartesian3) {
      return Cesium.Cartesian3.fromDegrees(Number(dev.lon), Number(dev.lat), Number(dev.altitude_m || 0));
    }
    return null;
  }

  function installCameraFollower(viewer, Cesium) {
    if (!viewer?.scene?.preRender) return;
    if (viewer.__godsEyeFollowerInstalled) return;
    viewer.__godsEyeFollowerInstalled = true;

    viewer.scene.preRender.addEventListener(() => {
      if (!activeFollowVehicleId || isCameraFlying) return;
      if (viewer.isDestroyed?.()) return;

      const currentPos = getLiveVehicleCartesian(activeFollowVehicleId);
      if (!currentPos) return;

      if (!lastFollowCartesian) {
        if (Cesium?.Cartesian3?.clone) {
          lastFollowCartesian = Cesium.Cartesian3.clone(currentPos, new Cesium.Cartesian3());
        } else {
          lastFollowCartesian = { x: currentPos.x, y: currentPos.y, z: currentPos.z };
        }
        return;
      }

      const dx = currentPos.x - lastFollowCartesian.x;
      const dy = currentPos.y - lastFollowCartesian.y;
      const dz = currentPos.z - lastFollowCartesian.z;
      const distSq = dx * dx + dy * dy + dz * dz;

      // Follow vehicle displacement smoothly.
      // Must have moved, but ignore abnormal jumps (> 5000m / frame, e.g. layer re-init)
      if (distSq > 0.0001 && distSq < 25000000) {
        viewer.camera.position.x += dx;
        viewer.camera.position.y += dy;
        viewer.camera.position.z += dz;
      }

      lastFollowCartesian.x = currentPos.x;
      lastFollowCartesian.y = currentPos.y;
      lastFollowCartesian.z = currentPos.z;
    });
  }

  function stopTrackingVehicle() {
    activeFollowVehicleId = null;
    lastFollowCartesian = null;
    isCameraFlying = false;
    const { viewer, Cesium } = getCesiumGlobals();
    if (viewer) {
      viewer.trackedEntity = undefined;
      if (Cesium?.Matrix4?.IDENTITY) {
        try { viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY); } catch (e) {}
      }
    }
    updateTrackingHudState();
    console.log('[GodsEyeSidecar] Camera tracking released');
  }

  function getActiveTrafficVehiclesSample(limit = 6) {
    const { gev } = getCesiumGlobals();
    const trafficLayer = gev?.dataManager?.layers?.get ? gev.dataManager.layers.get('traffic') : gev?.dataManager?.layers?.traffic;
    const module = trafficLayer?.module || trafficLayer;
    if (!module) return [];

    if (typeof module.getDetectableObjects === 'function') {
      try {
        const objs = module.getDetectableObjects({ maxCount: limit });
        if (Array.isArray(objs) && objs.length > 0) {
          return objs.map(o => ({
            id: o.id,
            num: getNumericVehicleId(o.id),
            position: o.position,
          }));
        }
      } catch (e) {}
    }

    const sample = [];
    if (typeof module.visitMotionDots === 'function') {
      try {
        module.visitMotionDots((id, roadKey, point, road) => {
          if (sample.length >= limit) return;
          sample.push({
            id: `VEH-${String(id).padStart(4, '0')}`,
            num: id,
            position: point?.position,
          });
        });
      } catch (e) {}
    }
    return sample;
  }

  async function renderEscortBubbles(devices) {
    if (devices && Array.isArray(devices)) {
      cachedEscortDevices = devices;
    }
    // Immediately render on-screen HUD
    renderEscortHud(cachedEscortDevices);

    const viewer = await waitForViewer(12000);
    const { gev, Cesium } = getCesiumGlobals();
    if (!viewer || !Cesium) return;

    // 1. Remove ONLY entities for devices that have been deleted/untagged
    // This preserves existing entities so viewer.trackedEntity camera lock is NOT broken!
    const activeDevIds = new Set(cachedEscortDevices.map(d => d.device_id));
    if (viewer.entities) {
      const toRemove = viewer.entities.values
        .filter(e => typeof e.id === 'string' && e.id.startsWith(ESCORT_PREFIX))
        .filter(e => {
          const rawId = e.id.slice(ESCORT_PREFIX.length);
          const parts = rawId.split('_');
          const devId = parts.slice(1).join('_');
          return devId && !activeDevIds.has(devId);
        })
        .map(e => e.id);

      for (const id of toRemove) {
        if (viewer.trackedEntity?.id === id) {
          viewer.trackedEntity = undefined;
        }
        viewer.entities.removeById(id);
      }
    }

    for (const dev of cachedEscortDevices) {
      if (dev.lat === undefined || dev.lon === undefined) continue;
      const radiusKm = dev.bubble_radius_km || 15.0;
      const radiusMeters = radiusKm * 1000.0;
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

      const typeStr = (dev.device_type || '').toLowerCase();
      const nameStr = (dev.name || '').toLowerCase();
      let icon = '👤';
      if (typeStr.includes('vehicle') || nameStr.includes('car') || nameStr.includes('convoy') || nameStr.includes('suv') || nameStr.includes('truck') || nameStr.includes('veh')) {
        icon = '🚙';
      } else if (typeStr.includes('drone') || typeStr.includes('uav')) {
        icon = '🛸';
      } else if (typeStr.includes('vessel') || typeStr.includes('boat')) {
        icon = '🚤';
      } else if (typeStr.includes('vip')) {
        icon = '👑';
      }

      // Live Position Callback: Evaluated every frame for real-time kinematic follow
      const livePositionCallback = new Cesium.CallbackProperty((time, result) => {
        const livePos = getLiveVehicleCartesian(dev.device_id);
        if (livePos) {
          if (result && Cesium.Cartesian3?.clone) {
            return Cesium.Cartesian3.clone(livePos, result);
          }
          return livePos;
        }
        return Cesium.Cartesian3.fromDegrees(dev.lon, dev.lat, dev.altitude_m || 0);
      }, false);

      const center = Cesium.Cartesian3.fromDegrees(dev.lon, dev.lat, dev.altitude_m || 0);
      const outerPositions = computeCirclePositions(Cesium, dev.lon, dev.lat, radiusMeters, 72);
      const innerBufferMeters = Math.min(5000.0, radiusMeters * 0.35);
      const innerPositions = computeCirclePositions(Cesium, dev.lon, dev.lat, innerBufferMeters, 48);

      // 1. Boundary Ring Entity
      let ringEnt = viewer.entities.getById(`${ESCORT_PREFIX}ring_${dev.device_id}`);
      if (!ringEnt && outerPositions.length > 2) {
        ringEnt = viewer.entities.add({
          id: `${ESCORT_PREFIX}ring_${dev.device_id}`,
          name: `Guardian Boundary: ${dev.name}`,
          polyline: {
            positions: outerPositions,
            clampToGround: true,
            width: 4,
            material: Cesium.Color?.fromCssColorString ? Cesium.Color.fromCssColorString(statusColorHex) : statusColorHex,
          },
        });
      } else if (ringEnt) {
        if (ringEnt.polyline) {
          ringEnt.polyline.material = Cesium.Color?.fromCssColorString ? Cesium.Color.fromCssColorString(statusColorHex) : statusColorHex;
        }
      }

      // 2. Geofence Fill Entity
      let fillEnt = viewer.entities.getById(`${ESCORT_PREFIX}fill_${dev.device_id}`);
      if (!fillEnt && outerPositions.length > 2) {
        fillEnt = viewer.entities.add({
          id: `${ESCORT_PREFIX}fill_${dev.device_id}`,
          name: `Guardian Fill: ${dev.name}`,
          polygon: {
            hierarchy: outerPositions,
            material: Cesium.Color?.fromCssColorString ? Cesium.Color.fromCssColorString(fillRgba) : fillRgba,
            classificationType: Cesium.ClassificationType?.BOTH ?? 2,
          },
        });
      }

      // 3. Inner Buffer Ring Entity
      let innerEnt = viewer.entities.getById(`${ESCORT_PREFIX}inner_${dev.device_id}`);
      if (!innerEnt && innerPositions.length > 2) {
        innerEnt = viewer.entities.add({
          id: `${ESCORT_PREFIX}inner_${dev.device_id}`,
          polyline: {
            positions: innerPositions,
            clampToGround: true,
            width: 2,
            material: Cesium.Color?.fromCssColorString ? Cesium.Color.fromCssColorString(statusColorHex).withAlpha(0.6) : statusColorHex,
          },
        });
      }

      // 4. Vertical 4,000m Tactical Light Pillar Entity (Live Position)
      const pillarPositionsCallback = new Cesium.CallbackProperty((time, result) => {
        const livePos = getLiveVehicleCartesian(dev.device_id);
        const base = livePos || Cesium.Cartesian3.fromDegrees(dev.lon, dev.lat, 0);
        const ellipsoid = viewer.scene.globe.ellipsoid;
        const normal = ellipsoid ? ellipsoid.geodeticSurfaceNormal(base) : { x: 0, y: 0, z: 1 };
        const top = Cesium.Cartesian3.add(
          base,
          Cesium.Cartesian3.multiplyByScalar(normal, 4000.0, new Cesium.Cartesian3()),
          new Cesium.Cartesian3()
        );
        return [base, top];
      }, false);

      let pillarEnt = viewer.entities.getById(`${ESCORT_PREFIX}pillar_${dev.device_id}`);
      if (!pillarEnt) {
        pillarEnt = viewer.entities.add({
          id: `${ESCORT_PREFIX}pillar_${dev.device_id}`,
          polyline: {
            positions: pillarPositionsCallback,
            width: 3,
            material: Cesium.Color?.fromCssColorString ? Cesium.Color.fromCssColorString(statusColorHex).withAlpha(0.85) : statusColorHex,
          },
        });
      }

      // 5. Center Tactical Point Marker (Attached to real-time kinematic position for camera tracking)
      let pointEnt = viewer.entities.getById(`${ESCORT_PREFIX}point_${dev.device_id}`);
      if (!pointEnt) {
        pointEnt = viewer.entities.add({
          id: `${ESCORT_PREFIX}point_${dev.device_id}`,
          name: `Guardian Point: ${dev.name}`,
          position: livePositionCallback,
          trackingReferenceFrame: Cesium.TrackingReferenceFrame?.ENU,
          viewFrom: new Cesium.Cartesian3(0, -600, 1000),
          point: {
            pixelSize: 16,
            color: Cesium.Color?.fromCssColorString ? Cesium.Color.fromCssColorString(statusColorHex) : statusColorHex,
            outlineColor: Cesium.Color?.BLACK || '#000000',
            outlineWidth: 3,
            heightReference: Cesium.HeightReference?.CLAMP_TO_GROUND ?? 1,
            disableDepthTestDistance: Number.POSITIVE_INFINITY,
          },
        });
      }

      // 6. Center Tactical HUD Badge Label (Live Position)
      const threatCount = dev.threat_count || (dev.active_threats ? dev.active_threats.length : 0);
      let labelEnt = viewer.entities.getById(`${ESCORT_PREFIX}label_${dev.device_id}`);
      const labelText = `${icon} ${dev.name.toUpperCase()} [${radiusKm}km BUBBLE]\nStatus: ${statusBadge} | Threats: ${threatCount}\nPos: ${dev.lat.toFixed(4)}, ${dev.lon.toFixed(4)}`;
      if (!labelEnt) {
        labelEnt = viewer.entities.add({
          id: `${ESCORT_PREFIX}label_${dev.device_id}`,
          position: livePositionCallback,
          label: {
            text: labelText,
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
      } else if (labelEnt.label) {
        labelEnt.label.text = labelText;
        labelEnt.label.fillColor = Cesium.Color?.fromCssColorString ? Cesium.Color.fromCssColorString(statusColorHex) : statusColorHex;
      }
    }

    gev?.requestRender?.('escort-bubbles-rendered');
    viewer.scene?.requestRender?.();
    renderEscortHud(cachedEscortDevices);
    console.log(`%c[GodsEyeSidecar]%c Synchronized ${cachedEscortDevices.length} Escort Guardian Bubble(s).`, 'color: #00ff88; font-weight: bold;', 'color: inherit;');
  }

  async function trackEscortVehicle(rawDeviceId) {
    if (!rawDeviceId) return;
    const deviceId = normalizeVehicleId(rawDeviceId);
    const { viewer, Cesium, gev } = getCesiumGlobals();
    if (!viewer) return;

    // Ensure camera follower listener is attached to scene.preRender
    installCameraFollower(viewer, Cesium);

    // 1. Ensure traffic layer is active so vehicles render and move
    if (gev?.dataManager?.enableLayer) {
      try { gev.dataManager.enableLayer('traffic'); } catch (e) {}
    }

    // 2. Clear any Cesium trackedEntity or transform lock so our 60 FPS follower has clean camera control
    viewer.trackedEntity = undefined;
    if (Cesium?.Matrix4?.IDENTITY) {
      try { viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY); } catch (e) {}
    }

    // 3. Resolve target coordinates
    let targetCoords = null;
    const dot = findTrafficDot(deviceId) || findTrafficDot(rawDeviceId);
    if (dot?.point?.position) {
      targetCoords = cartesianToLatLon(dot.point.position);
    }
    if (!targetCoords) {
      const dev = findEscortDevice(rawDeviceId);
      if (dev && dev.lat !== undefined && dev.lon !== undefined) {
        targetCoords = { lat: Number(dev.lat), lon: Number(dev.lon), alt: Number(dev.altitude_m || 0) };
      }
    }
    if (!targetCoords) {
      const tracked = trackedTrafficVehicles.get(deviceId) || trackedTrafficVehicles.get(rawDeviceId);
      if (tracked && tracked.lastLat && tracked.lastLon) {
        targetCoords = { lat: tracked.lastLat, lon: tracked.lastLon, alt: tracked.lastAlt || 0 };
      }
    }
    if (!targetCoords) {
      const sample = getActiveTrafficVehiclesSample(20);
      const match = sample.find(s => s.id === deviceId || s.id === rawDeviceId || s.num === getNumericVehicleId(rawDeviceId));
      if (match?.position) {
        targetCoords = cartesianToLatLon(match.position);
      }
    }

    if (!targetCoords) {
      console.warn(`[GodsEyeSidecar] Cannot locate coordinates for vehicle: ${rawDeviceId}`);
      return;
    }

    // 4. Set tracking active and mark camera flying
    activeFollowVehicleId = deviceId;
    isCameraFlying = true;
    lastFollowCartesian = null;
    updateTrackingHudState();

    console.log(`[GodsEyeSidecar] Flying camera to tracked vehicle ${deviceId} at (${targetCoords.lat.toFixed(4)}, ${targetCoords.lon.toFixed(4)})...`);

    // 5. Execute smooth camera flight: nadir top-down view (-89.9° pitch) at 2200m altitude
    await handleCommand({
      action: 'fly_to_coordinate',
      params: {
        lat: targetCoords.lat,
        lon: targetCoords.lon,
        altitude_m: 2200,
        pitch_deg: -89.9,
        heading_deg: 0,
        duration_s: 1.5,
      },
    });

    // 6. Flight complete: initialize follower position for 60 FPS lockstep follow
    isCameraFlying = false;
    const livePos = getLiveVehicleCartesian(deviceId);
    if (livePos) {
      if (Cesium?.Cartesian3?.clone) {
        lastFollowCartesian = Cesium.Cartesian3.clone(livePos, new Cesium.Cartesian3());
      } else {
        lastFollowCartesian = { x: livePos.x, y: livePos.y, z: livePos.z };
      }
    }
    updateTrackingHudState();
    console.log(`%c[GodsEyeSidecar]%c Camera locked and tracking vehicle ${deviceId} in real-time at 60 FPS`, 'color: #00ff88; font-weight: bold;', 'color: inherit;');
  }

  async function tagTrafficVehicle(rawVehicleId, bubbleRadiusKm = 15.0, customName = null) {
    const vehicleId = normalizeVehicleId(rawVehicleId);
    const numId = getNumericVehicleId(vehicleId);
    const name = customName || `Guardian ${vehicleId}`;
    const radius = Number(bubbleRadiusKm) || 15.0;

    // 1. Ensure Street Traffic layer is enabled in God's Eye View
    const { gev, Cesium } = getCesiumGlobals();
    if (gev?.dataManager?.enableLayer) {
      try {
        gev.dataManager.enableLayer('traffic');
      } catch (e) {}
    }

    // 2. Locate vehicle dot in current traffic layer
    let initialCoords = null;
    const dot = findTrafficDot(vehicleId) || findTrafficDot(rawVehicleId);
    if (dot && dot.point?.position) {
      initialCoords = cartesianToLatLon(dot.point.position);
    }

    // Fallback if dot not in current viewport
    if (!initialCoords) {
      const vp = getCameraViewport();
      if (vp && vp.lat && vp.lon) {
        initialCoords = { lat: vp.lat, lon: vp.lon, alt: 0 };
      } else {
        initialCoords = { lat: 12.9716, lon: 77.5946, alt: 0 };
      }
    }

    // 3. Register in local tracking Map for the kinematic follower loop
    const record = {
      vehicleId,
      numId,
      name,
      bubbleRadiusKm: radius,
      lastLat: initialCoords.lat,
      lastLon: initialCoords.lon,
      lastAlt: initialCoords.alt || 0,
      lastPingTime: 0,
      lastCartesian: dot?.point?.position ? (Cesium?.Cartesian3?.clone(dot.point.position, new Cesium.Cartesian3()) || dot.point.position) : null,
      dotRef: dot || null,
    };
    trackedTrafficVehicles.set(vehicleId, record);
    if (numId) trackedTrafficVehicles.set(numId, record);

    // 4. Register with Sentinel Escort Engine backend
    try {
      const host = window.location.hostname || 'localhost';
      let res = await fetch('/api/escort/tag', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          device_id: vehicleId,
          name,
          device_type: 'vehicle',
          lat: initialCoords.lat,
          lon: initialCoords.lon,
          alt_m: initialCoords.alt || 0,
          bubble_radius_km: radius,
          source: 'tomtom_traffic',
        }),
      }).catch(() => null);

      if (!res || !res.ok) {
        await fetch(`http://${host}:8000/api/escort/tag`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            device_id: vehicleId,
            name,
            device_type: 'vehicle',
            lat: initialCoords.lat,
            lon: initialCoords.lon,
            alt_m: initialCoords.alt || 0,
            bubble_radius_km: radius,
            source: 'tomtom_traffic',
          }),
        }).catch(() => {});
      }
    } catch (err) {
      console.warn('[GodsEyeSidecar] Error registering escort tag with backend:', err);
    }

    // 5. Instantly refresh visual escort bubbles
    await syncEscortVisuals();

    // 6. Lock camera directly onto moving vehicle for real-time tracking (just like flight tracking!)
    await trackEscortVehicle(vehicleId);

    console.log(`%c[GodsEyeSidecar]%c Tagged traffic vehicle ${vehicleId} with ${radius}km Guardian Bubble at (${initialCoords.lat.toFixed(4)}, ${initialCoords.lon.toFixed(4)}).`, 'color: #00ff88; font-weight: bold;', 'color: inherit;');
    return { ok: true, vehicle_id: vehicleId, name, radius_km: radius, initial_coords: initialCoords, tracking: true };
  }

  async function untagEscortDevice(rawDeviceId) {
    const deviceId = normalizeVehicleId(rawDeviceId);
    const numId = getNumericVehicleId(rawDeviceId);
    trackedTrafficVehicles.delete(deviceId);
    if (numId) trackedTrafficVehicles.delete(numId);
    for (const [k, v] of trackedTrafficVehicles.entries()) {
      if (v.vehicleId === deviceId || (numId && v.numId === numId)) {
        trackedTrafficVehicles.delete(k);
      }
    }

    // Release camera tracking if this vehicle was active
    if (activeFollowVehicleId === deviceId || activeFollowVehicleId === rawDeviceId || (numId && getNumericVehicleId(activeFollowVehicleId) === numId)) {
      stopTrackingVehicle();
    }

    // Call backend DELETE
    try {
      const host = window.location.hostname || 'localhost';
      let res = await fetch(`/api/escort/device/${encodeURIComponent(deviceId)}`, { method: 'DELETE' }).catch(() => null);
      if (!res || !res.ok) {
        await fetch(`http://${host}:8000/api/escort/device/${encodeURIComponent(deviceId)}`, { method: 'DELETE' }).catch(() => null);
      }
      if (rawDeviceId !== deviceId) {
        await fetch(`/api/escort/device/${encodeURIComponent(rawDeviceId)}`, { method: 'DELETE' }).catch(() => null);
        await fetch(`http://${host}:8000/api/escort/device/${encodeURIComponent(rawDeviceId)}`, { method: 'DELETE' }).catch(() => null);
      }
    } catch (e) {}

    // Clean up Cesium entities
    const { viewer, gev } = getCesiumGlobals();
    if (viewer?.entities) {
      const toRemove = viewer.entities.values
        .filter(e => typeof e.id === 'string' && (e.id.includes(deviceId) || e.id.includes(rawDeviceId)))
        .map(e => e.id);

      for (const id of toRemove) {
        viewer.entities.removeById(id);
      }
      gev?.requestRender?.('escort-bubble-removed');
      viewer.scene?.requestRender?.();
    }

    await syncEscortVisuals();
    updateTrackingHudState();
    return { ok: true, device_id: deviceId };
  }

  function updateEscortEntityPosition(deviceId, lat, lon, alt = 0, radiusKm = 15.0) {
    const { viewer, Cesium, gev } = getCesiumGlobals();
    if (!viewer?.entities || !Cesium) return;

    const radiusMeters = radiusKm * 1000.0;
    const ringEnt = viewer.entities.getById(`${ESCORT_PREFIX}ring_${deviceId}`);
    const fillEnt = viewer.entities.getById(`${ESCORT_PREFIX}fill_${deviceId}`);
    const innerEnt = viewer.entities.getById(`${ESCORT_PREFIX}inner_${deviceId}`);
    const labelEnt = viewer.entities.getById(`${ESCORT_PREFIX}label_${deviceId}`);

    if (ringEnt && fillEnt) {
      const outerPositions = computeCirclePositions(Cesium, lon, lat, radiusMeters, 72);
      if (ringEnt.polyline) ringEnt.polyline.positions = outerPositions;
      if (fillEnt.polygon) fillEnt.polygon.hierarchy = outerPositions;

      const innerBufferMeters = Math.min(5000.0, radiusMeters * 0.35);
      const innerPositions = computeCirclePositions(Cesium, lon, lat, innerBufferMeters, 48);
      if (innerEnt?.polyline) innerEnt.polyline.positions = innerPositions;

      if (labelEnt) {
        const currentText = labelEnt.label?.text?.getValue?.() || labelEnt.label?.text || '';
        const lines = String(currentText).split('\n');
        if (lines.length >= 3) {
          lines[2] = `Pos: ${lat.toFixed(4)}, ${lon.toFixed(4)}`;
          labelEnt.label.text = lines.join('\n');
        }
      }
      gev?.requestRender?.('escort-bubble-move');
      viewer.scene?.requestRender?.();

      const cached = cachedEscortDevices.find(d => d.device_id === deviceId);
      if (cached) {
        cached.lat = lat;
        cached.lon = lon;
      }
    }
  }

  // Smooth real-time update loop (250ms) for sliding bubble & throttled GPS telemetry pings
  setInterval(() => {
    if (trackedTrafficVehicles.size === 0) return;
    const { viewer } = getCesiumGlobals();
    if (!viewer) return;

    const now = Date.now();
    const processed = new Set();

    for (const tracked of trackedTrafficVehicles.values()) {
      if (processed.has(tracked.vehicleId)) continue;
      processed.add(tracked.vehicleId);

      const pos = getLiveVehicleCartesian(tracked.vehicleId);
      if (!pos) continue;

      const coords = cartesianToLatLon(pos);
      if (!coords || !coords.lat || !coords.lon) continue;

      const dLat = Math.abs(coords.lat - tracked.lastLat);
      const dLon = Math.abs(coords.lon - tracked.lastLon);
      const moved = dLat > 0.000005 || dLon > 0.000005;

      tracked.lastLat = coords.lat;
      tracked.lastLon = coords.lon;
      tracked.lastAlt = coords.alt || 0;

      // Update 15km perimeter circle positions smoothly
      updateEscortEntityPosition(tracked.vehicleId, coords.lat, coords.lon, coords.alt || 0, tracked.bubbleRadiusKm);

      // Throttled GPS ping for backend threat evaluation (every 2.5s)
      if (now - tracked.lastPingTime > 2500 || (moved && now - tracked.lastPingTime > 1500)) {
        tracked.lastPingTime = now;
        try {
          const host = window.location.hostname || 'localhost';
          const payload = {
            device_id: tracked.vehicleId,
            lat: coords.lat,
            lon: coords.lon,
            alt_m: coords.alt || 0,
            speed_kts: 30.0,
            source: 'tomtom_traffic',
          };
          fetch('/api/escort/ping', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          }).catch(() => {
            fetch(`http://${host}:8000/api/escort/ping`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload),
            }).catch(() => {});
          });
        } catch (e) {}
      }
    }
  }, 250);

  let escortHudExpanded = true;

  function updateTrackingHudState() {
    const flyBtns = document.querySelectorAll('.sidecar-hud-fly-btn');
    flyBtns.forEach(btn => {
      const btnId = btn.getAttribute('data-id');
      const isThisTracked = activeFollowVehicleId && (
        btnId === activeFollowVehicleId ||
        normalizeVehicleId(btnId) === activeFollowVehicleId ||
        (getNumericVehicleId(btnId) !== null && getNumericVehicleId(btnId) === getNumericVehicleId(activeFollowVehicleId))
      );
      if (isThisTracked) {
        btn.textContent = '● TRACKING';
        btn.style.background = '#00ffcc';
        btn.style.boxShadow = '0 0 12px #00ffcc';
        btn.style.color = '#000';
        btn.title = 'Click to untrack and free camera';
      } else {
        btn.textContent = 'FLY TO';
        btn.style.boxShadow = 'none';
        const color = btn.getAttribute('data-color') || '#00ff88';
        btn.style.background = color;
        btn.style.color = '#000';
        btn.title = 'Fly to and follow vehicle in real-time';
      }
    });
  }

  function renderEscortHud(devices) {
    let hud = document.getElementById('sidecar-escort-hud');
    const deviceList = Array.isArray(devices) ? devices : cachedEscortDevices;

    if (!hud) {
      hud = document.createElement('div');
      hud.id = 'sidecar-escort-hud';
      hud.style.cssText = `
        position: fixed;
        top: 76px;
        right: 20px;
        z-index: 99999;
        background: rgba(10, 18, 30, 0.95);
        backdrop-filter: blur(12px);
        border: 1px solid rgba(0, 255, 136, 0.6);
        border-radius: 8px;
        padding: 12px 14px;
        color: #fff;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace;
        box-shadow: 0 8px 32px rgba(0,0,0,0.85), 0 0 16px rgba(0,255,136,0.25);
        min-width: 310px;
        max-width: 370px;
        pointer-events: auto;
      `;
      document.body.appendChild(hud);
    }

    if (!escortHudExpanded) {
      hud.innerHTML = `
        <div style="display: flex; align-items: center; justify-content: space-between; cursor: pointer;" id="sidecar-hud-expand-toggle">
          <div style="font-size: 11px; font-weight: bold; color: #00ff88; display: flex; align-items: center; gap: 6px;">
            <span>🛡️</span> GUARDIAN BUBBLES (${deviceList.length})
          </div>
          <button style="background: transparent; border: none; color: #00ff88; font-size: 11px; font-weight: bold; cursor: pointer;">▲ EXPAND</button>
        </div>
      `;
      const tog = document.getElementById('sidecar-hud-expand-toggle');
      if (tog) tog.onclick = () => { escortHudExpanded = true; renderEscortHud(deviceList); };
      return;
    }

    let html = `
      <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; border-bottom: 1px solid rgba(255,255,255,0.12); padding-bottom: 6px;">
        <div style="font-size: 12px; font-weight: bold; color: #00ff88; letter-spacing: 0.5px; display: flex; align-items: center; gap: 6px;">
          <span>🛡️</span> GUARDIAN BUBBLES (${deviceList.length})
        </div>
        <div style="display: flex; align-items: center; gap: 6px;">
          <div style="font-size: 9px; background: rgba(0,255,136,0.15); color: #00ff88; padding: 2px 6px; border-radius: 3px; font-weight: bold;">
            LIVE
          </div>
          <button id="sidecar-hud-collapse-btn" style="background: transparent; border: none; color: #aaa; font-size: 11px; cursor: pointer; padding: 0 2px;" title="Minimize">▼</button>
        </div>
      </div>
    `;

    if (deviceList.length === 0) {
      html += `
        <div style="font-size: 11px; color: #8899aa; padding: 8px; background: rgba(255,255,255,0.03); border-radius: 6px; margin-bottom: 8px; text-align: center;">
          No vehicles or targets tagged yet.<br>Tag any live vehicle below to track with a Guardian Bubble.
        </div>
      `;
    }

    for (const dev of deviceList) {
      const isAlert = dev.status === 'ALERT';
      const isAdv = dev.status === 'ADVISORY';
      const color = isAlert ? '#ff3366' : (isAdv ? '#ffaa00' : '#00ff88');
      const typeStr = (dev.device_type || '').toLowerCase();
      const nameStr = (dev.name || '').toLowerCase();
      const icon = (typeStr.includes('vehicle') || nameStr.includes('suv') || nameStr.includes('car') || nameStr.includes('convoy') || nameStr.includes('veh')) ? '🚙' : '👤';

      html += `
        <div style="margin-bottom: 8px; padding: 8px; background: rgba(255,255,255,0.05); border: 1px solid ${color}55; border-radius: 6px; display: flex; align-items: center; justify-content: space-between; gap: 6px;">
          <div style="flex: 1; min-width: 0;">
            <div style="font-size: 11px; font-weight: bold; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
              ${icon} ${dev.name}
            </div>
            <div style="font-size: 10px; color: ${color}; margin-top: 2px;">
              ● ${dev.status || 'SAFE'} • ${dev.bubble_radius_km || 15}km bubble
            </div>
          </div>
          <button class="sidecar-hud-fly-btn" data-id="${dev.device_id}" data-color="${color}" style="
            background: ${color};
            border: none;
            color: #000;
            font-weight: bold;
            font-size: 10px;
            padding: 5px 8px;
            border-radius: 4px;
            cursor: pointer;
            letter-spacing: 0.5px;
            white-space: nowrap;
          ">FLY TO</button>
          <button class="sidecar-hud-untag-btn" data-id="${dev.device_id}" style="
            background: rgba(255, 51, 102, 0.2);
            border: 1px solid #ff3366;
            color: #ff3366;
            font-weight: bold;
            font-size: 10px;
            padding: 5px 7px;
            border-radius: 4px;
            cursor: pointer;
          " title="Untag and remove bubble">✕</button>
        </div>
      `;
    }

    // Interactive Tagging Form
    html += `
      <div style="margin-top: 10px; padding-top: 8px; border-top: 1px solid rgba(255,255,255,0.12);">
        <div style="font-size: 10px; font-weight: bold; color: #00ffcc; margin-bottom: 6px; letter-spacing: 0.5px; display: flex; justify-content: space-between;">
          <span>TAG LIVE TRAFFIC VEHICLE</span>
          <span style="opacity: 0.7;">TomTom / OSM</span>
        </div>
        <div style="display: flex; gap: 6px; margin-bottom: 6px;">
          <input id="sidecar-tag-input" type="text" placeholder="e.g. VEH-15108" style="
            flex: 1;
            min-width: 0;
            background: rgba(0,0,0,0.6);
            border: 1px solid rgba(0, 255, 204, 0.4);
            color: #fff;
            border-radius: 4px;
            padding: 5px 8px;
            font-size: 11px;
            font-family: monospace;
          " />
          <input id="sidecar-radius-input" type="number" value="15" min="1" max="100" style="
            width: 46px;
            background: rgba(0,0,0,0.6);
            border: 1px solid rgba(0, 255, 204, 0.4);
            color: #00ffcc;
            border-radius: 4px;
            padding: 5px 4px;
            font-size: 11px;
            font-family: monospace;
            text-align: center;
          " title="Bubble Radius in km" />
          <button id="sidecar-tag-btn" style="
            background: #00ff88;
            color: #000;
            border: none;
            font-weight: bold;
            font-size: 10px;
            border-radius: 4px;
            padding: 5px 10px;
            cursor: pointer;
            letter-spacing: 0.5px;
            white-space: nowrap;
          ">+ TAG</button>
        </div>
        <div id="sidecar-tag-chips-container" style="display: flex; flex-wrap: wrap; align-items: center; gap: 4px; font-size: 9px; margin-top: 4px;">
          <span style="color: #8899aa;">Nearby:</span>
          <div id="sidecar-tag-chips" style="display: inline-flex; flex-wrap: wrap; gap: 4px;"></div>
        </div>
      </div>
    `;

    hud.innerHTML = html;

    // Attach listeners
    const collapseBtn = document.getElementById('sidecar-hud-collapse-btn');
    if (collapseBtn) {
      collapseBtn.onclick = () => { escortHudExpanded = false; renderEscortHud(deviceList); };
    }

    const flyBtns = hud.querySelectorAll('.sidecar-hud-fly-btn');
    flyBtns.forEach(btn => {
      btn.onclick = async () => {
        const id = btn.getAttribute('data-id');
        const isCurrentlyTracked = activeFollowVehicleId && (
          id === activeFollowVehicleId ||
          normalizeVehicleId(id) === activeFollowVehicleId ||
          (getNumericVehicleId(id) !== null && getNumericVehicleId(id) === getNumericVehicleId(activeFollowVehicleId))
        );

        if (isCurrentlyTracked) {
          stopTrackingVehicle();
        } else {
          await trackEscortVehicle(id);
        }
      };
    });

    const untagBtns = hud.querySelectorAll('.sidecar-hud-untag-btn');
    untagBtns.forEach(btn => {
      btn.onclick = async () => {
        const id = btn.getAttribute('data-id');
        if (id) {
          btn.textContent = '...';
          await untagEscortDevice(id);
        }
      };
    });

    const tagBtn = document.getElementById('sidecar-tag-btn');
    const tagInput = document.getElementById('sidecar-tag-input');
    const radiusInput = document.getElementById('sidecar-radius-input');

    if (tagBtn && tagInput) {
      tagBtn.onclick = async () => {
        const val = tagInput.value.trim();
        if (!val) {
          tagInput.focus();
          return;
        }
        const radius = Number(radiusInput?.value || 15);
        tagBtn.textContent = 'TAGGING...';
        tagBtn.disabled = true;
        try {
          await tagTrafficVehicle(val, radius);
          tagInput.value = '';
        } finally {
          tagBtn.textContent = '+ TAG';
          tagBtn.disabled = false;
        }
      };

      tagInput.onkeydown = (e) => {
        if (e.key === 'Enter') tagBtn.click();
      };
    }

    // Populate nearby vehicle suggestion chips
    const chipsContainer = document.getElementById('sidecar-tag-chips');
    if (chipsContainer) {
      const detected = getActiveTrafficVehiclesSample(5);
      if (detected.length === 0) {
        chipsContainer.innerHTML = `<span style="color: #667788; font-style: italic;">Enable Traffic layer to detect cars</span>`;
      } else {
        chipsContainer.innerHTML = detected.map(d => `
          <button class="sidecar-chip-btn" data-id="${d.id}" style="
            background: rgba(0, 255, 204, 0.12);
            border: 1px solid rgba(0, 255, 204, 0.4);
            color: #00ffcc;
            font-family: monospace;
            font-size: 9px;
            padding: 2px 6px;
            border-radius: 3px;
            cursor: pointer;
          ">${d.id}</button>
        `).join('');

        chipsContainer.querySelectorAll('.sidecar-chip-btn').forEach(chip => {
          chip.onclick = () => {
            const vid = chip.getAttribute('data-id');
            if (tagInput) {
              tagInput.value = vid;
              tagBtn?.click();
            }
          };
        });
      }
    }

    updateTrackingHudState();
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

  // Global APIs on window for developer console and integration
  window.__godsEyeSidecarFlyTo = (lat, lon, alt = 2200, pitch_deg = -89.9) => {
    handleCommand({
      action: 'fly_to_coordinate',
      params: { lat, lon, altitude_m: alt, pitch_deg, duration_s: 1.5 },
    });
  };

  window.__godsEyeSidecarTrackVehicle = (vehicleId) => {
    return trackEscortVehicle(vehicleId);
  };

  window.__godsEyeSidecarStopTracking = () => {
    stopTrackingVehicle();
  };

  window.__godsEyeSidecarTagTrafficVehicle = (vehicleId, bubbleRadiusKm = 15.0, name = null) => {
    return tagTrafficVehicle(vehicleId, bubbleRadiusKm, name);
  };

  window.__godsEyeSidecarUntagDevice = (deviceId) => {
    return untagEscortDevice(deviceId);
  };

  window.__godsEyeSidecarGetDetectedTrafficVehicles = (limit = 10) => {
    return getActiveTrafficVehiclesSample(limit);
  };

  // Wire Cesium viewer follower and state listeners on ready
  waitForViewer(8000).then(v => {
    if (v) {
      const { Cesium } = getCesiumGlobals();
      installCameraFollower(v, Cesium);
      if (v?.trackedEntityChanged?.addEventListener) {
        v.trackedEntityChanged.addEventListener(() => {
          updateTrackingHudState();
        });
      }
    }
  });

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
