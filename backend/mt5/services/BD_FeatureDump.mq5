//+------------------------------------------------------------------+
//|  BD_FeatureDump.mq5                                               |
//|                                                                  |
//|  One-shot feature dumper for the v0.7.0 validation harness.       |
//|                                                                  |
//|  Attaches to the chart whose (symbol, timeframe) matches the       |
//|  Python ground-truth CSV. On the first tick after an indicator    |
//|  warmup period, walks the last InpBars closed bars, samples the   |
//|  12 BD_* indicators + standard ones, and writes one row per bar    |
//|  to <terminal_common>\Files\bd_feature_dump.csv.                  |
//|                                                                  |
//|  Output schema matches validate_ea_features.py's --diff input:    |
//|    time, open, high, low, close,                                  |
//|    rsi14, macd_norm, atr_pct, bb_width, trend, mtf_bull_score,    |
//|    body_pct, rng_atr, vol_ratio, vol_body_conf, regime,           |
//|    vol_price_div, bb_expanding, prev_sess_bias, poc_dist, bull,   |
//|    uwk_pct, lwk_pct, stoch_k, stoch_d, pin_bar,                   |
//|    inside_bar, outside_bar, htf_div, rolling_sharpe,              |
//|    sd_zone, vwap_dist                                             |
//|                                                                  |
//|  USAGE                                                            |
//|  -----                                                           |
//|  1. Make sure BD_*.ex5 are compiled and live in                   |
//|     MQL5\\Indicators\\BetterDiscovery\\.                          |
//|  2. Drag this EA onto the chart, allow algo trading, click OK.    |
//|  3. Wait for "DONE" message; detach the EA.                       |
//|  4. Copy bd_feature_dump.csv to your validation folder.           |
//+------------------------------------------------------------------+
#property copyright "BETTER DISCOVERY v0.7"
#property version   "1.00"
#property strict

input int    InpBars     = 5000;          // Bars of history to dump
input string InpOutFile  = "bd_feature_dump.csv";
input ENUM_TIMEFRAMES InpHtfDiv = PERIOD_M15;   // Match Python HTF_DIV setting
input ENUM_TIMEFRAMES InpMtfTF1 = PERIOD_M15;
input ENUM_TIMEFRAMES InpMtfTF2 = PERIOD_H1;
input ENUM_TIMEFRAMES InpMtfTF3 = PERIOD_CURRENT;
input ENUM_TIMEFRAMES InpMtfTF4 = PERIOD_CURRENT;

const string IND_DIR = "BetterDiscovery";

string IND_NAMES[] = {
   "BD_RollingSharpe", "BD_MacdNorm", "BD_VwapDist", "BD_SDZone",
   "BD_VolPriceDiv", "BD_BBExpanding", "BD_POCdist",
   "BD_PinBar", "BD_PrevSessBias", "BD_Regime", "BD_HtfDiv",
   "BD_MtfBullScore"
};

// Map indicator stem -> handle (filled in OnInit)
int g_handles[12];
// Standard MT5 indicator handles for direct features (rsi, atr, bb, ema, stoch)
int g_hRSI = INVALID_HANDLE, g_hATR = INVALID_HANDLE, g_hBB = INVALID_HANDLE;
int g_hEMA20 = INVALID_HANDLE, g_hEMA50 = INVALID_HANDLE, g_hEMA200 = INVALID_HANDLE;
int g_hStoch = INVALID_HANDLE;

bool g_done = false;

int OnInit()
  {
   // Standard handles
   g_hRSI    = iRSI    (_Symbol, _Period, 14, PRICE_CLOSE);
   g_hATR    = iATR    (_Symbol, _Period, 14);
   g_hBB     = iBands  (_Symbol, _Period, 20, 0, 2.0, PRICE_CLOSE);
   g_hEMA20  = iMA     (_Symbol, _Period, 20,  0, MODE_EMA, PRICE_CLOSE);
   g_hEMA50  = iMA     (_Symbol, _Period, 50,  0, MODE_EMA, PRICE_CLOSE);
   g_hEMA200 = iMA     (_Symbol, _Period, 200, 0, MODE_EMA, PRICE_CLOSE);
   g_hStoch  = iStochastic(_Symbol, _Period, 14, 3, 1, MODE_SMA, STO_LOWHIGH);
   if(g_hRSI==INVALID_HANDLE || g_hATR==INVALID_HANDLE || g_hBB==INVALID_HANDLE)
     { Print("BD_FeatureDump: standard handles failed"); return INIT_FAILED; }

   // BD indicator handles via IndicatorCreate
   for(int i = 0; i < ArraySize(IND_NAMES); i++)
      g_handles[i] = CreateBdHandle(IND_NAMES[i]);

   for(int i = 0; i < ArraySize(IND_NAMES); i++)
      if(g_handles[i] == INVALID_HANDLE)
        {
         PrintFormat("BD_FeatureDump: %s handle failed — make sure %s\\%s.ex5 is compiled.",
                     IND_NAMES[i], IND_DIR, IND_NAMES[i]);
         return INIT_FAILED;
        }

   PrintFormat("BD_FeatureDump ready. Will dump %d bars on next new bar.", InpBars);
   return INIT_SUCCEEDED;
  }

