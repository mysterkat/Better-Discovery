//+------------------------------------------------------------------+
//|  BD_POCdist.mq5                                                   |
//|  Distance from a 100-bar Volume Profile POC, as % of close.       |
//|                                                                  |
//|  Algorithm:                                                       |
//|    1. Take the price range (min low .. max high) over LOOKBACK.   |
//|    2. Bin volume by typical_price into BINS=20 buckets.           |
//|    3. POC = midpoint of the bucket with the most volume.          |
//|    4. dist = (close - POC) / close                                |
//|                                                                  |
//|  Mirrors GetPOCdist() in PatternDiscoveryEA.mq5 (FIX v3) and the  |
//|  poc_dist feature in pattern_discovery_v6.py.                     |
//+------------------------------------------------------------------+
#property copyright "BETTER DISCOVERY v0.7"
#property version   "1.00"
#property strict
#property indicator_separate_window
#property indicator_buffers 1
#property indicator_plots   1
#property indicator_label1  "poc_dist"
#property indicator_type1   DRAW_LINE
#property indicator_color1  clrTomato
#property indicator_width1  2

#property indicator_level1  0.0
#property indicator_levelcolor clrDimGray
#property indicator_levelstyle STYLE_DOT

input int InpLookback = 100;
input int InpBins     = 20;

double DistBuf[];

int OnInit()
  {
   if(InpBins < 4 || InpBins > 100)
     { Print("BD_POCdist: InpBins must be in [4,100]"); return INIT_PARAMETERS_INCORRECT; }
   SetIndexBuffer(0, DistBuf, INDICATOR_DATA);
   PlotIndexSetString(0, PLOT_LABEL, "poc_dist");
   PlotIndexSetInteger(0, PLOT_DRAW_BEGIN, InpLookback);
   IndicatorSetString(INDICATOR_SHORTNAME,
       StringFormat("BD POCdist(%dbar / %dbins)", InpLookback, InpBins));
   IndicatorSetInteger(INDICATOR_DIGITS, 5);
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

   double binVol[];
   ArrayResize(binVol, InpBins);

   for(int i = start; i < rates_total; i++)
     {
      // Range across bars [i-LOOKBACK+1 .. i]
      double price_lo = low [i];
      double price_hi = high[i];
      for(int k = 1; k < InpLookback; k++)
        {
         int idx = i - k;
         if(high[idx] > price_hi) price_hi = high[idx];
         if(low [idx] < price_lo) price_lo = low [idx];
        }
      if(price_hi <= price_lo) { DistBuf[i] = 0.0; continue; }
      double bin_size = (price_hi - price_lo) / InpBins;

      ArrayInitialize(binVol, 0.0);
      for(int k = 0; k < InpLookback; k++)
        {
         int idx = i - k;
         double tp = (high[idx] + low[idx] + close[idx]) / 3.0;
         int bi = (int)((tp - price_lo) / bin_size);
         if(bi < 0)        bi = 0;
         if(bi >= InpBins) bi = InpBins - 1;
         binVol[bi] += (double)tick_volume[idx];
        }

      int pocBin = 0; double maxVol = binVol[0];
      for(int b = 1; b < InpBins; b++)
         if(binVol[b] > maxVol) { maxVol = binVol[b]; pocBin = b; }

      double poc = price_lo + (pocBin + 0.5) * bin_size;
      if(poc <= 0.0 || close[i] <= 0.0) { DistBuf[i] = 0.0; continue; }
      DistBuf[i] = (close[i] - poc) / close[i];
     }
   return rates_total;
  }
