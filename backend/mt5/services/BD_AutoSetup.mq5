//+------------------------------------------------------------------+
//|  BD_AutoSetup.mq5                                                 |
//|  BETTER DISCOVERY auto-chart helper.                              |
//|                                                                  |
//|  WHAT IT DOES                                                     |
//|  -----------                                                     |
//|  Watches the file `bd_setup.json` inside MT5's terminal_common    |
//|  Files folder. Whenever the file's `version` field changes, it    |
//|  opens a chart for every (symbol, timeframe) listed in the JSON,  |
//|  attaches the BETTER DISCOVERY indicator stack to each chart, and |
//|  writes `bd_setup_ack.json` back so the host app can confirm.     |
//|                                                                  |
//|  EXPECTED bd_setup.json SCHEMA                                    |
//|  ------------------------------                                  |
//|  {                                                               |
//|    "version": <int, monotonically increasing>,                    |
//|    "symbol":  "XAUUSD",                                          |
//|    "timeframes": ["M5", "M15", "H1"],                             |
//|    "indicators": ["BD_PinBar", "BD_RollingSharpe", ...],          |
//|    "htf_for_div": "M15"                                          |
//|  }                                                               |
//|                                                                  |
//|  ACK FILE bd_setup_ack.json                                      |
//|  --------------------------                                      |
//|  {                                                               |
//|    "version_acked": <echoes input version>,                       |
//|    "timestamp": <unix seconds>,                                   |
//|    "opened":     [{"symbol":"XAUUSD","timeframe":"M5","ok":true}],|
//|    "errors":     ["..."]                                         |
//|  }                                                               |
//|                                                                  |
//|  USAGE                                                            |
//|  -----                                                           |
//|  Drag onto any single chart once. Allow algorithmic trading       |
//|  ("AutoTrading" button). Leave it running — the host app drives.  |
//+------------------------------------------------------------------+
#property copyright "BETTER DISCOVERY v0.7"
#property version   "1.00"
#property strict

input int    InpPollIntervalMs = 1000;   // How often to re-stat the JSON file
input string InpConfigName     = "bd_setup.json";
input string InpAckName        = "bd_setup_ack.json";

// Folder under MQL5/Indicators/ where our compiled .ex5 indicators live.
// Must match BackendInstaller paths in src-tauri.
const string BD_IND_DIR = "BetterDiscovery";

// All 12 indicator stems (no prefix path, no .ex5 — `IndicatorCreate` adds those)
string ALL_INDICATORS[] = {
   "BD_PinBar",
   "BD_RollingSharpe",
   "BD_MacdNorm",
   "BD_VwapDist",
   "BD_SDZone",
   "BD_VolPriceDiv",
   "BD_BBExpanding",
   "BD_PrevSessBias",
   "BD_POCdist",
   "BD_Regime",
   "BD_HtfDiv",
   "BD_MtfBullScore"
};

int   g_lastVersion = -1;
ulong g_lastPollMs  = 0;

//+------------------------------------------------------------------+
int OnInit()
  {
   EventSetMillisecondTimer(MathMax(200, InpPollIntervalMs));
   PrintFormat("BD_AutoSetup ready. Watching %s in terminal_common\\Files\\.", InpConfigName);
   return INIT_SUCCEEDED;
  }
void OnDeinit(const int reason) { EventKillTimer(); }
void OnTick() { /* no per-tick work */ }

void OnTimer()
  {
   ulong now = GetTickCount64();
   if(now - g_lastPollMs < (ulong)InpPollIntervalMs) return;
   g_lastPollMs = now;
   ProcessConfig();
  }

