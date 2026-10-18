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

  // Periodic heartbeat reporting camera changes
  setInterval(() => {
    sendViewportUpdate();
  }, 4000);

  // Connect when DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', connect);
  } else {
    connect();
  }
})();
