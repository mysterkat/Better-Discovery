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
   if(t == "M1")  return PERIOD_M1;
   if(t == "M5")  return PERIOD_M5;
   if(t == "M15") return PERIOD_M15;
   if(t == "M30") return PERIOD_M30;
   if(t == "H1")  return PERIOD_H1;
   if(t == "H4")  return PERIOD_H4;
   if(t == "D1")  return PERIOD_D1;
   if(t == "W1")  return PERIOD_W1;
   if(t == "MN1") return PERIOD_MN1;
   return PERIOD_CURRENT;
  }
