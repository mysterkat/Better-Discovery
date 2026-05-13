/**
 * Shared Plotly component for all chart files in this folder.
 *
 * Why this exists:
 *   - `react-plotly.js`'s default export internally `import`s the full
 *     `plotly.js` package, which bundles ~4 MB of code AND mismatches
 *     the v3 API our older `react-plotly.js@2.6.0` was written against.
 *   - Importing the factory directly + a v2 plotly distribution gives us:
 *       1. ONE plotly bundle for the whole window (no duplicate parses
 *          across the five lazy-loaded 3-D chart chunks),
 *       2. a version pair that's actually tested together,
 *       3. a smaller, pre-built dist (`plotly.js-dist-min`).
 *
 * Every chart file should `import Plot from "./plotly"` instead of
 * `import Plot from "react-plotly.js"`.
 */

// `plotly.js-dist-min` ships a single CJS bundle with no Node-only deps —
// safe to import directly into the Vite/Tauri renderer. The `@ts-ignore` is
// because @types/plotly.js-dist-min sometimes lags behind the runtime types.
// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore — typing comes from @types/plotly.js for the public API surface.
import Plotly from "plotly.js-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";

const Plot = createPlotlyComponent(Plotly);

export default Plot;
export { Plotly };
