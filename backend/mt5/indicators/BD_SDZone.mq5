//+------------------------------------------------------------------+
//|  BD_SDZone.mq5                                                    |
//|  Supply/Demand zone proximity:                                    |
//|     +1 = price within 1 ATR of the 25-bar swing low (near support)|
//|     -1 = price within 1 ATR of the 25-bar swing high (near res)   |
//|      0 = middle of the range                                      |
//|                                                                  |
//|  Mirrors GetSDzone() in PatternDiscoveryEA.mq5 and the            |
//|  sd_zone feature in pattern_discovery_v6.py (FIX v3 — lookback    |
//|  starts at bar[shift], inclusive).                                |
//+------------------------------------------------------------------+
#property copyright "BETTER DISCOVERY v0.7"
#property version   "1.00"
#property strict
#property indicator_separate_window
#property indicator_minimum -1.5
#property indicator_maximum  1.5
#property indicator_buffers 1
#property indicator_plots   1
#property indicator_label1  "sd_zone"
#property indicator_type1   DRAW_HISTOGRAM
#property indicator_color1  clrMediumOrchid
#property indicator_width1  2

#property indicator_level1  0.0
#property indicator_levelcolor clrDimGray
#property indicator_levelstyle STYLE_DOT

input int InpLookback  = 25;   // Swing window
input int InpAtrPeriod = 14;   // ATR period

double ZoneBuf[];

int g_hAtr = INVALID_HANDLE;

int OnInit()
  {
   SetIndexBuffer(0, ZoneBuf, INDICATOR_DATA);
   PlotIndexSetString(0, PLOT_LABEL, "sd_zone");
   g_hAtr = iATR(_Symbol, _Period, InpAtrPeriod);
   if(g_hAtr == INVALID_HANDLE)
     { Print("BD_SDZone: failed to create ATR handle"); return INIT_FAILED; }
   PlotIndexSetInteger(0, PLOT_DRAW_BEGIN, InpLookback + InpAtrPeriod);
   IndicatorSetString(INDICATOR_SHORTNAME, StringFormat("BD SDzone(%d, ATR%d)", InpLookback, InpAtrPeriod));
   IndicatorSetInteger(INDICATOR_DIGITS, 0);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   if(g_hAtr != INVALID_HANDLE) IndicatorRelease(g_hAtr);
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
   int need = InpLookback + InpAtrPeriod + 5;
   if(rates_total < need) return 0;
   if(BarsCalculated(g_hAtr) < rates_total) return prev_calculated;

   int start = (prev_calculated > need) ? prev_calculated - 1 : need;
   double atr[];
   if(CopyBuffer(g_hAtr, 0, 0, rates_total, atr) <= 0) return prev_calculated;
   // CopyBuffer with start_pos=0 + count=rates_total returns oldest..newest aligned to indices.
   for(int i = start; i < rates_total; i++)
     {
      double a = atr[i];
      if(a <= 0.0) { ZoneBuf[i] = 0.0; continue; }
      // Inclusive 25-bar window from i down to i-24
      double swing_hi = high[i];
      double swing_lo = low [i];
      for(int k = 1; k < InpLookback; k++)
        {
         int idx = i - k;
         if(idx < 0) break;
         if(high[idx] > swing_hi) swing_hi = high[idx];
         if(low [idx] < swing_lo) swing_lo = low [idx];
        }
      double dist_to_res = (swing_hi - close[i]) / a;
      double dist_to_sup = (close[i] - swing_lo) / a;
      if(dist_to_sup < 1.0)      ZoneBuf[i] =  1.0;
      else if(dist_to_res < 1.0) ZoneBuf[i] = -1.0;
      else                        ZoneBuf[i] =  0.0;
     }
   return rates_total;
  }
