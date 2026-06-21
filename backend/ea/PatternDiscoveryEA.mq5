//+------------------------------------------------------------------+
//|  PatternDiscoveryEA.mq5                                          |
//|  Universal EA — loads Pattern Discovery v5 .set files            |
//|  Supports: RSI14, MACD_norm, ATR_pct, BB_width, EMA trend,      |
//|  MTF bull score (M5+H1), Body/Range, Range/ATR, Volume/MA20,    |
//|  Vol x Body, Market regime, Vol-price div, BB expanding,         |
//|  Prev session bias, POC dist, Bullish candle, Upper/Lower wick,  |
//|  Stoch %K/%D, Pin-bar, Inside/Outside bar, HTF RSI div,         |
//|  Rolling Sharpe, S/D zone, VWAP dist                            |
//|  Session filter, SL/TP %, Breakeven, Trailing, Cooldown,        |
//|  Direction discriminator (any column), Long/Short/Auto          |
//|                                                                  |
//|  TIMEFRAMES USED (v4 multi-TF):                                  |
//|    PERIOD_CURRENT      — PRIMARY: bar stream + all M5 features  |
//|    SignalTF1..SignalTF4 — up to 4 SIGNAL TFs (user-configurable) |
//|       Each signal TF contributes:                                |
//|         · trend (EMA20>50>200) — added to mtf_bull_score        |
//|         · RSI14 — first active signal feeds htf_div             |
//|       Set a slot to PERIOD_CURRENT to disable it.                |
//|    mtf_bull_score range = 0..(1 + active signals); max 5.       |
//|                                                                  |
//|  COLUMN INDEX TABLE (Discrim_Col reference):                     |
//|   0=rsi14          1=macd_norm       2=atr_pct      3=bb_width  |
//|   4=trend          5=mtf_bull_score [0..N+1]  6=body_pct  7=rng_atr  |
//|   8=vol_ratio      9=vol_body_conf  10=regime      11=vol_p_div |
//|  12=bb_expanding  13=prev_sess_bias 14=poc_dist    15=bull      |
//|  16=uwk_pct       17=lwk_pct       18=stoch_k     19=stoch_d   |
//|  20=pin_bar       21=inside_bar    22=outside_bar  23=htf_div   |
//|  24=rolling_sharpe 25=sd_zone      26=vwap_dist                 |
//|                                                                  |
//|  CHANGELOG:                                                      |
//|  v4.01 — Modeled costs: input Commission_R / Swap_R_PerBar (.set names)|
//|  v4.00 — N-TF support: replaced hardcoded M15+H1 with up to 4   |
//|           configurable SignalTF inputs. mtf_bull_score is now    |
//|           additive across all active signal TFs (range 0..N+1). |
//|           GetHtfDiv uses the first active SignalTF's RSI.       |
//|  v3.03 — Added: HoursBan — comma-separated UTC hours where no  |
//|           new trades are opened (e.g. "1,4,14" bans 01-02,    |
//|           04-05, 14-15 UTC). Default "" = disabled.            |
//|         — Added: EODCloseEnabled / EODCloseHour — closes ALL   |
//|           open positions once UTC hour >= EODCloseHour and     |
//|           blocks new entries for the rest of that bar.         |
//|  v3.02 — Fixed: GetMacdNorm used buffer 2 (doesn't exist in    |
//|           MT5's iMACD — only buf0=MACD, buf1=Signal). Now      |
//|           computes histogram as (buf0 - buf1) / ATR.           |
//|           This was silently blocking ALL entries when macd_norm |
//|           filter was active, returning EMPTY_VALUE every bar.  |
//|  v3.01 — Fixed: macd_norm (feat[1]) removed from critical guard |
//|         — MACD needs ~34 extra warmup bars; excluding it stops  |
//|           EMPTY_VALUE spam blocking all entries during warmup.   |
//|         — InRange() already blocks entry if MACD filter active  |
//|           and value is EMPTY, so safety is preserved.           |
//|  v3.00 — Fixed: file now compiles as a complete EA (OnTick etc) |
//|         — Fixed: Stochastic params corrected (K=14,D=3,slow=1)  |
//|         — Fixed: GetVolPriceDiv sign logic corrected            |
//|         — Fixed: GetSDzone now includes bar[shift] in lookback  |
//|         — Fixed: HasOpenPosition cooldown fires on trade close  |
//|         — Fixed: GetPOCdist improved to 100-bar M5 vol-profile  |
//|         — Added: OnTradeTransaction for immediate close detect  |
//|         — Added: MaxSpreadPoints guard before entry             |
//|         — Added: daily loss limit (MaxDailyLossR)              |
//|         — Added: max open positions guard                       |
//|         — Added: feature value logging on every new entry signal|
//|         — Added: warmup bar guard (MinBarsRequired)            |
//|         — Improved: session boundary logic handles midnight edge|
//|         — Improved: GetMTFbull returns 0 on H1 EMPTY (not skip)|
//+------------------------------------------------------------------+
#property copyright "Pattern Discovery v6"
#property version   "4.01"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\DealInfo.mqh>

//--- Trade objects
CTrade        trade;
CPositionInfo pos;
CDealInfo     deal;

//==================================================================//
//  INPUT PARAMETERS  (names must match COL_TO_EA mapping exactly)  //
//==================================================================//
// @BD_INPUT_BEGIN  (Set-to-MQL converter replaces through @BD_INPUT_END only)

//--- Identity
input long   MagicNumber         = 10001;

//--- Signal timeframes (multi-TF)
//    Each non-PERIOD_CURRENT slot becomes an active signal TF whose
//    trend (EMA20>50>200) contributes to mtf_bull_score. htf_div uses
//    the slowest active slot by default, matching discovery auto mode.
//    Set a slot to PERIOD_CURRENT to disable it.
input ENUM_TIMEFRAMES SignalTF1   = PERIOD_M15;
input ENUM_TIMEFRAMES SignalTF2   = PERIOD_H1;
input ENUM_TIMEFRAMES SignalTF3   = PERIOD_CURRENT;
input ENUM_TIMEFRAMES SignalTF4   = PERIOD_CURRENT;
input int    HtfDivSignalSlot     = 0; // 0=slowest active, 1..4=active slot

//--- Direction
//    0 = LongOnly  |  1 = ShortOnly  |  2 = Auto (discriminator)
input int    DirectionMode       = 1;

//--- Risk
input double SL_Pct              = 0.005220;
input double TP_Pct              = 0.003630;
input double Lots                = 0.10;

//--- Trading costs (R multiples) — names match discovery .set (Commission_R, Swap_R_PerBar)
input double Commission_R        = 0.0;
input double Swap_R_PerBar       = 0.0;

//--- Cooldown
input int    CooldownBars        = 3;

//--- Breakeven / Trailing
input double BreakevenAtR        = 0.0;
input bool   UseTrailing         = false;
input double TrailingStart       = 1.0;
input double TrailingStep        = 0.5;

//--- Max hold — force-close a position after this many fully-closed bars if
//    neither SL nor TP has been hit. Mirrors the discovery simulator's
//    MAX_HOLD_BARS so live trades exit on the same schedule the .set was
//    scored under. 0 = disabled (hold until SL/TP only).
input int    MaxHoldBars         = 0;

//--- Session filter (UTC hours)
input bool   TradeAsian          = true;
input bool   TradeLondon         = true;
input bool   TradeNY             = true;
input bool   TradeOverlap        = true;
input bool   TradeOff            = true;

//--- Direction discriminator (only used when DirectionMode == 2)
//    Discrim_Col: see COLUMN INDEX TABLE in header above
input int    Discrim_Col         = 1;
input double Discrim_Thresh      = 0.012;
input int    Discrim_Dir         = 1;   // 1=col>thresh->LONG | -1=col>thresh->SHORT

//--- Risk controls
input double MaxSpreadPoints     = 30.0;  // Skip entry if spread > this (0=disabled)
input double MaxDailyLossR       = 0.0;   // Max daily loss in R units (0=disabled)
input int    MaxOpenPositions    = 1;     // Max simultaneous positions for this magic

//--- Debug
input bool   DebugMode           = false;   // Enable per-bar filter diagnostics in terminal

//--- Hours ban (LOCAL PC time)
//    Comma-separated LOCAL hours where NO new trades will be opened.
//    Each entry bans the full clock-hour: "1" = 01:00-02:00 local.
//    Example: "1,4,14" bans 01-02, 04-05, 14-15 local time.
//    Leave empty ("") to disable.
//    Uses TimeLocal() — matches your Windows system clock.
input string HoursBan            = "";

//--- End-of-day close (LOCAL PC time)
//    When enabled, all open positions are closed once the LOCAL hour
//    reaches EODCloseHour, and no new entries are allowed after that.
//    Uses TimeLocal() — matches your Windows system clock.
input bool   EODCloseEnabled     = false;
input int    EODCloseHour        = 22;     // local hour (0-23) — e.g. 22 = 22:00 local time

//--- Entry filter: RSI(14) [0-100]
input double rsi14_lo            = 0.0;
input double rsi14_hi            = 100.0;

//--- Entry filter: MACD_hist/ATR [normalised]
input double macd_norm_lo        = -999.0;
input double macd_norm_hi        =  999.0;

//--- Entry filter: ATR/Close [volatility %]
input double atr_pct_lo          = -999.0;
input double atr_pct_hi          =  999.0;

//--- Entry filter: BB width/midline [squeeze<0.01]
input double bb_width_lo         = -999.0;
input double bb_width_hi         =  999.0;

//--- Entry filter: EMA trend [-1=dn 0=range 1=up]
input double trend_lo            = -999.0;
input double trend_hi            =  999.0;

//--- Entry filter: MTF bull score [0..1 + active signal TFs]
//    With default config (M15 + H1 signals), range is 0..3.
//    Range scales up when more signal TFs are configured (max 0..5).
input double mtf_bull_score_lo   = -999.0;
input double mtf_bull_score_hi   =  999.0;

