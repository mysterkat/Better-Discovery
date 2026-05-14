//+------------------------------------------------------------------+
//|  BD_MtfBullScore.mq5                                              |
//|  Additive multi-timeframe bull score:                             |
//|     score = (chart trend == up ? 1 : 0) +                         |
//|             sum over each non-PERIOD_CURRENT signal slot of       |
//|               (signal trend == up ? 1 : 0)                        |
//|                                                                  |
//|  trend(tf) = up    if EMA20 > EMA50 > EMA200                      |
//|              down  if EMA20 < EMA50 < EMA200                      |
//|              flat  otherwise                                      |
//|                                                                  |
//|  Range: 0 .. (1 + active signal slots), max 5.                    |
//|  A signal slot whose handle is invalid contributes 0 (so the      |
//|  score still works on instruments with limited HTF history).      |
//|                                                                  |
//|  Mirrors GetMTFbull() in PatternDiscoveryEA.mq5 (additive mode)   |
//|  and the mtf_bull_score feature in pattern_discovery_v6.py with   |
//|  MTF_SCORE_MODE = "additive".                                     |
//+------------------------------------------------------------------+
#property copyright "BETTER DISCOVERY v0.7"
#property version   "1.00"
#property strict
#property indicator_separate_window
#property indicator_minimum -0.5
#property indicator_maximum  5.5
#property indicator_buffers 1
#property indicator_plots   1
#property indicator_label1  "mtf_bull_score"
#property indicator_type1   DRAW_HISTOGRAM
#property indicator_color1  clrLimeGreen
#property indicator_width1  3

input ENUM_TIMEFRAMES InpSignalTF1 = PERIOD_M15;       // Slot 1 (PERIOD_CURRENT = disabled)
input ENUM_TIMEFRAMES InpSignalTF2 = PERIOD_H1;        // Slot 2
input ENUM_TIMEFRAMES InpSignalTF3 = PERIOD_CURRENT;   // Slot 3
input ENUM_TIMEFRAMES InpSignalTF4 = PERIOD_CURRENT;   // Slot 4

double ScoreBuf[];

int g_hEMA20  = INVALID_HANDLE;
int g_hEMA50  = INVALID_HANDLE;
int g_hEMA200 = INVALID_HANDLE;

#define MAX_SIG 4
int               g_hSig20 [MAX_SIG];
int               g_hSig50 [MAX_SIG];
int               g_hSig200[MAX_SIG];
ENUM_TIMEFRAMES   g_sigTFs [MAX_SIG];
int               g_nSig = 0;

int OnInit()
  {
   SetIndexBuffer(0, ScoreBuf, INDICATOR_DATA);
   PlotIndexSetString(0, PLOT_LABEL, "mtf_bull_score");

   g_hEMA20  = iMA(_Symbol, _Period, 20,  0, MODE_EMA, PRICE_CLOSE);
   g_hEMA50  = iMA(_Symbol, _Period, 50,  0, MODE_EMA, PRICE_CLOSE);
   g_hEMA200 = iMA(_Symbol, _Period, 200, 0, MODE_EMA, PRICE_CLOSE);
   if(g_hEMA20 == INVALID_HANDLE || g_hEMA50 == INVALID_HANDLE || g_hEMA200 == INVALID_HANDLE)
     { Print("BD_MtfBullScore: failed to create chart EMA handles"); return INIT_FAILED; }

   ENUM_TIMEFRAMES cfg[MAX_SIG];
   cfg[0] = InpSignalTF1; cfg[1] = InpSignalTF2;
   cfg[2] = InpSignalTF3; cfg[3] = InpSignalTF4;
   g_nSig = 0;
   for(int i = 0; i < MAX_SIG; i++)
     {
      if(cfg[i] == PERIOD_CURRENT) continue;
      g_sigTFs [g_nSig] = cfg[i];
      g_hSig20 [g_nSig] = iMA(_Symbol, cfg[i], 20,  0, MODE_EMA, PRICE_CLOSE);
      g_hSig50 [g_nSig] = iMA(_Symbol, cfg[i], 50,  0, MODE_EMA, PRICE_CLOSE);
      g_hSig200[g_nSig] = iMA(_Symbol, cfg[i], 200, 0, MODE_EMA, PRICE_CLOSE);
      if(g_hSig20[g_nSig]  == INVALID_HANDLE ||
         g_hSig50[g_nSig]  == INVALID_HANDLE ||
         g_hSig200[g_nSig] == INVALID_HANDLE)
        {
         PrintFormat("BD_MtfBullScore: invalid handles for slot %d (%s) — skipping",
                     i + 1, EnumToString(cfg[i]));
         continue;
        }
      g_nSig++;
     }

   PlotIndexSetInteger(0, PLOT_DRAW_BEGIN, 220);
   IndicatorSetString(INDICATOR_SHORTNAME,
       StringFormat("BD MTFbull [chart + %d sig TF%s]", g_nSig, g_nSig == 1 ? "" : "s"));
   IndicatorSetInteger(INDICATOR_DIGITS, 0);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   if(g_hEMA20  != INVALID_HANDLE) IndicatorRelease(g_hEMA20);
   if(g_hEMA50  != INVALID_HANDLE) IndicatorRelease(g_hEMA50);
   if(g_hEMA200 != INVALID_HANDLE) IndicatorRelease(g_hEMA200);
   for(int i = 0; i < g_nSig; i++)
     {
      if(g_hSig20[i]  != INVALID_HANDLE) IndicatorRelease(g_hSig20[i]);
      if(g_hSig50[i]  != INVALID_HANDLE) IndicatorRelease(g_hSig50[i]);
      if(g_hSig200[i] != INVALID_HANDLE) IndicatorRelease(g_hSig200[i]);
     }
  }

// Helper: 1 if EMA20>EMA50>EMA200 at signalShift on signal handles slot s, else 0
double SignalUp(int s, datetime barTime)
  {
   int ss = iBarShift(_Symbol, g_sigTFs[s], barTime, false);
   if(ss < 0) return 0.0;
   double e20[1], e50[1], e200[1];
   if(CopyBuffer(g_hSig20[s],  0, ss, 1, e20)  <= 0) return 0.0;
   if(CopyBuffer(g_hSig50[s],  0, ss, 1, e50)  <= 0) return 0.0;
   if(CopyBuffer(g_hSig200[s], 0, ss, 1, e200) <= 0) return 0.0;
   return (e20[0] > e50[0] && e50[0] > e200[0]) ? 1.0 : 0.0;
  }

int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[])
  {
   int need = 220;
   if(rates_total < need) return 0;
   if(BarsCalculated(g_hEMA200) < rates_total) return prev_calculated;

   double e20[], e50[], e200[];
   if(CopyBuffer(g_hEMA20,  0, 0, rates_total, e20)  <= 0) return prev_calculated;
   if(CopyBuffer(g_hEMA50,  0, 0, rates_total, e50)  <= 0) return prev_calculated;
   if(CopyBuffer(g_hEMA200, 0, 0, rates_total, e200) <= 0) return prev_calculated;

   int start = (prev_calculated > need) ? prev_calculated - 1 : need;
   for(int i = start; i < rates_total; i++)
     {
      double total = (e20[i] > e50[i] && e50[i] > e200[i]) ? 1.0 : 0.0;
      for(int s = 0; s < g_nSig; s++)
         total += SignalUp(s, time[i]);
      ScoreBuf[i] = total;
     }
   return rates_total;
  }
