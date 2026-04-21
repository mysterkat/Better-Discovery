import { useSettings } from "../state/settings";
import { THEME_LIST } from "../state/theme";

export default function ThemePicker() {
  const { theme, setTheme } = useSettings();

  return (
    <div className="theme-picker">
      <p className="settings-section-label">Theme</p>
      <div className="theme-grid">
        {THEME_LIST.map((t) => (
          <button
            key={t.id}
            className={`theme-btn${theme === t.id ? " active" : ""}`}
            onClick={() => setTheme(t.id)}
            title={t.label}
            aria-pressed={theme === t.id}
          >
            <div className="theme-swatch" aria-hidden="true">
              {t.swatches.map((color, i) => (
                <span key={i} style={{ background: color }} />
              ))}
            </div>
            {t.label}
          </button>
        ))}
      </div>
    </div>
  );
}