//--- Entry filter: Body/Range [0-1]
input double body_pct_lo         = -999.0;
input double body_pct_hi         =  999.0;

//--- Entry filter: Range/ATR [1=avg bar]
input double rng_atr_lo          = -999.0;
input double rng_atr_hi          =  999.0;

//--- Entry filter: Volume/MA20 [1=avg vol]
input double vol_ratio_lo        = -999.0;
input double vol_ratio_hi        =  999.0;

//--- Entry filter: Vol x Body [confirmation]
input double vol_body_conf_lo    = -999.0;
input double vol_body_conf_hi    =  999.0;

//--- Entry filter: Market regime [0=TrendUp..4=Choppy]
input double regime_lo           = -999.0;
input double regime_hi           =  999.0;

//--- Entry filter: Vol-price diverge [+1=accum -1=distrib]
input double vol_price_div_lo    = -999.0;
input double vol_price_div_hi    =  999.0;

//--- Entry filter: BB expanding [0=no 1=yes]
input double bb_expanding_lo     = -999.0;
input double bb_expanding_hi     =  999.0;

//--- Entry filter: Prev session bias [-1=bear 0=flat 1=bull]
input double prev_sess_bias_lo   = -999.0;
input double prev_sess_bias_hi   =  999.0;

//--- Entry filter: Dist from POC [% of price]
input double poc_dist_lo         = -999.0;
input double poc_dist_hi         =  999.0;

//--- Entry filter: Bullish candle [0=bear 1=bull]
input double bull_lo             = -999.0;
input double bull_hi             =  999.0;

//--- Entry filter: Upper wick / Range [0-1]
input double uwk_pct_lo          = -999.0;
input double uwk_pct_hi          =  999.0;

//--- Entry filter: Lower wick / Range [0-1]
input double lwk_pct_lo          = -999.0;
input double lwk_pct_hi          =  999.0;

//--- Entry filter: Stochastic %K [0-100]
input double stoch_k_lo          = -999.0;
input double stoch_k_hi          =  999.0;

//--- Entry filter: Stochastic %D [0-100]
input double stoch_d_lo          = -999.0;
input double stoch_d_hi          =  999.0;

//--- Entry filter: Pin-bar score [0-1]
input double pin_bar_lo          = -999.0;
input double pin_bar_hi          =  999.0;

//--- Entry filter: Inside bar [0=no 1=yes]
input double inside_bar_lo       = -999.0;
input double inside_bar_hi       =  999.0;

//--- Entry filter: Outside bar [0=no 1=yes]
input double outside_bar_lo      = -999.0;
input double outside_bar_hi      =  999.0;

//--- Entry filter: HTF RSI divergence [+1=bull -1=bear 0=none]
input double htf_div_lo          = -999.0;
input double htf_div_hi          =  999.0;

//--- Entry filter: Rolling Sharpe(20) [risk-adj momentum]
input double rolling_sharpe_lo   = -999.0;
input double rolling_sharpe_hi   =  999.0;

//--- Entry filter: S/D zone proximity [+1=near supp -1=near res]
input double sd_zone_lo          = -999.0;
input double sd_zone_hi          =  999.0;

//--- Entry filter: VWAP distance [% from VWAP, 0 if no vol]
input double vwap_dist_lo        = -999.0;
input double vwap_dist_hi        =  999.0;

// @BD_INPUT_END

//==================================================================//
//  GLOBALS                                                          //
//==================================================================//

//--- Indicator handles — PERIOD_CURRENT (M5)
int    g_hRSI;        // RSI(14)
int    g_hMACD;       // MACD(12,26,9) — buf 0=main 1=signal 2=histogram
int    g_hATR;        // ATR(14)
int    g_hBB;         // Bollinger Bands(20,2) — buf 0=mid 1=upper 2=lower
int    g_hEMA20;      // EMA(20) — needed for trend: EMA20>EMA50>EMA200
int    g_hEMA50;      // EMA(50)
int    g_hEMA200;     // EMA(200)
int    g_hStoch;      // Stochastic(14,3,1) — buf 0=%K  buf 1=%D

//--- Indicator handles — signal TFs (parallel arrays, slot 0..g_nSignals-1)
//    Populated in OnInit() from SignalTF1..SignalTF4 inputs (any slot set
//    to PERIOD_CURRENT is skipped). g_signalTFs[i] holds the chosen TF.
//    g_hRSI_signal feeds htf_div from the FIRST active slot.
//    g_hEMA*_signal feed mtf_bull_score from EVERY active slot (additive).
#define MAX_SIGNAL_TFS 4
int               g_hRSI_signal   [MAX_SIGNAL_TFS];
int               g_hEMA20_signal [MAX_SIGNAL_TFS];
int               g_hEMA50_signal [MAX_SIGNAL_TFS];
int               g_hEMA200_signal[MAX_SIGNAL_TFS];
ENUM_TIMEFRAMES   g_signalTFs     [MAX_SIGNAL_TFS];
int               g_nSignals = 0;

//--- Minimum bars needed before any features are valid
//    EMA(200) needs 200 bars + extra safety margin
#define MIN_BARS_REQUIRED 220

//--- State
int    g_cooldownBar     = -1;
double g_slDist          = 0.0;
ulong  g_openTicket      = 0;
datetime g_entryBarTime  = 0;     // open time of the bar we entered on (MaxHoldBars)
double g_dailyLossR      = 0.0;   // accumulated loss today in R units
datetime g_dailyResetTime = 0;    // UTC midnight of current trading day

//+------------------------------------------------------------------+
//| Detect order filling mode                                        |
//+------------------------------------------------------------------+
ENUM_ORDER_TYPE_FILLING GetFillMode()
  {
   long fillFlags = SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((fillFlags & SYMBOL_FILLING_FOK) != 0) return ORDER_FILLING_FOK;
   if((fillFlags & SYMBOL_FILLING_IOC) != 0) return ORDER_FILLING_IOC;
   return ORDER_FILLING_RETURN;
  }

