import { latLngToCell } from 'h3-js';

export interface CanonicalTelemetryRecord {
  ts: Date;
  layer: string;
  entity_id: string;
  lat: number;
  lon: number;
  altitude_m: number | null;
  speed_kts: number | null;
  heading_deg: number | null;
  h3_res5: string; // BigInt as decimal string for SQL BIGINT
  h3_res9: string;
  payload: Record<string, any>;
}

export interface CanonicalTleRecord {
  norad_id: number;
  name: string;
  group_name: string;
  epoch_ts: Date;
  line1: string;
  line2: string;
}

/** Convert H3 hex string into decimal string for BIGINT SQL insertion */
export function h3ToBigInt(hexString: string): string {
  try {
    return BigInt('0x' + hexString).toString();
  } catch {
    return '0';
  }
}

/** Normalize Flights (OpenSky format) */
export function normalizeFlights(data: any): CanonicalTelemetryRecord[] {
  if (!data || !Array.isArray(data.states)) return [];
  const records: CanonicalTelemetryRecord[] = [];
  const now = new Date();

  for (const s of data.states) {
    if (!Array.isArray(s) || s.length < 11) continue;
    const icao24 = s[0];
    const callsign = (s[1] || '').trim();
    const lon = s[5];
    const lat = s[6];
    const baroAlt = s[7]; // meters
    const velocityMs = s[9]; // m/s
    const heading = s[10]; // degrees
    const timePos = s[3]; // epoch seconds

    if (lat === null || lon === null || isNaN(lat) || isNaN(lon)) continue;
    if (lat < -90 || lat > 90 || lon < -180 || lon > 180) continue;

    const ts = timePos ? new Date(timePos * 1000) : now;
    const speedKts = velocityMs !== null && !isNaN(velocityMs) ? velocityMs * 1.94384 : null;
    const h3_5 = h3ToBigInt(latLngToCell(lat, lon, 5));
    const h3_9 = h3ToBigInt(latLngToCell(lat, lon, 9));

    records.push({
      ts,
      layer: 'flights',
      entity_id: icao24 || callsign,
      lat,
      lon,
      altitude_m: baroAlt !== null && !isNaN(baroAlt) ? baroAlt : null,
      speed_kts: speedKts,
      heading_deg: heading !== null && !isNaN(heading) ? heading : null,
      h3_res5: h3_5,
      h3_res9: h3_9,
      payload: {
        icao24,
        callsign,
        country: s[2],
        geo_altitude: s[13],
        squawk: s[14],
        category: s[17],
      },
    });
  }
  return records;
}

/** Normalize Military Aircraft (adsb.lol format) */
export function normalizeMilitary(data: any): CanonicalTelemetryRecord[] {
  if (!data || !Array.isArray(data.ac)) return [];
  const records: CanonicalTelemetryRecord[] = [];
  const now = new Date();

  for (const ac of data.ac) {
    const lat = ac.lat;
    const lon = ac.lon;
    if (lat === null || lon === null || isNaN(lat) || isNaN(lon)) continue;
    if (lat < -90 || lat > 90 || lon < -180 || lon > 180) continue;

    const hex = ac.hex;
    const flight = (ac.flight || '').trim();
    const altBaroFt = typeof ac.alt_baro === 'number' ? ac.alt_baro : null;
    const altM = altBaroFt !== null ? altBaroFt * 0.3048 : null;
    const speedKts = typeof ac.gs === 'number' ? ac.gs : null;
    const track = typeof ac.track === 'number' ? ac.track : null;

    const h3_5 = h3ToBigInt(latLngToCell(lat, lon, 5));
    const h3_9 = h3ToBigInt(latLngToCell(lat, lon, 9));

    records.push({
      ts: now,
      layer: 'military',
      entity_id: hex || flight,
      lat,
      lon,
      altitude_m: altM,
      speed_kts: speedKts,
      heading_deg: track,
      h3_res5: h3_5,
      h3_res9: h3_9,
      payload: {
        hex,
        flight,
        type: ac.t,
        reg: ac.r,
        squawk: ac.squawk,
      },
    });
  }
  return records;
}

