//+------------------------------------------------------------------+
//|  BD_Regime.mq5                                                    |
//|  Categorical market regime [0..4]:                                |
//|     0 = TrendUp        EMA20>EMA50>EMA200 AND ATR%<0.5%           |
//|     1 = TrendDown      EMA20<EMA50<EMA200 AND ATR%<0.5%           |
//|     2 = Squeeze        |trend|<0.5 AND BB_width<0.02              |
//|     3 = WideVol        ATR% >= 0.8%                               |
//|     4 = Choppy         (everything else)                          |
//|                                                                  |
//|  NOTE: This is a fixed-threshold approximation of the Python      |
//|  algorithm, which uses rolling 100-200 bar quantiles. Useful for  |
//|  visualisation; if a rule's regime filter is critical, neutralise |
//|  it (lo=-999, hi=999) until the rolling-quantile version lands.   |
//|                                                                  |
//|  Mirrors GetRegime() in PatternDiscoveryEA.mq5.                   |
//+------------------------------------------------------------------+
#property copyright "BETTER DISCOVERY v0.7"
#property version   "1.00"
#property strict
#property indicator_separate_window
#property indicator_minimum -0.5
#property indicator_maximum  4.5
#property indicator_buffers 1
#property indicator_plots   1
#property indicator_label1  "regime"
#property indicator_type1   DRAW_HISTOGRAM
#property indicator_color1  clrLightSteelBlue
#property indicator_width1  3

double RegBuf[];

int g_hEMA20 = INVALID_HANDLE;
int g_hEMA50 = INVALID_HANDLE;
int g_hEMA200= INVALID_HANDLE;
int g_hATR   = INVALID_HANDLE;
int g_hBB    = INVALID_HANDLE;

int OnInit()
  {
   SetIndexBuffer(0, RegBuf, INDICATOR_DATA);
   PlotIndexSetString(0, PLOT_LABEL, "regime");
   g_hEMA20  = iMA   (_Symbol, _Period, 20,  0, MODE_EMA,  PRICE_CLOSE);
   g_hEMA50  = iMA   (_Symbol, _Period, 50,  0, MODE_EMA,  PRICE_CLOSE);
   g_hEMA200 = iMA   (_Symbol, _Period, 200, 0, MODE_EMA,  PRICE_CLOSE);
   g_hATR    = iATR  (_Symbol, _Period, 14);
   g_hBB     = iBands(_Symbol, _Period, 20, 0, 2.0, PRICE_CLOSE);
   if(g_hEMA20 == INVALID_HANDLE || g_hEMA50 == INVALID_HANDLE ||
      g_hEMA200== INVALID_HANDLE || g_hATR   == INVALID_HANDLE ||
      g_hBB    == INVALID_HANDLE)
     { Print("BD_Regime: failed to create indicator handles"); return INIT_FAILED; }
   PlotIndexSetInteger(0, PLOT_DRAW_BEGIN, 220);
   IndicatorSetString(INDICATOR_SHORTNAME, "BD Regime [0=TrU 1=TrD 2=Sqz 3=WideV 4=Chop]");
   IndicatorSetInteger(INDICATOR_DIGITS, 0);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   if(g_hEMA20  != INVALID_HANDLE) IndicatorRelease(g_hEMA20);
   if(g_hEMA50  != INVALID_HANDLE) IndicatorRelease(g_hEMA50);
   if(g_hEMA200 != INVALID_HANDLE) IndicatorRelease(g_hEMA200);
   if(g_hATR    != INVALID_HANDLE) IndicatorRelease(g_hATR);
   if(g_hBB     != INVALID_HANDLE) IndicatorRelease(g_hBB);
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
   if(BarsCalculated(g_hATR)    < rates_total) return prev_calculated;
   if(BarsCalculated(g_hBB)     < rates_total) return prev_calculated;

   double e20[], e50[], e200[], atr[], bbm[], bbu[], bbl[];
   if(CopyBuffer(g_hEMA20,  0, 0, rates_total, e20)  <= 0) return prev_calculated;
   if(CopyBuffer(g_hEMA50,  0, 0, rates_total, e50)  <= 0) return prev_calculated;
   if(CopyBuffer(g_hEMA200, 0, 0, rates_total, e200) <= 0) return prev_calculated;
   if(CopyBuffer(g_hATR,    0, 0, rates_total, atr)  <= 0) return prev_calculated;
   if(CopyBuffer(g_hBB,     0, 0, rates_total, bbm)  <= 0) return prev_calculated;
   if(CopyBuffer(g_hBB,     1, 0, rates_total, bbu)  <= 0) return prev_calculated;
   if(CopyBuffer(g_hBB,     2, 0, rates_total, bbl)  <= 0) return prev_calculated;

   int start = (prev_calculated > need) ? prev_calculated - 1 : need;
   for(int i = start; i < rates_total; i++)
     {
      double tr = 0.0;
      if(e20[i] > e50[i] && e50[i] > e200[i]) tr =  1.0;
      else if(e20[i] < e50[i] && e50[i] < e200[i]) tr = -1.0;
      double bw     = (bbm[i] > 0.0) ? (bbu[i] - bbl[i]) / bbm[i] : 0.0;
      double atrPct = (close[i] > 0.0) ? atr[i] / close[i] : 0.0;
      double r = 4.0;
      if(tr ==  1.0 && atrPct < 0.005) r = 0.0;
      else if(tr == -1.0 && atrPct < 0.005) r = 1.0;
      else if(MathAbs(tr) < 0.5 && bw < 0.02) r = 2.0;
      else if(atrPct >= 0.008) r = 3.0;
      RegBuf[i] = r;
     }
   return rates_total;
  }