//+------------------------------------------------------------------+
//| Expert initialisation                                            |
//+------------------------------------------------------------------+
int OnInit()
  {
   if(SL_Pct <= 0.0 || TP_Pct <= 0.0)
     { Print("ERROR: SL_Pct and TP_Pct must be > 0."); return INIT_PARAMETERS_INCORRECT; }
   if(Lots <= 0.0)
     { Print("ERROR: Lots must be > 0."); return INIT_PARAMETERS_INCORRECT; }
   if(DirectionMode < 0 || DirectionMode > 2)
     { Print("ERROR: DirectionMode must be 0, 1, or 2."); return INIT_PARAMETERS_INCORRECT; }
   if(DirectionMode == 2 && (Discrim_Dir != 1 && Discrim_Dir != -1))
     { Print("ERROR: Discrim_Dir must be +1 or -1 when DirectionMode=2."); return INIT_PARAMETERS_INCORRECT; }

   //--- M5 handles
   g_hRSI     = iRSI      (_Symbol, PERIOD_CURRENT, 14, PRICE_CLOSE);
   g_hMACD    = iMACD     (_Symbol, PERIOD_CURRENT, 12, 26, 9, PRICE_CLOSE);
   g_hATR     = iATR      (_Symbol, PERIOD_CURRENT, 14);
   g_hBB      = iBands    (_Symbol, PERIOD_CURRENT, 20, 0, 2.0, PRICE_CLOSE);
   g_hEMA20   = iMA       (_Symbol, PERIOD_CURRENT, 20,  0, MODE_EMA, PRICE_CLOSE);
   g_hEMA50   = iMA       (_Symbol, PERIOD_CURRENT, 50,  0, MODE_EMA, PRICE_CLOSE);
   g_hEMA200  = iMA       (_Symbol, PERIOD_CURRENT, 200, 0, MODE_EMA, PRICE_CLOSE);
   // Stochastic: K period=14, slowing=1 (fast/raw K), D period=3 SMA — matches Python algo
   // MT5 iStochastic arg order: (symbol, tf, Kperiod, Dperiod, slowing, ma_method, price_field)
   // buf 0 = main (%K after slowing),  buf 1 = signal (%D)
   g_hStoch   = iStochastic(_Symbol, PERIOD_CURRENT, 14, 3, 1, MODE_SMA, STO_LOWHIGH);

   //--- Signal TF handles (multi-TF). Skip slots set to PERIOD_CURRENT.
   ENUM_TIMEFRAMES configured[MAX_SIGNAL_TFS];
   configured[0] = SignalTF1;
   configured[1] = SignalTF2;
   configured[2] = SignalTF3;
   configured[3] = SignalTF4;
   g_nSignals = 0;
   for(int i = 0; i < MAX_SIGNAL_TFS; i++)
     {
      if(configured[i] == PERIOD_CURRENT) continue;
      g_signalTFs     [g_nSignals] = configured[i];
      g_hRSI_signal   [g_nSignals] = iRSI(_Symbol, configured[i], 14, PRICE_CLOSE);
      g_hEMA20_signal [g_nSignals] = iMA (_Symbol, configured[i], 20,  0, MODE_EMA, PRICE_CLOSE);
      g_hEMA50_signal [g_nSignals] = iMA (_Symbol, configured[i], 50,  0, MODE_EMA, PRICE_CLOSE);
      g_hEMA200_signal[g_nSignals] = iMA (_Symbol, configured[i], 200, 0, MODE_EMA, PRICE_CLOSE);
      if(g_hRSI_signal[g_nSignals]   == INVALID_HANDLE ||
         g_hEMA20_signal[g_nSignals] == INVALID_HANDLE ||
         g_hEMA50_signal[g_nSignals] == INVALID_HANDLE ||
         g_hEMA200_signal[g_nSignals]== INVALID_HANDLE)
        {
         PrintFormat("ERROR: failed to create signal-TF handles for slot %d (TF=%s)",
                     i + 1, EnumToString(configured[i]));
         return INIT_FAILED;
        }
      g_nSignals++;
     }
   PrintFormat("Signal TFs active: %d", g_nSignals);

   if(g_hRSI    == INVALID_HANDLE || g_hMACD    == INVALID_HANDLE ||
      g_hATR    == INVALID_HANDLE || g_hBB      == INVALID_HANDLE ||
      g_hEMA20  == INVALID_HANDLE || g_hEMA50   == INVALID_HANDLE ||
      g_hEMA200 == INVALID_HANDLE || g_hStoch   == INVALID_HANDLE)
     {
      Print("ERROR: Failed to create one or more indicator handles.");
      return INIT_FAILED;
     }

   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(10);
   trade.SetTypeFilling(GetFillMode());

   //--- Reset daily loss tracking
   g_dailyLossR      = 0.0;
   g_dailyResetTime  = GetDayStartUTC();

   string dirLabel = (DirectionMode==0)?"LONG_ONLY":(DirectionMode==1)?"SHORT_ONLY":"AUTO_DISC";
   PrintFormat("PatternDiscoveryEA v3 initialised | Magic=%d | Dir=%s | SL=%.4f%% TP=%.4f%% | DebugMode=%s | Spread<=%.0f pts",
               MagicNumber, dirLabel, SL_Pct*100.0, TP_Pct*100.0,
               DebugMode?"ON":"OFF", MaxSpreadPoints);

   //--- Print all active entry filters so you can verify .set file loaded correctly
   string f_names[] = {"rsi14","macd_norm","atr_pct","bb_width","trend","mtf_bull_score",
                        "body_pct","rng_atr","vol_ratio","vol_body_conf","regime","vol_price_div",
                        "bb_expanding","prev_sess_bias","poc_dist","bull","uwk_pct","lwk_pct",
                        "stoch_k","stoch_d","pin_bar","inside_bar","outside_bar","htf_div",
                        "rolling_sharpe","sd_zone","vwap_dist"};
   double f_lo[] = {rsi14_lo,macd_norm_lo,atr_pct_lo,bb_width_lo,trend_lo,mtf_bull_score_lo,
                    body_pct_lo,rng_atr_lo,vol_ratio_lo,vol_body_conf_lo,regime_lo,vol_price_div_lo,
                    bb_expanding_lo,prev_sess_bias_lo,poc_dist_lo,bull_lo,uwk_pct_lo,lwk_pct_lo,
                    stoch_k_lo,stoch_d_lo,pin_bar_lo,inside_bar_lo,outside_bar_lo,htf_div_lo,
                    rolling_sharpe_lo,sd_zone_lo,vwap_dist_lo};
   double f_hi[] = {rsi14_hi,macd_norm_hi,atr_pct_hi,bb_width_hi,trend_hi,mtf_bull_score_hi,
                    body_pct_hi,rng_atr_hi,vol_ratio_hi,vol_body_conf_hi,regime_hi,vol_price_div_hi,
                    bb_expanding_hi,prev_sess_bias_hi,poc_dist_hi,bull_hi,uwk_pct_hi,lwk_pct_hi,
                    stoch_k_hi,stoch_d_hi,pin_bar_hi,inside_bar_hi,outside_bar_hi,htf_div_hi,
                    rolling_sharpe_hi,sd_zone_hi,vwap_dist_hi};
   string active_str = "";
   int    active_cnt = 0;
   for(int _i = 0; _i < 27; _i++)
     {
      if(!(f_lo[_i] <= -999.0 && f_hi[_i] >= 999.0))
        {
         if(active_str != "") active_str += " | ";
         active_str += StringFormat("%s=[%.4g, %.4g]", f_names[_i], f_lo[_i], f_hi[_i]);
         active_cnt++;
        }
     }
   if(active_cnt > 0)
      PrintFormat("Active entry filters (%d/27): %s", active_cnt, active_str);
   else
      Print("WARNING: No active entry filters — EA will signal on every bar!");

   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
//| Expert deinitialisation                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   IndicatorRelease(g_hRSI);
   IndicatorRelease(g_hMACD);
   IndicatorRelease(g_hATR);
   IndicatorRelease(g_hBB);
   IndicatorRelease(g_hEMA20);
   IndicatorRelease(g_hEMA50);
   IndicatorRelease(g_hEMA200);
   IndicatorRelease(g_hStoch);
   for(int i = 0; i < g_nSignals; i++)
     {
      IndicatorRelease(g_hRSI_signal[i]);
      IndicatorRelease(g_hEMA20_signal[i]);
      IndicatorRelease(g_hEMA50_signal[i]);
      IndicatorRelease(g_hEMA200_signal[i]);
     }
  }

//+------------------------------------------------------------------+
//| Expert tick                                                      |
//+------------------------------------------------------------------+
void OnTick()
  {
   if(!IsNewBar()) return;

   //--- Reset daily loss counter at UTC midnight
   ResetDailyLossIfNeeded();

   ManageOpenPosition();

   //--- EOD close: shut down all positions once close hour is reached
   //    and block any new entries for the rest of this session.
   if(EODCloseEnabled)
     {
      MqlDateTime _eodDt;
      TimeToStruct(TimeLocal(), _eodDt);   // local PC clock
      if(_eodDt.hour >= EODCloseHour)
        {
         if(HasOpenPosition())
           {
            PrintFormat("EOD close: local %02d:xx >= EODCloseHour %02d — closing all positions.",
                        _eodDt.hour, EODCloseHour);
            CloseAllPositions();
           }
         return;  // no new entries after close hour
        }
     }

   if(HasOpenPosition()) return;

   //--- Warmup guard — EMA(200) and other long-period indicators need history
   if(Bars(_Symbol, PERIOD_CURRENT) < MIN_BARS_REQUIRED)
     {
      if(DebugMode) PrintFormat("[DBG] SKIP — Warmup: %d bars available, need %d",
                               Bars(_Symbol, PERIOD_CURRENT), MIN_BARS_REQUIRED);
      return;
     }

   //--- Cooldown guard
   int currentBar = Bars(_Symbol, PERIOD_CURRENT) - 1;
   if(g_cooldownBar >= 0 && (currentBar - g_cooldownBar) < CooldownBars)
     {
      if(DebugMode) PrintFormat("[DBG] SKIP — Cooldown: %d bars remaining (%d/%d)",
                               CooldownBars - (currentBar - g_cooldownBar),
                               currentBar - g_cooldownBar, CooldownBars);
      return;
     }

   //--- Daily loss limit
   if(MaxDailyLossR > 0.0 && g_dailyLossR >= MaxDailyLossR)
     {
      static datetime lastDailyLimitMsg = 0;
      if(TimeCurrent() - lastDailyLimitMsg > 3600)
        {
         PrintFormat("Daily loss limit reached: %.2fR of %.2fR max. No new entries today.", g_dailyLossR, MaxDailyLossR);
         lastDailyLimitMsg = TimeCurrent();
        }
      return;
     }

   //--- Max positions guard
   if(CountOpenPositions() >= MaxOpenPositions)
     {
      if(DebugMode) PrintFormat("[DBG] SKIP — MaxOpenPositions reached (%d/%d)",
                               CountOpenPositions(), MaxOpenPositions);
      return;
     }

   //--- Session filter
   if(!IsSessionAllowed())
     {
      static datetime _lastSessMsg = 0;
      if(TimeCurrent() - _lastSessMsg > 3600)
        {
         MqlDateTime _dt; TimeToStruct(TimeGMT(), _dt);
         PrintFormat("SKIP — Session filter: UTC hour=%d not in allowed session (this message throttled 1/hr)", _dt.hour);
         _lastSessMsg = TimeCurrent();
        }
      return;
     }

   //--- Hours ban check (UTC hours where new entries are blocked)
   if(IsHourBanned())
     {
      static datetime _lastBanMsg = 0;
      if(TimeCurrent() - _lastBanMsg > 3600)
        {
         MqlDateTime _bDt; TimeToStruct(TimeLocal(), _bDt);
         PrintFormat("SKIP — HoursBan: local hour=%d is in banned list \"%s\" (msg throttled 1/hr)",
                     _bDt.hour, HoursBan);
         _lastBanMsg = TimeCurrent();
        }
      return;
     }

   //--- Spread filter
   if(MaxSpreadPoints > 0.0)
     {
      double spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD) * _Point / _Point; // in points
      double spreadPts = (double)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
      if(spreadPts > MaxSpreadPoints)
        {
         PrintFormat("Spread filter: %.1f pts > %.1f max. Skipping bar.", spreadPts, MaxSpreadPoints);
         return;
        }
     }

   //--- Compute all 27 feature values at bar index 1 (last fully closed bar)
   double feat[27];
   feat[0]  = GetRSI14(1);
   feat[1]  = GetMacdNorm(1);
   feat[2]  = GetAtrPct(1);
   feat[3]  = GetBBwidth(1);
   feat[4]  = GetEMATrend(1);
   feat[5]  = GetMTFbull(1);
   feat[6]  = GetBodyPct(1);
   feat[7]  = GetRngATR(1);
   feat[8]  = GetVolRatio(1);
   feat[9]  = GetVolBodyConf(1);
   feat[10] = GetRegime(1);
   feat[11] = GetVolPriceDiv(1);
   feat[12] = GetBBexpanding(1);
   feat[13] = GetPrevSessBias(1);
   feat[14] = GetPOCdist(1);
   feat[15] = GetBull(1);
   feat[16] = GetUwkPct(1);
   feat[17] = GetLwkPct(1);
   feat[18] = GetStochK(1);
   feat[19] = GetStochD(1);
   feat[20] = GetPinBar(1);
   feat[21] = GetInsideBar(1);
   feat[22] = GetOutsideBar(1);
   feat[23] = GetHtfDiv(1);
   feat[24] = GetRollingSharpe(1);
   feat[25] = GetSDzone(1);
   feat[26] = GetVwapDist(1);

   //--- Abort if any critical base indicator is unavailable
   //    macd_norm (feat[1]) is intentionally excluded: it needs ~34 extra warmup bars
   //    and InRange() already returns false on EMPTY_VALUE if the filter is active.
   if(feat[0] == EMPTY_VALUE || feat[2] == EMPTY_VALUE || feat[3] == EMPTY_VALUE)
     {
      static datetime _lastEmptyMsg = 0;
      if(TimeCurrent() - _lastEmptyMsg > 300)
        {
         PrintFormat("SKIP — Indicator not ready (check chart history): rsi14=%s atr_pct=%s bb_width=%s",
                     feat[0]==EMPTY_VALUE?"EMPTY":"ok",
                     feat[2]==EMPTY_VALUE?"EMPTY":"ok",
                     feat[3]==EMPTY_VALUE?"EMPTY":"ok");
         _lastEmptyMsg = TimeCurrent();
        }
      return;
     }

   //--- Heartbeat counter — prints every 100 bars that reach filter evaluation
   //    Visible without DebugMode so you know the EA is alive and processing
   static int _barCount = 0;
   _barCount++;
   if(_barCount % 100 == 0)
      PrintFormat("[INFO] %d bars evaluated past pre-guards (no signal yet) | enable DebugMode for per-bar filter detail",
                  _barCount);

   //--- Debug: evaluate all filters without short-circuiting
   if(DebugMode) DebugEntryConditions(feat);

   //--- Apply all entry range filters
   if(!InRange(feat[0],  rsi14_lo,          rsi14_hi))          return;
   if(!InRange(feat[1],  macd_norm_lo,       macd_norm_hi))      return;
   if(!InRange(feat[2],  atr_pct_lo,         atr_pct_hi))        return;
   if(!InRange(feat[3],  bb_width_lo,        bb_width_hi))       return;
   if(!InRange(feat[4],  trend_lo,           trend_hi))          return;
   if(!InRange(feat[5],  mtf_bull_score_lo,  mtf_bull_score_hi)) return;
   if(!InRange(feat[6],  body_pct_lo,        body_pct_hi))       return;
   if(!InRange(feat[7],  rng_atr_lo,         rng_atr_hi))        return;
   if(!InRange(feat[8],  vol_ratio_lo,       vol_ratio_hi))      return;
   if(!InRange(feat[9],  vol_body_conf_lo,   vol_body_conf_hi))  return;
   if(!InRange(feat[10], regime_lo,          regime_hi))         return;
   if(!InRange(feat[11], vol_price_div_lo,   vol_price_div_hi))  return;
   if(!InRange(feat[12], bb_expanding_lo,    bb_expanding_hi))   return;
   if(!InRange(feat[13], prev_sess_bias_lo,  prev_sess_bias_hi)) return;
   if(!InRange(feat[14], poc_dist_lo,        poc_dist_hi))       return;
   if(!InRange(feat[15], bull_lo,            bull_hi))           return;
   if(!InRange(feat[16], uwk_pct_lo,         uwk_pct_hi))        return;
   if(!InRange(feat[17], lwk_pct_lo,         lwk_pct_hi))        return;
   if(!InRange(feat[18], stoch_k_lo,         stoch_k_hi))        return;
   if(!InRange(feat[19], stoch_d_lo,         stoch_d_hi))        return;
   if(!InRange(feat[20], pin_bar_lo,         pin_bar_hi))        return;
   if(!InRange(feat[21], inside_bar_lo,      inside_bar_hi))     return;
   if(!InRange(feat[22], outside_bar_lo,     outside_bar_hi))    return;
   if(!InRange(feat[23], htf_div_lo,         htf_div_hi))        return;
   if(!InRange(feat[24], rolling_sharpe_lo,  rolling_sharpe_hi)) return;
   if(!InRange(feat[25], sd_zone_lo,         sd_zone_hi))        return;
   if(!InRange(feat[26], vwap_dist_lo,       vwap_dist_hi))      return;

   //--- Resolve direction
   int direction = ResolveDirection(feat);
   if(direction == 0) return;

   //--- Log all feature values at signal bar for debugging/verification
   LogFeatures(feat, direction);

   if(direction ==  1) OpenLong();
   if(direction == -1) OpenShort();
  }

