//+------------------------------------------------------------------+
//|  BD_MacdNorm.mq5                                                  |
//|  MACD histogram normalised by ATR(14):                            |
//|     macd_norm = (MACD_line - Signal_line) / ATR                   |
//|                                                                  |
//|  Why normalise:  ATR-scaling makes the value comparable across    |
//|  symbols and timeframes — Pattern Discovery filter ranges only    |
//|  make sense on the normalised series.                             |
//|                                                                  |
//|  Mirrors GetMacdNorm() in PatternDiscoveryEA.mq5 and the          |
//|  macd_norm feature in pattern_discovery_v6.py.                    |
//+------------------------------------------------------------------+
#property copyright "BETTER DISCOVERY v0.7"
#property version   "1.00"
#property strict
#property indicator_separate_window
#property indicator_buffers 1
#property indicator_plots   1
#property indicator_label1  "macd_norm"
#property indicator_type1   DRAW_HISTOGRAM
#property indicator_color1  clrMediumSeaGreen
#property indicator_width1  2

#property indicator_level1  0.0
#property indicator_levelcolor clrDimGray
#property indicator_levelstyle STYLE_DOT

input int InpFastEMA   = 12;   // MACD fast EMA
input int InpSlowEMA   = 26;   // MACD slow EMA
input int InpSignalSMA = 9;    // MACD signal SMA
input int InpAtrPeriod = 14;   // ATR period

double NormBuf[];

int g_hMacd = INVALID_HANDLE;
int g_hAtr  = INVALID_HANDLE;

int OnInit()
  {
   SetIndexBuffer(0, NormBuf, INDICATOR_DATA);
   PlotIndexSetString(0, PLOT_LABEL, "macd_norm");

   g_hMacd = iMACD(_Symbol, _Period, InpFastEMA, InpSlowEMA, InpSignalSMA, PRICE_CLOSE);
   g_hAtr  = iATR (_Symbol, _Period, InpAtrPeriod);
   if(g_hMacd == INVALID_HANDLE || g_hAtr == INVALID_HANDLE)
     { Print("BD_MacdNorm: failed to create indicator handles"); return INIT_FAILED; }

   PlotIndexSetInteger(0, PLOT_DRAW_BEGIN, InpSlowEMA + InpSignalSMA + 5);
   IndicatorSetString(INDICATOR_SHORTNAME,
       StringFormat("BD MACDnorm(%d,%d,%d)/ATR(%d)",
                    InpFastEMA, InpSlowEMA, InpSignalSMA, InpAtrPeriod));
   IndicatorSetInteger(INDICATOR_DIGITS, 4);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   if(g_hMacd != INVALID_HANDLE) IndicatorRelease(g_hMacd);
   if(g_hAtr  != INVALID_HANDLE) IndicatorRelease(g_hAtr);
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
   int need = InpSlowEMA + InpSignalSMA + 5;
   if(rates_total < need) return 0;

   // BarsCalculated() guards against partial indicator data on first paint
   if(BarsCalculated(g_hMacd) < rates_total) return prev_calculated;
   if(BarsCalculated(g_hAtr)  < rates_total) return prev_calculated;

   int to_copy = (prev_calculated == 0) ? rates_total : rates_total - prev_calculated + 1;
   if(to_copy > rates_total) to_copy = rates_total;
   int start   = rates_total - to_copy;

   double macd[], sig[], atr[];
   if(CopyBuffer(g_hMacd, 0, 0, to_copy, macd) <= 0) return prev_calculated;
   if(CopyBuffer(g_hMacd, 1, 0, to_copy, sig)  <= 0) return prev_calculated;
   if(CopyBuffer(g_hAtr,  0, 0, to_copy, atr)  <= 0) return prev_calculated;
   // CopyBuffer returns oldest..newest; align to NormBuf[start..rates_total-1]
   for(int k = 0; k < to_copy; k++)
     {
      int dst = start + k;
      if(atr[k] <= 0.0) { NormBuf[dst] = 0.0; continue; }
      NormBuf[dst] = (macd[k] - sig[k]) / atr[k];
     }
   return rates_total;
  }
