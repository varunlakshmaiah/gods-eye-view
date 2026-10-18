import express, { Request, Response, NextFunction } from 'express';
import { createProxyMiddleware, responseInterceptor } from 'http-proxy-middleware';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import dotenv from 'dotenv';

import { Tile38Sink } from './sinks/tile38.js';
import { TimescaleSink } from './sinks/timescale.js';
import { PlaybackController } from './playback.js';
import { HeartbeatManager } from './heartbeat.js';
import {
  normalizeFlights,
  normalizeMilitary,
  normalizeVessels,
  normalizeFirms,
  normalizeCelestrakTle,
} from './normalizer.js';

dotenv.config();

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();
const PORT = parseInt(process.env.PROXY_PORT || '8080', 10);
const UPSTREAM_URL = (process.env.UPSTREAM_URL || 'http://127.0.0.1:4173').replace(/\/$/, '');

// Initialize Core Sinks & Managers
const tile38Sink = new Tile38Sink();
const timescaleSink = new TimescaleSink();
const playbackController = new PlaybackController();
const heartbeatManager = new HeartbeatManager(UPSTREAM_URL, tile38Sink, timescaleSink);

heartbeatManager.start();

// ----------------------------------------------------------------------------
// 1. Sidecar Status & Internal Routes
// ----------------------------------------------------------------------------
app.get('/health', (_req: Request, res: Response) => {
  res.json({
    status: 'ok',
    service: 'proxy-tee',
    uptime: process.uptime(),
    headless: heartbeatManager.isHeadlessMode(),
    playback: playbackController.getMode(),
  });
});

app.get('/__sidecar/status', (_req: Request, res: Response) => {
  res.json({
    upstream: UPSTREAM_URL,
    headless: heartbeatManager.isHeadlessMode(),
    playback: playbackController.getMode(),
  });
});

app.all(['/__sidecar/playback', '/api/replay'], express.json(), (req: Request, res: Response) => {
  if (req.method === 'GET') {
    res.json({ status: 'ok', current: playbackController.getMode() });
    return;
  }
  const { mode, timestamp } = req.body || {};
  if (mode !== 'live' && mode !== 'replay') {
    res.status(400).json({ error: 'mode must be "live" or "replay"' });
    return;
  }
  playbackController.setMode(mode, timestamp);
  res.json({ status: 'ok', current: playbackController.getMode() });
});

// Serve the Co-Pilot bridge script dynamically
app.get('/__sidecar/copilot-bridge.js', (_req: Request, res: Response) => {
  const possiblePaths = [
    '/app/ui-bridge/copilot-bridge.js',
    path.resolve(process.cwd(), 'ui-bridge/copilot-bridge.js'),
    path.resolve(__dirname, '../../ui-bridge/copilot-bridge.js'),
    path.resolve(__dirname, '../ui-bridge/copilot-bridge.js'),
    path.resolve(process.cwd(), 'sidecar/ui-bridge/copilot-bridge.js'),
    path.resolve(process.cwd(), '../ui-bridge/copilot-bridge.js'),
  ];

  for (const p of possiblePaths) {
    if (fs.existsSync(p)) {
      res.setHeader('Content-Type', 'application/javascript');
      res.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate, proxy-revalidate');
      res.setHeader('Pragma', 'no-cache');
      res.setHeader('Expires', '0');
      res.sendFile(p);
      return;
    }
  }

  // Fallback inline script if file not found
  res.setHeader('Content-Type', 'application/javascript');
  res.send(`console.log('[Sidecar UI Bridge] Loaded via inline fallback');`);
});

// Serve the Mobile GPS Transmitter web app
app.get(['/gps', '/mobile-gps'], (_req: Request, res: Response) => {
  const possiblePaths = [
    '/app/ui-bridge/gps.html',
    path.resolve(process.cwd(), 'ui-bridge/gps.html'),
    path.resolve(__dirname, '../../ui-bridge/gps.html'),
    path.resolve(__dirname, '../ui-bridge/gps.html'),
    path.resolve(process.cwd(), 'sidecar/ui-bridge/gps.html'),
    path.resolve(process.cwd(), '../ui-bridge/gps.html'),
  ];

  for (const p of possiblePaths) {
    if (fs.existsSync(p)) {
      res.setHeader('Content-Type', 'text/html');
      res.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate, proxy-revalidate');
      res.sendFile(p);
      return;
    }
  }

  res.status(404).send('GPS transmitter page not found');
});

// ----------------------------------------------------------------------------
// 2. Playback Interceptor Middleware
// ----------------------------------------------------------------------------
app.use(async (req: Request, res: Response, next: NextFunction) => {
  heartbeatManager.recordClientActivity();

  if (req.method === 'GET' && (req.path === '/api/flights' || req.path === '/api/vessels')) {
    try {
      const handled = await playbackController.handleReplay(req, res);
      if (handled) return;
    } catch (e: any) {
      console.warn(`[ProxyTee] Playback handler error: ${e.message}`);
    }
  }
  next();
});

// ----------------------------------------------------------------------------
// 2.5 Gateway Proxy for Sidecar APIs (Escort, AOIs, Radar)
// ----------------------------------------------------------------------------
const GATEWAY_URL = process.env.GATEWAY_URL || 'http://upstream:8000';
const gatewayProxy = createProxyMiddleware({
  target: GATEWAY_URL,
  changeOrigin: true,
  ws: true,
});