//+------------------------------------------------------------------+
//| Trade transaction handler — detects position close immediately   |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest     &request,
                        const MqlTradeResult      &result)
  {
   // Only care about deal additions (position closed or TP/SL hit)
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD) return;

   ulong dealTicket = HistoryDealGetTicket(HistoryDealsTotal() - 1);
   if(dealTicket == 0) return;
   if(!HistoryDealSelect(dealTicket)) return;
   deal.Ticket(dealTicket);
   if(deal.Magic() != (ulong)MagicNumber) return;
   if(deal.Symbol() != _Symbol) return;

   // Only count exit deals (DEAL_ENTRY_OUT means closing a position)
   if(deal.Entry() != DEAL_ENTRY_OUT) return;

   double dealProfit = deal.Profit() + deal.Swap() + deal.Commission();

   //--- Apply the discovery simulator's MODELED costs (Commission_R round-turn
   //    + Swap_R_PerBar * bars held) so the live daily-loss gate matches the
   //    .set's scored metrics. These are in R; convert to money via the same
   //    rValue used below. NOTE: deal.Swap()/deal.Commission() above are the
   //    broker's REAL costs already in dealProfit — these modeled costs are an
   //    ADDITIONAL synthetic charge that exists only to mirror the simulator.
   //    Leave Commission_R and Swap_R_PerBar at 0.0 to avoid double-charging.
   double modeledCostR = 0.0;
   if(Commission_R > 0.0 || Swap_R_PerBar > 0.0)
     {
      int    barsHeld = (g_entryBarTime > 0)
                        ? iBarShift(_Symbol, PERIOD_CURRENT, g_entryBarTime, false) : 0;
      modeledCostR = Commission_R + Swap_R_PerBar * barsHeld;
     }

   //--- Update daily loss counter (track losses in R units)
   if(g_slDist > 0.0)
     {
      double tickValue  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
      double tickSize   = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
      double rValue     = (tickSize > 0.0) ? Lots * g_slDist / tickSize * tickValue : 0.0;
      if(rValue > 0.0)
        {
         // Charge the modeled cost as additional money loss, then book net.
         double dealProfitNet = dealProfit - modeledCostR * rValue;
         if(dealProfitNet < 0.0)
            g_dailyLossR += MathAbs(dealProfitNet) / rValue;
        }
     }

   //--- Set cooldown from transaction (immediate, not waiting for next tick)
   g_cooldownBar  = Bars(_Symbol, PERIOD_CURRENT) - 1;
   g_openTicket   = 0;
   g_slDist       = 0.0;
   g_entryBarTime = 0;

   PrintFormat("Position closed | P&L=%.2f | DailyLossR=%.2f | CooldownBar=%d",
               dealProfit, g_dailyLossR, g_cooldownBar);
  }

//+------------------------------------------------------------------+
//| Helpers — bar detection                                          |
//+------------------------------------------------------------------+
bool IsNewBar()
  {
   static datetime lastBarTime = 0;
   datetime currentBarTime = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(currentBarTime != lastBarTime)
     { lastBarTime = currentBarTime; return true; }
   return false;
  }

//+------------------------------------------------------------------+
//| Helpers — UTC day start (for daily loss reset)                  |
//+------------------------------------------------------------------+
datetime GetDayStartUTC()
  {
   MqlDateTime dt;
   TimeToStruct(TimeGMT(), dt);
   dt.hour = 0; dt.min = 0; dt.sec = 0;
   return StructToTime(dt);
  }

void ResetDailyLossIfNeeded()
  {
   datetime todayStart = GetDayStartUTC();
   if(todayStart > g_dailyResetTime)
     {
      g_dailyLossR     = 0.0;
      g_dailyResetTime = todayStart;
     }
  }

//+------------------------------------------------------------------+
//| Helpers — count open positions for this magic on this symbol     |
//+------------------------------------------------------------------+
int CountOpenPositions()
  {
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
      if(pos.SelectByIndex(i))
         if(pos.Symbol() == _Symbol && pos.Magic() == (ulong)MagicNumber)
            count++;
   return count;
  }

//+------------------------------------------------------------------+
//| Helpers — session filter                                         |
//+------------------------------------------------------------------+
bool IsSessionAllowed()
  {
   MqlDateTime dt;
   TimeToStruct(TimeGMT(), dt);
   int h = dt.hour;

   // Overlap (London/NY crossover): 12:00-16:00 UTC
   // London: 07:00-16:00 UTC (excluding overlap handled above)
   // NY:     12:00-21:00 UTC (excluding overlap handled above)
   // Asian:  00:00-08:00 UTC
   // Off:    everything else (21:00-00:00 UTC)
   bool inOverlap = (h >= 12 && h < 16);
   bool inLondon  = (h >= 7  && h < 16);
   bool inNY      = (h >= 12 && h < 21);
   bool inAsian   = (h >= 0  && h < 8);

   if(inOverlap)              return TradeOverlap;
   if(inLondon && !inOverlap) return TradeLondon;
   if(inNY     && !inOverlap) return TradeNY;
   if(inAsian  && !inLondon)  return TradeAsian;
   return TradeOff;  // 21:00-07:00 UTC (non-Asian non-session)
  }

