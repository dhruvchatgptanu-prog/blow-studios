import type { SupabaseClient } from "@supabase/supabase-js";

import { DISPLAY_MEASUREMENT_JSON_SCHEMA, DISPLAY_MEASUREMENT_PROMPT, displayMeasurementSchema, resolveDisplay, type DisplayMeasurement, type DisplaySpec } from "./display";
import { availableProviders, structuredJson } from "./providers.server";
import { mergeProfiles, profileFromText, STORE_ANALYSIS_PROMPT, STORE_PROFILE_JSON_SCHEMA, storeProfileSchema, type StoreProfile } from "./store";
import { recommendTemplates, type TemplateMatch } from "./templates";

/**
 * Server side of the store wizard. Both steps degrade gracefully: with no AI
 * key the profile comes from the text baseline and the display from the
 * user-entered size; with a key, a vision engine fills in the rest.
 */

const ANALYSIS_TIMEOUT_MS = Number(process.env["HALFTONE_ANALYSIS_TIMEOUT_MS"] ?? 90_000);

async function db(): Promise<SupabaseClient | undefined> {
  if (process.env["HALFTONE_MEMORY"] === "off") return undefined;
  if (!process.env["SUPABASE_URL"] || !process.env["SUPABASE_SERVICE_ROLE_KEY"]) return undefined;
  try {
    const mod = await import("@/integrations/supabase/client.server");
    return mod.supabaseAdmin as unknown as SupabaseClient;
  } catch {
    return undefined;
  }
}

function signal(ms: number) {
  const controller = new AbortController();
  setTimeout(() => controller.abort(), ms).unref?.();
  return controller.signal;
}

function parseJson<T>(text: string, parse: (json: unknown) => T): T | undefined {
  try {
    return parse(JSON.parse(text));
  } catch {
    return undefined;
  }
}

export async function analyzeStoreOnServer(input: { description: string; venueName?: string | undefined; images?: string[] | undefined }) {
  const warnings: string[] = [];
  const baseline = profileFromText(input.description, input.venueName ?? "");
  let profile: StoreProfile = baseline;
  let engine: { provider: string; model: string } | null = null;

  if (availableProviders().length === 0) {
    warnings.push("No AI engine is configured; the profile comes from the written description only.");
  } else {
    try {
      const result = await structuredJson({
        system: STORE_ANALYSIS_PROMPT,
        text: [`DESCRIPTION:\n${input.description}`, input.venueName ? `VENUE NAME: ${input.venueName}` : "", input.images?.length ? `${input.images.length} photo(s) attached.` : "No photos attached."].filter(Boolean).join("\n\n"),
        images: input.images,
        schemaName: "store_profile",
        jsonSchema: STORE_PROFILE_JSON_SCHEMA,
        signal: signal(ANALYSIS_TIMEOUT_MS),
      });
      const parsed = parseJson(result.text, (json) => storeProfileSchema.partial().parse(json));
      if (parsed) {
        profile = mergeProfiles(baseline, parsed);
        engine = { provider: result.provider, model: result.model };
      } else {
        warnings.push("The engine returned an unreadable profile; using the description-only profile.");
      }
    } catch (error) {
      warnings.push(`Store analysis fell back to the description only (${error instanceof Error ? error.message : "engine error"}).`);
    }
  }
  if (!profile.palette.length && (input.images?.length ?? 0) === 0) warnings.push("Add a storefront or signage photo to detect brand colours.");

  const templates: TemplateMatch[] = recommendTemplates(profile, 4);
  let storeId: string | undefined;
  const client = await db();
  if (client) {
    try {
      const inserted = await client
        .from("halftone_stores")
        .insert({
          description: input.description,
          photo_count: input.images?.length ?? 0,
          profile,
          recommended_templates: templates.map((match) => ({ id: match.template.id, score: match.score, reasons: match.reasons })),
          engine_provider: engine?.provider ?? null,
          engine_model: engine?.model ?? null,
          confidence: profile.confidence,
        })
        .select("id")
        .single();
      storeId = (inserted.data as { id?: string } | null)?.id;
    } catch {
      /* memory is best-effort */
    }
  }
  return { storeId, profile, templates, engine, warnings };
}

export async function measureDisplayOnServer(input: { image: string; imageAspect?: number | undefined; widthCm?: number | undefined; heightCm?: number | undefined; viewingDistanceM?: number | undefined; storeId?: string | undefined; hint?: string | undefined }) {
  const warnings: string[] = [];
  let measurement: DisplayMeasurement | undefined;
  let engine: { provider: string; model: string } | null = null;

  if (availableProviders().length === 0) {
    warnings.push("No AI engine is configured; enter the display width and height to fit the menu.");
  } else {
    try {
      const result = await structuredJson({
        system: DISPLAY_MEASUREMENT_PROMPT,
        text: [
          "Measure the display in the attached photo.",
          input.hint ? `OWNER'S HINT: ${input.hint}` : "",
          input.widthCm ? `The owner says the width is ${input.widthCm} cm.` : "",
          input.heightCm ? `The owner says the height is ${input.heightCm} cm.` : "",
        ].filter(Boolean).join("\n"),
        images: [input.image],
        schemaName: "display_measurement",
        jsonSchema: DISPLAY_MEASUREMENT_JSON_SCHEMA,
        signal: signal(ANALYSIS_TIMEOUT_MS),
      });
      measurement = parseJson(result.text, (json) => displayMeasurementSchema.parse(json));
      if (measurement) engine = { provider: result.provider, model: result.model };
      else warnings.push("The engine returned an unreadable measurement; using your entered size.");
    } catch (error) {
      warnings.push(`Display measurement fell back to your entered size (${error instanceof Error ? error.message : "engine error"}).`);
    }
  }

  const spec: DisplaySpec = resolveDisplay(measurement, { widthCm: input.widthCm, heightCm: input.heightCm, viewingDistanceM: input.viewingDistanceM, imageAspect: input.imageAspect });
  if (spec.sizeSource === "model" && spec.sizeConfidence < 0.5) warnings.push("The size estimate is low-confidence. Enter the real width or height for exact fitting.");
  if (spec.sizeSource === "aspect-only") warnings.push("Only the shape of the display was detected. Enter the real width or height for exact fitting.");

  let displayId: string | undefined;
  const client = await db();
  if (client) {
    try {
      const inserted = await client
        .from("halftone_displays")
        .insert({
          store_id: input.storeId ?? null,
          measurement: measurement ?? {},
          spec,
          user_width_cm: input.widthCm ?? null,
          user_height_cm: input.heightCm ?? null,
          engine_provider: engine?.provider ?? null,
          engine_model: engine?.model ?? null,
        })
        .select("id")
        .single();
      displayId = (inserted.data as { id?: string } | null)?.id;
    } catch {
      /* best-effort */
    }
  }
  return { displayId, spec, engine, warnings };
}
