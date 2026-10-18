import { Tile38Sink } from './sinks/tile38.js';
import { TimescaleSink } from './sinks/timescale.js';
import {
  normalizeFlights,
  normalizeVessels,
  normalizeFirms,
  normalizeCelestrakTle,
} from './normalizer.js';

export class HeartbeatManager {
  private upstreamUrl: string;
  private tile38Sink: Tile38Sink;
  private timescaleSink: TimescaleSink;
  private enabled: boolean;

  private lastClientActivity: number = Date.now();
  private activeClientTimeoutMs = 60 * 1000; // 60s without browser requests = headless

  private flightsTimer: NodeJS.Timeout | null = null;
  private vesselsTimer: NodeJS.Timeout | null = null;
  private firmsTimer: NodeJS.Timeout | null = null;
  private celestrakTimer: NodeJS.Timeout | null = null;

  constructor(
    upstreamUrl: string,
    tile38Sink: Tile38Sink,
    timescaleSink: TimescaleSink,
    enabled = process.env.HEADLESS_ENABLED !== 'false'
  ) {
    this.upstreamUrl = upstreamUrl.replace(/\/$/, '');
    this.tile38Sink = tile38Sink;
    this.timescaleSink = timescaleSink;
    this.enabled = enabled;
  }

  recordClientActivity(): void {
    this.lastClientActivity = Date.now();
  }

  isHeadlessMode(): boolean {
    return Date.now() - this.lastClientActivity > this.activeClientTimeoutMs;
  }

  start(): void {
    if (!this.enabled) {
      console.log('[HeartbeatManager] Headless keep-alive disabled via config');
      return;
    }

    console.log('[HeartbeatManager] Starting headless background keep-alive governor...');

    // Flights: 15s interval
    const flightsInterval = parseInt(process.env.HEADLESS_FLIGHTS_INTERVAL_MS || '15000', 10);
    this.flightsTimer = setInterval(() => this.pollFlights(), flightsInterval);

    // Vessels: 30s interval
    const vesselsInterval = parseInt(process.env.HEADLESS_VESSELS_INTERVAL_MS || '30000', 10);
    this.vesselsTimer = setInterval(() => this.pollVessels(), vesselsInterval);

    // FIRMS Fires: 30m interval
    const firmsInterval = parseInt(process.env.HEADLESS_FIRMS_INTERVAL_MS || '1800000', 10);
    this.firmsTimer = setInterval(() => this.pollFirms(), firmsInterval);

    // CelesTrak TLEs: 6h interval
    const celestrakInterval = parseInt(process.env.HEADLESS_CELESTRAK_INTERVAL_MS || '21600000', 10);
    this.celestrakTimer = setInterval(() => this.pollCelestrak(), celestrakInterval);

    // Initial warm-up poll after 5 seconds
    setTimeout(() => {
      this.pollCelestrak();
      this.pollFlights();
      this.pollVessels();
    }, 5000);
  }

  private async pollFlights(): Promise<void> {
    if (!this.isHeadlessMode()) return; // Browser is active and polling, skip to avoid duplicate quota
    try {
      const res = await fetch(`${this.upstreamUrl}/api/flights`);
      if (res.ok) {
        const json = await res.json();
        const records = normalizeFlights(json);
        if (records.length > 0) {
          await this.tile38Sink.updateEntities(records);
          this.timescaleSink.enqueue(records);
        }
      }
    } catch (e: any) {
      console.warn(`[HeartbeatManager] Headless flights poll error: ${e.message}`);
    }
  }

  private async pollVessels(): Promise<void> {
    if (!this.isHeadlessMode()) return;
    try {
      const res = await fetch(`${this.upstreamUrl}/api/vessels`);
      if (res.ok) {
        const json = await res.json();
        const records = normalizeVessels(json);
        if (records.length > 0) {
          await this.tile38Sink.updateEntities(records);
          this.timescaleSink.enqueue(records);
        }
      }
    } catch (e: any) {
      console.warn(`[HeartbeatManager] Headless vessels poll error: ${e.message}`);
    }
  }

  private async pollFirms(): Promise<void> {
    if (!this.isHeadlessMode()) return;
    try {
      const res = await fetch(`${this.upstreamUrl}/api/firms`);
      if (res.ok) {
        const json = await res.json();
        const records = normalizeFirms(json);
        if (records.length > 0) {
          await this.tile38Sink.updateEntities(records, 1800);
          this.timescaleSink.enqueue(records);
        }
      }
    } catch (e: any) {
      console.warn(`[HeartbeatManager] Headless FIRMS poll error: ${e.message}`);
    }
  }

  private async pollCelestrak(): Promise<void> {
    const groups = ['active', 'stations', 'visual'];
    for (const g of groups) {
      try {
        const res = await fetch(`${this.upstreamUrl}/api/celestrak/${g}`);
        if (res.ok) {
          const text = await res.text();
          const tles = normalizeCelestrakTle(g, text);
          if (tles.length > 0) {
            this.timescaleSink.enqueueTles(tles);
          }
        }
      } catch (e: any) {
        console.warn(`[HeartbeatManager] Headless Celestrak poll error (${g}): ${e.message}`);
      }
    }
  }

  stop(): void {
    if (this.flightsTimer) clearInterval(this.flightsTimer);
    if (this.vesselsTimer) clearInterval(this.vesselsTimer);
    if (this.firmsTimer) clearInterval(this.firmsTimer);
    if (this.celestrakTimer) clearInterval(this.celestrakTimer);
  }
}