//+------------------------------------------------------------------+
//| Helpers — hours ban check (LOCAL PC time)                        |
//| Returns true if the current local hour is in the HoursBan list. |
//|                                                                  |
//| Strategy: wrap both the list and the search token in commas so  |
//| ",7," inside ",1,2,7,8," matches correctly and "7" never        |
//| accidentally matches "17" or "27".                              |
//+------------------------------------------------------------------+
bool IsHourBanned()
  {
   if(StringLen(HoursBan) == 0) return false;
   MqlDateTime dt;
   TimeToStruct(TimeLocal(), dt);   // local PC clock

   // Build ",haystack," and ",needle,"
   string haystack = "," + HoursBan + ",";
   string needle   = "," + IntegerToString(dt.hour) + ",";

   return (StringFind(haystack, needle) >= 0);
  }

//+------------------------------------------------------------------+
//| Helpers — close all positions for this magic on this symbol      |
//+------------------------------------------------------------------+
void CloseAllPositions()
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(pos.SelectByIndex(i))
         if(pos.Symbol() == _Symbol && pos.Magic() == (ulong)MagicNumber)
           {
            if(!trade.PositionClose(pos.Ticket()))
               PrintFormat("CloseAllPositions: failed to close #%I64u  err=%d  retcode=%d",
                           pos.Ticket(), GetLastError(), trade.ResultRetcode());
           }
     }
  }

//+------------------------------------------------------------------+
//| Helpers — direction resolution                                   |
//+------------------------------------------------------------------+
int ResolveDirection(double &feat[])
  {
   if(DirectionMode == 0) return  1;
   if(DirectionMode == 1) return -1;

   int colCount = ArraySize(feat);
   if(Discrim_Col < 0 || Discrim_Col >= colCount)
     {
      PrintFormat("WARNING: Discrim_Col=%d out of range [0-%d]. Skipping bar.",
                  Discrim_Col, colCount - 1);
      return 0;
     }

   double colValue = feat[Discrim_Col];
   if(colValue == EMPTY_VALUE)
     { Print("WARNING: Discriminator feature is EMPTY_VALUE. Skipping bar."); return 0; }

   bool colAbove = (colValue > Discrim_Thresh);
   if(Discrim_Dir == -1) return colAbove ? -1 : 1;
   return colAbove ? 1 : -1;
  }

//+------------------------------------------------------------------+
//| Helpers — range check                                            |
//+------------------------------------------------------------------+
bool InRange(double val, double lo, double hi)
  {
   // Disabled features use lo=-999 / hi=999 sentinels — always pass.
   if(lo <= -999.0 && hi >= 999.0) return true;
   if(val == EMPTY_VALUE) return false;
   return (val >= lo && val <= hi);
  }

//+------------------------------------------------------------------+
//| Helpers — safe single buffer read                                |
//+------------------------------------------------------------------+
double BufVal(int handle, int bufIdx, int shift)
  {
   double b[];
   ArraySetAsSeries(b, true);
   if(CopyBuffer(handle, bufIdx, shift, 1, b) < 1) return EMPTY_VALUE;
   return b[0];
  }

//+------------------------------------------------------------------+
//| Debug — evaluate all 27 entry filters without short-circuiting   |
//|  Prints value vs [lo,hi] and pass/fail for every feature.        |
//|  Also prints a summary line with the count and failing names.    |
//+------------------------------------------------------------------+
void DebugEntryConditions(double &feat[])
  {
   string bar_ts = TimeToString(iTime(_Symbol, PERIOD_CURRENT, 1), TIME_DATE|TIME_MINUTES);

   // Each entry: {name, feat_index, lo, hi}
   string names[27];
   double lo_vals[27];
   double hi_vals[27];

   names[0]  = "rsi14";          lo_vals[0]  = rsi14_lo;          hi_vals[0]  = rsi14_hi;
   names[1]  = "macd_norm";      lo_vals[1]  = macd_norm_lo;       hi_vals[1]  = macd_norm_hi;
   names[2]  = "atr_pct";        lo_vals[2]  = atr_pct_lo;         hi_vals[2]  = atr_pct_hi;
   names[3]  = "bb_width";       lo_vals[3]  = bb_width_lo;        hi_vals[3]  = bb_width_hi;
   names[4]  = "trend";          lo_vals[4]  = trend_lo;           hi_vals[4]  = trend_hi;
   names[5]  = "mtf_bull_score"; lo_vals[5]  = mtf_bull_score_lo;  hi_vals[5]  = mtf_bull_score_hi;
   names[6]  = "body_pct";       lo_vals[6]  = body_pct_lo;        hi_vals[6]  = body_pct_hi;
   names[7]  = "rng_atr";        lo_vals[7]  = rng_atr_lo;         hi_vals[7]  = rng_atr_hi;
   names[8]  = "vol_ratio";      lo_vals[8]  = vol_ratio_lo;       hi_vals[8]  = vol_ratio_hi;
   names[9]  = "vol_body_conf";  lo_vals[9]  = vol_body_conf_lo;   hi_vals[9]  = vol_body_conf_hi;
   names[10] = "regime";         lo_vals[10] = regime_lo;          hi_vals[10] = regime_hi;
   names[11] = "vol_price_div";  lo_vals[11] = vol_price_div_lo;   hi_vals[11] = vol_price_div_hi;
   names[12] = "bb_expanding";   lo_vals[12] = bb_expanding_lo;    hi_vals[12] = bb_expanding_hi;
   names[13] = "prev_sess_bias"; lo_vals[13] = prev_sess_bias_lo;  hi_vals[13] = prev_sess_bias_hi;
   names[14] = "poc_dist";       lo_vals[14] = poc_dist_lo;        hi_vals[14] = poc_dist_hi;
   names[15] = "bull";           lo_vals[15] = bull_lo;            hi_vals[15] = bull_hi;
   names[16] = "uwk_pct";        lo_vals[16] = uwk_pct_lo;         hi_vals[16] = uwk_pct_hi;
   names[17] = "lwk_pct";        lo_vals[17] = lwk_pct_lo;         hi_vals[17] = lwk_pct_hi;
   names[18] = "stoch_k";        lo_vals[18] = stoch_k_lo;         hi_vals[18] = stoch_k_hi;
   names[19] = "stoch_d";        lo_vals[19] = stoch_d_lo;         hi_vals[19] = stoch_d_hi;
   names[20] = "pin_bar";        lo_vals[20] = pin_bar_lo;         hi_vals[20] = pin_bar_hi;
   names[21] = "inside_bar";     lo_vals[21] = inside_bar_lo;      hi_vals[21] = inside_bar_hi;
   names[22] = "outside_bar";    lo_vals[22] = outside_bar_lo;     hi_vals[22] = outside_bar_hi;
   names[23] = "htf_div";        lo_vals[23] = htf_div_lo;         hi_vals[23] = htf_div_hi;
   names[24] = "rolling_sharpe"; lo_vals[24] = rolling_sharpe_lo;  hi_vals[24] = rolling_sharpe_hi;
   names[25] = "sd_zone";        lo_vals[25] = sd_zone_lo;         hi_vals[25] = sd_zone_hi;
   names[26] = "vwap_dist";      lo_vals[26] = vwap_dist_lo;       hi_vals[26] = vwap_dist_hi;

   int  pass_count   = 0;
   int  fail_count   = 0;
   int  skip_count   = 0;   // disabled filters (lo<=-999 && hi>=999)
   string fail_names = "";

   Print("[DBG] ---- Filter diagnostic @ ", bar_ts, " ----");

   for(int i = 0; i < 27; i++)
     {
      bool disabled = (lo_vals[i] <= -999.0 && hi_vals[i] >= 999.0);
      bool passed   = InRange(feat[i], lo_vals[i], hi_vals[i]);
      string status;

      if(disabled)
        {
         status = "SKIP";
         skip_count++;
        }
      else if(passed)
        {
         status = "OK  ";
         pass_count++;
        }
      else
        {
         status = "FAIL";
         fail_count++;
         fail_names += (fail_names == "" ? "" : ", ") + names[i];
        }

      // Only print non-disabled filters to keep the log readable
      if(!disabled)
        {
         if(feat[i] == EMPTY_VALUE)
            PrintFormat("[DBG]  [%s] %-16s = EMPTY_VALUE  range=[%.4f, %.4f]",
                        status, names[i], lo_vals[i], hi_vals[i]);
         else
            PrintFormat("[DBG]  [%s] %-16s = %+.5f  range=[%.4f, %.4f]",
                        status, names[i], feat[i], lo_vals[i], hi_vals[i]);
        }
     }

   int active = 27 - skip_count;
   if(fail_count == 0)
      PrintFormat("[DBG] SUMMARY: ALL %d active filters PASSED (%d disabled) — proceeding to direction check",
                  active, skip_count);
   else
      PrintFormat("[DBG] SUMMARY: %d/%d active filters FAILED (%d disabled) — BLOCKED: %s",
                  fail_count, active, skip_count, fail_names);

   Print("[DBG] -------------------------------------------");
  }

//+------------------------------------------------------------------+
//| Helpers — feature diagnostics log                                |
//+------------------------------------------------------------------+
void LogFeatures(double &feat[], int direction)
  {
   string dir_str = (direction == 1) ? "LONG" : "SHORT";
   PrintFormat("SIGNAL [%s] Magic=%d Bar=%s",
               dir_str, MagicNumber,
               TimeToString(iTime(_Symbol, PERIOD_CURRENT, 1), TIME_DATE|TIME_MINUTES));
   PrintFormat(" rsi14=%.2f macd_norm=%.4f atr_pct=%.5f bb_width=%.4f",
               feat[0], feat[1], feat[2], feat[3]);
   PrintFormat(" trend=%.0f mtf_bull=%.0f body=%.3f rng_atr=%.3f",
               feat[4], feat[5], feat[6], feat[7]);
   PrintFormat(" vol_ratio=%.3f vol_body=%.3f regime=%.0f vpdiv=%.0f",
               feat[8], feat[9], feat[10], feat[11]);
   PrintFormat(" bb_exp=%.0f sess_bias=%.0f poc_dist=%.5f bull=%.0f",
               feat[12], feat[13], feat[14], feat[15]);
   PrintFormat(" uwk=%.3f lwk=%.3f stoch_k=%.2f stoch_d=%.2f",
               feat[16], feat[17], feat[18], feat[19]);
   PrintFormat(" pin=%.3f inside=%.0f outside=%.0f htf_div=%.0f",
               feat[20], feat[21], feat[22], feat[23]);
   PrintFormat(" sharpe=%.3f sd_zone=%.0f vwap_dist=%.3f",
               feat[24], feat[25], feat[26]);
  }