int CreateBdHandle(const string name)
  {
   string path = IND_DIR + "\\" + name;
   MqlParam p[]; ArrayResize(p, 1);
   p[0].type = TYPE_STRING; p[0].string_value = path;
   if(name == "BD_HtfDiv")
     { ArrayResize(p, 5);
       p[1].type = TYPE_INT; p[1].integer_value = (int)InpHtfDiv;
       p[2].type = TYPE_INT; p[2].integer_value = 14;
       p[3].type = TYPE_INT; p[3].integer_value = 5;
       p[4].type = TYPE_INT; p[4].integer_value = 3; }
   else if(name == "BD_MtfBullScore")
     { ArrayResize(p, 5);
       p[1].type = TYPE_INT; p[1].integer_value = (int)InpMtfTF1;
       p[2].type = TYPE_INT; p[2].integer_value = (int)InpMtfTF2;
       p[3].type = TYPE_INT; p[3].integer_value = (int)InpMtfTF3;
       p[4].type = TYPE_INT; p[4].integer_value = (int)InpMtfTF4; }
   return IndicatorCreate(_Symbol, _Period, IND_CUSTOM, ArraySize(p), p);
  }

void OnDeinit(const int reason)
  {
   for(int i = 0; i < ArraySize(g_handles); i++)
      if(g_handles[i] != INVALID_HANDLE) IndicatorRelease(g_handles[i]);
   IndicatorRelease(g_hRSI); IndicatorRelease(g_hATR); IndicatorRelease(g_hBB);
   IndicatorRelease(g_hEMA20); IndicatorRelease(g_hEMA50); IndicatorRelease(g_hEMA200);
   IndicatorRelease(g_hStoch);
  }

void OnTick()
  {
   if(g_done) return;
   // Wait for all indicators to fully calculate
   if(BarsCalculated(g_hRSI)    < InpBars + 50) return;
   if(BarsCalculated(g_hATR)    < InpBars + 50) return;
   if(BarsCalculated(g_hEMA200) < InpBars + 50) return;
   for(int i = 0; i < ArraySize(g_handles); i++)
      if(BarsCalculated(g_handles[i]) < InpBars + 50) return;

   if(!Dump())
      Print("BD_FeatureDump: dump failed.");
   else
      PrintFormat("BD_FeatureDump: DONE — wrote %s. You can detach this EA now.", InpOutFile);
   g_done = true;
  }

// Helper: read a single buffer value at shift `s`, or 0 on failure
double Buf(int handle, int buffer, int s)
  {
   double v[1];
   if(CopyBuffer(handle, buffer, s, 1, v) != 1) return 0.0;
   return v[0];
  }