/** Normalize AIS Vessels */
export function normalizeVessels(data: any): CanonicalTelemetryRecord[] {
  if (!data || !Array.isArray(data.rows)) return [];
  const records: CanonicalTelemetryRecord[] = [];
  const now = new Date();

  for (const v of data.rows) {
    const lat = v.lat;
    const lon = v.lon;
    if (lat === null || lon === null || isNaN(lat) || isNaN(lon)) continue;
    if (lat < -90 || lat > 90 || lon < -180 || lon > 180) continue;

    const mmsi = String(v.mmsi || '');
    const ts = v.last_position_epoch ? new Date(v.last_position_epoch * 1000) : now;
    const speed = typeof v.speed === 'number' ? v.speed : null;
    const heading = typeof v.heading === 'number' ? v.heading : (typeof v.course === 'number' ? v.course : null);

    const h3_5 = h3ToBigInt(latLngToCell(lat, lon, 5));
    const h3_9 = h3ToBigInt(latLngToCell(lat, lon, 9));

    records.push({
      ts,
      layer: 'vessels',
      entity_id: mmsi || v.name || 'UNKNOWN',
      lat,
      lon,
      altitude_m: 0,
      speed_kts: speed,
      heading_deg: heading,
      h3_res5: h3_5,
      h3_res9: h3_9,
      payload: {
        mmsi,
        name: v.name,
        type: v.type,
        destination: v.destination,
        imo: v.imo,
      },
    });
  }
  return records;
}

/** Normalize NASA FIRMS Wildfires */
export function normalizeFirms(data: any): CanonicalTelemetryRecord[] {
  if (!data || !Array.isArray(data.fires)) return [];
  const records: CanonicalTelemetryRecord[] = [];
  const now = new Date();

  for (const f of data.fires) {
    const lat = f.latitude;
    const lon = f.longitude;
    if (lat === null || lon === null || isNaN(lat) || isNaN(lon)) continue;
    if (lat < -90 || lat > 90 || lon < -180 || lon > 180) continue;

    const id = `fire_${lat.toFixed(4)}_${lon.toFixed(4)}_${f.acq_date || ''}_${f.acq_time || ''}`;
    const h3_5 = h3ToBigInt(latLngToCell(lat, lon, 5));
    const h3_9 = h3ToBigInt(latLngToCell(lat, lon, 9));

    records.push({
      ts: now,
      layer: 'firms',
      entity_id: id,
      lat,
      lon,
      altitude_m: 0,
      speed_kts: 0,
      heading_deg: null,
      h3_res5: h3_5,
      h3_res9: h3_9,
      payload: {
        brightness: f.brightness,
        confidence: f.confidence,
        frp: f.frp,
        acq_date: f.acq_date,
        acq_time: f.acq_time,
        satellite: f.satellite,
      },
    });
  }
  return records;
}

/** Parse CelesTrak TLE Text into Canonical TLE Records */
export function normalizeCelestrakTle(group: string, text: string): CanonicalTleRecord[] {
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter((l) => l.length > 0);
  const records: CanonicalTleRecord[] = [];

  for (let i = 0; i < lines.length; i++) {
    // Standard 3-line format: Line 0 = name, Line 1 = line1, Line 2 = line2
    if (lines[i].startsWith('1 ') && i + 1 < lines.length && lines[i + 1].startsWith('2 ')) {
      const name = i > 0 && !lines[i - 1].startsWith('1 ') && !lines[i - 1].startsWith('2 ') ? lines[i - 1] : 'UNKNOWN';
      const line1 = lines[i];
      const line2 = lines[i + 1];

      const noradStr = line1.substring(2, 7).trim();
      const noradId = parseInt(noradStr, 10);

      // Parse epoch from line 1 (cols 18-32): yyddd.dddddddd
      const epochStr = line1.substring(18, 32).trim();
      let epochDate = new Date();
      if (epochStr.length >= 5) {
        const yearPrefix = parseInt(epochStr.substring(0, 2), 10);
        const fullYear = yearPrefix < 57 ? 2000 + yearPrefix : 1900 + yearPrefix;
        const dayOfYear = parseFloat(epochStr.substring(2));
        const dayMillis = (dayOfYear - 1) * 86400 * 1000;
        epochDate = new Date(Date.UTC(fullYear, 0, 1) + dayMillis);
      }

      if (!isNaN(noradId)) {
        records.push({
          norad_id: noradId,
          name,
          group_name: group,
          epoch_ts: epochDate,
          line1,
          line2,
        });
      }
      i++; // skip line 2
    }
  }
  return records;
}
