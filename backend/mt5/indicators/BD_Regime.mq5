//+------------------------------------------------------------------+
//|  BD_Regime.mq5                                                    |
//|  Categorical market regime [0..4]:                                |
//|     0 = TrendUp        EMA20>EMA50>EMA200 AND high ATR%           |
//|     1 = TrendDown      EMA20<EMA50<EMA200 AND high ATR%           |
//|     2 = Squeeze        BB_width<Q25 AND low ATR%                  |
//|     3 = WideVol        flat trend AND BB_width>Q75                |
//|     4 = Choppy         (everything else)                          |
//|                                                                  |
//|  Uses the same ATR% median(200) and BB quantiles(100) as Python.   |
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

double LinearQuantile(double &values[], double q)
  {
   int n = ArraySize(values);
   ArraySort(values);
   double rankPos = (n - 1) * q;
   int lower = (int)MathFloor(rankPos);
   int upper = (int)MathCeil(rankPos);
   if(lower == upper) return values[lower];
   double weight = rankPos - lower;
   return values[lower] * (1.0 - weight) + values[upper] * weight;
  }

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

      double atrWindow[200], bbWindow[100];
      for(int k = 0; k < 200; k++)
         atrWindow[k] = (close[i-k] > 0.0) ? atr[i-k] / close[i-k] : 0.0;
      for(int k = 0; k < 100; k++)
         bbWindow[k] = (bbm[i-k] > 0.0) ? (bbu[i-k] - bbl[i-k]) / bbm[i-k] : 0.0;
      double atrMedian = LinearQuantile(atrWindow, 0.50);
      double bbQ25 = LinearQuantile(bbWindow, 0.25);
      double bbQ75 = LinearQuantile(bbWindow, 0.75);
      bool hiVol = atrPct > atrMedian * 1.1;
      bool loVol = atrPct < atrMedian * 0.9;

      double r = 4.0;
      if(tr ==  1.0 && hiVol) r = 0.0;
      else if(tr == -1.0 && hiVol) r = 1.0;
      else if(MathAbs(tr) < 0.5 && bw > bbQ75) r = 3.0;
      else if(bw < bbQ25 && loVol) r = 2.0;
      RegBuf[i] = r;
     }
   return rates_total;
  }
