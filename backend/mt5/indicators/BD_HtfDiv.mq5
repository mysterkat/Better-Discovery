//+------------------------------------------------------------------+
//|  BD_HtfDiv.mq5                                                    |
//|  Higher-timeframe RSI divergence:                                 |
//|     +1 = LTF RSI rising > +2  AND  HTF RSI falling < -1 (bull)    |
//|     -1 = LTF RSI falling < -2 AND  HTF RSI rising  > +1 (bear)    |
//|      0 = no divergence                                            |
//|                                                                  |
//|  LTF = current chart period; HTF = InpHtfPeriod input.            |
//|  LTF slope = RSI(now) - RSI(5 bars ago)                           |
//|  HTF slope = RSI(now) - RSI(3 HTF bars ago)                       |
//|                                                                  |
//|  Mirrors GetHtfDiv() in PatternDiscoveryEA.mq5 and the htf_div    |
//|  feature in pattern_discovery_v6.py.                              |
//+------------------------------------------------------------------+
#property copyright "BETTER DISCOVERY v0.7"
#property version   "1.00"
#property strict
#property indicator_separate_window
#property indicator_minimum -1.5
#property indicator_maximum  1.5
#property indicator_buffers 1
#property indicator_plots   1
#property indicator_label1  "htf_div"
#property indicator_type1   DRAW_HISTOGRAM
#property indicator_color1  clrViolet
#property indicator_width1  2

#property indicator_level1  0.0
#property indicator_levelcolor clrDimGray
#property indicator_levelstyle STYLE_DOT

input ENUM_TIMEFRAMES InpHtfPeriod = PERIOD_M15;   // HTF used by Pattern Discovery
input int             InpRsiPeriod = 14;
input int             InpLtfLookback = 5;
input int             InpHtfLookback = 3;

double DivBuf[];

int g_hRsiLtf = INVALID_HANDLE;
int g_hRsiHtf = INVALID_HANDLE;

int OnInit()
  {
   SetIndexBuffer(0, DivBuf, INDICATOR_DATA);
   PlotIndexSetString(0, PLOT_LABEL, "htf_div");
   g_hRsiLtf = iRSI(_Symbol, _Period,       InpRsiPeriod, PRICE_CLOSE);
   g_hRsiHtf = iRSI(_Symbol, InpHtfPeriod,  InpRsiPeriod, PRICE_CLOSE);
   if(g_hRsiLtf == INVALID_HANDLE || g_hRsiHtf == INVALID_HANDLE)
     { Print("BD_HtfDiv: failed to create RSI handles"); return INIT_FAILED; }
   PlotIndexSetInteger(0, PLOT_DRAW_BEGIN, InpRsiPeriod + InpLtfLookback + 5);
   IndicatorSetString(INDICATOR_SHORTNAME,
       StringFormat("BD HtfDiv(LTF RSI%d vs %s RSI%d)",
                    InpRsiPeriod, EnumToString(InpHtfPeriod), InpRsiPeriod));
   IndicatorSetInteger(INDICATOR_DIGITS, 0);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   if(g_hRsiLtf != INVALID_HANDLE) IndicatorRelease(g_hRsiLtf);
   if(g_hRsiHtf != INVALID_HANDLE) IndicatorRelease(g_hRsiHtf);
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
   int need = InpRsiPeriod + InpLtfLookback + 5;
   if(rates_total < need) return 0;
   if(BarsCalculated(g_hRsiLtf) < rates_total) return prev_calculated;

   int start = (prev_calculated > need) ? prev_calculated - 1 : need;

   // Pull all LTF RSI values once
   double ltfRsi[];
   if(CopyBuffer(g_hRsiLtf, 0, 0, rates_total, ltfRsi) <= 0) return prev_calculated;

   for(int i = start; i < rates_total; i++)
     {
      if(i - InpLtfLookback < 0) { DivBuf[i] = 0.0; continue; }
      double ltf_now = ltfRsi[i];
      double ltf_ago = ltfRsi[i - InpLtfLookback];
      double ltf_slope = ltf_now - ltf_ago;

      // HTF lookup is per-bar because HTF bars don't align 1:1 with LTF.
      int htfShift = iBarShift(_Symbol, InpHtfPeriod, time[i], false) + 1;
      if(htfShift < 0) { DivBuf[i] = 0.0; continue; }
      double htfBuf[];
      if(CopyBuffer(g_hRsiHtf, 0, htfShift, InpHtfLookback + 1, htfBuf) <= 0)
        { DivBuf[i] = 0.0; continue; }
      // CopyBuffer returns chronological (oldest..newest); newest is the last element.
      int n = ArraySize(htfBuf);
      if(n < InpHtfLookback + 1) { DivBuf[i] = 0.0; continue; }
      double htf_now = htfBuf[n - 1];
      double htf_ago = htfBuf[0];
      double htf_slope = htf_now - htf_ago;

      if(ltf_slope >  2.0 && htf_slope < -1.0) { DivBuf[i] =  1.0; continue; }
      if(ltf_slope < -2.0 && htf_slope >  1.0) { DivBuf[i] = -1.0; continue; }
      DivBuf[i] = 0.0;
     }
   return rates_total;
  }
