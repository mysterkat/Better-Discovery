/**
 * ChartErrorBoundary
 *
 * Wrap the chart area so a runtime error inside Plotly (e.g. a future
 * version mismatch, an unexpected data shape, a WebGL context failure)
 * renders a visible message instead of unmounting silently — which is
 * how the previous react-plotly.js@2 + plotly.js@3 mismatch looked
 * to the user (charts simply never appeared, no error in the UI).
 *
 * Stay narrow: this is a pure React error boundary. No portals, no
 * effects. The fallback shows the error message and a hint about
 * checking the browser console for the full stack.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** Short label for the section being guarded — included in the fallback. */
  label?: string;
}

interface State {
  error: Error | null;
}

export default class ChartErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Log so the dev console + tauri stdout sees it. Production builds
    // strip console.* in some setups but Vite preserves console.error.
    console.error(`[ChartErrorBoundary${this.props.label ? ` :: ${this.props.label}` : ""}]`, error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="alert alert-error" style={{ marginTop: 12 }}>
          <strong>Chart failed to render.</strong>
          <p style={{ margin: "6px 0 0", fontSize: 12 }}>
            {this.props.label ? `${this.props.label}: ` : ""}
            {this.state.error.message || String(this.state.error)}
          </p>
          <p style={{ margin: "4px 0 0", fontSize: 11, opacity: 0.7 }}>
            Open the dev console (Ctrl+Shift+I) for the full stack trace.
          </p>
        </div>
      );
    }
    return this.props.children;
  }
}
