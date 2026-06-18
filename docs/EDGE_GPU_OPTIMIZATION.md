# Edge, GPU, and Speed Notes

## What changed

- LightGBM candidate generation can now run on `cpu`, `gpu`, or `auto`.
- Discovery writes `performance_profile_seed*.json` next to each report with stage timings and the actual LightGBM device used.
- Enhanced research features now include causal cross-asset context when matching CSVs exist in `userdata/hist_data`.
- Discovery auto-selects timeframe slots from one main trading symbol so imported edge data such as DXY or XAGUSD does not become an EA signal timeframe by accident.

## GPU usage

Set `CLUSTERING_METHOD=lightgbm` and `LIGHTGBM_DEVICE=auto` to try the GPU path. `auto` attempts GPU first and falls back to CPU if the installed LightGBM build or OpenCL runtime cannot use the GPU.

Use `LIGHTGBM_DEVICE=gpu` only when debugging GPU setup, because that mode fails loudly instead of falling back.

The GPU path accelerates LightGBM training only. It does not move the full GA/backtest pipeline to VRAM. Use the profile JSON to verify whether LightGBM is actually a bottleneck before increasing tree counts.

## Edge data

When `USE_RESEARCH_FEATURES=true`, the engine looks for external symbol CSVs in the same folder as the trading data. By default it considers:

```text
DXY,XAGUSD,US500,VIX,US10Y,US02Y
```

For each external symbol, it adds:

- `xa_<symbol>_ret1`
- `xa_<symbol>_ret4`
- `xa_<symbol>_mom_z`
- `xa_<symbol>_rel_ret1`
- `xa_<symbol>_ratio_z`

The alignment is causal: external data is reindexed to the primary bar timeline and forward-filled, so a bar only sees external values available at or before that timestamp.

## Recommended research flow

1. Run a baseline with `USE_RESEARCH_FEATURES=false`.
2. Run the same seed/settings with `USE_RESEARCH_FEATURES=true`.
3. Compare `performance_profile_seed*.json`, EA-OOS PF, trade count, and stability.
4. Add one external data family at a time: DXY first, then XAGUSD, then indices/rates.
5. Treat improvement as real only when it survives multiple seeds and walk-forward splits.
