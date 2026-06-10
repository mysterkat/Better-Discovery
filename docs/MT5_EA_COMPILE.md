# Compile a pattern EA (.set → .mq5) — step by step

`pattern_01_C01_LONG.mq5` is **not** the template. It is a **generated** file built by merging your `.set` with `backend/ea/PatternDiscoveryEA.mq5`. Compiling only the template in MetaEditor does **not** update the pattern file.

## What causes `undeclared identifier 'Commission_R'`

The EA **body** uses `Commission_R` and `Swap_R_PerBar`. Those names must appear as `input double ...` lines in the **generated** `.mq5`. If you compile an old copy in MetaTrader’s `Experts` folder (from before the converter fix), you will see errors on the lines that reference those variables.

The fixed source in this repo is:

`C:\Users\micha\Desktop\BETTER DISCOVERY\userdata\mql\pattern_01_C01_LONG.mq5`

(lines ~108–109 should contain `input double Commission_R` and `input double Swap_R_PerBar`).

---

## Option A — BETTER DISCOVERY app (Set → MQL tab)

1. **Quit** the BETTER DISCOVERY app completely (so the Python backend restarts with the latest code).
2. **Start** the app again from this project folder.
3. Open the **Set → MQL** tab (not “MQL → Set”; direction is **.set → .mq5**).
4. Open your pattern `.set` in Notepad (usually under `userdata\discovery\...\pattern_01_C01_LONG.set` or similar).
5. **Select all** → **Copy** → paste into the big text box on Set → MQL.
6. Click **Convert to .mq5**.
7. Note the output path shown (typically `userdata\mql\pattern_01_C01_LONG.mq5`).
8. Continue with **Copy into MetaTrader** below.

## Option B — Command line (no UI)

From the project root in PowerShell:

```powershell
cd "C:\Users\micha\Desktop\BETTER DISCOVERY"
python backend\tools\convert_set_to_mql.py "C:\full\path\to\pattern_01_C01_LONG.set" -o pattern_01_C01_LONG
```

Use the real path to your `.set` file. On success it prints the full path to the new `.mq5`.

---

## Copy into MetaTrader (required)

MetaEditor compiles whatever file is under **your terminal’s** `MQL5\Experts\` tree — not the copy in `userdata\mql` unless you copy it there.

1. In MetaEditor or Explorer, find your terminal folder, e.g.  
   `C:\Users\<you>\AppData\Roaming\MetaQuotes\Terminal\<ID>\MQL5\Experts\`
2. **Delete** or overwrite the old `pattern_01_C01_LONG.mq5` (and `.ex5` if present) in that folder.
3. Copy the **new** file from:  
   `C:\Users\micha\Desktop\BETTER DISCOVERY\userdata\mql\pattern_01_C01_LONG.mq5`  
   into that `Experts` folder (or a subfolder you use).
4. In MetaEditor, open the **copied** file (check the path in the tab title).
5. Press **F7** to compile.

## Quick check before F7

In MetaEditor, press **Ctrl+F** and search for:

`// @BD_INPUT_END`

A few lines **below** that marker you must see:

```mql5
input double Commission_R        = ...
input double Swap_R_PerBar       = ...
```

Those lines sit **outside** the converter replace zone (around line 255+ in current exports). If they are missing, you are still on an old file — repeat Option A or B and copy again.

**Compile `PatternDiscoveryEA.mq5` from the repo** (`backend\ea\`) after pulling this fix — it should compile without Set→MQL at all.

## What does *not* fix this

| Action | Why it fails |
|--------|----------------|
| Compile `PatternDiscoveryEA.mq5` only | That is the template; `pattern_01_C01_LONG.mq5` is separate. |
| Load the `.set` in Strategy Tester | `.set` only sets tester inputs; it does not add missing `input` lines to source. |
| Re-export discovery `.set` only | You still must run **Set → MQL** to regenerate the `.mq5`. |

---

## Still failing?

1. Confirm the tab path in MetaEditor matches the file you copied from `userdata\mql`.
2. Restart the BETTER DISCOVERY app and convert again (backend must load updated `set_to_mql.py`).
3. Run the CLI command above; if it errors, paste the message into an issue.
