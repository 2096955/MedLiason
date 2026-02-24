/**
 * Session Memory Panel
 *
 * Shows what the system remembers for the current session
 * and allows users to clear their data (GDPR right to erasure).
 */

import React, { useState, useEffect, useCallback } from "react";
import { Database, Trash2, RefreshCw, AlertCircle } from "lucide-react";
import { Button } from "@/lib/components/ui";

interface MemorySummary {
  session_id: string;
  namespaces: Record<string, number>;
  total_keys: number;
}

const NAMESPACE_LABELS: Record<string, string> = {
  citations: "Citations & References",
  evidence: "Research Evidence",
  intermediate: "Working Data",
  learning: "Learning Signals",
  verification: "Verification Results",
};

interface SessionMemoryPanelProps {
  sessionId: string | null;
  serverUrl?: string;
}

export function SessionMemoryPanel({ sessionId, serverUrl = "" }: SessionMemoryPanelProps) {
  const [summary, setSummary] = useState<MemorySummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showConfirm, setShowConfirm] = useState(false);

  const fetchSummary = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${serverUrl}/api/v1/sessions/${sessionId}/memory`, {
        credentials: "include",
      });
      if (res.ok) {
        setSummary(await res.json());
      } else if (res.status === 404) {
        setSummary(null);
      } else {
        setError("Failed to load memory data");
      }
    } catch {
      setError("Could not connect to server");
    } finally {
      setLoading(false);
    }
  }, [sessionId, serverUrl]);

  useEffect(() => {
    fetchSummary();
  }, [fetchSummary]);

  const handleClear = async () => {
    if (!sessionId) return;
    setClearing(true);
    try {
      const res = await fetch(`${serverUrl}/api/v1/sessions/${sessionId}/memory`, {
        method: "DELETE",
        credentials: "include",
      });
      if (res.ok) {
        setShowConfirm(false);
        await fetchSummary();
      } else {
        setError("Failed to clear memory data");
      }
    } catch {
      setError("Could not connect to server");
    } finally {
      setClearing(false);
    }
  };

  if (!sessionId) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-400 text-sm p-4">
        <Database className="w-8 h-8 mb-2 opacity-50" />
        <p>No active session</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full text-sm">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-200 dark:border-gray-700">
        <div className="flex items-center gap-2">
          <Database className="w-4 h-4 text-gray-500" />
          <span className="font-medium text-gray-700 dark:text-gray-300">Session Memory</span>
        </div>
        <button
          onClick={fetchSummary}
          disabled={loading}
          className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-400"
          title="Refresh"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-2">
        {error && (
          <div className="flex items-center gap-2 text-amber-600 dark:text-amber-400 text-xs">
            <AlertCircle className="w-3.5 h-3.5" />
            <span>{error}</span>
          </div>
        )}

        {loading && !summary && (
          <div className="text-gray-400 text-xs">Loading...</div>
        )}

        {summary && (
          <>
            <div className="text-xs text-gray-500 dark:text-gray-400">
              {summary.total_keys} items stored across {Object.keys(summary.namespaces).length} categories
            </div>

            {Object.entries(summary.namespaces).map(([ns, count]) => (
              <div
                key={ns}
                className="flex items-center justify-between py-1.5 px-2 rounded bg-gray-50 dark:bg-gray-800"
              >
                <span className="text-gray-600 dark:text-gray-400">
                  {NAMESPACE_LABELS[ns] || ns}
                </span>
                <span className={`text-xs font-mono ${count > 0 ? "text-blue-600 dark:text-blue-400" : "text-gray-400"}`}>
                  {count < 0 ? "n/a" : count}
                </span>
              </div>
            ))}
          </>
        )}
      </div>

      {/* Footer actions */}
      <div className="px-3 py-2 border-t border-gray-200 dark:border-gray-700">
        {showConfirm ? (
          <div className="space-y-2">
            <p className="text-xs text-red-600 dark:text-red-400">
              This will permanently delete all session memory data. This cannot be undone.
            </p>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="destructive"
                onClick={handleClear}
                disabled={clearing}
                className="flex-1 text-xs"
              >
                {clearing ? "Clearing..." : "Confirm Delete"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setShowConfirm(false)}
                className="flex-1 text-xs"
              >
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <Button
            size="sm"
            variant="outline"
            onClick={() => setShowConfirm(true)}
            disabled={!summary || summary.total_keys === 0}
            className="w-full text-xs flex items-center gap-1.5"
          >
            <Trash2 className="w-3.5 h-3.5" />
            Clear session data
          </Button>
        )}
      </div>
    </div>
  );
}
