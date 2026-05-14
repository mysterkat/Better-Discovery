//+------------------------------------------------------------------+
//|  BD_BBExpanding.mq5                                               |
//|  Bollinger-band expansion flag:                                   |
//|     1 = BB width is now greater than 3 bars ago (expanding)       |
//|     0 = BB width is flat or contracting                           |
//|                                                                  |
//|  bb_width = (Upper - Lower) / Middle                              |
//|                                                                  |
//|  Mirrors GetBBexpanding() in PatternDiscoveryEA.mq5 and the       |
//|  bb_expanding feature in pattern_discovery_v6.py.                 |
//+------------------------------------------------------------------+
#property copyright "BETTER DISCOVERY v0.7"
#property version   "1.00"
#property strict
#property indicator_separate_window
#property indicator_minimum -0.2
#property indicator_maximum  1.2
#property indicator_buffers 1
#property indicator_plots   1
#property indicator_label1  "bb_expanding"
#property indicator_type1   DRAW_HISTOGRAM
#property indicator_color1  clrAqua
#property indicator_width1  2

#property indicator_level1  0.5
#property indicator_levelcolor clrDimGray
#property indicator_levelstyle STYLE_DOT

input int    InpBBPeriod = 20;
input double InpBBStdev  = 2.0;
input int    InpLookback = 3;     // Compare BB width to this many bars ago

double ExpBuf[];

int g_hBB = INVALID_HANDLE;

int OnInit()
  {
   SetIndexBuffer(0, ExpBuf, INDICATOR_DATA);
   PlotIndexSetString(0, PLOT_LABEL, "bb_expanding");
   g_hBB = iBands(_Symbol, _Period, InpBBPeriod, 0, InpBBStdev, PRICE_CLOSE);
   if(g_hBB == INVALID_HANDLE)
     { Print("BD_BBExpanding: failed to create iBands handle"); return INIT_FAILED; }
   PlotIndexSetInteger(0, PLOT_DRAW_BEGIN, InpBBPeriod + InpLookback);
   IndicatorSetString(INDICATOR_SHORTNAME,
       StringFormat("BD BBExp(%d,%.1f) >%d ago", InpBBPeriod, InpBBStdev, InpLookback));
   IndicatorSetInteger(INDICATOR_DIGITS, 0);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   if(g_hBB != INVALID_HANDLE) IndicatorRelease(g_hBB);
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
   int need = InpBBPeriod + InpLookback + 5;
   if(rates_total < need) return 0;
   if(BarsCalculated(g_hBB) < rates_total) return prev_calculated;

   double mid[], up[], lo[];
   if(CopyBuffer(g_hBB, 0, 0, rates_total, mid) <= 0) return prev_calculated;
   if(CopyBuffer(g_hBB, 1, 0, rates_total, up)  <= 0) return prev_calculated;
   if(CopyBuffer(g_hBB, 2, 0, rates_total, lo)  <= 0) return prev_calculated;

   int start = (prev_calculated > need) ? prev_calculated - 1 : need;
   for(int i = start; i < rates_total; i++)
     {
      if(mid[i] <= 0.0 || mid[i - InpLookback] <= 0.0)
        { ExpBuf[i] = 0.0; continue; }
      double bw_now = (up[i]  - lo[i])  / mid[i];
      double bw_ago = (up[i - InpLookback] - lo[i - InpLookback]) / mid[i - InpLookback];
      ExpBuf[i] = (bw_now > bw_ago) ? 1.0 : 0.0;
     }
   return rates_total;
  }
