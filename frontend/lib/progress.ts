// ─────────────────────────────────────────────────────────────────────────────
// Progress message parser
//
// The backend encodes percentage as a suffix:  "Parsing source files…|pct=15"
// This module strips the suffix and exposes the numeric value.
// ─────────────────────────────────────────────────────────────────────────────

export interface ParsedProgress {
  message: string;   // human-readable text without the |pct= suffix
  pct: number | null; // 0–100, or null if indeterminate
}

export function parseProgress(raw: string | null | undefined): ParsedProgress {
  if (!raw) return { message: "", pct: null };
  const idx = raw.lastIndexOf("|pct=");
  if (idx === -1) return { message: raw, pct: null };
  const message = raw.slice(0, idx);
  const num = parseInt(raw.slice(idx + 5), 10);
  const pct = isNaN(num) ? null : Math.min(100, Math.max(0, num));
  return { message, pct };
}

// Stage keyword → overall % band
// Used to derive per-stage completion from the active keyword when pct is null
export const STAGE_BANDS: Record<string, [number, number]> = {
  cloning:   [0,   10],
  reading:   [0,   10],
  parsing:   [10,  25],
  resolving: [25,  35],
  embedding: [35,  90],
  saving:    [90,  99],
};

export function pctFromKeyword(keyword: string): number {
  const band = STAGE_BANDS[keyword];
  return band ? band[0] : 0;
}
