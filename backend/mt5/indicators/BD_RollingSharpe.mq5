//+------------------------------------------------------------------+
//|  BD_RollingSharpe.mq5                                             |
//|  Rolling Sharpe ratio of last N close-to-close returns,           |
//|  clipped to [-3, +3].                                             |
//|                                                                  |
//|  Mirrors GetRollingSharpe() in PatternDiscoveryEA.mq5 and the    |
//|  rolling_sharpe feature in pattern_discovery_v6.py.              |
//|                                                                  |
//|  Reading: positive = risk-adjusted up momentum,                   |
//|           negative = risk-adjusted down momentum,                 |
//|           |value| > 1 is unusually strong directional drift.      |
//+------------------------------------------------------------------+
#property copyright "BETTER DISCOVERY v0.7"
#property version   "1.00"
#property strict
#property indicator_separate_window
#property indicator_minimum -3.0
#property indicator_maximum  3.0
#property indicator_buffers 1
#property indicator_plots   1
#property indicator_label1  "rolling_sharpe"
#property indicator_type1   DRAW_LINE
#property indicator_color1  clrGold
#property indicator_width1  2

#property indicator_level1  0.0
#property indicator_levelcolor clrDimGray
#property indicator_levelstyle STYLE_DOT

input int InpPeriod = 20;   // Lookback (Python default = 20)

double SharpeBuf[];

int OnInit()
  {
   if(InpPeriod < 5)
     { Print("BD_RollingSharpe: InpPeriod must be >= 5"); return INIT_PARAMETERS_INCORRECT; }
   SetIndexBuffer(0, SharpeBuf, INDICATOR_DATA);
   PlotIndexSetString(0, PLOT_LABEL, "rolling_sharpe");
   PlotIndexSetInteger(0, PLOT_DRAW_BEGIN, InpPeriod + 1);
   IndicatorSetString(INDICATOR_SHORTNAME, StringFormat("BD RollingSharpe(%d)", InpPeriod));
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
   int need = InpPeriod + 1;   // need N returns -> N+1 closes
   int start = (prev_calculated > need) ? prev_calculated - 1 : need;
   for(int i = start; i < rates_total; i++)
     {
      // Build N returns: close[i-k] / close[i-k-1] - 1   for k = 0..N-1
      double mean = 0.0;
      double rets[];
      ArrayResize(rets, InpPeriod);
      bool ok = true;
      for(int k = 0; k < InpPeriod; k++)
        {
         double c1 = close[i - k];
         double c2 = close[i - k - 1];
         if(c2 <= 0.0) { ok = false; break; }
         rets[k] = (c1 - c2) / c2;
         mean += rets[k];
        }
      if(!ok) { SharpeBuf[i] = 0.0; continue; }
      mean /= InpPeriod;
      double var = 0.0;
      for(int k = 0; k < InpPeriod; k++) { double d = rets[k] - mean; var += d * d; }
      double std = MathSqrt(var / InpPeriod);
      if(std <= 0.0) { SharpeBuf[i] = 0.0; continue; }
      double v = mean / std;
      if(v >  3.0) v =  3.0;
      if(v < -3.0) v = -3.0;
      SharpeBuf[i] = v;
     }
   return rates_total;
  }
