import { useState } from "react";
import ThemePicker from "./ThemePicker";
import UpdateSection from "./UpdateSection";
import ParamDefaultsModal from "./ParamDefaultsModal";
import { clearCache } from "../api/system";

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

export default function SettingsPanel({ open, onClose }: SettingsPanelProps) {
  const [paramDefaultsOpen, setParamDefaultsOpen] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [clearMsg, setClearMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const onClearCache = async () => {
    if (clearing) return;
    // Cheap confirm — destructive but fully recoverable by re-running discovery.
    const ok = window.confirm(
      "Delete all generated files in userdata/discovery and userdata/mql?\n\n" +
        "This removes .set / .mq5 / .csv / .png artifacts. Settings, themes, " +
        "parameter defaults, and imported MT5 history are NOT touched.",
    );
    if (!ok) return;
    setClearing(true);
    setClearMsg(null);
    try {
      const r = await clearCache();
      setClearMsg({
        kind: "ok",
        text: `Cleared ${r.total_files} file${r.total_files === 1 ? "" : "s"} (${formatBytes(r.total_bytes)}).`,
      });
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

          {/* ── Cache ── */}
          <div className="settings-section">
            <p className="settings-section-title">Cache</p>
            <button
              className="pd-settings-btn"
              onClick={onClearCache}
              disabled={clearing}
            >
              <span className="pd-settings-btn-icon">🗑</span>
              <span>{clearing ? "Clearing…" : "Clear generated files"}</span>
            </button>
            <p style={{ fontSize: 11, color: "var(--text2)", marginTop: 8, lineHeight: 1.4 }}>
              Deletes .set / .mq5 / .csv / .png artifacts in
              <code> userdata/discovery</code> and <code>userdata/mql</code>. Imported
              MT5 history, settings, and themes are kept.
            </p>
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
