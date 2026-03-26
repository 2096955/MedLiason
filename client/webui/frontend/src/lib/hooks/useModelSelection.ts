import { useState, useEffect, useCallback, useMemo } from "react";
import {
  MODEL_OPTIONS,
  MODE_OPTIONS,
  resolveAgentName,
  inferModelFromAgentName,
  inferModeFromAgentName,
} from "@/lib/config/modelOptions";
import type { ModelValue, ModeValue } from "@/lib/config/modelOptions";
import { useChatContext } from "@/lib/hooks/useChatContext";
import { useAgentSelection } from "./useAgentSelection";

const STORAGE_KEY_MODEL = "medexpert-model";
const STORAGE_KEY_MODE = "medexpert-mode";

export function useModelSelection() {
  const { isResponding, selectedAgentName, agents } = useChatContext();
  const { handleAgentSelection } = useAgentSelection();

  const [selectedModel, setSelectedModel] = useState<ModelValue>(
    () => (localStorage.getItem(STORAGE_KEY_MODEL) as ModelValue) || "flash"
  );
  const [selectedMode, setSelectedMode] = useState<ModeValue>(
    () => (localStorage.getItem(STORAGE_KEY_MODE) as ModeValue) || "research"
  );

  // Sync dropdowns when selectedAgentName changes externally (e.g., session restoration)
  useEffect(() => {
    if (selectedAgentName) {
      const inferred = resolveAgentName(
        inferModelFromAgentName(selectedAgentName),
        inferModeFromAgentName(selectedAgentName)
      );
      // Only sync if the agent name doesn't match what our dropdowns would produce
      // (avoids infinite loop when we ourselves trigger the change)
      if (inferred !== resolveAgentName(selectedModel, selectedMode)) {
        setSelectedModel(inferModelFromAgentName(selectedAgentName));
        setSelectedMode(inferModeFromAgentName(selectedAgentName));
      }
    }
  }, [selectedAgentName]); // eslint-disable-line react-hooks/exhaustive-deps

  // Persist to localStorage
  useEffect(() => {
    localStorage.setItem(STORAGE_KEY_MODEL, selectedModel);
  }, [selectedModel]);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY_MODE, selectedMode);
  }, [selectedMode]);

  const resolvedAgentName = useMemo(
    () => resolveAgentName(selectedModel, selectedMode),
    [selectedModel, selectedMode]
  );

  // Guard: disable selectors when the resolved agent hasn't registered yet
  const isAgentAvailable = useMemo(
    () => agents.some(a => a.name === resolvedAgentName),
    [agents, resolvedAgentName]
  );

  const handleModelChange = useCallback(
    (model: ModelValue) => {
      setSelectedModel(model);
      const newAgent = resolveAgentName(model, selectedMode);
      handleAgentSelection(newAgent, true);
    },
    [selectedMode, handleAgentSelection]
  );

  const handleModeChange = useCallback(
    (mode: ModeValue) => {
      setSelectedMode(mode);
      const newAgent = resolveAgentName(selectedModel, mode);
      handleAgentSelection(newAgent, true);
    },
    [selectedModel, handleAgentSelection]
  );

  return {
    selectedModel,
    selectedMode,
    resolvedAgentName,
    isAgentAvailable,
    handleModelChange,
    handleModeChange,
    isResponding,
    modelOptions: MODEL_OPTIONS,
    modeOptions: MODE_OPTIONS,
  };
}
