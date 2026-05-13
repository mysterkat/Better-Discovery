/**
 * Drag-and-drop file selector themed via app CSS variables.
 *
 * Tauri 2: real filesystem paths arrive via the webview's
 * `tauri://drag-drop` event — `getCurrentWebview().onDragDropEvent(...)`.
 * Standard browsers (vite dev outside Tauri) only expose File objects
 * without an absolute path; we fall back to the `name` field there so the
 * UI still renders, but the backend won't resolve a name-only path.
 *
 * The component is purely visual — the parent owns the path string.
 */

import { useEffect, useRef, useState } from "react";

export interface FileDropZoneProps {
  /** Current absolute path (or empty when nothing selected). */
  value: string;
  /** Called when the user drops a file or browses to one. */
  onChange: (path: string) => void;
  /** Visible label above the dropzone. */
  label: string;
  /** Helper text under the dropzone. */
  hint?: string;
  /** File-type description (e.g. ".csv", ".html"). Used in the empty state. */
  accept?: string;
  /** When true, the dropzone is disabled (e.g. job in progress). */
  disabled?: boolean;
}

export default function FileDropZone({
  value, onChange, label, hint, accept, disabled,
}: FileDropZoneProps) {
  const [hover, setHover] = useState(false);
  const zoneRef = useRef<HTMLDivElement | null>(null);

  // Tauri-specific: register a webview-level drag/drop listener so we can
  // access real filesystem paths (browser DataTransfer hides them).
  useEffect(() => {
    if (disabled) return;
    let unlisten: (() => void) | null = null;
    let cancelled = false;

    (async () => {
      try {
        const { getCurrentWebview } = await import("@tauri-apps/api/webview");
        const w = getCurrentWebview();
        const off = await w.onDragDropEvent((event) => {
          // Only react when the cursor is actually over our zone.
          if (!zoneRef.current) return;
          const rect = zoneRef.current.getBoundingClientRect();
          // Tauri reports physical pixels; we approximate with logical via DPR.
          const dpr = window.devicePixelRatio || 1;
          const px  = (event.payload as { position?: { x: number; y: number } }).position?.x;
          const py  = (event.payload as { position?: { x: number; y: number } }).position?.y;
          const xLogical = px != null ? px / dpr : null;
          const yLogical = py != null ? py / dpr : null;
          const inside =
            xLogical != null && yLogical != null &&
            xLogical >= rect.left && xLogical <= rect.right &&
            yLogical >= rect.top  && yLogical <= rect.bottom;

          if (event.payload.type === "over") {
            setHover(inside);
          } else if (event.payload.type === "drop") {
            setHover(false);
            if (!inside) return;
            const paths = (event.payload as { paths?: string[] }).paths ?? [];
            const first = paths[0];
            if (first) onChange(first);
          } else if (event.payload.type === "leave") {
            setHover(false);
          }
        });
        if (cancelled) { off(); return; }
        unlisten = off;
      } catch {
        // Not in a Tauri context — fall back to HTML5 events handled in JSX.
      }
    })();

    return () => {
      cancelled = true;
      if (unlisten) unlisten();
    };
  }, [disabled, onChange]);

  const fileName = value ? value.split(/[\\/]/).pop() : "";

  return (
    <div className="dropzone-field">
      <label className="field-label">{label}</label>
      <div
        ref={zoneRef}
        className={`dropzone${hover ? " dropzone-hover" : ""}${value ? " dropzone-filled" : ""}${disabled ? " dropzone-disabled" : ""}`}
        // HTML5 fallback for plain browser dev — won't get a real path,
        // but Tauri overrides via the listener above.
        onDragOver={(e) => { e.preventDefault(); if (!disabled) setHover(true); }}
        onDragLeave={() => setHover(false)}
        onDrop={(e) => {
          e.preventDefault();
          setHover(false);
          if (disabled) return;
          const f = e.dataTransfer.files[0];
          if (f) {
            const maybePath = (f as File & { path?: string }).path;
            onChange(maybePath || f.name);
          }
        }}
      >
        {value ? (
          <div className="dropzone-filled-row">
            <div className="dropzone-file-icon">📄</div>
            <div className="dropzone-file-meta">
              <div className="dropzone-file-name">{fileName}</div>
              <div className="dropzone-file-path" title={value}>{value}</div>
            </div>
            <button type="button"
              className="dropzone-clear"
              onClick={() => onChange("")}
              disabled={disabled}
              title="Remove file"
            >×</button>
          </div>
        ) : (
          <div className="dropzone-empty">
            <div className="dropzone-empty-icon">⤓</div>
            <div className="dropzone-empty-title">Drop {accept ?? "file"} here</div>
            <div className="dropzone-empty-sub">or paste the absolute path below</div>
          </div>
        )}
      </div>
      {!value && (
        <input
          type="text"
          className="field-input dropzone-fallback-input"
          placeholder={`C:\\…${accept ? `\\${accept.replace(".", "")}` : ""}`}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
      {hint && <span className="field-hint">{hint}</span>}
    </div>
  );
}
