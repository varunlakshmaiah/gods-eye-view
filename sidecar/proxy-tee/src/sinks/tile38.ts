import { Redis } from 'ioredis';
import { CanonicalTelemetryRecord } from '../normalizer.js';

export class Tile38Sink {
  private client: Redis | null = null;
  private isConnected = false;
  private defaultTtl: number;

  constructor(
    host = process.env.TILE38_HOST || '127.0.0.1',
    port = parseInt(process.env.TILE38_PORT || '9851', 10),
    ttl = 120
  ) {
    this.defaultTtl = ttl;
    try {
      this.client = new Redis({
        host,
        port,
        retryStrategy: (times: number) => Math.min(times * 1000, 10000),
        lazyConnect: false,
        maxRetriesPerRequest: 1,
      });

      this.client.on('connect', () => {
        this.isConnected = true;
        console.log(`[Tile38Sink] Connected to Tile38 at ${host}:${port}`);
      });

      this.client.on('error', (err: any) => {
        this.isConnected = false;
        console.warn(`[Tile38Sink] Tile38 connection warning: ${err?.message || err}`);
      });
    } catch (e: any) {
      console.warn(`[Tile38Sink] Failed to initialize Redis client for Tile38: ${e.message}`);
    }
  }

  /**
   * Set live spatial objects in Tile38
   * Command: SET <layer> <id> [FIELD <name> <value> ...] EX <ttl> POINT <lat> <lon>
   */
  async updateEntities(records: CanonicalTelemetryRecord[], ttlSeconds = this.defaultTtl): Promise<void> {
    if (!this.client || !this.isConnected || records.length === 0) return;

    const pipeline = this.client.pipeline();

    for (const r of records) {
      const args: (string | number)[] = [r.layer, r.entity_id];

      if (r.altitude_m !== null && !isNaN(r.altitude_m)) {
        args.push('FIELD', 'alt', Math.round(r.altitude_m));
      }
      if (r.heading_deg !== null && !isNaN(r.heading_deg)) {
        args.push('FIELD', 'hdg', Math.round(r.heading_deg));
      }
      if (r.speed_kts !== null && !isNaN(r.speed_kts)) {
        args.push('FIELD', 'spd', Math.round(r.speed_kts));
      }

      // Add TTL expiration in seconds
      args.push('EX', ttlSeconds);

      // Coordinates
      args.push('POINT', r.lat, r.lon);

      pipeline.call('SET', ...args);
    }

    try {
      await pipeline.exec();
    } catch (err: any) {
      console.warn(`[Tile38Sink] Pipeline execution error: ${err.message}`);
    }
  }

  async close(): Promise<void> {
    if (this.client) {
      await this.client.quit();
    }
  }
}
