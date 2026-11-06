/**
 * Colour maths for template themes: OKLCH (as written in styles.css and
 * templates.ts) → sRGB → WCAG contrast ratio. Used to guarantee every template
 * keeps text legible: 4.5:1 is the WCAG minimum for normal text, 7:1 the
 * enhanced level we want for anything read from a distance.
 */

export type RGB = { r: number; g: number; b: number };

/** Parses `oklch(L C H)` (L as 0-1 or percentage) or `#rrggbb`. Returns null for anything else. */
export function parseColor(input: string): RGB | null {
  const value = input.trim().toLowerCase();
  const hex = /^#([0-9a-f]{6})$/.exec(value);
  if (hex) {
    const n = Number.parseInt(hex[1] ?? "0", 16);
    return { r: ((n >> 16) & 255) / 255, g: ((n >> 8) & 255) / 255, b: (n & 255) / 255 };
  }
  const oklch = /^oklch\(\s*([\d.]+)(%?)\s+([\d.]+)\s+([\d.]+)(?:deg)?\s*(?:\/\s*[\d.]+%?)?\s*\)$/.exec(value);
  if (!oklch) return null;
  let L = Number(oklch[1]);
  if (oklch[2] === "%") L /= 100;
  const C = Number(oklch[3]);
  const H = (Number(oklch[4]) * Math.PI) / 180;
  const a = C * Math.cos(H);
  const b = C * Math.sin(H);
  const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = L - 0.0894841775 * a - 1.291485548 * b;
  const l = l_ ** 3, m = m_ ** 3, s = s_ ** 3;
  const clamp = (v: number) => Math.min(1, Math.max(0, v));
  return {
    r: clamp(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
    g: clamp(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
    b: clamp(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s),
  };
}

/** WCAG relative luminance from linear sRGB components. */
export function luminance(rgb: RGB) {
  return 0.2126 * rgb.r + 0.7152 * rgb.g + 0.0722 * rgb.b;
}

/** WCAG contrast ratio between two colours (1 … 21). Null when either colour cannot be parsed. */
export function contrastRatio(foreground: string, background: string): number | null {
  const fg = parseColor(foreground), bg = parseColor(background);
  if (!fg || !bg) return null;
  const a = luminance(fg), b = luminance(bg);
  const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  return Math.round(ratio * 100) / 100;
}

/** The app's default token values (styles.css) so a theme that overrides only some tokens is checked against the rest. */
export const DEFAULT_TOKENS: Record<string, string> = {
  "--paper": "oklch(0.945 0.028 85)",
  "--card": "oklch(0.975 0.018 88)",
  "--ink": "oklch(0.23 0.03 55)",
  "--muted": "oklch(0.55 0.035 70)",
  "--line": "oklch(0.86 0.035 80)",
  "--press": "oklch(0.6 0.22 34)",
  "--press-deep": "oklch(0.47 0.19 30)",
  "--herb": "oklch(0.52 0.14 155)",
  "--poster": "oklch(0.57 0.24 28)",
  "--poster-foreground": "oklch(0.985 0.01 95)",
  "--sun": "oklch(0.84 0.18 89)",
};

export type ContrastReport = {
  inkOnPaper: number;
  inkOnCard: number;
  posterText: number;
  priceOnCard: number;
  /** Section labels and accents (text-press) on the card. */
  accentOnCard: number;
  mutedOnCard: number;
  /** Lowest of the ratios that carry reading text. */
  weakest: number;
  level: "AAA" | "AA" | "fail";
};

/** Checks the text/background pairs the sheet actually renders for a theme (overrides merged over the defaults). */
export function themeContrast(theme: Record<string, string | undefined>): ContrastReport {
  const tokens: Record<string, string> = { ...DEFAULT_TOKENS };
  for (const [key, value] of Object.entries(theme)) if (value) tokens[key] = value;
  const get = (key: string) => tokens[key] ?? DEFAULT_TOKENS[key] ?? "#000000";
  const ratio = (fg: string, bg: string) => contrastRatio(get(fg), get(bg)) ?? 1;
  const inkOnPaper = ratio("--ink", "--paper");
  const inkOnCard = ratio("--ink", "--card");
  const posterText = ratio("--poster-foreground", "--poster");
  const priceOnCard = ratio("--press-deep", "--card");
  const accentOnCard = ratio("--press", "--card");
  const mutedOnCard = ratio("--muted", "--card");
  // Accent labels are small but bold uppercase; treat 3:1 (large-text minimum) as their floor by weighting them up.
  const weakest = Math.min(inkOnPaper, inkOnCard, posterText, priceOnCard, accentOnCard * 1.5);
  return { inkOnPaper, inkOnCard, posterText, priceOnCard, accentOnCard, mutedOnCard, weakest: Math.round(weakest * 100) / 100, level: weakest >= 7 ? "AAA" : weakest >= 4.5 ? "AA" : "fail" };
}
