//+------------------------------------------------------------------+
//|  BD_PrevSessBias.mq5                                              |
//|  Previous-day candle bias as a session proxy:                     |
//|     +1 = prior D1 close-open > 0  AND |body|/range >= 0.1         |
//|     -1 = prior D1 close-open < 0  AND |body|/range >= 0.1         |
//|      0 = doji-ish or no data                                      |
//|                                                                  |
//|  NOTE: This is a D1 approximation of the actual session bias used |
//|  in the Python algo (which derives it from intraday session       |
//|  boundaries). It matches GetPrevSessBias() in PatternDiscoveryEA. |
//+------------------------------------------------------------------+
#property copyright "BETTER DISCOVERY v0.7"
#property version   "1.00"
#property strict
#property indicator_separate_window
#property indicator_minimum -1.5
#property indicator_maximum  1.5
#property indicator_buffers 1
#property indicator_plots   1
#property indicator_label1  "prev_sess_bias"
#property indicator_type1   DRAW_HISTOGRAM
#property indicator_color1  clrKhaki
#property indicator_width1  2

#property indicator_level1  0.0
#property indicator_levelcolor clrDimGray
#property indicator_levelstyle STYLE_DOT

double BiasBuf[];

int OnInit()
  {
   SetIndexBuffer(0, BiasBuf, INDICATOR_DATA);
   PlotIndexSetString(0, PLOT_LABEL, "prev_sess_bias");
   IndicatorSetString(INDICATOR_SHORTNAME, "BD PrevSessBias (D1 proxy)");
   IndicatorSetInteger(INDICATOR_DIGITS, 0);
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
      // Find the D1 candle PREVIOUS to bar[i]
      int d1Now  = iBarShift(_Symbol, PERIOD_D1, time[i], false);
      if(d1Now < 0) { BiasBuf[i] = 0.0; continue; }
      double o = iOpen (_Symbol, PERIOD_D1, d1Now + 1);
      double h = iHigh (_Symbol, PERIOD_D1, d1Now + 1);
      double l = iLow  (_Symbol, PERIOD_D1, d1Now + 1);
      double c = iClose(_Symbol, PERIOD_D1, d1Now + 1);
      double rng = h - l;
      if(rng <= 0.0) { BiasBuf[i] = 0.0; continue; }
      double body    = c - o;
      double bodyPct = MathAbs(body) / rng;
      if(bodyPct < 0.1) { BiasBuf[i] = 0.0; continue; }
      BiasBuf[i] = (body > 0.0) ? 1.0 : -1.0;
     }
   return rates_total;
  }