app.use((req: Request, res: Response, next: NextFunction) => {
  if (
    req.path.startsWith('/api/escort') ||
    req.path.startsWith('/api/aois') ||
    req.path.startsWith('/api/radar') ||
    req.path.startsWith('/ws/copilot')
  ) {
    return gatewayProxy(req, res, next);
  }
  next();
});

// ----------------------------------------------------------------------------
// 3. Reverse Proxy with Tap / Interceptor
// ----------------------------------------------------------------------------
app.use(
  '/',
  createProxyMiddleware({
    target: UPSTREAM_URL,
    changeOrigin: true,
    xfwd: false,
    headers: { host: 'localhost:4173' },
    ws: true,
    selfHandleResponse: true, // Allows modifying HTML and inspecting API responses
    on: {
      proxyReq: (proxyReq, req: any) => {
        proxyReq.removeHeader('x-forwarded-for');
        proxyReq.removeHeader('x-forwarded-host');
        proxyReq.removeHeader('x-forwarded-port');
        proxyReq.removeHeader('x-forwarded-proto');
        proxyReq.removeHeader('forwarded');
        proxyReq.removeHeader('via');

        proxyReq.setHeader('host', 'localhost:4173');
        if (req.headers.origin) {
          proxyReq.setHeader('origin', 'http://localhost:4173');
        }

        if (req.body && Object.keys(req.body).length > 0 && !proxyReq.writableEnded) {
          const bodyData = typeof req.body === 'string' ? req.body : JSON.stringify(req.body);
          proxyReq.setHeader('Content-Type', 'application/json');
          proxyReq.setHeader('Content-Length', Buffer.byteLength(bodyData));
          proxyReq.write(bodyData);
        }
      },
      proxyRes: responseInterceptor(async (responseBuffer, proxyRes, req, res) => {
        const url = req.url || '';
        const contentType = proxyRes.headers['content-type'] || '';

        // Case A: Inject Co-Pilot script into HTML
        if (contentType.includes('text/html') || url === '/' || url.endsWith('.html')) {
          let html = responseBuffer.toString('utf-8');
          const scriptTag = `<script src="/__sidecar/copilot-bridge.js" defer></script>`;
          if (!html.includes(scriptTag)) {
            html = html.replace('</body>', `${scriptTag}\n</body>`);
          }
          return html;
        }

        // Case B: Tap API Responses asynchronously
        if (url.startsWith('/api/')) {
          // Asynchronous non-blocking processing
          setImmediate(async () => {
            try {
              if (url.startsWith('/api/flights')) {
                const data = JSON.parse(responseBuffer.toString('utf-8'));
                const records = normalizeFlights(data);
                if (records.length > 0) {
                  await tile38Sink.updateEntities(records);
                  timescaleSink.enqueue(records);
                }
              } else if (url.startsWith('/api/military')) {
                const data = JSON.parse(responseBuffer.toString('utf-8'));
                const records = normalizeMilitary(data);
                if (records.length > 0) {
                  await tile38Sink.updateEntities(records);
                  timescaleSink.enqueue(records);
                }
              } else if (url.startsWith('/api/vessels')) {
                const data = JSON.parse(responseBuffer.toString('utf-8'));
                const records = normalizeVessels(data);
                if (records.length > 0) {
                  await tile38Sink.updateEntities(records);
                  timescaleSink.enqueue(records);
                }
              } else if (url.startsWith('/api/firms')) {
                const data = JSON.parse(responseBuffer.toString('utf-8'));
                const records = normalizeFirms(data);
                if (records.length > 0) {
                  await tile38Sink.updateEntities(records, 1800);
                  timescaleSink.enqueue(records);
                }
              } else if (url.startsWith('/api/celestrak/')) {
                const match = url.match(/\/api\/celestrak\/([a-zA-Z0-9_-]+)/);
                const group = match ? match[1] : 'unknown';
                const text = responseBuffer.toString('utf-8');
                const tles = normalizeCelestrakTle(group, text);
                if (tles.length > 0) {
                  timescaleSink.enqueueTles(tles);
                }
              }
            } catch (err: any) {
              console.warn(`[ProxyTee] Asynchronous tap error on ${url}: ${err.message}`);
            }
          });
        }

        // Pass-through exact upstream response
        return responseBuffer;
      }),
      error: (err: any, _req: any, res: any) => {
        console.warn(`[ProxyTee] Upstream proxy error (${err.message}): Upstream may be restarting or unavailable.`);
        if (res && typeof res.writeHead === 'function' && !res.headersSent) {
          res.writeHead(503, { 'Content-Type': 'application/json' });
          res.end(
            JSON.stringify({
              error: 'Upstream Service Unavailable',
              message: 'Upstream server is temporarily unavailable or restarting. Please retry in a few seconds.',
              code: 'UPSTREAM_RESTARTING',
            })
          );
        }
      },
    },
  })
);

app.listen(PORT, '0.0.0.0', () => {
  console.log(`[ProxyTee] Non-Invasive Reverse Proxy listening on 0.0.0.0:${PORT}`);
  console.log(`[ProxyTee] Target Upstream: ${UPSTREAM_URL}`);
});
