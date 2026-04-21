import { useState } from "react";
import FilePicker from "../components/FilePicker";
import { importCsv, type DataPreview } from "../api/data";

export default function DataImportTab() {
  const [path, setPath] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<DataPreview | null>(null);

  const handleImport = async () => {
    const trimmed = path.trim();
    if (!trimmed) return;
    setLoading(true);
    setError(null);
    setPreview(null);
    try {
      const result = await importCsv(trimmed);
      setPreview(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="tab-content">
      <div className="tab-header">
        <h2>Data Import</h2>
        <p className="tab-subtitle">
          Load historical trade data from a CSV file. The first recognised P&amp;L
          column is used as input for Monte Carlo simulations.
        </p>
      </div>

      <div className="form-section">
        <FilePicker
          label="CSV File Path"
          value={path}
          onChange={setPath}
          placeholder="C:\Users\…\trades.csv"
          hint="Paste or type the full path to your CSV file."
        />
        <div className="action-row">
          <button
            className="btn btn-primary"
            onClick={handleImport}
            disabled={loading || !path.trim()}
          >
            {loading ? "Loading…" : "↑ Import & Preview"}
          </button>
          {preview && (
            <button
              className="btn btn-secondary"
              onClick={() => { setPreview(null); setError(null); }}
            >
              Clear
            </button>
          )}
        </div>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {preview && (
        <div className="preview-section">
          <div className="stat-row">
            <div className="stat-card">
              <span className="stat-label">Rows</span>
              <span className="stat-value">{preview.n_rows.toLocaleString()}</span>
            </div>
            <div className="stat-card">
              <span className="stat-label">Columns</span>
              <span className="stat-value">{preview.columns.length}</span>
            </div>
          </div>

          <div className="section-label" style={{ marginTop: 16 }}>Columns</div>
          <div className="tag-row">
            {preview.columns.map((col) => (
              <span key={col} className="tag">{col}</span>
            ))}
          </div>

          {preview.sample.length > 0 && (
            <>
              <div className="section-label" style={{ marginTop: 16 }}>
                Sample ({preview.sample.length} rows)
              </div>
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      {preview.columns.map((col) => (
                        <th key={col}>{col}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {preview.sample.map((row, i) => (
                      <tr key={i}>
                        {preview.columns.map((col) => (
                          <td key={col}>{String(row[col] ?? "")}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
