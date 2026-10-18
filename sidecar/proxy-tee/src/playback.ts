import pg from 'pg';
import { Request, Response } from 'express';

const { Pool } = pg;

export class PlaybackController {
  private pool: pg.Pool;
  private currentMode: 'live' | 'replay' = 'live';
  private replayTimestamp: Date | null = null;

  constructor(connectionString = process.env.DATABASE_URL || 'postgresql://postgres:postgres@127.0.0.1:5432/gods_eye_sidecar') {
    this.pool = new Pool({ connectionString });
  }

  setMode(mode: 'live' | 'replay', timestamp?: string | Date): void {
    this.currentMode = mode;
    this.replayTimestamp = timestamp ? new Date(timestamp) : (mode === 'replay' ? new Date() : null);
    console.log(`[PlaybackController] Mode set to: ${mode} (ts: ${this.replayTimestamp?.toISOString()})`);
  }

  getMode(): { mode: 'live' | 'replay'; timestamp: string | null } {
    return {
      mode: this.currentMode,
      timestamp: this.replayTimestamp ? this.replayTimestamp.toISOString() : null,
    };
  }

  /**
   * Determine target timestamp for request (checks request param/header or active global mode)
   */
  resolveTargetTimestamp(req: Request): Date | null {
    const queryTs = req.query.replay_ts as string;
    const headerTs = req.headers['x-sidecar-replay-time'] as string;
    if (queryTs) return new Date(queryTs);
    if (headerTs) return new Date(headerTs);
    if (this.currentMode === 'replay' && this.replayTimestamp) {
      return this.replayTimestamp;
    }
    return null;
  }

  /**
   * Attempts to handle request from DB historical playback.
   * Returns true if handled, false to pass through to live upstream.
   */
  async handleReplay(req: Request, res: Response): Promise<boolean> {
    const targetTs = this.resolveTargetTimestamp(req);
    if (!targetTs || isNaN(targetTs.getTime())) return false;

    const path = req.path;

    if (path === '/api/flights') {
      return await this.replayFlights(targetTs, res);
    } else if (path === '/api/vessels') {
      return await this.replayVessels(targetTs, res);
    }

    return false;
  }

  private async replayFlights(targetTs: Date, res: Response): Promise<boolean> {
    const windowStart = new Date(targetTs.getTime() - 5 * 60 * 1000); // 5 min window
    const query = `
      SELECT DISTINCT ON (entity_id)
        entity_id, ts, ST_X(geom::geometry) AS lon, ST_Y(geom::geometry) AS lat,
        altitude_m, speed_kts, heading_deg, payload
      FROM telemetry_history
      WHERE layer = 'flights'
        AND ts >= $1 AND ts <= $2
      ORDER BY entity_id, ts DESC;
    `;

    try {
      const result = await this.pool.query(query, [windowStart, targetTs]);
      const states: any[] = [];

      for (const row of result.rows) {
        const payload = row.payload || {};
        const velocityMs = row.speed_kts ? row.speed_kts / 1.94384 : null;
        states.push([
          payload.icao24 || row.entity_id,
          payload.callsign || '',
          payload.country || 'Unknown',
          Math.floor(new Date(row.ts).getTime() / 1000),
          Math.floor(new Date(row.ts).getTime() / 1000),
          row.lon,
          row.lat,
          row.altitude_m,
          false,
          velocityMs,
          row.heading_deg,
          0,
          null,
          row.altitude_m,
          payload.squawk || null,
          false,
          0,
          payload.category || 0,
        ]);
      }

      res.setHeader('X-Sidecar-Replay', 'true');
      res.setHeader('X-Sidecar-Target-Time', targetTs.toISOString());
      res.json({
        time: Math.floor(targetTs.getTime() / 1000),
        states,
        _replay: true,
      });
      return true;
    } catch (e: any) {
      console.warn(`[PlaybackController] Failed to query flights replay: ${e.message}`);
      return false;
    }
  }

  private async replayVessels(targetTs: Date, res: Response): Promise<boolean> {
    const windowStart = new Date(targetTs.getTime() - 30 * 60 * 1000); // 30 min window
    const query = `
      SELECT DISTINCT ON (entity_id)
        entity_id, ts, ST_X(geom::geometry) AS lon, ST_Y(geom::geometry) AS lat,
        speed_kts, heading_deg, payload
      FROM telemetry_history
      WHERE layer = 'vessels'
        AND ts >= $1 AND ts <= $2
      ORDER BY entity_id, ts DESC;
    `;

    try {
      const result = await this.pool.query(query, [windowStart, targetTs]);
      const rows = result.rows.map((r) => {
        const payload = r.payload || {};
        return {
          mmsi: r.entity_id,
          name: payload.name || 'UNKNOWN',
          type: payload.type || 'Vessel',
          lat: r.lat,
          lon: r.lon,
          speed: r.speed_kts,
          course: r.heading_deg,
          heading: r.heading_deg,
          destination: payload.destination || null,
          last_position_epoch: Math.floor(new Date(r.ts).getTime() / 1000),
          last_position_UTC: new Date(r.ts).toISOString(),
        };
      });

      res.setHeader('X-Sidecar-Replay', 'true');
      res.json({
        rows,
        source: 'SidecarTimescaleDB_Replay',
        status: 'replay',
        newestPositionAt: Math.floor(targetTs.getTime() / 1000),
      });
      return true;
    } catch (e: any) {
      console.warn(`[PlaybackController] Failed to query vessels replay: ${e.message}`);
      return false;
    }
  }

  async close(): Promise<void> {
    await this.pool.end();
  }
}