//+------------------------------------------------------------------+
//| Feature functions  (index matches COLUMN INDEX TABLE in header) |
//+------------------------------------------------------------------+

//--- col 0: RSI(14) [0-100]
double GetRSI14(int shift)
  { return BufVal(g_hRSI, 0, shift); }

//--- col 1: MACD histogram / ATR(14) [normalised]
//   MT5 iMACD has only 2 buffers: 0=MACD line, 1=Signal line.
//   There is NO buffer 2 (histogram) unlike MT4.
//   Histogram is computed manually as (MACD_line - Signal_line).
double GetMacdNorm(int shift)
  {
   double macd   = BufVal(g_hMACD, 0, shift);   // fast EMA − slow EMA
   double signal = BufVal(g_hMACD, 1, shift);   // signal EMA of MACD
   double atr    = BufVal(g_hATR,  0, shift);
   if(macd == EMPTY_VALUE || signal == EMPTY_VALUE || atr == EMPTY_VALUE || atr <= 0.0)
      return EMPTY_VALUE;
   return (macd - signal) / atr;
  }

//--- col 2: ATR(14) / Close [volatility %]
double GetAtrPct(int shift)
  {
   double atr   = BufVal(g_hATR, 0, shift);
   double close = iClose(_Symbol, PERIOD_CURRENT, shift);
   if(atr == EMPTY_VALUE || close <= 0.0) return EMPTY_VALUE;
   return atr / close;
  }

//--- col 3: Bollinger width = (Upper - Lower) / Middle
double GetBBwidth(int shift)
  {
   double mid   = BufVal(g_hBB, 0, shift);
   double upper = BufVal(g_hBB, 1, shift);
   double lower = BufVal(g_hBB, 2, shift);
   if(mid == EMPTY_VALUE || upper == EMPTY_VALUE ||
      lower == EMPTY_VALUE || mid <= 0.0) return EMPTY_VALUE;
   return (upper - lower) / mid;
  }

//--- col 4: EMA trend [+1=up  0=range  -1=down]
//           Matches Python algo: EMA20 > EMA50 > EMA200 = up
//                                EMA20 < EMA50 < EMA200 = down
double GetEMATrend(int shift)
  {
   double e20  = BufVal(g_hEMA20,  0, shift);
   double e50  = BufVal(g_hEMA50,  0, shift);
   double e200 = BufVal(g_hEMA200, 0, shift);
   if(e20 == EMPTY_VALUE || e50 == EMPTY_VALUE || e200 == EMPTY_VALUE) return EMPTY_VALUE;
   if(e20 > e50 && e50 > e200) return  1.0;
   if(e20 < e50 && e50 < e200) return -1.0;
   return 0.0;
  }

//--- col 5: MTF bull score [0..(1 + active signal TFs)]
//           Matches Python algo (Option B / additive): primary trend +
//           sum of every active signal TF's trend, where
//           trend = EMA20>EMA50>EMA200 → 1, else 0.
//           Each signal TF whose data is unavailable contributes 0
//           so the filter still works on instruments with limited HTF
//           history. Range: 0..(g_nSignals + 1); max 5.
double GetMTFbull(int shift)
  {
   double m5_trend = GetEMATrend(shift);
   if(m5_trend == EMPTY_VALUE) return EMPTY_VALUE;

   datetime barTime = iTime(_Symbol, PERIOD_CURRENT, shift);
   double total = (m5_trend == 1.0 ? 1.0 : 0.0);

   for(int i = 0; i < g_nSignals; i++)
     {
      // The containing signal candle is still forming. Use the previous fully
      // closed signal candle, matching Python's shifted HTF asof join.
      int sShift = iBarShift(_Symbol, g_signalTFs[i], barTime, false) + 1;
      double e20  = BufVal(g_hEMA20_signal[i],  0, sShift);
      double e50  = BufVal(g_hEMA50_signal[i],  0, sShift);
      double e200 = BufVal(g_hEMA200_signal[i], 0, sShift);
      if(e20  == EMPTY_VALUE || e50  == EMPTY_VALUE || e200 == EMPTY_VALUE)
         continue; // count 0 for this slot
      if(e20 > e50 && e50 > e200) total += 1.0;
     }
   return total;
  }

//--- col 6: Body / Range [0-1]
double GetBodyPct(int shift)
  {
   double o     = iOpen (_Symbol, PERIOD_CURRENT, shift);
   double h     = iHigh (_Symbol, PERIOD_CURRENT, shift);
   double l     = iLow  (_Symbol, PERIOD_CURRENT, shift);
   double c     = iClose(_Symbol, PERIOD_CURRENT, shift);
   double range = h - l;
   if(range <= 0.0) return EMPTY_VALUE;
   return MathAbs(c - o) / range;
  }

//--- col 7: Range / ATR(14) [1=avg bar]
double GetRngATR(int shift)
  {
   double h   = iHigh(_Symbol, PERIOD_CURRENT, shift);
   double l   = iLow (_Symbol, PERIOD_CURRENT, shift);
   double atr = BufVal(g_hATR, 0, shift);
   if(atr == EMPTY_VALUE || atr <= 0.0) return EMPTY_VALUE;
   return (h - l) / atr;
  }

//--- col 8: Volume / MA20(volume) [1=avg vol]
double GetVolRatio(int shift)
  {
   long volArr[];
   ArraySetAsSeries(volArr, true);
   if(CopyTickVolume(_Symbol, PERIOD_CURRENT, shift, 20, volArr) < 20)
      return EMPTY_VALUE;
   double vol = (double)volArr[0];
   double sum = 0.0;
   for(int i = 0; i < 20; i++) sum += (double)volArr[i];
   double ma20 = sum / 20.0;
   if(ma20 <= 0.0) return EMPTY_VALUE;
   return MathMin(vol / ma20, 5.0);
  }

//--- col 9: Vol x Body confirmation [vol_ratio * body_pct]
double GetVolBodyConf(int shift)
  {
   double vr = GetVolRatio(shift);
   double bp = GetBodyPct(shift);
   if(vr == EMPTY_VALUE || bp == EMPTY_VALUE) return EMPTY_VALUE;
   return MathMin(vr * bp, 5.0);
  }

double LinearQuantile(double &values[], double q)
  {
   int n = ArraySize(values);
   if(n < 1) return EMPTY_VALUE;
   ArraySort(values);
   double rankPos = (n - 1) * q;
   int lower = (int)MathFloor(rankPos);
   int upper = (int)MathCeil(rankPos);
   if(lower == upper) return values[lower];
   double weight = rankPos - lower;
   return values[lower] * (1.0 - weight) + values[upper] * weight;
  }

//--- col 10: Market regime [0=TrendUp 1=TrendDn 2=Squeeze 3=WideVol 4=Choppy]
// Exact discovery parity: ATR% rolling median(200) and BB-width rolling
// quantiles(100), evaluated on the current fully closed primary bar.
double GetRegime(int shift)
  {
   double tr    = GetEMATrend(shift);
   double bw    = GetBBwidth(shift);
   double atr   = BufVal(g_hATR, 0, shift);
   double close = iClose(_Symbol, PERIOD_CURRENT, shift);
   if(tr == EMPTY_VALUE || bw == EMPTY_VALUE || atr == EMPTY_VALUE) return EMPTY_VALUE;
   double atrPct = (close > 0.0) ? atr / close : 0.0;

   double atrRaw[200], closeRaw[200], atrHistory[200];
   if(CopyBuffer(g_hATR, 0, shift, 200, atrRaw) < 200 ||
      CopyClose(_Symbol, PERIOD_CURRENT, shift, 200, closeRaw) < 200)
      return EMPTY_VALUE;
   for(int i = 0; i < 200; i++)
     {
      if(atrRaw[i] == EMPTY_VALUE || closeRaw[i] <= 0.0) return EMPTY_VALUE;
      atrHistory[i] = atrRaw[i] / closeRaw[i];
     }

   double bbMid[100], bbUpper[100], bbLower[100], bbHistory[100];
   if(CopyBuffer(g_hBB, 0, shift, 100, bbMid) < 100 ||
      CopyBuffer(g_hBB, 1, shift, 100, bbUpper) < 100 ||
      CopyBuffer(g_hBB, 2, shift, 100, bbLower) < 100)
      return EMPTY_VALUE;
   for(int i = 0; i < 100; i++)
     {
      if(bbMid[i] == EMPTY_VALUE || bbMid[i] <= 0.0) return EMPTY_VALUE;
      bbHistory[i] = (bbUpper[i] - bbLower[i]) / bbMid[i];
     }
   double atrMedian = LinearQuantile(atrHistory, 0.50);
   double bbQ25 = LinearQuantile(bbHistory, 0.25);
   double bbQ75 = LinearQuantile(bbHistory, 0.75);
   bool hiVol = atrPct > atrMedian * 1.1;
   bool loVol = atrPct < atrMedian * 0.9;

   if(tr ==  1.0 && hiVol) return 0.0;
   if(tr == -1.0 && hiVol) return 1.0;
   if(MathAbs(tr) < 0.5 && bw > bbQ75) return 3.0;
   if(bw < bbQ25 && loVol) return 2.0;
   return 4.0;
  }

