import type { LibraryEntry } from "../api/library";

function strategyKind(entry: LibraryEntry): string {
  const metadata = entry.metadata as Record<string, unknown>;
  return String(metadata.__kind ?? (entry.set_path ? "set_pattern" : "strategy"));
}

function strategyName(entry: LibraryEntry): string {
  const metadata = entry.metadata as Record<string, unknown>;
  return String(metadata.name ?? entry.pattern_id);
}

function metricText(entry: LibraryEntry): string {
  const metadata = entry.metadata as Record<string, unknown>;
  const metrics = (metadata.metrics ?? metadata) as Record<string, unknown>;
  const pf = metrics.profit_factor ?? metrics.test_pf ?? metrics.ea_test_pf;
  const pass = metrics.challenge_active_pass_rate;
  const trades = metrics.trades ?? metrics.test_trades ?? metrics.ea_test_trades;
  const parts: string[] = [];
  if (typeof pf === "number") parts.push(`PF ${pf.toFixed(2)}`);
  if (typeof pass === "number") parts.push(`Pass ${(pass * 100).toFixed(1)}%`);
  if (typeof trades === "number") parts.push(`${Math.round(trades)} trades`);
  return parts.join(" · ") || "No metrics";
}

interface SavedStrategyPickerProps {
  entries: LibraryEntry[];
  selected: string[];
  onToggle: (patternId: string) => void;
  maxSelected?: number;
}

export default function SavedStrategyPicker({
  entries,
  selected,
  onToggle,
  maxSelected = 3,
}: SavedStrategyPickerProps) {
  return (
    <div className="saved-picker">
      {entries.length === 0 ? (
        <div className="empty-state">No saved strategies yet.</div>
      ) : (
        entries.map((entry) => {
          const checked = selected.includes(entry.pattern_id);
          const blocked = !checked && selected.length >= maxSelected;
          return (
            <label
              key={entry.pattern_id}
              className={`saved-picker-row${checked ? " selected" : ""}${blocked ? " blocked" : ""}`}
            >
              <input
                type="checkbox"
                checked={checked}
                disabled={blocked}
                onChange={() => onToggle(entry.pattern_id)}
              />
              <span className="saved-picker-main">
                <span className="saved-picker-title" title={entry.pattern_id}>
                  {strategyName(entry)}
                </span>
                <span className="saved-picker-meta">
                  {strategyKind(entry)} · {metricText(entry)}
                </span>
              </span>
            </label>
          );
        })
      )}
    </div>
  );
}
