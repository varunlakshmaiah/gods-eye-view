import pg from 'pg';
import { CanonicalTelemetryRecord, CanonicalTleRecord } from '../normalizer.js';

const { Pool } = pg;

export class TimescaleSink {
  private pool: pg.Pool | null = null;
  private buffer: CanonicalTelemetryRecord[] = [];
  private tleBuffer: CanonicalTleRecord[] = [];
  private flushTimer: NodeJS.Timeout | null = null;
  private maxBufferSize: number;
  private flushIntervalMs: number;
  private isConnected = false;

  constructor(
    connectionString = process.env.DATABASE_URL || 'postgresql://postgres:postgres@127.0.0.1:5432/gods_eye_sidecar',
    flushIntervalMs = parseInt(process.env.TIMESCALE_BATCH_FLUSH_INTERVAL_MS || '2000', 10),
    maxBufferSize = parseInt(process.env.TIMESCALE_BATCH_MAX_SIZE || '500', 10)
  ) {
    this.flushIntervalMs = flushIntervalMs;
    this.maxBufferSize = maxBufferSize;

    try {
      this.pool = new Pool({
        connectionString,
        max: 10,
        idleTimeoutMillis: 30000,
        connectionTimeoutMillis: 5000,
      });

      this.pool.on('error', (err: any) => {
        console.warn(`[TimescaleSink] Unexpected DB pool error: ${err?.message || err}`);
        this.isConnected = false;
      });

      // Test connectivity
      this.pool.query('SELECT 1').then(() => {
        this.isConnected = true;
        console.log(`[TimescaleSink] Connected to TimescaleDB`);
      }).catch((err: any) => {
        console.warn(`[TimescaleSink] Initial DB connection warning: ${err?.message || err}`);
      });

      this.flushTimer = setInterval(() => {
        this.flush().catch((err: any) => {
          console.warn(`[TimescaleSink] Error during periodic flush: ${err?.message || err}`);
        });
      }, this.flushIntervalMs);
    } catch (e: any) {
      console.warn(`[TimescaleSink] Initialization error: ${e.message}`);
    }
  }

  enqueue(records: CanonicalTelemetryRecord[]): void {
    if (records.length === 0) return;
    this.buffer.push(...records);

    if (this.buffer.length >= this.maxBufferSize) {
      this.flush().catch((err: any) => {
        console.warn(`[TimescaleSink] Flush error on max buffer: ${err?.message || err}`);
      });
    }
  }

  enqueueTles(records: CanonicalTleRecord[]): void {
    if (records.length === 0) return;
    this.tleBuffer.push(...records);
  }

  async flush(): Promise<void> {
    if (!this.pool || (this.buffer.length === 0 && this.tleBuffer.length === 0)) return;

    // Flush telemetry
    if (this.buffer.length > 0) {
      const recordsToInsert = this.buffer.splice(0, this.maxBufferSize);
      await this.insertTelemetryBatch(recordsToInsert);
    }

    // Flush TLEs
    if (this.tleBuffer.length > 0) {
      const tlesToInsert = this.tleBuffer.splice(0, 100);
      await this.insertTleBatch(tlesToInsert);
    }
  }

  private async insertTelemetryBatch(records: CanonicalTelemetryRecord[]): Promise<void> {
    if (!this.pool || records.length === 0) return;

    const valueRows: string[] = [];
    const params: any[] = [];
    let pIdx = 1;

    for (const r of records) {
      const alt = r.altitude_m !== null && !isNaN(r.altitude_m) ? r.altitude_m : 0;
      valueRows.push(
        `($${pIdx++}, $${pIdx++}, $${pIdx++}, $${pIdx++}, $${pIdx++}, ST_SetSRID(ST_MakePoint($${pIdx++}, $${pIdx++}, $${pIdx++}), 4326)::geography, $${pIdx++}, $${pIdx++}, $${pIdx++}, $${pIdx++})`
      );
      params.push(
        r.ts,
        r.layer,
        r.entity_id,
        r.h3_res5,
        r.h3_res9,
        r.lon,
        r.lat,
        alt,
        r.altitude_m,
        r.speed_kts,
        r.heading_deg,
        JSON.stringify(r.payload || {})
      );
    }

    const query = `
      INSERT INTO telemetry_history (
        ts, layer, entity_id, h3_res5, h3_res9, geom, altitude_m, speed_kts, heading_deg, payload
      )
      VALUES ${valueRows.join(', ')}
      ON CONFLICT DO NOTHING;
    `;

    try {
      await this.pool.query(query, params);
    } catch (err: any) {
      console.warn(`[TimescaleSink] Batch insert failure (${records.length} records): ${err.message}`);
    }
  }

  private async insertTleBatch(tles: CanonicalTleRecord[]): Promise<void> {
    if (!this.pool || tles.length === 0) return;

    const valueRows: string[] = [];
    const params: any[] = [];
    let pIdx = 1;

    for (const t of tles) {
      valueRows.push(`($${pIdx++}, $${pIdx++}, $${pIdx++}, $${pIdx++}, $${pIdx++}, $${pIdx++})`);
      params.push(t.norad_id, t.name, t.group_name, t.epoch_ts, t.line1, t.line2);
    }

    const query = `
      INSERT INTO tle_archive (norad_id, name, group_name, epoch_ts, line1, line2)
      VALUES ${valueRows.join(', ')}
      ON CONFLICT (norad_id, epoch_ts) DO UPDATE 
      SET fetched_at = NOW();
    `;

    try {
      await this.pool.query(query, params);
    } catch (err: any) {
      console.warn(`[TimescaleSink] TLE batch insert failure: ${err.message}`);
    }
  }

  async close(): Promise<void> {
    if (this.flushTimer) clearInterval(this.flushTimer);
    await this.flush();
    if (this.pool) await this.pool.end();
  }
}
