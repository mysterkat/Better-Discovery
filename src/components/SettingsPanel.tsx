import { useState } from "react";
import ThemePicker from "./ThemePicker";
import UpdateSection from "./UpdateSection";
import ParamDefaultsModal from "./ParamDefaultsModal";

interface SettingsPanelProps {
  open: boolean;
  onClose: () => void;
}

export default function SettingsPanel({ open, onClose }: SettingsPanelProps) {
  const [paramDefaultsOpen, setParamDefaultsOpen] = useState(false);

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