//--- col 11: Vol-price divergence [+1=accumulation  -1=distribution  0=neutral]
// FIX v3: sign was inverted in v2.
//   Accumulation = high volume DOWN bar (smart money buying into selling pressure)
//   Distribution = high volume UP bar   (smart money selling into buying pressure)
//   Matches Python: vol_price_div = where(vol_ratio>1.2, where(close<open, +1, -1), 0)
double GetVolPriceDiv(int shift)
  {
   double vr = GetVolRatio(shift);
   double o  = iOpen (_Symbol, PERIOD_CURRENT, shift);
   double c  = iClose(_Symbol, PERIOD_CURRENT, shift);
   if(vr == EMPTY_VALUE) return EMPTY_VALUE;
   if(vr <= 1.2) return  0.0;
   if(c < o)     return  1.0;   // high vol down bar = accumulation
   if(c > o)     return -1.0;   // high vol up bar   = distribution
   return 0.0;
  }

//--- col 12: BB expanding [1=yes  0=no]
//            Matches Python: bb_w > bb_w.shift(3) — compare 3 bars back
double GetBBexpanding(int shift)
  {
   double bwNow  = GetBBwidth(shift);
   double bw3ago = GetBBwidth(shift + 3);
   if(bwNow == EMPTY_VALUE || bw3ago == EMPTY_VALUE) return EMPTY_VALUE;
   return (bwNow > bw3ago) ? 1.0 : 0.0;
  }

//--- col 13: Previous session bias [+1=bull  0=flat  -1=bear]
//            Based on the D1 candle prior to bar[shift]
//            NOTE: Python algo uses actual intraday session boundaries;
//            this D1 proxy is an approximation.
double GetPrevSessBias(int shift)
  {
   int d1shift  = iBarShift(_Symbol, PERIOD_D1,
                             iTime(_Symbol, PERIOD_CURRENT, shift), false);
   double o     = iOpen (_Symbol, PERIOD_D1, d1shift + 1);
   double h     = iHigh (_Symbol, PERIOD_D1, d1shift + 1);
   double l     = iLow  (_Symbol, PERIOD_D1, d1shift + 1);
   double c     = iClose(_Symbol, PERIOD_D1, d1shift + 1);
   double range = h - l;
   if(range <= 0.0) return 0.0;
   double body    = c - o;
   double bodyPct = MathAbs(body) / range;
   if(bodyPct < 0.1) return 0.0;
   return (body > 0.0) ? 1.0 : -1.0;
  }

//--- col 14: Distance from POC [% of price]
//   FIX v3: Improved from D1 midpoint to a 100-bar M5 volume-profile POC.
//   Uses 20 price bins over the 100-bar range to find the highest-volume bin,
//   matching the Python algo's compute_price_distributions logic.
double GetPOCdist(int shift)
  {
   const int LOOKBACK = 100;
   const int BINS     = 20;

   // Need LOOKBACK+1 bars for tick volume copy
   long volArr[];
   ArraySetAsSeries(volArr, true);
   if(CopyTickVolume(_Symbol, PERIOD_CURRENT, shift, LOOKBACK, volArr) < LOOKBACK)
      return EMPTY_VALUE;

   // Find price range over lookback
   double price_lo = iLow (_Symbol, PERIOD_CURRENT, shift);
   double price_hi = iHigh(_Symbol, PERIOD_CURRENT, shift);
   for(int i = 1; i < LOOKBACK; i++)
     {
      double h = iHigh(_Symbol, PERIOD_CURRENT, shift + i);
      double l = iLow (_Symbol, PERIOD_CURRENT, shift + i);
      if(h > price_hi) price_hi = h;
      if(l < price_lo) price_lo = l;
     }
   if(price_hi <= price_lo) return EMPTY_VALUE;

   double binSize = (price_hi - price_lo) / BINS;
   double binVol[20] = {0};

   // Accumulate volume into bins using typical price
   for(int i = 0; i < LOOKBACK; i++)
     {
      double h  = iHigh (_Symbol, PERIOD_CURRENT, shift + i);
      double l  = iLow  (_Symbol, PERIOD_CURRENT, shift + i);
      double c  = iClose(_Symbol, PERIOD_CURRENT, shift + i);
      double tp = (h + l + c) / 3.0;
      int    bi = (int)((tp - price_lo) / binSize);
      if(bi < 0)    bi = 0;
      if(bi >= BINS) bi = BINS - 1;
      binVol[bi] += (double)volArr[i];
     }

   // Find bin with highest volume (POC bin)
   int    pocBin = 0;
   double maxVol = binVol[0];
   for(int i = 1; i < BINS; i++)
      if(binVol[i] > maxVol) { maxVol = binVol[i]; pocBin = i; }

   // POC price = midpoint of winning bin
   double poc = price_lo + (pocBin + 0.5) * binSize;
   double cl  = iClose(_Symbol, PERIOD_CURRENT, shift);
   if(poc <= 0.0 || cl <= 0.0) return EMPTY_VALUE;
   return (cl - poc) / cl;
  }

//--- col 15: Bullish candle [1=bull  0=bear]
double GetBull(int shift)
  {
   double o = iOpen (_Symbol, PERIOD_CURRENT, shift);
   double c = iClose(_Symbol, PERIOD_CURRENT, shift);
   return (c > o) ? 1.0 : 0.0;
  }

//--- col 16: Upper wick / Range [0-1]
double GetUwkPct(int shift)
  {
   double o     = iOpen (_Symbol, PERIOD_CURRENT, shift);
   double h     = iHigh (_Symbol, PERIOD_CURRENT, shift);
   double l     = iLow  (_Symbol, PERIOD_CURRENT, shift);
   double c     = iClose(_Symbol, PERIOD_CURRENT, shift);
   double range = h - l;
   if(range <= 0.0) return EMPTY_VALUE;
   return (h - MathMax(o, c)) / range;
  }

//--- col 17: Lower wick / Range [0-1]
double GetLwkPct(int shift)
  {
   double o     = iOpen (_Symbol, PERIOD_CURRENT, shift);
   double h     = iHigh (_Symbol, PERIOD_CURRENT, shift);
   double l     = iLow  (_Symbol, PERIOD_CURRENT, shift);
   double c     = iClose(_Symbol, PERIOD_CURRENT, shift);
   double range = h - l;
   if(range <= 0.0) return EMPTY_VALUE;
   return (MathMin(o, c) - l) / range;
  }

//--- col 18: Stochastic %K [0-100]
//            buf 0 = main (%K after slowing=1 = raw K)
double GetStochK(int shift)
  { return BufVal(g_hStoch, 0, shift); }

//--- col 19: Stochastic %D [0-100]  (3-period SMA of %K)
//            buf 1 = signal (%D)
double GetStochD(int shift)
  { return BufVal(g_hStoch, 1, shift); }

//--- col 20: Pin-bar score [0-1]  = dominant wick / range
double GetPinBar(int shift)
  {
   double o   = iOpen (_Symbol, PERIOD_CURRENT, shift);
   double h   = iHigh (_Symbol, PERIOD_CURRENT, shift);
   double l   = iLow  (_Symbol, PERIOD_CURRENT, shift);
   double c   = iClose(_Symbol, PERIOD_CURRENT, shift);
   double rng = h - l;
   if(rng <= 0.0) return 0.0;
   double uwk     = h - MathMax(o, c);
   double lwk     = MathMin(o, c) - l;
   double domWick = MathMax(uwk, lwk);
   return MathMin(domWick / rng, 1.0);
  }

//--- col 21: Inside bar [1=yes  0=no]
double GetInsideBar(int shift)
  {
   double h  = iHigh(_Symbol, PERIOD_CURRENT, shift);
   double l  = iLow (_Symbol, PERIOD_CURRENT, shift);
   double ph = iHigh(_Symbol, PERIOD_CURRENT, shift + 1);
   double pl = iLow (_Symbol, PERIOD_CURRENT, shift + 1);
   return (h < ph && l > pl) ? 1.0 : 0.0;
  }

//--- col 22: Outside bar [1=yes  0=no]
double GetOutsideBar(int shift)
  {
   double h  = iHigh(_Symbol, PERIOD_CURRENT, shift);
   double l  = iLow (_Symbol, PERIOD_CURRENT, shift);
   double ph = iHigh(_Symbol, PERIOD_CURRENT, shift + 1);
   double pl = iLow (_Symbol, PERIOD_CURRENT, shift + 1);
   return (h > ph && l < pl) ? 1.0 : 0.0;
  }

//--- col 23: HTF RSI divergence [+1=bull -1=bear 0=none]
//            LTF (M5) RSI slope (5 bars) vs HTF (M15) RSI slope (3 bars)
//            Bullish: LTF RSI rising >+2 but M15 RSI still falling <-1
//            Bearish: LTF RSI falling <-2 but M15 RSI still rising >+1
double GetHtfDiv(int shift)
  {
   double rsiNow  = BufVal(g_hRSI, 0, shift);
   double rsi5ago = BufVal(g_hRSI, 0, shift + 5);
   if(rsiNow == EMPTY_VALUE || rsi5ago == EMPTY_VALUE) return 0.0;
   double ltf_slope = rsiNow - rsi5ago;

   // Auto uses the slowest active signal TF, matching Python. A non-zero input
   // selects a compact active slot (1..g_nSignals).
   if(g_nSignals < 1) return 0.0;
   int source = (HtfDivSignalSlot >= 1 && HtfDivSignalSlot <= g_nSignals)
                ? HtfDivSignalSlot - 1 : g_nSignals - 1;
   datetime barTime = iTime(_Symbol, PERIOD_CURRENT, shift);
   int sShift = iBarShift(_Symbol, g_signalTFs[source], barTime, false) + 1;
   double htfNow  = BufVal(g_hRSI_signal[source], 0, sShift);
   double htfAgo  = BufVal(g_hRSI_signal[source], 0, sShift + 3);
   if(htfNow == EMPTY_VALUE || htfAgo == EMPTY_VALUE) return 0.0;
   double htf_slope = htfNow - htfAgo;

   if(ltf_slope >  2.0 && htf_slope < -1.0) return  1.0;
   if(ltf_slope < -2.0 && htf_slope >  1.0) return -1.0;
   return 0.0;
  }

