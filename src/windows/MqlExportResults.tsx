/**
 * MQL Export results window.
 * Loaded when URL has ?window=mql-results&path=…&name=…
 * (The conversion is synchronous so there is no job; params are passed directly.)
 */

import { useEffect } from "react";
import { useSettings } from "../state/settings";

export default function MqlExportResults() {
  const params = new URLSearchParams(window.location.search);
  const filePath = decodeURIComponent(params.get("path") ?? "");
  const fileName = decodeURIComponent(params.get("name") ?? "");

  const loadSettings = useSettings((s) => s.load);
  useEffect(() => { loadSettings(); }, [loadSettings]);

  return (
    <div className="results-window">
      <div className="results-header">
        <h1>MQL Export Complete</h1>
      </div>

      {filePath ? (
        <div className="alert alert-success">
          <strong>✓ Ready to compile</strong>
          {fileName && <p style={{ margin: "8px 0 2px" }}>File: <strong>{fileName}</strong></p>}
          <p style={{ margin: "8px 0 4px" }}>Saved to:</p>
          <code className="output-path-code">{filePath}</code>
          <p className="hint" style={{ marginTop: 10 }}>
            Open in MetaTrader 5 MetaEditor and press <kbd>F7</kbd> to compile.
          </p>
        </div>
      ) : (
        <div className="alert alert-warn">No file path provided.</div>
      )}
    </div>
  );
}
