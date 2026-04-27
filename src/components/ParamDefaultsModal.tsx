/**
 * ParamDefaultsModal
 *
 * A full-screen overlay for editing persistent parameter defaults.
 * Changes are debounced-saved to userdata/param_defaults.json via the
 * paramDefaults Zustand store → PUT /param-defaults.
 *
 * When a field has a user-set default it appears filled-in.
 * The code-level default (from the backend modules) is shown as a grey hint.
 * The ↺ button resets a single field back to the code default.
 */

import { useEffect, useMemo, useState } from "react";
import { getParams, type ParamDef } from "../api/discovery";
import { getMcParams } from "../api/mc";
import { useParamDefaults, type ParamDefaultsStore } from "../state/paramDefaults";

// ── Which groups to expose per top-level tab ─────────────────────────────────

const DISCOVERY_GROUPS = [
  "SL / TP",
  "Genetic Pass 1",
  "Genetic Pass 2",
  "Ensemble",
  "Bidirectional",
  "Scoring",
  "Quality Filters",
  "MC Auto-run",
];

const MC_GROUPS = [
  "Simulation",
  "Phase 1",
  "Phase 2",
  "Funded",
  "Long-term",
];

type TopTab = "discovery" | "mc";

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function ParamDefaultsModal({ open, onClose }: Props) {
  const [discParams, setDiscParams] = useState<ParamDef[]>([]);
  const [mcParams, setMcParams] = useState<ParamDef[]>([]);
  const [topTab, setTopTab] = useState<TopTab>("discovery");
  const [openGroups, setOpenGroups] = useState<Set<string>>(
    new Set(["SL / TP", "Simulation"]),
  );

  const store = useParamDefaults();

  // Load param metadata once when the modal opens for the first time.
  useEffect(() => {
    if (!open) return;
    if (discParams.length === 0) getParams().then(setDiscParams).catch(() => {});
    if (mcParams.length === 0) getMcParams().then(setMcParams).catch(() => {});
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  // Build group → params maps for each top-level tab.
  const discGroups = useMemo(() => buildGroupMap(discParams, DISCOVERY_GROUPS), [discParams]);
  const mcGroupMap = useMemo(() => buildGroupMap(mcParams, MC_GROUPS), [mcParams]);

  if (!open) return null;

  const activeGroups = topTab === "discovery" ? DISCOVERY_GROUPS : MC_GROUPS;
  const activeMap = topTab === "discovery" ? discGroups : mcGroupMap;

  const toggleGroup = (g: string) =>
    setOpenGroups((prev) => {
      const next = new Set(prev);
      if (next.has(g)) next.delete(g);
      else next.add(g);
      return next;
    });

  const handleBackdropClick = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget) onClose();
  };

  return (
    <div className="pd-backdrop" onClick={handleBackdropClick} role="dialog" aria-modal="true">
      <div className="pd-modal">
        {/* ── Header ── */}
        <div className="pd-header">
          <div>
            <h2 className="pd-title">Parameter Defaults</h2>
            <p className="pd-subtitle">
              These values pre-fill every new run. Override per-run in the Discovery / MC tabs.
            </p>
          </div>
          <button className="pd-close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        {/* ── Top-level tab bar ── */}
        <div className="pd-toptabs">
          <button
            className={`pd-toptab${topTab === "discovery" ? " active" : ""}`}
            onClick={() => setTopTab("discovery")}
          >
            Discovery
          </button>
          <button
            className={`pd-toptab${topTab === "mc" ? " active" : ""}`}
            onClick={() => setTopTab("mc")}
          >
            MC Sim
          </button>
        </div>

        {/* ── Body: accordion groups ── */}
        <div className="pd-body">
          {activeGroups.map((groupName) => {
            const groupParams = activeMap.get(groupName) ?? [];
            if (groupParams.length === 0) return null;
            const isOpen = openGroups.has(groupName);
            const modifiedCount = groupParams.filter(
              (p) => p.key in store.defaults,
            ).length;

            return (
              <div key={groupName} className="param-group">
                <button
                  className="param-group-header"
                  onClick={() => toggleGroup(groupName)}
                >
                  <span className="param-group-arrow">{isOpen ? "▾" : "▸"}</span>
                  <span>{groupName}</span>
                  {modifiedCount > 0 && (
                    <span
                      className="pd-modified-badge"
                      title={`${modifiedCount} value${modifiedCount > 1 ? "s" : ""} with custom defaults`}
                    >
                      {modifiedCount}
                    </span>
                  )}
                  <span className="param-group-count">{groupParams.length} params</span>
                </button>

                {isOpen && (
                  <div className="param-group-body">
                    {groupParams.map((p) => renderField(p, store))}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* ── Footer ── */}
        <div className="pd-footer">
          <span className="pd-footer-hint">
            {store.loaded ? "Changes are saved automatically." : "Loading…"}
          </span>
          <button className="btn-secondary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function buildGroupMap(
  params: ParamDef[],
  allowedGroups: string[],
): Map<string, ParamDef[]> {
  const map = new Map<string, ParamDef[]>(allowedGroups.map((g) => [g, []]));
  for (const p of params) {
    if (map.has(p.group)) map.get(p.group)!.push(p);
  }
  return map;
}

function renderField(p: ParamDef, store: ParamDefaultsStore) {
  const storedVal = store.defaults[p.key];
  const hasCustom = p.key in store.defaults;
  const codeDefault = p.value != null ? String(p.value) : "";

  const handleReset = (e: React.MouseEvent) => {
    e.stopPropagation();
    store.reset(p.key);
  };

  if (p.type === "bool") {
    const checked =
      storedVal !== undefined ? Boolean(storedVal) : Boolean(p.value);
    return (
      <div key={p.key} className="field field-inline pd-field">
        <label className="toggle-label">
          <span className="toggle-wrap">
            <input
              type="checkbox"
              className="toggle-input"
              checked={checked}
              onChange={(e) => store.set(p.key, e.target.checked)}
            />
            <span className="toggle-track" />
          </span>
          <span>
            <span className="field-label" style={{ display: "inline" }}>
              {p.label}
            </span>
            {p.description && (
              <span className="field-hint"> — {p.description}</span>
            )}
          </span>
        </label>
        {hasCustom && (
          <button className="pd-reset-btn" onClick={handleReset} title="Reset to code default">
            ↺
          </button>
        )}
      </div>
    );
  }

  if (p.type === "str" && p.options && p.options.length > 0) {
    const current = storedVal != null ? String(storedVal) : codeDefault;
    return (
      <div key={p.key} className="field pd-field">
        <div className="pd-field-row">
          <label className="field-label">{p.label}</label>
          {hasCustom && (
            <button className="pd-reset-btn" onClick={handleReset} title="Reset to code default">
              ↺
            </button>
          )}
        </div>
        <select
          className="field-input"
          value={current}
          onChange={(e) => store.set(p.key, e.target.value)}
        >
          {p.options.map((o) => (
            <option key={o} value={o}>{o}</option>
          ))}
        </select>
        <span className="pd-code-hint">code default: {codeDefault}</span>
      </div>
    );
  }

  // int / float / str (free text)
  const currentStr = storedVal != null ? String(storedVal) : "";
  const hint = [
    p.min != null ? `min ${p.min}` : "",
    p.max != null ? `max ${p.max}` : "",
    p.step != null ? `step ${p.step}` : "",
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <div key={p.key} className="field pd-field">
      <div className="pd-field-row">
        <label className="field-label">{p.label}</label>
        {hasCustom && (
          <button className="pd-reset-btn" onClick={handleReset} title="Reset to code default">
            ↺
          </button>
        )}
      </div>
      <input
        className={`field-input${hasCustom ? " pd-input-custom" : ""}`}
        type={p.type === "int" ? "number" : "text"}
        step={p.step ?? (p.type === "float" ? 0.01 : 1)}
        min={p.min}
        max={p.max}
        value={currentStr}
        placeholder={codeDefault}
        onChange={(e) => {
          const raw = e.target.value;
          if (raw === "") {
            store.reset(p.key);
            return;
          }
          if (p.type === "int") {
            const n = parseInt(raw, 10);
            if (!isNaN(n)) store.set(p.key, n);
          } else if (p.type === "float") {
            const n = parseFloat(raw);
            if (!isNaN(n)) store.set(p.key, n);
          } else {
            store.set(p.key, raw);
          }
        }}
      />
      <span className="pd-code-hint">
        code default: {codeDefault}
        {hint ? ` · ${hint}` : ""}
        {p.description ? ` · ${p.description}` : ""}
      </span>
    </div>
  );
}