//+------------------------------------------------------------------+
//| Read the JSON, dispatch on version change                        |
//+------------------------------------------------------------------+
void ProcessConfig()
  {
   string text = ReadCommonFile(InpConfigName);
   if(text == "") return;

   int version = (int)JsonInt(text, "version", -1);
   if(version <= g_lastVersion) return;

   // Action dispatcher — default is "setup" (back-compat with v0.7.0 JSON
   // that has no action field). Supported actions:
   //   "setup"          — open charts, attach indicators  (default)
   //   "dump_features"  — write the 27-feature CSV for one symbol+TF
   string action = JsonStr(text, "action", "setup");

   if(action == "dump_features")
     {
      DispatchDump(text, version);
      g_lastVersion = version;
      return;
     }

   string symbol = JsonStr(text, "symbol", _Symbol);
   string tfs    = JsonStrArrayJoin(text, "timeframes", ",", "M5");
   string inds   = JsonStrArrayJoin(text, "indicators", ",", "");
   string htfDiv = JsonStr(text, "htf_for_div", "M15");

   PrintFormat("BD_AutoSetup: applying version=%d symbol=%s tfs=[%s] htfDiv=%s",
               version, symbol, tfs, htfDiv);

   string opened = "";
   string errors = "";
   string parts[];
   int n = StringSplit(tfs, ',', parts);
   for(int i = 0; i < n; i++)
     {
      ENUM_TIMEFRAMES tf = StrToTimeframe(parts[i]);
      if(tf == PERIOD_CURRENT) { errors += StringFormat("\"unknown TF '%s'\",", parts[i]); continue; }
      long chartId = OpenOrFocus(symbol, tf);
      if(chartId == 0) { errors += StringFormat("\"failed to open %s/%s\",", symbol, parts[i]); continue; }
      AttachIndicators(chartId, htfDiv, inds);
      opened += StringFormat("{\"symbol\":\"%s\",\"timeframe\":\"%s\",\"chart_id\":%I64d,\"ok\":true},",
                             symbol, parts[i], chartId);
     }

   WriteAck(version, opened, errors);
   g_lastVersion = version;
  }

//+------------------------------------------------------------------+
//| Dispatcher for the dump_features action                          |
//+------------------------------------------------------------------+
void DispatchDump(const string text, int version)
  {
   string symbol     = JsonStr(text, "symbol", _Symbol);
   string tfStr      = JsonStr(text, "timeframe", "M5");
   string outFile    = JsonStr(text, "out_file", "bd_feature_dump.csv");
   int    nBars      = (int)JsonInt(text, "n_bars", 5000);
   string htfDiv     = JsonStr(text, "htf_for_div", "M15");
   string mtf1       = JsonStr(text, "mtf_tf1", "M15");
   string mtf2       = JsonStr(text, "mtf_tf2", "H1");

   ENUM_TIMEFRAMES tf       = StrToTimeframe(tfStr);
   ENUM_TIMEFRAMES tfHtfDiv = StrToTimeframe(htfDiv);
   ENUM_TIMEFRAMES tfMtf1   = StrToTimeframe(mtf1);
   ENUM_TIMEFRAMES tfMtf2   = StrToTimeframe(mtf2);

   if(tf == PERIOD_CURRENT) tf = _Period;
   if(tfHtfDiv == PERIOD_CURRENT) tfHtfDiv = PERIOD_M15;
   PrintFormat("BD_AutoSetup: dump version=%d sym=%s tf=%s out=%s n=%d htfDiv=%s mtf=[%s,%s]",
               version, symbol, tfStr, outFile, nBars, htfDiv, mtf1, mtf2);

   string err = "";
   bool ok = DumpFeatures(symbol, tf, nBars, outFile, tfHtfDiv, tfMtf1, tfMtf2, err);

   string ackOpened = ok
      ? StringFormat("{\"action\":\"dump_features\",\"out_file\":\"%s\",\"n_bars\":%d,\"ok\":true}", outFile, nBars)
      : "";
   string ackErrors = ok ? "" : StringFormat("\"%s\",", err);
   WriteAck(version, ackOpened, ackErrors);
  }

