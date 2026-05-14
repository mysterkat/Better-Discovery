//+------------------------------------------------------------------+
//|  BD_VwapDist.mq5                                                  |
//|  Distance from rolling 96-bar VWAP, expressed as % of current     |
//|  close, clipped to ±5%.                                           |
//|                                                                  |
//|  vwap = sum(typical_price * vol) / sum(vol)                       |
//|  dist = (close - vwap) / vwap * 100                               |
//|                                                                  |
//|  Mirrors GetVwapDist() in PatternDiscoveryEA.mq5 and the          |
//|  vwap_dist feature in pattern_discovery_v6.py.                    |
//+------------------------------------------------------------------+
#property copyright "BETTER DISCOVERY v0.7"
#property version   "1.00"
#property strict
#property indicator_separate_window
#property indicator_minimum -5.0
#property indicator_maximum  5.0
#property indicator_buffers 1
#property indicator_plots   1
#property indicator_label1  "vwap_dist_pct"
#property indicator_type1   DRAW_LINE
#property indicator_color1  clrOrange
#property indicator_width1  2

#property indicator_level1  0.0
#property indicator_levelcolor clrDimGray
#property indicator_levelstyle STYLE_DOT

input int InpLookback = 96;   // VWAP lookback bars (Python default = 96)

double DistBuf[];

int OnInit()
  {
   if(InpLookback < 5) { Print("BD_VwapDist: InpLookback must be >= 5"); return INIT_PARAMETERS_INCORRECT; }
   SetIndexBuffer(0, DistBuf, INDICATOR_DATA);
   PlotIndexSetString(0, PLOT_LABEL, "vwap_dist_pct");
   PlotIndexSetInteger(0, PLOT_DRAW_BEGIN, InpLookback);
   IndicatorSetString(INDICATOR_SHORTNAME, StringFormat("BD VWAPdist(%d) %%", InpLookback));
   IndicatorSetInteger(INDICATOR_DIGITS, 3);
   return INIT_SUCCEEDED;
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
   if(rates_total < InpLookback) return 0;
   int start = (prev_calculated > InpLookback) ? prev_calculated - 1 : InpLookback;
   for(int i = start; i < rates_total; i++)
     {
      double sum_tp_vol = 0.0;
      double sum_vol    = 0.0;
      for(int k = 0; k < InpLookback; k++)
        {
         int idx = i - k;
         double tp = (high[idx] + low[idx] + close[idx]) / 3.0;
         double v  = (double)tick_volume[idx];
         if(v <= 0.0) v = 1.0;
         sum_tp_vol += tp * v;
         sum_vol    += v;
        }
      if(sum_vol <= 0.0) { DistBuf[i] = 0.0; continue; }
      double vwap = sum_tp_vol / sum_vol;
      if(vwap <= 0.0) { DistBuf[i] = 0.0; continue; }
      double v = (close[i] - vwap) / vwap * 100.0;
      if(v >  5.0) v =  5.0;
      if(v < -5.0) v = -5.0;
      DistBuf[i] = v;
     }
   return rates_total;
  }
