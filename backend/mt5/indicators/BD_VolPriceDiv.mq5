//+------------------------------------------------------------------+
//|  BD_VolPriceDiv.mq5                                               |
//|  Volume-price divergence:                                         |
//|     +1 = high volume DOWN bar  (accumulation — smart money buying)|
//|     -1 = high volume UP   bar  (distribution — smart money selling)|
//|      0 = below the volume threshold (vol_ratio <= 1.2)            |
//|                                                                  |
//|  vol_ratio = current_volume / MA20(volume)                        |
//|                                                                  |
//|  Mirrors GetVolPriceDiv() in PatternDiscoveryEA.mq5 and the       |
//|  vol_price_div feature in pattern_discovery_v6.py.                |
//+------------------------------------------------------------------+
#property copyright "BETTER DISCOVERY v0.7"
#property version   "1.00"
#property strict
#property indicator_separate_window
#property indicator_minimum -1.5
#property indicator_maximum  1.5
#property indicator_buffers 1
#property indicator_plots   1
#property indicator_label1  "vol_price_div"
#property indicator_type1   DRAW_HISTOGRAM
#property indicator_color1  clrSilver
#property indicator_width1  2

#property indicator_level1  0.0
#property indicator_levelcolor clrDimGray
#property indicator_levelstyle STYLE_DOT

input int    InpVolMaPeriod = 20;     // Volume MA period
input double InpVolThresh   = 1.2;    // Min vol_ratio to flag a div

double DivBuf[];

int OnInit()
  {
   SetIndexBuffer(0, DivBuf, INDICATOR_DATA);
   PlotIndexSetString(0, PLOT_LABEL, "vol_price_div");
   PlotIndexSetInteger(0, PLOT_DRAW_BEGIN, InpVolMaPeriod + 1);
   IndicatorSetString(INDICATOR_SHORTNAME,
       StringFormat("BD VolPriceDiv(MA%d > %.2f)", InpVolMaPeriod, InpVolThresh));
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
   int need = InpVolMaPeriod + 1;
   if(rates_total < need) return 0;
   int start = (prev_calculated > need) ? prev_calculated - 1 : need;
   for(int i = start; i < rates_total; i++)
     {
      double sum = 0.0;
      for(int k = 1; k <= InpVolMaPeriod; k++)
         sum += (double)tick_volume[i - k];
      double ma = sum / InpVolMaPeriod;
      if(ma <= 0.0) { DivBuf[i] = 0.0; continue; }
      double ratio = (double)tick_volume[i] / ma;
      if(ratio > 5.0) ratio = 5.0;
      if(ratio <= InpVolThresh) { DivBuf[i] = 0.0; continue; }
      if(close[i] < open[i])    { DivBuf[i] =  1.0; continue; }   // accumulation
      if(close[i] > open[i])    { DivBuf[i] = -1.0; continue; }   // distribution
      DivBuf[i] = 0.0;
     }
   return rates_total;
  }