//+------------------------------------------------------------------+
//| Feature CSV dumper — lifted from BD_FeatureDump.mq5, parametric  |
//+------------------------------------------------------------------+
bool DumpFeatures(const string symbol, ENUM_TIMEFRAMES tf, int nBars, const string outFile,
                  ENUM_TIMEFRAMES tfHtfDiv, ENUM_TIMEFRAMES tfMtf1, ENUM_TIMEFRAMES tfMtf2,
                  string &err)
  {
   // Standard built-in handles
   int hRSI    = iRSI    (symbol, tf, 14, PRICE_CLOSE);
   int hATR    = iATR    (symbol, tf, 14);
   int hBB     = iBands  (symbol, tf, 20, 0, 2.0, PRICE_CLOSE);
   int hEMA20  = iMA     (symbol, tf, 20,  0, MODE_EMA, PRICE_CLOSE);
   int hEMA50  = iMA     (symbol, tf, 50,  0, MODE_EMA, PRICE_CLOSE);
   int hEMA200 = iMA     (symbol, tf, 200, 0, MODE_EMA, PRICE_CLOSE);
   int hStoch  = iStochastic(symbol, tf, 14, 3, 1, MODE_SMA, STO_LOWHIGH);
   if(hRSI==INVALID_HANDLE || hATR==INVALID_HANDLE || hBB==INVALID_HANDLE)
     { err = "failed to create standard handles"; return false; }

   // BD custom handles — order matters for the lookup below
   string BD_NAMES[] = {
      "BD_RollingSharpe","BD_MacdNorm","BD_VwapDist","BD_SDZone",
      "BD_VolPriceDiv","BD_BBExpanding","BD_POCdist","BD_PinBar",
      "BD_PrevSessBias","BD_Regime","BD_HtfDiv","BD_MtfBullScore"
   };
   int bd[12]; for(int i=0;i<12;i++) bd[i] = INVALID_HANDLE;
   for(int i = 0; i < 12; i++)
     {
      string nm = BD_NAMES[i];
      MqlParam p[]; ArrayResize(p, 1);
      p[0].type = TYPE_STRING; p[0].string_value = BD_IND_DIR + "\\" + nm;
      if(nm == "BD_HtfDiv")
        { ArrayResize(p, 5);
          p[1].type = TYPE_INT; p[1].integer_value = (int)tfHtfDiv;
          p[2].type = TYPE_INT; p[2].integer_value = 14;
          p[3].type = TYPE_INT; p[3].integer_value = 5;
          p[4].type = TYPE_INT; p[4].integer_value = 3; }
      else if(nm == "BD_MtfBullScore")
        { ArrayResize(p, 5);
          p[1].type = TYPE_INT; p[1].integer_value = (int)tfMtf1;
          p[2].type = TYPE_INT; p[2].integer_value = (int)tfMtf2;
          p[3].type = TYPE_INT; p[3].integer_value = (int)PERIOD_CURRENT;
          p[4].type = TYPE_INT; p[4].integer_value = (int)PERIOD_CURRENT; }
      bd[i] = IndicatorCreate(symbol, tf, IND_CUSTOM, ArraySize(p), p);
      if(bd[i] == INVALID_HANDLE)
        { err = StringFormat("IndicatorCreate failed for %s", nm); return false; }
     }

   // Wait for warmup (up to 10s)
   ulong t0 = GetTickCount64();
   int  warmupTarget = nBars + 50;
   while(GetTickCount64() - t0 < 10000)
     {
      bool ready = (BarsCalculated(hRSI) >= warmupTarget) &&
                   (BarsCalculated(hEMA200) >= warmupTarget) &&
                   (BarsCalculated(hATR) >= warmupTarget);
      for(int i = 0; ready && i < 12; i++)
         if(BarsCalculated(bd[i]) < warmupTarget) ready = false;
      if(ready) break;
      Sleep(200);
     }

   int f = FileOpen(outFile, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(f == INVALID_HANDLE)
     { err = StringFormat("FileOpen failed: %d", GetLastError()); goto release; }

   FileWriteString(f,
       "time,open,high,low,close,"
       "rsi14,macd_norm,atr_pct,bb_width,trend,mtf_bull_score,"
       "body_pct,rng_atr,vol_ratio,vol_body_conf,regime,vol_price_div,bb_expanding,"
       "prev_sess_bias,poc_dist,bull,uwk_pct,lwk_pct,"
       "stoch_k,stoch_d,pin_bar,inside_bar,outside_bar,htf_div,"
       "rolling_sharpe,sd_zone,vwap_dist\n");

   int totalBars = Bars(symbol, tf);
   int n = MathMin(nBars, totalBars - 220);
   for(int s = n; s >= 1; s--)
     {
      datetime t = iTime(symbol, tf, s);
      double   o = iOpen(symbol, tf, s);
      double   h = iHigh(symbol, tf, s);
      double   l = iLow (symbol, tf, s);
      double   c = iClose(symbol, tf, s);

      double v[1];
      double rsi14 = (CopyBuffer(hRSI, 0, s, 1, v) == 1) ? v[0] : 0;
      double atr   = (CopyBuffer(hATR, 0, s, 1, v) == 1) ? v[0] : 0;
      double bbm   = (CopyBuffer(hBB,  0, s, 1, v) == 1) ? v[0] : 0;
      double bbu   = (CopyBuffer(hBB,  1, s, 1, v) == 1) ? v[0] : 0;
      double bbl   = (CopyBuffer(hBB,  2, s, 1, v) == 1) ? v[0] : 0;
      double e20   = (CopyBuffer(hEMA20,  0, s, 1, v) == 1) ? v[0] : 0;
      double e50   = (CopyBuffer(hEMA50,  0, s, 1, v) == 1) ? v[0] : 0;
      double e200  = (CopyBuffer(hEMA200, 0, s, 1, v) == 1) ? v[0] : 0;
      double stk   = (CopyBuffer(hStoch, 0, s, 1, v) == 1) ? v[0] : 0;
      double std_  = (CopyBuffer(hStoch, 1, s, 1, v) == 1) ? v[0] : 0;

      double atrPct  = (c > 0) ? atr/c : 0;
      double bbWidth = (bbm > 0) ? (bbu-bbl)/bbm : 0;
      double trend   = (e20>e50 && e50>e200) ? 1.0 : (e20<e50 && e50<e200 ? -1.0 : 0.0);
      double rng     = h - l;
      double body    = MathAbs(c - o);
      double uwk     = h - MathMax(o, c);
      double lwk     = MathMin(o, c) - l;
      double bull    = (c >= o) ? 1.0 : 0.0;
      double rngAtr  = (atr > 0) ? rng/atr : 0;
      double bodyPct = (rng > 0) ? body/rng : 0;
      double uwkPct  = (rng > 0) ? uwk/rng  : 0;
      double lwkPct  = (rng > 0) ? lwk/rng  : 0;
      double inside  = (h < iHigh(symbol,tf,s+1) && l > iLow(symbol,tf,s+1)) ? 1.0 : 0.0;
      double outside = (h > iHigh(symbol,tf,s+1) && l < iLow(symbol,tf,s+1)) ? 1.0 : 0.0;

      double rs   = (CopyBuffer(bd[0],  0, s, 1, v) == 1) ? v[0] : 0;
      double mn   = (CopyBuffer(bd[1],  0, s, 1, v) == 1) ? v[0] : 0;
      double vwd  = (CopyBuffer(bd[2],  0, s, 1, v) == 1) ? v[0] : 0;
      double sdz  = (CopyBuffer(bd[3],  0, s, 1, v) == 1) ? v[0] : 0;
      double vpd  = (CopyBuffer(bd[4],  0, s, 1, v) == 1) ? v[0] : 0;
      double bbe  = (CopyBuffer(bd[5],  0, s, 1, v) == 1) ? v[0] : 0;
      double pocd = (CopyBuffer(bd[6],  0, s, 1, v) == 1) ? v[0] : 0;
      double pin  = (CopyBuffer(bd[7],  0, s, 1, v) == 1) ? v[0] : 0;
      double psb  = (CopyBuffer(bd[8],  0, s, 1, v) == 1) ? v[0] : 0;
      double reg  = (CopyBuffer(bd[9],  0, s, 1, v) == 1) ? v[0] : 0;
      double hd   = (CopyBuffer(bd[10], 0, s, 1, v) == 1) ? v[0] : 0;
      double mtfb = (CopyBuffer(bd[11], 0, s, 1, v) == 1) ? v[0] : 0;

      // vol_ratio + vol_body_conf computed inline
      double vsum = 0;
      for(int k=1;k<=20;k++) vsum += (double)iTickVolume(symbol,tf,s+k);
      double vma  = vsum / 20.0;
      double vr   = (vma > 0) ? MathMin((double)iTickVolume(symbol,tf,s) / vma, 5.0) : 0.0;
      double vbc  = MathMin(vr * bodyPct, 5.0);

      string line = StringFormat(
         "%s,%.5f,%.5f,%.5f,%.5f,"
         "%.6f,%.6f,%.6f,%.6f,%.0f,%.0f,"
         "%.6f,%.6f,%.6f,%.6f,%.0f,%.0f,%.0f,"
         "%.0f,%.6f,%.0f,%.6f,%.6f,"
         "%.4f,%.4f,%.6f,%.0f,%.0f,%.0f,"
         "%.6f,%.0f,%.6f\n",
         TimeToString(t, TIME_DATE | TIME_MINUTES | TIME_SECONDS),
         o, h, l, c,
         rsi14, mn, atrPct, bbWidth, trend, mtfb,
         bodyPct, rngAtr, vr, vbc, reg, vpd, bbe,
         psb, pocd, bull, uwkPct, lwkPct,
         stk, std_, pin, inside, outside, hd,
         rs, sdz, vwd);
      FileWriteString(f, line);
     }
   FileClose(f);
   PrintFormat("BD_AutoSetup: dumped %d bars to %s", n, outFile);

release:
   IndicatorRelease(hRSI); IndicatorRelease(hATR); IndicatorRelease(hBB);
   IndicatorRelease(hEMA20); IndicatorRelease(hEMA50); IndicatorRelease(hEMA200);
   IndicatorRelease(hStoch);
   for(int i = 0; i < 12; i++)
      if(bd[i] != INVALID_HANDLE) IndicatorRelease(bd[i]);
   return (err == "");
  }

//+------------------------------------------------------------------+
//| Reuse an existing chart on (symbol, tf) or open a new one        |
//+------------------------------------------------------------------+
long OpenOrFocus(const string symbol, ENUM_TIMEFRAMES tf)
  {
   long id = ChartFirst();
   while(id >= 0)
     {
      if(ChartSymbol(id) == symbol && ChartPeriod(id) == tf)
         return id;
      id = ChartNext(id);
     }
   return ChartOpen(symbol, tf);
  }

//+------------------------------------------------------------------+
//| Strip ALL existing custom indicators in subwindows, then add ours|
//+------------------------------------------------------------------+
void AttachIndicators(long chartId, const string htfDivPeriod, const string indCsv)
  {
   // Wipe existing subwindow indicators on this chart so we don't stack duplicates
   int wins = (int)ChartGetInteger(chartId, CHART_WINDOWS_TOTAL);
   for(int w = wins - 1; w >= 1; w--)
     {
      int total = ChartIndicatorsTotal(chartId, w);
      for(int j = total - 1; j >= 0; j--)
        {
         string nm = ChartIndicatorName(chartId, w, j);
         if(StringFind(nm, "BD ") == 0)
            ChartIndicatorDelete(chartId, w, nm);
        }
     }

   string indList[]; int nReq = StringSplit(indCsv, ',', indList);
   ENUM_TIMEFRAMES tfHtf = StrToTimeframe(htfDivPeriod);
   if(tfHtf == PERIOD_CURRENT) tfHtf = PERIOD_M15;

   for(int i = 0; i < ArraySize(ALL_INDICATORS); i++)
     {
      string nm = ALL_INDICATORS[i];
      if(nReq > 0 && !StrInList(nm, indList)) continue;
      if(!AddOne(chartId, nm, tfHtf))
         PrintFormat("BD_AutoSetup: ChartIndicatorAdd failed for %s on chart %I64d (err %d)",
                     nm, chartId, GetLastError());
     }

   ChartRedraw(chartId);
  }

//+------------------------------------------------------------------+
//| Add a single indicator with parameters appropriate to its type   |
//+------------------------------------------------------------------+
bool AddOne(long chartId, const string name, ENUM_TIMEFRAMES tfHtfForDiv)
  {
   string path = BD_IND_DIR + "\\" + name;
   MqlParam p[];
   int n = 0;

   // Default: 1 string param = path
   ArrayResize(p, 1);
   p[0].type = TYPE_STRING; p[0].string_value = path; n = 1;

   if(name == "BD_RollingSharpe" || name == "BD_VwapDist")
     { ArrayResize(p, 2); p[1].type = TYPE_INT;
       p[1].integer_value = (name == "BD_RollingSharpe" ? 20 : 96); n = 2; }
   else if(name == "BD_MacdNorm")
     { ArrayResize(p, 5);
       p[1].type = TYPE_INT; p[1].integer_value = 12;
       p[2].type = TYPE_INT; p[2].integer_value = 26;
       p[3].type = TYPE_INT; p[3].integer_value = 9;
       p[4].type = TYPE_INT; p[4].integer_value = 14; n = 5; }
   else if(name == "BD_SDZone")
     { ArrayResize(p, 3);
       p[1].type = TYPE_INT; p[1].integer_value = 25;
       p[2].type = TYPE_INT; p[2].integer_value = 14; n = 3; }
   else if(name == "BD_VolPriceDiv")
     { ArrayResize(p, 3);
       p[1].type = TYPE_INT;    p[1].integer_value = 20;
       p[2].type = TYPE_DOUBLE; p[2].double_value  = 1.2; n = 3; }
   else if(name == "BD_BBExpanding")
     { ArrayResize(p, 4);
       p[1].type = TYPE_INT;    p[1].integer_value = 20;
       p[2].type = TYPE_DOUBLE; p[2].double_value  = 2.0;
       p[3].type = TYPE_INT;    p[3].integer_value = 3; n = 4; }
   else if(name == "BD_POCdist")
     { ArrayResize(p, 3);
       p[1].type = TYPE_INT; p[1].integer_value = 100;
       p[2].type = TYPE_INT; p[2].integer_value = 20; n = 3; }
   else if(name == "BD_HtfDiv")
     { ArrayResize(p, 5);
       p[1].type = TYPE_INT; p[1].integer_value = (int)tfHtfForDiv;
       p[2].type = TYPE_INT; p[2].integer_value = 14;
       p[3].type = TYPE_INT; p[3].integer_value = 5;
       p[4].type = TYPE_INT; p[4].integer_value = 3; n = 5; }
   else if(name == "BD_MtfBullScore")
     { ArrayResize(p, 5);
       p[1].type = TYPE_INT; p[1].integer_value = (int)PERIOD_M15;
       p[2].type = TYPE_INT; p[2].integer_value = (int)PERIOD_H1;
       p[3].type = TYPE_INT; p[3].integer_value = (int)PERIOD_CURRENT;
       p[4].type = TYPE_INT; p[4].integer_value = (int)PERIOD_CURRENT; n = 5; }

   int handle = IndicatorCreate(ChartSymbol(chartId), (ENUM_TIMEFRAMES)ChartPeriod(chartId),
                                IND_CUSTOM, n, p);
   if(handle == INVALID_HANDLE) return false;
   bool ok = ChartIndicatorAdd(chartId, (int)ChartGetInteger(chartId, CHART_WINDOWS_TOTAL), handle);
   // Note: handle stays alive; MT5 takes ownership when ChartIndicatorAdd succeeds
   return ok;
  }

//+------------------------------------------------------------------+
//| Read the JSON config from the terminal_common Files folder       |
//+------------------------------------------------------------------+
string ReadCommonFile(const string name)
  {
   ResetLastError();
   int f = FileOpen(name, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(f == INVALID_HANDLE) return "";
   string acc = "";
   while(!FileIsEnding(f)) acc += FileReadString(f) + "\n";
   FileClose(f);
   return acc;
  }

void WriteAck(int version, const string opened, const string errors)
  {
   string body = StringFormat(
       "{\"version_acked\":%d,\"timestamp\":%I64d,\"opened\":[%s],\"errors\":[%s]}",
       version, (long)TimeCurrent(),
       TrimTrailingComma(opened), TrimTrailingComma(errors));
   int f = FileOpen(InpAckName, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(f == INVALID_HANDLE) { Print("BD_AutoSetup: cannot write ack file: ", GetLastError()); return; }
   FileWriteString(f, body);
   FileClose(f);
  }

string TrimTrailingComma(const string s)
  {
   int len = StringLen(s);
   if(len > 0 && StringGetCharacter(s, len - 1) == ',')
      return StringSubstr(s, 0, len - 1);
   return s;
  }

//+------------------------------------------------------------------+
//| Tiny JSON helpers — enough for our flat schema                   |
//+------------------------------------------------------------------+
int JsonInt(const string s, const string key, int dflt)
  {
   string pat = "\"" + key + "\"";
   int p = StringFind(s, pat);
   if(p < 0) return dflt;
   int colon = StringFind(s, ":", p);
   if(colon < 0) return dflt;
   int i = colon + 1;
   while(i < StringLen(s))
     {
      ushort ch = StringGetCharacter(s, i);
      if(ch != ' ' && ch != '\t' && ch != '\n' && ch != '\r') break;
      i++;
     }
   string num = "";
   while(i < StringLen(s))
     {
      ushort ch = StringGetCharacter(s, i);
      if((ch >= '0' && ch <= '9') || ch == '-') { num += ShortToString(ch); i++; }
      else break;
     }
   if(num == "") return dflt;
   return (int)StringToInteger(num);
  }

string JsonStr(const string s, const string key, const string dflt)
  {
   string pat = "\"" + key + "\"";
   int p = StringFind(s, pat);
   if(p < 0) return dflt;
   int q1 = StringFind(s, "\"", p + StringLen(pat) + 1);
   if(q1 < 0) return dflt;
   int q2 = StringFind(s, "\"", q1 + 1);
   if(q2 < 0) return dflt;
   return StringSubstr(s, q1 + 1, q2 - q1 - 1);
  }

string JsonStrArrayJoin(const string s, const string key, const string sep, const string dflt)
  {
   string pat = "\"" + key + "\"";
   int p = StringFind(s, pat);
   if(p < 0) return dflt;
   int lb = StringFind(s, "[", p);
   int rb = StringFind(s, "]", lb);
   if(lb < 0 || rb < 0) return dflt;
   string body = StringSubstr(s, lb + 1, rb - lb - 1);
   string out = "";
   int i = 0;
   while(i < StringLen(body))
     {
      ushort ch = StringGetCharacter(body, i);
      if(ch == '"')
        {
         int j = StringFind(body, "\"", i + 1);
         if(j < 0) break;
         if(out != "") out += sep;
         out += StringSubstr(body, i + 1, j - i - 1);
         i = j + 1;
        }
      else i++;
     }
   return (out == "") ? dflt : out;
  }

bool StrInList(const string needle, string &haystack[])
  {
   for(int i = 0; i < ArraySize(haystack); i++)
      if(haystack[i] == needle) return true;
   return false;
  }

ENUM_TIMEFRAMES StrToTimeframe(const string s)
  {
   string t = s; StringToUpper(t);
   // Minute frames — MT5 supports M1, M2, M3, M4, M5, M6, M10, M12, M15, M20, M30
   if(t == "M1")  return PERIOD_M1;
   if(t == "M2")  return PERIOD_M2;
   if(t == "M3")  return PERIOD_M3;
   if(t == "M4")  return PERIOD_M4;
   if(t == "M5")  return PERIOD_M5;
   if(t == "M6")  return PERIOD_M6;
   if(t == "M10") return PERIOD_M10;
   if(t == "M12") return PERIOD_M12;
   if(t == "M15") return PERIOD_M15;
   if(t == "M20") return PERIOD_M20;
   if(t == "M30") return PERIOD_M30;
   // Hour frames — MT5 supports H1, H2, H3, H4, H6, H8, H12
   if(t == "H1")  return PERIOD_H1;
   if(t == "H2")  return PERIOD_H2;
   if(t == "H3")  return PERIOD_H3;
   if(t == "H4")  return PERIOD_H4;
   if(t == "H6")  return PERIOD_H6;
   if(t == "H8")  return PERIOD_H8;
   if(t == "H12") return PERIOD_H12;
   if(t == "D1")  return PERIOD_D1;
   if(t == "W1")  return PERIOD_W1;
   if(t == "MN1") return PERIOD_MN1;
   return PERIOD_CURRENT;
  }
