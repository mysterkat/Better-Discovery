import { useState, useEffect } from "react";
import { exportMql, getTemplate, type MqlExportResult } from "../api/mql";
import { openFolder } from "../api/system";

export default function SetToMqlTab() {
  const [setContent, setSetContent] = useState("");
  const [templatePath, setTemplatePath] = useState<string>("");
  const [outputPath, setOutputPath] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<MqlExportResult | null>(null);

  useEffect(() => {
    getTemplate()
      .then((r) => setTemplatePath(r.path))
      .catch(() => {/* backend not ready yet */});
  }, []);

  const handleConvert = async () => {
    const content = setContent.trim();
    if (!content) return;
    setLoading(true);
    setError(null);
    setOutputPath(null);
    setReport(null);
    try {
      const result = await exportMql(content, templatePath || null);
      setOutputPath(result.path);
      setReport(result);
      if (result.missing_inputs?.length) {
        setError(`Missing inputs: ${result.missing_inputs.join(", ")}`);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const handleClear = () => {
    setSetContent("");
    setOutputPath(null);
    setReport(null);
    setError(null);
  };

  const loadSetFile = (file: File | null) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      setSetContent(String(reader.result ?? ""));
      setError(null);
    };
    reader.readAsText(file);
  };

  return (
    <div className="tab-content">
      <div className="tab-header">
        <h2>Set → MQL</h2>
        <p className="tab-subtitle">
          Convert a Pattern Discovery <code>.set</code> file into a ready-to-compile
          MetaTrader 5 Expert Advisor using the bundled PatternDiscoveryEA v3.03 template.
        </p>
      </div>

      {templatePath && (
        <div className="info-bar">
          <span className="info-label">Template:</span>
          <code className="info-path">{templatePath}</code>
        </div>
      )}

      <div className="form-section">
        <label className="field-label">Load <code>.set</code> file</label>
        <input
          type="file"
          accept=".set,text/plain"
          onChange={(e) => loadSetFile(e.target.files?.[0] ?? null)}
        />
      </div>

      <div className="form-section">
        <label className="field-label">
          Or paste <code>.set</code> file content
        </label>
        <textarea
          className="code-textarea"
          value={setContent}
          onChange={(e) => setSetContent(e.target.value)}
          placeholder={
            "; Pattern 1 — Cluster 3 [SHORT] [SHORT_ONLY]\n" +
            "; Train: WR=61.5%  Wilson=54.2%  PF=1.52  Score=0.74\n" +
            "; Test:  WR=58.3%  PF=1.41  Trades=24\n" +
            "; SL=0.522%  TP=0.363%  Implied RR=0.696\n" +
            "MagicNumber=10001\n" +
            "DirectionMode=1\n" +
            "rsi14_lo=35.0\n" +
            "rsi14_hi=65.0\n" +
            "trend_lo=1.0\n" +
            "trend_hi=1.0\n" +
            "..."
          }
          rows={14}
          spellCheck={false}
        />
      </div>

      <div className="action-row">
        <button
          className="btn btn-primary"
          onClick={handleConvert}
          disabled={loading || !setContent.trim()}
        >
          {loading ? "Converting…" : "⚙ Convert to .mq5"}
        </button>
        {(setContent || outputPath) && (
          <button className="btn btn-secondary" onClick={handleClear}>
            Clear
          </button>
        )}
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {outputPath && !error && (
        <div className="alert alert-success">
          <strong>✓ Conversion complete</strong>
          {report && (
            <p style={{ margin: "8px 0 4px" }}>
              Inputs: {report.inputs_present}/{report.inputs_required}
              {" · "}
              Commission_R: {report.has_commission_r ? "yes" : "NO"}
              {" · "}
              Swap_R_PerBar: {report.has_swap_r_per_bar ? "yes" : "NO"}
            </p>
          )}
          <p style={{ margin: "8px 0 4px" }}>Output saved to:</p>
          <code className="output-path-code">{outputPath}</code>
          <div style={{ marginTop: 10 }}>
            <button
              className="btn-mini"
              onClick={() => openFolder(outputPath).catch(() => {})}
              title="Reveal in file manager"
            >
              📂 Open folder
            </button>
          </div>
          <p className="hint" style={{ marginTop: 8 }}>
            Open in MetaTrader 5 MetaEditor and press <kbd>F7</kbd> to compile.
          </p>
        </div>
      )}
    </div>
  );
}
