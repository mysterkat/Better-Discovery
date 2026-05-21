import { useState } from "react";
import ThemePicker from "./ThemePicker";
import UpdateSection from "./UpdateSection";
import ParamDefaultsModal from "./ParamDefaultsModal";
import { clearCache, CACHE_TYPE_LABELS, type CacheType } from "../api/system";

interface SettingsPanelProps {
  open: boolean;
  onClose: () => void;
}

function formatBytes(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  if (b < 1024 * 1024 * 1024) return `${(b / 1024 / 1024).toFixed(1)} MB`;
  return `${(b / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

// fix 4b: all known cache types
const ALL_CACHE_TYPES = Object.keys(CACHE_TYPE_LABELS) as CacheType[];

export default function SettingsPanel({ open, onClose }: SettingsPanelProps) {
  const [paramDefaultsOpen, setParamDefaultsOpen] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [clearMsg, setClearMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  // fix 4b: per-cache-type selection (all checked by default)
  const [selectedTypes, setSelectedTypes] = useState<Set<CacheType>>(new Set(ALL_CACHE_TYPES));

  const toggleCacheType = (t: CacheType) =>
    setSelectedTypes((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t); else next.add(t);
      return next;
    });

  const onClearCache = async (types?: CacheType[]) => {
    if (clearing) return;
    const toDelete = types ?? ALL_CACHE_TYPES;
    if (toDelete.length === 0) return;
    const labels = toDelete.map((t) => `• ${CACHE_TYPE_LABELS[t]}`).join("\n");
    const ok = window.confirm(
      `Delete the following cache types?\n\n${labels}\n\nSettings, themes, parameter defaults, and imported MT5 history are NOT touched.`,
    );
    if (!ok) return;
    setClearing(true);
    setClearMsg(null);
    try {
      const r = await clearCache(toDelete);
      // v1.1.4: surface per-file errors instead of always showing success.
      const errCount = r.errors?.length ?? 0;
      const base = `Cleared ${r.total_files} file${r.total_files === 1 ? "" : "s"} (${formatBytes(r.total_bytes)}).`;
      if (errCount > 0) {
        const head = r.errors!.slice(0, 3).join("\n• ");
        const more = errCount > 3 ? `\n…and ${errCount - 3} more` : "";
        setClearMsg({
          kind: "err",
          text: `${base}\n${errCount} file${errCount === 1 ? "" : "s"} could NOT be deleted (likely open in Excel / Explorer / antivirus):\n• ${head}${more}`,
        });
      } else {
        setClearMsg({ kind: "ok", text: base });
      }
    } catch (e) {
      setClearMsg({
        kind: "err",
        text: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setClearing(false);
    }
  };

  return (
    <>
      {open && (
        <div
          className="settings-backdrop"
          onClick={onClose}
          aria-hidden="true"
        />
      )}
      <aside
        className={`settings-panel${open ? " open" : ""}`}
        aria-label="Settings"
        role="dialog"
        aria-modal="true"
        aria-hidden={!open}
      >
        <div className="settings-header">
          <h2>Settings</h2>
          <button
            className="settings-close"
            onClick={onClose}
            aria-label="Close settings"
          >
            ✕
          </button>
        </div>
        <div className="settings-body">
          <ThemePicker />

          {/* ── Parameter Defaults ── */}
          <div className="settings-section">
            <p className="settings-section-title">Parameter Defaults</p>
            <button
              className="pd-settings-btn"
              onClick={() => setParamDefaultsOpen(true)}
            >
              <span className="pd-settings-btn-icon">⚙</span>
              <span>Edit Default Values…</span>
            </button>
            <p style={{ fontSize: 11, color: "var(--text2)", marginTop: 8, lineHeight: 1.4 }}>
              Set persistent starting values for Discovery and MC Sim parameters.
              Per-run overrides still take precedence.
            </p>
          </div>

          {/* fix 4a/4b: Granular cache controls ── */}
          <div className="settings-section">
            <p className="settings-section-title">Cache</p>
            <p style={{ fontSize: 11, color: "var(--text2)", marginBottom: 8, lineHeight: 1.4 }}>
              Select which caches to clear. History, settings, themes, and library entries are never removed.
            </p>
            {ALL_CACHE_TYPES.map((t) => (
              <label key={t} className="toggle-label" style={{ marginBottom: 5, fontSize: 12 }}>
                <span className="toggle-wrap">
                  <input
                    type="checkbox"
                    className="toggle-input"
                    checked={selectedTypes.has(t)}
                    onChange={() => toggleCacheType(t)}
                    disabled={clearing}
                  />
                  <span className="toggle-track" />
                </span>
                {CACHE_TYPE_LABELS[t]}
              </label>
            ))}
            <div style={{ display: "flex", gap: 6, marginTop: 10, flexWrap: "wrap" }}>
              <button
                className="pd-settings-btn"
                style={{ flex: 1 }}
                onClick={() => onClearCache([...selectedTypes])}
                disabled={clearing || selectedTypes.size === 0}
                title="Clear only the checked types"
              >
                <span className="pd-settings-btn-icon">🗑</span>
                <span>{clearing ? "Clearing…" : `Clear selected (${selectedTypes.size})`}</span>
              </button>
              <button
                className="pd-settings-btn"
                style={{ flex: 1 }}
                onClick={() => { setSelectedTypes(new Set(ALL_CACHE_TYPES)); onClearCache(ALL_CACHE_TYPES); }}
                disabled={clearing}
                title="Clear all cache types at once"
              >
                <span className="pd-settings-btn-icon">🗑</span>
                <span>{clearing ? "Clearing…" : "Clear all"}</span>
              </button>
            </div>
            {clearMsg && (
              <p
                style={{
                  fontSize: 11,
                  marginTop: 8,
                  color: clearMsg.kind === "ok" ? "var(--success, #57ab5a)" : "var(--danger, #e5534b)",
                }}
              >
                {clearMsg.text}
              </p>
            )}
          </div>

          <UpdateSection />
        </div>
      </aside>

      <ParamDefaultsModal
        open={paramDefaultsOpen}
        onClose={() => setParamDefaultsOpen(false)}
      />
    </>
  );
}