//--- col 24: Rolling Sharpe(20) [mean/std of 20-bar close returns, clipped ±3]
double GetRollingSharpe(int shift)
  {
   double returns[20];
   for(int i = 0; i < 20; i++)
     {
      double c1 = iClose(_Symbol, PERIOD_CURRENT, shift + i);
      double c2 = iClose(_Symbol, PERIOD_CURRENT, shift + i + 1);
      if(c2 <= 0.0) return 0.0;
      returns[i] = (c1 - c2) / c2;
     }
   double mean = 0.0;
   for(int i = 0; i < 20; i++) mean += returns[i];
   mean /= 20.0;
   double var = 0.0;
   for(int i = 0; i < 20; i++) { double d = returns[i] - mean; var += d * d; }
   double std = MathSqrt(var / 20.0);
   if(std <= 0.0) return 0.0;
   return MathMax(-3.0, MathMin(3.0, mean / std));
  }

//--- col 25: S/D zone proximity [+1=near support  -1=near resistance  0=middle]
//            Uses 25-bar rolling swing high/low as S/D proxies (< 1 ATR away)
//   FIX v3: lookback now starts at bar[shift] (was bar[shift+1]), matching Python
double GetSDzone(int shift)
  {
   double atr = BufVal(g_hATR, 0, shift);
   if(atr == EMPTY_VALUE || atr <= 0.0) return 0.0;
   double cl = iClose(_Symbol, PERIOD_CURRENT, shift);

   // Include bar[shift] itself in the 25-bar window (FIX: was starting at shift+1)
   double swing_hi = iHigh(_Symbol, PERIOD_CURRENT, shift);
   double swing_lo = iLow (_Symbol, PERIOD_CURRENT, shift);
   for(int i = 1; i < 25; i++)
     {
      double h = iHigh(_Symbol, PERIOD_CURRENT, shift + i);
      double l = iLow (_Symbol, PERIOD_CURRENT, shift + i);
      if(h > swing_hi) swing_hi = h;
      if(l < swing_lo) swing_lo = l;
     }
   double dist_to_res = (swing_hi - cl) / atr;
   double dist_to_sup = (cl - swing_lo) / atr;
   if(dist_to_sup < 1.0) return  1.0;
   if(dist_to_res < 1.0) return -1.0;
   return 0.0;
  }

//--- col 26: VWAP distance [% from 96-bar VWAP, 0 if no tick volume]
double GetVwapDist(int shift)
  {
   const int LOOKBACK = 96;
   long volArr[];
   ArraySetAsSeries(volArr, true);
   if(CopyTickVolume(_Symbol, PERIOD_CURRENT, shift, LOOKBACK, volArr) < LOOKBACK)
      return 0.0;
   double sum_tp_vol = 0.0, sum_vol = 0.0;
   for(int i = 0; i < LOOKBACK; i++)
     {
      double h   = iHigh (_Symbol, PERIOD_CURRENT, shift + i);
      double l   = iLow  (_Symbol, PERIOD_CURRENT, shift + i);
      double c   = iClose(_Symbol, PERIOD_CURRENT, shift + i);
      double vol = (double)volArr[i];
      if(vol <= 0.0) vol = 1.0;
      sum_tp_vol += ((h + l + c) / 3.0) * vol;
      sum_vol    += vol;
     }
   if(sum_vol <= 0.0) return 0.0;
   double vwap = sum_tp_vol / sum_vol;
   double cl   = iClose(_Symbol, PERIOD_CURRENT, shift);
   if(vwap <= 0.0) return 0.0;
   return MathMax(-5.0, MathMin(5.0, (cl - vwap) / vwap * 100.0));
  }

//+------------------------------------------------------------------+
//| Trade execution                                                  |
//+------------------------------------------------------------------+
void OpenLong()
  {
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double sl  = NormalisePrice(ask * (1.0 - SL_Pct));
   double tp  = NormalisePrice(ask * (1.0 + TP_Pct));
   if(trade.Buy(Lots, _Symbol, ask, sl, tp, "PD_EA R:" + IntegerToString((int)GetRegime(1))))
     {
      g_openTicket   = trade.ResultOrder();
      g_slDist       = ask - sl;
      g_entryBarTime = iTime(_Symbol, PERIOD_CURRENT, 0);
      PrintFormat("LONG opened | Ask=%.5f SL=%.5f TP=%.5f Lots=%.2f Ticket=%I64u",
                  ask, sl, tp, Lots, g_openTicket);
     }
   else
      PrintFormat("Buy failed | Error=%d Retcode=%d", GetLastError(), trade.ResultRetcode());
  }

void OpenShort()
  {
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double sl  = NormalisePrice(bid * (1.0 + SL_Pct));
   double tp  = NormalisePrice(bid * (1.0 - TP_Pct));
   if(trade.Sell(Lots, _Symbol, bid, sl, tp, "PD_EA R:" + IntegerToString((int)GetRegime(1))))
     {
      g_openTicket   = trade.ResultOrder();
      g_slDist       = sl - bid;
      g_entryBarTime = iTime(_Symbol, PERIOD_CURRENT, 0);
      PrintFormat("SHORT opened | Bid=%.5f SL=%.5f TP=%.5f Lots=%.2f Ticket=%I64u",
                  bid, sl, tp, Lots, g_openTicket);
     }
   else
      PrintFormat("Sell failed | Error=%d Retcode=%d", GetLastError(), trade.ResultRetcode());
  }

//+------------------------------------------------------------------+
//| Position management — breakeven & trailing stop                  |
//+------------------------------------------------------------------+
void ManageOpenPosition()
  {
   if(g_openTicket == 0) return;
   if(!pos.SelectByTicket(g_openTicket))
     {
      // Position no longer exists — cooldown handled by OnTradeTransaction
      // but set it here as fallback if transaction wasn't caught
      if(g_openTicket != 0)
        {
         g_cooldownBar  = Bars(_Symbol, PERIOD_CURRENT) - 1;
         g_openTicket   = 0;
         g_slDist       = 0.0;
         g_entryBarTime = 0;
        }
      return;
     }
   if(pos.Magic() != (ulong)MagicNumber) return;

   //--- Max-hold timeout: force-close once the position has been open for
   //    MaxHoldBars fully-formed bars without hitting SL/TP. Mirrors the
   //    discovery simulator's MAX_HOLD_BARS so live exits stay on schedule.
   if(MaxHoldBars > 0 && g_entryBarTime > 0)
     {
      int barsHeld = iBarShift(_Symbol, PERIOD_CURRENT, g_entryBarTime, false);
      if(barsHeld >= MaxHoldBars)
        {
         PrintFormat("MaxHold reached: %d bars held >= %d — closing position %I64u at market.",
                     barsHeld, MaxHoldBars, g_openTicket);
         if(trade.PositionClose(g_openTicket))
           {
            g_cooldownBar  = Bars(_Symbol, PERIOD_CURRENT) - 1;
            g_openTicket   = 0;
            g_slDist       = 0.0;
            g_entryBarTime = 0;
           }
         else
            PrintFormat("MaxHold close failed | Error=%d Retcode=%d",
                        GetLastError(), trade.ResultRetcode());
         return;
        }
     }

   double currentSL  = pos.StopLoss();
   double openPrice  = pos.PriceOpen();
   double currentBid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double currentAsk = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double newSL      = currentSL;

   if(pos.PositionType() == POSITION_TYPE_BUY)
     {
      double profit = currentBid - openPrice;
      if(BreakevenAtR > 0.0 && g_slDist > 0.0 && profit >= BreakevenAtR * g_slDist)
        { double beSL = NormalisePrice(openPrice); if(beSL > currentSL) newSL = beSL; }
      if(UseTrailing && g_slDist > 0.0 && profit >= TrailingStart * g_slDist)
        { double trailSL = NormalisePrice(currentBid - TrailingStep * g_slDist); if(trailSL > newSL) newSL = trailSL; }
     }
   else if(pos.PositionType() == POSITION_TYPE_SELL)
     {
      double profit = openPrice - currentAsk;
      if(BreakevenAtR > 0.0 && g_slDist > 0.0 && profit >= BreakevenAtR * g_slDist)
        { double beSL = NormalisePrice(openPrice); if(beSL < currentSL || currentSL == 0.0) newSL = beSL; }
      if(UseTrailing && g_slDist > 0.0 && profit >= TrailingStart * g_slDist)
        { double trailSL = NormalisePrice(currentAsk + TrailingStep * g_slDist); if(trailSL < newSL || newSL == 0.0) newSL = trailSL; }
     }

   if(MathAbs(newSL - currentSL) >= SymbolInfoDouble(_Symbol, SYMBOL_POINT))
     {
      if(!trade.PositionModify(g_openTicket, newSL, pos.TakeProfit()))
         PrintFormat("Modify failed | Error=%d Retcode=%d", GetLastError(), trade.ResultRetcode());
     }
  }

//+------------------------------------------------------------------+
//| Check if an open position with our magic exists                  |
//+------------------------------------------------------------------+
bool HasOpenPosition()
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(pos.SelectByIndex(i))
         if(pos.Symbol() == _Symbol && pos.Magic() == (ulong)MagicNumber)
           { g_openTicket = pos.Ticket(); return true; }
     }
   // No position found — cooldown handled by OnTradeTransaction or ManageOpenPosition
   return false;
  }

//+------------------------------------------------------------------+
//| Normalise price to symbol's tick size                            |
//+------------------------------------------------------------------+
double NormalisePrice(double price)
  {
   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickSize <= 0.0) return NormalizeDouble(price, _Digits);
   return NormalizeDouble(MathRound(price / tickSize) * tickSize, _Digits);
  }
//+------------------------------------------------------------------+
