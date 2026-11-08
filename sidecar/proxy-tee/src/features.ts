import fs from 'node:fs';

/**
 * Sidecar feature flags (shared sidecar/config/features.json).
 * Mirrors mcp-gateway/src/features.py: env overrides lock a feature, `requires`
 * dependencies must be enabled, and missing features default to enabled.
 */

interface FeatureSpec {
  enabled?: boolean;
  requires?: string[];
  service?: string;
  category?: string;
  restart_required?: boolean;
  description?: string;
}

const DEFAULT_OFF = new Set(['feature_admin_mcp']);
const CANDIDATE_PATHS = [
  '/app/config/features.json',
  'sidecar/config/features.json',
  '../config/features.json',
  'config/features.json',
];

function parseBool(value: string | undefined): boolean | null {
  if (value === undefined) return null;
  const v = value.trim().toLowerCase();
  if (['1', 'true', 'yes', 'on'].includes(v)) return true;
  if (['0', 'false', 'no', 'off'].includes(v)) return false;
  return null;
}

function parseList(value: string | undefined): Set<string> {
  return new Set((value || '').split(',').map((s) => s.trim()).filter(Boolean));
}

export class FeatureRegistry {
  readonly path: string;
  private env: NodeJS.ProcessEnv;
  private catalog: Record<string, FeatureSpec> = {};
  private effective: Record<string, boolean> = {};
  private mtimeMs: number | null = null;
  private timer: NodeJS.Timeout | null = null;
  loadError: string | null = null;

  constructor(path?: string, env: NodeJS.ProcessEnv = process.env) {
    this.env = env;
    this.path =
      path ||
      env.SIDECAR_FEATURES_FILE ||
      CANDIDATE_PATHS.find((p) => fs.existsSync(p)) ||
      CANDIDATE_PATHS[0];
    this.load();
  }

  /** (Re)load from disk. Returns features whose effective state changed. */
  load(): Record<string, boolean> {
    let catalog: Record<string, FeatureSpec>;
    let mtimeMs: number | null;
    let error: string | null = null;
    try {
      const raw = JSON.parse(fs.readFileSync(this.path, 'utf-8'));
      const entries = raw?.features;
      if (!entries || typeof entries !== 'object' || Array.isArray(entries)) {
        throw new Error("'features' must be an object");
      }
      catalog = {};
      for (const [id, spec] of Object.entries(entries)) {
        if (typeof spec === 'boolean') {
          catalog[id] = { enabled: spec }; // shorthand: "cot_atak": false
        } else if (spec && typeof spec === 'object' && !Array.isArray(spec)) {
          catalog[id] = spec as FeatureSpec;
        } else {
          throw new Error(`feature '${id}' must be an object or a boolean`);
        }
      }
      mtimeMs = fs.statSync(this.path).mtimeMs;
    } catch (e: any) {
      if (e?.code !== 'ENOENT') {
        // Keep the last good catalog so a half-written edit doesn't flip everything.
        this.loadError = `Failed to load ${this.path}: ${e.message}`;
        console.warn(`[Features] ${this.loadError}`);
        return {};
      }
      catalog = {};
      mtimeMs = null;
      error = `${this.path} not found; all features use built-in defaults`;
    }
    // Diff against the states actually in force before this load (including defaults).
    const ids = new Set([...Object.keys(this.catalog), ...Object.keys(catalog)]);
    const before = Object.fromEntries([...ids].map((id) => [id, this.isEnabled(id)]));
    this.catalog = catalog;
    this.mtimeMs = mtimeMs;
    this.loadError = error;
    this.effective = Object.fromEntries(Object.keys(this.catalog).map((id) => [id, this.evaluate(id)]));
    const changes: Record<string, boolean> = {};
    for (const id of ids) {
      const now = this.isEnabled(id);
      if (before[id] !== now) changes[id] = now;
    }
    return changes;
  }

  reloadIfChanged(): Record<string, boolean> {
    let mtime: number | null = null;
    try {
      mtime = fs.statSync(this.path).mtimeMs;
    } catch {
      mtime = null;
    }
    return mtime === this.mtimeMs ? {} : this.load();
  }

  /** Poll the file for edits made by hand or by mcp-gateway's PUT /api/features. */
  watch(intervalMs = parseInt(this.env.SIDECAR_FEATURES_RELOAD_MS || '3000', 10)): void {
    if (this.timer) return;
    this.timer = setInterval(() => {
      const changes = this.reloadIfChanged();
      if (Object.keys(changes).length > 0) {
        console.log(`[Features] Reloaded ${this.path}: ${JSON.stringify(changes)}`);
      }
    }, intervalMs);
    this.timer.unref?.();
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  private envLock(id: string): [boolean, string] | null {
    const key = `SIDECAR_FEATURE_${id.toUpperCase()}`;
    const val = parseBool(this.env[key]);
    if (val !== null) return [val, key];
    if (parseList(this.env.SIDECAR_FEATURES_DISABLED).has(id)) return [false, 'SIDECAR_FEATURES_DISABLED'];
    if (parseList(this.env.SIDECAR_FEATURES_ENABLED).has(id)) return [true, 'SIDECAR_FEATURES_ENABLED'];
    return null;
  }

  private ownState(id: string): boolean {
    const lock = this.envLock(id);
    if (lock) return lock[0];
    const enabled = this.catalog[id]?.enabled;
    return typeof enabled === 'boolean' ? enabled : !DEFAULT_OFF.has(id);
  }

  private evaluate(id: string, stack: Set<string> = new Set()): boolean {
    if (stack.has(id)) return false;
    if (!this.ownState(id)) return false;
    const next = new Set(stack).add(id);
    return (this.catalog[id]?.requires || []).every((dep) => this.evaluate(dep, next));
  }

  isEnabled(id: string): boolean {
    return id in this.effective ? this.effective[id] : this.evaluate(id);
  }

  snapshot(): Record<string, boolean> {
    return { ...this.effective };
  }
}

export const features = new FeatureRegistry();
