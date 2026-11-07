import { createServerFn } from "@tanstack/react-start";

import { engineStatus, type EngineStatus } from "@/lib/ai/providers.server";

export type { EngineStatus } from "@/lib/ai/providers.server";

/** Which engines are configured (no model call, no credits). The chat shows this so the owner can see keys being picked up. */
export const getEngineStatus = createServerFn({ method: "GET" }).handler(async (): Promise<EngineStatus[]> => engineStatus());
