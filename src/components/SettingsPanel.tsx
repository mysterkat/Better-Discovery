import ThemePicker from "./ThemePicker";

interface SettingsPanelProps {
  open: boolean;
  onClose: () => void;
}

export default function SettingsPanel({ open, onClose }: SettingsPanelProps) {
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
        </div>
      </aside>
    </>
  );
}