bool Dump()
  {
   int f = FileOpen(InpOutFile, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(f == INVALID_HANDLE)
     { PrintFormat("FileOpen failed: %d", GetLastError()); return false; }

   // CSV header
   FileWriteString(f,
       "time,open,high,low,close,"
       "rsi14,macd_norm,atr_pct,bb_width,trend,mtf_bull_score,"
       "body_pct,rng_atr,vol_ratio,vol_body_conf,regime,vol_price_div,bb_expanding,"
       "prev_sess_bias,poc_dist,bull,uwk_pct,lwk_pct,"
       "stoch_k,stoch_d,pin_bar,inside_bar,outside_bar,htf_div,"
       "rolling_sharpe,sd_zone,vwap_dist\n");

   int n = MathMin(InpBars, Bars(_Symbol, _Period) - 220);
   // shift = n .. 1 (skip live bar 0)
   for(int s = n; s >= 1; s--)
     {
      datetime t = iTime  (_Symbol, _Period, s);
      double   o = iOpen  (_Symbol, _Period, s);
      double   h = iHigh  (_Symbol, _Period, s);
      double   l = iLow   (_Symbol, _Period, s);
      double   c = iClose (_Symbol, _Period, s);

      double rsi14 = Buf(g_hRSI, 0, s);
      double atr   = Buf(g_hATR, 0, s);
      double bbm   = Buf(g_hBB,  0, s);
      double bbu   = Buf(g_hBB,  1, s);
      double bbl   = Buf(g_hBB,  2, s);
      double e20   = Buf(g_hEMA20, 0, s);
      double e50   = Buf(g_hEMA50, 0, s);
      double e200  = Buf(g_hEMA200, 0, s);
      double stk   = Buf(g_hStoch, 0, s);
      double std_  = Buf(g_hStoch, 1, s);

      double atr_pct  = (c > 0) ? atr / c : 0.0;
      double bb_width = (bbm > 0) ? (bbu - bbl) / bbm : 0.0;
      double trend    = (e20>e50 && e50>e200) ? 1.0 : (e20<e50 && e50<e200 ? -1.0 : 0.0);
      double rng      = h - l;
      double body     = MathAbs(c - o);
      double uwk      = h - MathMax(o, c);
      double lwk      = MathMin(o, c) - l;
      double bull     = (c >= o) ? 1.0 : 0.0;
      double rng_atr  = (atr > 0) ? rng / atr : 0.0;
      double body_pct = (rng > 0) ? body / rng : 0.0;
      double uwk_pct  = (rng > 0) ? uwk  / rng : 0.0;
      double lwk_pct  = (rng > 0) ? lwk  / rng : 0.0;
      double inside   = (h < iHigh(_Symbol,_Period,s+1) && l > iLow(_Symbol,_Period,s+1)) ? 1.0 : 0.0;
      double outside  = (h > iHigh(_Symbol,_Period,s+1) && l < iLow(_Symbol,_Period,s+1)) ? 1.0 : 0.0;

      // Map BD indicator names to handle slots in the order of IND_NAMES[]
      // 0:BD_RollingSharpe  1:BD_MacdNorm  2:BD_VwapDist  3:BD_SDZone
      // 4:BD_VolPriceDiv    5:BD_BBExpanding  6:BD_POCdist 7:BD_PinBar
      // 8:BD_PrevSessBias   9:BD_Regime  10:BD_HtfDiv  11:BD_MtfBullScore
      double rs    = Buf(g_handles[0],  0, s);
      double mn    = Buf(g_handles[1],  0, s);
      double vwd   = Buf(g_handles[2],  0, s);
      double sdz   = Buf(g_handles[3],  0, s);
      double vpd   = Buf(g_handles[4],  0, s);
      double bbe   = Buf(g_handles[5],  0, s);
      double pocd  = Buf(g_handles[6],  0, s);
      double pin   = Buf(g_handles[7],  0, s);
      double psb   = Buf(g_handles[8],  0, s);
      double reg   = Buf(g_handles[9],  0, s);
      double hd    = Buf(g_handles[10], 0, s);
      double mtfb  = Buf(g_handles[11], 0, s);

      // vol_ratio + vol_body_conf computed inline (no BD indicator yet)
      double vsum = 0; for(int k=1;k<=20;k++) vsum += (double)iTickVolume(_Symbol,_Period,s+k);
      double vma  = vsum / 20.0;
      double vr   = (vma > 0) ? MathMin((double)iTickVolume(_Symbol,_Period,s) / vma, 5.0) : 0.0;
      double vbc  = MathMin(vr * body_pct, 5.0);

      string line = StringFormat(
         "%s,%.5f,%.5f,%.5f,%.5f,"
         "%.6f,%.6f,%.6f,%.6f,%.0f,%.0f,"
         "%.6f,%.6f,%.6f,%.6f,%.0f,%.0f,%.0f,"
         "%.0f,%.6f,%.0f,%.6f,%.6f,"
         "%.4f,%.4f,%.6f,%.0f,%.0f,%.0f,"
         "%.6f,%.0f,%.6f\n",
         TimeToString(t, TIME_DATE | TIME_MINUTES | TIME_SECONDS),
         o, h, l, c,
         rsi14, mn, atr_pct, bb_width, trend, mtfb,
         body_pct, rng_atr, vr, vbc, reg, vpd, bbe,
         psb, pocd, bull, uwk_pct, lwk_pct,
         stk, std_, pin, inside, outside, hd,
         rs, sdz, vwd);
      FileWriteString(f, line);
     }
   FileClose(f);
   return true;
  }
