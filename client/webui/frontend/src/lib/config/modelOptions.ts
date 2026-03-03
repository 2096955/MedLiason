export const MODEL_OPTIONS = [
  { value: "flash", label: "Gemini 2.5 Flash", shortLabel: "Flash", suffix: "" },
  { value: "pro", label: "Gemini 3.1 Pro", shortLabel: "Pro", suffix: "Pro" },
  { value: "opus", label: "Claude Opus 4.6", shortLabel: "Opus", suffix: "Opus" },
] as const;

export const MODE_OPTIONS = [
  { value: "research", label: "Deep Research", shortLabel: "Research", agentBase: "OrchestratorAgent" },
  { value: "triage", label: "Medical Triage", shortLabel: "Triage", agentBase: "TriageIntakeAgent" },
] as const;

export type ModelValue = (typeof MODEL_OPTIONS)[number]["value"];
export type ModeValue = (typeof MODE_OPTIONS)[number]["value"];

export function resolveAgentName(model: ModelValue, mode: ModeValue): string {
  const modelOpt = MODEL_OPTIONS.find((m) => m.value === model);
  const modeOpt = MODE_OPTIONS.find((m) => m.value === mode);
  if (!modelOpt || !modeOpt) return "OrchestratorAgent";
  return modeOpt.agentBase + modelOpt.suffix;
}

export function inferModelFromAgentName(agentName: string): ModelValue {
  if (agentName.endsWith("Opus")) return "opus";
  if (agentName.endsWith("Pro")) return "pro";
  return "flash";
}

export function inferModeFromAgentName(agentName: string): ModeValue {
  const baseName = agentName.replace(/Pro$|Opus$/, "");
  if (baseName === "TriageIntakeAgent" || baseName === "TriageOrchestratorAgent") return "triage";
  return "research";
}
