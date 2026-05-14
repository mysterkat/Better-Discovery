//+------------------------------------------------------------------+
//|  BD_PinBar.mq5                                                    |
//|  Pin-bar score [0..1] = dominant wick / range                     |
//|                                                                  |
//|  Mirrors GetPinBar() in PatternDiscoveryEA.mq5 and the           |
//|  pin_bar feature in pattern_discovery_v6.py byte-for-byte.       |
//|                                                                  |
//|  0.0 = no wick (full body)   ~0.5 = one mediocre wick            |
//|  0.7+ = strong pin bar       1.0  = pure wick (doji-pin hybrid)  |
//+------------------------------------------------------------------+
#property copyright "BETTER DISCOVERY v0.7"
#property version   "1.00"
#property strict
#property indicator_separate_window
#property indicator_minimum 0.0
#property indicator_maximum 1.0
#property indicator_buffers 1
#property indicator_plots   1
#property indicator_label1  "pin_bar"
#property indicator_type1   DRAW_HISTOGRAM
#property indicator_color1  clrDeepSkyBlue
#property indicator_width1  2

#property indicator_level1 0.7
#property indicator_levelcolor clrDimGray
#property indicator_levelstyle STYLE_DOT

double PinBuf[];

int OnInit()
  {
   SetIndexBuffer(0, PinBuf, INDICATOR_DATA);
   PlotIndexSetString(0, PLOT_LABEL, "pin_bar");
   IndicatorSetString(INDICATOR_SHORTNAME, "BD PinBar");
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
   int start = (prev_calculated > 0) ? prev_calculated - 1 : 0;
   for(int i = start; i < rates_total; i++)
     {
      double rng = high[i] - low[i];
      if(rng <= 0.0) { PinBuf[i] = 0.0; continue; }
      double upper_wick = high[i] - MathMax(open[i], close[i]);
      double lower_wick = MathMin(open[i], close[i]) - low[i];
      double dom_wick   = MathMax(upper_wick, lower_wick);
      double v          = dom_wick / rng;
      PinBuf[i] = (v > 1.0) ? 1.0 : v;
     }
   return rates_total;
  }
