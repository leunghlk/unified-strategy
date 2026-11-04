#!/usr/bin/python3
# 今日台數據產生器 — gen_today.py → today.json（同一目錄，index.html 今日台 tab 讀）
# 口徑（拍板④ 11-02）：真倉 screen＝基座 config（δ0.30/PT50/spr10/35td）；winner δ0.28/PT60＝paper 影子。
# 數據：本地 engine CSV（pool118 結構權威）＋ yfinance 新鮮價（118 名 batch＋SPX/VIX/NBIS＋top6 期權鏈）。
# 防禦：每段 try/except，失敗→null＋notes 留痕，唔准 crash。
import csv, json, math, os, sys, datetime, warnings
warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_JSON = os.path.join(HERE, "today.json")
ENGINE_DIR = "/Users/leungkathy/shortput_research/oos_multiperiod_20261101"
DAILY_CSV = "/Users/leungkathy/shortput_research/ytd2026_composite/results_enhance/pool118/data_daily_pool118.csv"
RADAR_JSON = "/Users/leungkathy/quant-dashboard/kill_radar_status.json"
LIGHTS_JSON = "/Users/leungkathy/quant_model/csp_lights_state.json"

CASH_HKD = 382349.0            # 拍板② OpenD 口徑流動本金
FX_USDHKD = 7.78               # 顯示用錨（標註）
NAV_USD = CASH_HKD / FX_USDHKD
SINGLE_CAP_FRAC = 0.05         # D1-D8：單名 5% NAV
CUM_CAP_FRAC = 0.12            # 累計 12% NAV
LIVE_DELTA = 0.30              # 基座（拍板④ 真倉口徑）
PAPER_DELTA = 0.28             # 📒 Paper 台 winner 口徑（PT60+δ0.28，雷達唔縮深）
WIDTH = 0.10                   # spr10
TENOR_TD = 35                  # 35td（band 30-45）
notes = []


def note(s):
    notes.append(s)


def usd(x):
    return None if x is None else round(float(x), 2)


# ---------- BS put（engine 同一 convention：r=0.043，IV 輸入） ----------
def bs_put(S, K, T, r, iv):
    if T <= 0 or S <= 0 or K <= 0 or not iv or iv <= 0 or math.isnan(iv):
        return max(0.0, K - S)
    from math import log, sqrt, exp, erf
    def N(x):
        return 0.5 * (1.0 + erf(x / sqrt(2.0)))
    d1 = (log(S / K) + (r + 0.5 * iv * iv) * T) / (iv * sqrt(T))
    d2 = d1 - iv * sqrt(T)
    return max(0.0, K * exp(-r * T) * N(-d2) - S * N(-d1))


def put_delta(S, K, T, r, iv):
    if T <= 0 or not iv or iv <= 0:
        return -1.0 if K > S else 0.0
    from math import log, sqrt, exp, erf
    def N(x):
        return 0.5 * (1.0 + erf(x / sqrt(2.0)))
    d1 = (log(S / K) + (r + 0.5 * iv * iv) * T) / (iv * sqrt(T))
    return N(d1) - 1.0


# ---------- ET 交易日曆（簡化：週一至五，唔計假期；tenor 選擇誤差可容忍，td 數另標） ----------
def td_between(d0, d1):
    d, n = d0, 0
    while d < d1:
        d += datetime.timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def next_fridays(d0, k=8):
    out, d = [], d0
    while len(out) < k:
        d += datetime.timedelta(days=1)
        if d.weekday() == 4:
            out.append(d)
    return out


# ---------- 1. 本地 engine 結構數據 ----------
rows = list(csv.DictReader(open(DAILY_CSV)))
loc_dates = [r["date"] for r in rows]


def loc_col(c):
    out = []
    for r in rows:
        try:
            out.append(float(r[c]))
        except (TypeError, ValueError):
            out.append(float("nan"))
    return out


mkt_spx = loc_col("spx")
mkt_vix = loc_col("vix")
mkt_breadth = loc_col("breadth_pct12")
last_loc = loc_dates[-1]
import numpy as np
names = ["NVDA","AMD","AVGO","MRVL","ARM","QCOM","ADI","MPWR","VICR","AMBA","SITM","TSM","INTC","GFS","UMC","TSEM","STM","AMAT","LRCX","KLAC","ASML","TER","ONTO","ENTG","UCTT","CDNS","MU","SNDK","WDC","STX","FN","COHR","LITE","CIEN","AAOI","ALAB","CRDO","ANET","CSCO","NTGR","FFIV","HPE","GLW","APH","TEL","UI","VST","CEG","TLN","GEV","ETN","PWR","HUBB","NVT","POWL","BE","LEU","CCJ","NEE","SO","DUK","D","AEP","EXC","XEL","ETR","VRT","DELL","MOD","DLR","EQIX","IRM","MSFT","GOOGL","AMZN","META","IBM","NOW","CRM","ADBE","INTU","WDAY","SHOP","DDOG","SNOW","NET","PLTR","APP","TTD","U","RBLX","DUOL","UBER","DASH","ABNB","MELI","CRWD","PANW","ZS","S","FTNT","OKTA","TENB","QLYS","GEN","RPD","CHKP","NBIS","CIFR","CORZ","APLD","TEM","SOUN","BBAI","IONQ","MBLY","PONY"]

# 每名最新本地 mom63/rv21_pctl（新鮮度由 yf 覆寫價重算 mom63；rv21_pctl 用本地，標註）
loc = {}
for s in names:
    c = loc_col(s + "_close")
    m = loc_col(s + "_mom63")
    p = loc_col(s + "_rv21_pctl")
    e = loc_col(s + "_earnings_days")
    loc[s] = dict(close=c[-1], mom63=m[-1], rv21_pctl=p[-1], earnings_days=e[-1])

# ---------- 2. yfinance 新鮮價 ----------
yf_ok = False
px = {}
def series(t):
    """yf download (Field,Ticker) layout：回傳該 ticker 收市價 Series（dropna）。"""
    import pandas as _pd
    try:
        if isinstance(dl.columns, _pd.MultiIndex):
            return dl["Close"][t].dropna()
        return dl[t]["Close"].dropna()
    except Exception:
        return None


try:
    import yfinance as yf
    tick_map = {"^GSPC": "SPX", "^VIX": "VIX"}
    dl = yf.download(names + ["^GSPC", "^VIX", "NBIS"], period="2y", interval="1d",
                     group_by="column", auto_adjust=False, progress=False, threads=True)
    last_close, prev_close = {}, {}
    for t in names + ["^GSPC", "^VIX", "NBIS"]:
        try:
            sub = series(t)
            if sub is not None and len(sub) >= 2:
                key = tick_map.get(t, t)
                last_close[key] = float(sub.iloc[-1])
                prev_close[key] = float(sub.iloc[-2])
        except Exception:
            pass
    if len(last_close) > 100:
        yf_ok = True
        px = last_close
    else:
        note("yf download names<100，用本地 %s 數據兜底" % last_loc)
except Exception as ex:
    note("yfinance 失敗（%s）——今日台用本地 %s 數據，標註過時" % (ex.__class__.__name__, last_loc))

spx = px.get("SPX", mkt_spx[-1])
vix = px.get("VIX", mkt_vix[-1])
spx_prev = prev_close.get("SPX", mkt_spx[-2] if len(mkt_spx) > 1 else float("nan"))
vix_prev = prev_close.get("VIX", mkt_vix[-2] if len(mkt_vix) > 1 else float("nan"))

# 價格新鮮度：yf 最後 bar 日期（(Field,Ticker) layout，用 series() accessor）
try:
    _s = series("^VIX")
    bar_date = str(_s.index[-1].date()) if _s is not None and len(_s) else last_loc
except Exception:
    bar_date = last_loc

# ---------- 3. 廣度（% above SMA50，118 名，用新鮮價全量重算） ----------
breadth = None
mom = {}
try:
    import numpy as _np
    above = 0
    tot = 0
    for t in names:
        try:
            sub = series(t)
            if sub is None or len(sub) < 60:
                continue
            arr = _np.asarray(sub, dtype=float)
            sma50 = arr[-50:].mean()
            tot += 1
            if arr[-1] > sma50:
                above += 1
            r63 = arr[-1] / arr[-64] - 1.0 if len(arr) > 64 else float("nan")
            mom[t] = r63
        except Exception:
            pass
    breadth = round(100.0 * above / max(tot, 1), 1)
except Exception:
    breadth = float(mkt_breadth[-1]) if mkt_breadth else None
    note("廣度用本地 breadth_pct12（%s）" % last_loc)

# 廣度先兆梯（10-30 判決：92%→25% 早 2-4 日；80→50/5日=鐘）
breadth_regime = "normal"
if breadth is not None:
    if breadth < 50:
        breadth_regime = "collapse"
    elif breadth < 65:
        breadth_regime = "warning"
    elif breadth < 80:
        breadth_regime = "caution"

# ---------- 4. VIX band 判決（⭐11-02 拍板熊狀態門：VIX<20 牛狀態單名放行） ----------
# 狀態定義＝引擎 spx_above_sma200 口徑：牛＝SPX 收市 ≥ SMA200；熊＝< SMA200 或攞唔到（fail-closed）。
try:
    _spx_ser = series("^GSPC").dropna()
    spx_sma200 = float(_spx_ser.rolling(200).mean().iloc[-1]) if len(_spx_ser) >= 200 else float("nan")
except Exception:
    spx_sma200 = float("nan")
if math.isnan(spx_sma200) or math.isnan(vix):
    mkt_state = "nan"
else:
    mkt_state = "bull" if spx >= spx_sma200 else "bear"
if math.isnan(vix):
    vix_band, band_action = "nan", "封（fail-closed）"
elif vix > 32:
    vix_band, band_action = ">32", "全封＋撤（fail-closed）"
elif vix < 20 and mkt_state == "bear":
    vix_band, band_action = "<20", "熊狀態門封（SPX<SMA200）— XSP＋單名唔開"
elif vix < 20:
    vix_band, band_action = "<20", "牛狀態放行單名（SPX≥SMA200，⭐11-02 拍板）；XSP 照封"
else:
    vix_band, band_action = "20-32", "XSP 開倉窗"

# ---------- 5. 殺倉雷達＋技術燈 ----------
radar = {}
try:
    radar = json.load(open(RADAR_JSON))
except Exception as ex:
    note("kill_radar_status.json 讀唔到： %s" % ex.__class__.__name__)
radar_level = radar.get("level", "?")
radar_reasons = radar.get("reasons", [])[:4]

lights = {}
try:
    lights = json.load(open(LIGHTS_JSON)).get("lights", {})
except Exception:
    pass

# 單名 δ 調整（10-18 判決：CSP 唔停手＋δ縮深）
delta_today = LIVE_DELTA
radar_note = ""
delta_note = "δ%.2f＝基座（雷達 %s 唔調整）" % (LIVE_DELTA, radar_level)
if radar_level == "RED":
    radar_note = "🔴 RED——新倉暫停（殺倉雷達）"
    delta_note = "δ%.2f＝基座；雷達 RED 新倉暫停" % LIVE_DELTA
elif radar_level == "YEL":
    delta_today = round(LIVE_DELTA - 0.02, 2)
    radar_note = "🟡 YEL——單名照做，δ 縮深至 %.2f（10-18 判決）" % delta_today
    delta_note = "δ%.2f＝基座 %.2f −0.02（雷達 YEL 縮深，唔係 winner）" % (delta_today, LIVE_DELTA)
else:
    radar_note = "🟢 正常"

# ---------- 6. 今日 screen：mom63 top-6＋閘 ----------
today = datetime.date.today()
screen = []
for s in names:
    m63 = mom.get(s, loc[s]["mom63"])
    if not m63 or (isinstance(m63, float) and math.isnan(m63)) or m63 <= 0:
        continue
    rv_p = loc[s]["rv21_pctl"]
    ed = loc[s]["earnings_days"]
    r1d = (px.get(s, loc[s]["close"]) / prev_close.get(s, loc[s]["close"]) - 1.0) if s in px and s in prev_close else float("nan")
    gates = dict(
        iv=("死亡區" if (rv_p is not None and not math.isnan(rv_p) and rv_p < 25) else ("半" if (rv_p is not None and not math.isnan(rv_p) and rv_p < 50) else "全")),
        earn=("禁" if (ed is not None and not math.isnan(ed) and ed <= 21) else "ok"),
        gap=("禁" if (not math.isnan(r1d) and r1d > 0.08) else "ok"),
    )
    screen.append(dict(sym=s, mom63=round(100 * m63, 1), close=usd(px.get(s, loc[s]["close"])),
                       rv21_pctl=(None if rv_p is None or math.isnan(rv_p) else round(rv_p, 0)),
                       earn_td=(None if ed is None or math.isnan(ed) else int(ed)),
                       chg1d=(None if math.isnan(r1d) else round(100 * r1d, 2)),
                       **gates))
screen.sort(key=lambda x: -x["mom63"])
top6 = screen[:6]
tradeable = [x for x in top6 if x["iv"] != "死亡區" and x["earn"] == "ok" and x["gap"] == "ok"]

# ---------- 7. 候選倉：δ_today put spread spr10，tenor 30-45td（真鏈價優先，BS 兜底） ----------
BS_R = 0.043
candidates = []
paper_candidates = []
if yf_ok:
    try:
        exps = next_fridays(today, 10)
        tgt = [d for d in exps if 30 <= td_between(today, d) <= 45]
        exp_d = tgt[0] if tgt else exps[min(5, len(exps) - 1)]
        exp_str = exp_d.strftime("%Y-%m-%d")
        T = max(td_between(today, exp_d), 1) / 252.0
        for x in tradeable[:5]:
            s = x["sym"]
            S = px.get(s)
            if not S:
                continue
            # 候補 strike：δ_today（BS，IV=本地 rv21×1.10）
            rv21 = loc[s]["rv21_pctl"]
            iv = float(loc[s].get("rv21_pctl") or 0)  # pctl 唔係 iv；正確 iv 用本地 rv21 欄
            try:
                iv = float(loc_col(s + "_rv21")[-1]) * 1.10
            except Exception:
                iv = 0.35
            iv = max(min(iv, 1.5), 0.12)
            # strike walk 落去至 |put delta| ≤ delta_today（OTM 方向）
            Ks = S
            for k in range(0, 240):
                K = round(S * (1.0 - 0.005 * k), 0)
                Ks = K
                if -put_delta(S, K, T, BS_R, iv) <= delta_today or K < S * 0.6:
                    break
            Kl = round(Ks * (1 - WIDTH), 0)
            credit = bs_put(S, Ks, T, BS_R, iv) - bs_put(S, Kl, T, BS_R, iv)
            src = "BS(RV21×1.10)"
            bid_s = ask_s = bid_l = ask_l = None
            try:
                oc = yf.Ticker(s).option_chain(exp_str)
                puts = oc.puts
                row_s = puts.iloc[(puts["strike"] - Ks).abs().argmin()]
                row_l = puts.iloc[(puts["strike"] - Kl).abs().argmin()]
                bid_s, ask_s = float(row_s["bid"]), float(row_s["ask"])
                bid_l, ask_l = float(row_l["bid"]), float(row_l["ask"])
                if bid_s > 0 and ask_s > 0 and bid_l >= 0 and ask_l > 0:
                    credit = (bid_s + ask_s) / 2 - (bid_l + ask_l) / 2
                    Ks, Kl = float(row_s["strike"]), float(row_l["strike"])
                    src = "鏈 mid（延遲價）"
            except Exception:
                pass
            credit = max(credit, 0.01)
            max_loss_pc = (Ks - Kl) * 100 - credit * 100
            cap = NAV_USD * SINGLE_CAP_FRAC
            n_ct = max(int(cap // max_loss_pc), 1) if max_loss_pc > 0 else 1
            # 📒 Paper 台（winner PT60+δ0.28）：同批數據/鏈價，δ 強制 0.28（雷達唔縮深）
            Ks_p = S
            for k in range(0, 240):
                K = round(S * (1.0 - 0.005 * k), 0)
                Ks_p = K
                if -put_delta(S, K, T, BS_R, iv) <= PAPER_DELTA or K < S * 0.6:
                    break
            Kl_p = round(Ks_p * (1 - WIDTH), 0)
            credit_p = bs_put(S, Ks_p, T, BS_R, iv) - bs_put(S, Kl_p, T, BS_R, iv)
            src_p = "BS(RV21×1.10)"
            try:
                _oc = yf.Ticker(s).option_chain(exp_str)
                _puts = _oc.puts
                _rs = _puts.iloc[(_puts["strike"] - Ks_p).abs().argmin()]
                _rl = _puts.iloc[(_puts["strike"] - Kl_p).abs().argmin()]
                if float(_rs["bid"]) > 0 and float(_rs["ask"]) > 0 and float(_rl["ask"]) > 0:
                    credit_p = (float(_rs["bid"]) + float(_rs["ask"])) / 2 - (float(_rl["bid"]) + float(_rl["ask"])) / 2
                    Ks_p, Kl_p = float(_rs["strike"]), float(_rl["strike"])
                    src_p = "鏈 mid（延遲價）"
            except Exception:
                pass
            credit_p = max(credit_p, 0.01)
            ml_p = (Ks_p - Kl_p) * 100 - credit_p * 100
            n_p = max(int(cap // ml_p), 1) if ml_p > 0 else 1
            paper_candidates.append(dict(
                sym=s, spot=usd(S), short_k=Ks_p, long_k=Kl_p, expiry=exp_str,
                dte_td=td_between(today, exp_d), credit=usd(credit_p),
                contracts=int(n_p), premium_in=usd(credit_p * 100 * n_p),
                margin=usd(ml_p * n_p), iv_used=round(iv * 100, 1), src=src_p,
                delta=PAPER_DELTA, pt="PT60"))
            candidates.append(dict(
                sym=s, spot=usd(S), short_k=Ks, long_k=Kl, expiry=exp_str,
                dte_td=td_between(today, exp_d), credit=usd(credit), width_pct=10,
                contracts=int(n_ct), max_loss=usd(max_loss_pc * n_ct / 100.0 * 100 / 100.0) if False else usd(max_loss_pc * n_ct),
                premium_in=usd(credit * 100 * n_ct),
                margin=usd(max_loss_pc * n_ct),
                cash_after=usd(CASH_HKD / FX_USDHKD + credit * 100 * n_ct),
                iv_used=round(iv * 100, 1), src=src, delta=delta_today,
                rv21_pctl=x["rv21_pctl"], earn_td=x["earn_td"]))
    except Exception as ex:
        note("候選倉定價失敗：%s %s" % (ex.__class__.__name__, ex))
else:
    note("無新鮮鏈價——候選倉只出 screen 唔出定價")

cum_margin = sum(c["margin"] for c in candidates)
cum_frac = cum_margin / NAV_USD if NAV_USD else None
if cum_frac and cum_frac > CUM_CAP_FRAC and candidates:
    keep, tot = [], 0.0
    for c in candidates:
        if tot + c["margin"] <= NAV_USD * CUM_CAP_FRAC:
            keep.append(c)
            tot += c["margin"]
    dropped = len(candidates) - len(keep)
    candidates = keep
    note("累計 12%% NAV 閘裁走 %d 個候選（12%% 上限=%.0f 美元）" % (dropped, NAV_USD * CUM_CAP_FRAC))

# ---------- 8. NBIS 倒數 ----------
nbis_spot = px.get("NBIS")
nbis = dict(spot=usd(nbis_spot), strike=165, expiry="2026-11-06", trigger="163–165（週四 11-05）",
            td_left=4, shares=200, contracts=2,
            light=lights.get("NBIS-C-165.0-2026-11-06", "?"),
            plan="A 照企；週四到 163–165 → 揀：接受被 call @165（實現 −$7,000）／roll 170@11-20 收 credit")
if nbis_spot:
    if nbis_spot < 163:
        nbis["state"] = "觸發區下——盯"
    elif nbis_spot <= 165:
        nbis["state"] = "⚠️ 觸發區內"
    else:
        nbis["state"] = "觸發區上 %+.1f%%" % (100 * (nbis_spot / 163 - 1))

# ---------- 9. 總判決（⭐11-02 拍板熊狀態門：VIX<20 牛狀態單名半開、熊狀態全封；XSP floor20 照舊） ----------
verdict_level, verdict_txt = "封", ""
if radar_level == "RED" or vix_band == ">32" or breadth_regime == "collapse":
    verdict_level, verdict_txt = "🔴 封刀", "雷達/廣度/VIX 至少一項封——今日唔開新倉，現有腿照 PT50/roll 規則行"
elif vix_band == "<20" and mkt_state == "bull":
    verdict_level, verdict_txt = "🟡 半開（熊狀態門放行）", "VIX %.1f <20 但牛狀態（SPX %.0f ≥ SMA200 %.0f）→ 單名照 screen 過閘開（⭐11-02 拍板，七窗 OOS 20bull 實錘）；XSP 照封（指數腿 floor20 唔郁）；現有腿照 PT50/roll" % (vix, spx, spx_sma200)
elif vix_band == "<20":
    verdict_level, verdict_txt = "🟡 全封（熊狀態門）", "VIX %.1f <20 且熊狀態（SPX %.0f < SMA200 %.0f 或攞唔到）→ XSP＋單名新倉一律唔開（fail-closed）；現有腿照 PT50/roll" % (vix, spx, spx_sma200)
elif radar_level == "YEL":
    verdict_level, verdict_txt = "🟡 半開", "雷達 YEL：單名照做 δ 縮深 0.02；XSP 照 band 規則"
else:
    verdict_level, verdict_txt = "🟢 開倉窗", "band＋雷達＋廣度全過——跟 top-6 screen 開 spr10"

# 過閘漏斗（回答「候選係過咗幾多閘先出線」）
funnel = dict(
    pool=len(names),
    mom_pos=len(screen),
    top6=len(top6),
    iv_ok=sum(1 for x in top6 if x["iv"] != "死亡區"),
    earn_ok=sum(1 for x in top6 if x["earn"] == "ok"),
    gap_ok=sum(1 for x in top6 if x["gap"] == "ok"),
    final=len(candidates),
    verdict=verdict_level)

# 黃金窗
gm = today.month
in_gold = gm in (11, 12, 1, 2, 3, 4, 5)
gold = dict(in_window=in_gold, window="11月–5月",
            note=("現正身處黃金窗——季節性最強 7 個月，照規則開倉唔猶豫" if in_gold
                  else "6–10 月非黃金窗——照閘行，唔追段"))

# 開市偵測：bar 日期＝ET 今日 → 該「收市價」實為現價（開市中）＋ET 日曆說明（防 HKT/ET 誤會）
WD_ZH = {0: "週一", 1: "週二", 2: "週三", 3: "週四", 4: "週五", 5: "週六", 6: "週日"}
et_note, bar_wd = None, None
try:
    from zoneinfo import ZoneInfo
    _et = datetime.datetime.now(ZoneInfo("America/New_York"))
    _in_session = datetime.time(9, 30) <= _et.time() <= datetime.time(16, 0)
    intraday = bool(bar_date == str(_et.date()) and _in_session)
    try:
        bar_wd = WD_ZH[datetime.date.fromisoformat(bar_date).weekday()]
    except Exception:
        bar_wd = ""
    if intraday:
        et_note = "ET 而家 %s %s——開市中，下列價＝現價（延遲約 0–15 分）" % (str(_et.date()), WD_ZH[_et.weekday()])
    elif bar_date == str(_et.date()):
        # ET 16:00 已收市（盤後）——bar 係今日收市價，唔可以話「現價」
        et_note = "ET 而家 %s %s（已收市，16:00 ET）——下列＝今日（%s %s）收市價；HKT 而家 %s" % (
            str(_et.date()), WD_ZH[_et.weekday()], bar_date, bar_wd,
            datetime.datetime.now().strftime("%m-%d %H:%M"))
    else:
        et_note = "ET 而家 %s %s（休市）——最新美股交易日係 %s（%s），HKT 而家 %s" % (
            str(_et.date()), WD_ZH[_et.weekday()], bar_date, bar_wd,
            datetime.datetime.now().strftime("%m-%d %H:%M"))
except Exception:
    intraday = None

out = dict(
    as_of=datetime.datetime.now().strftime("%Y-%m-%d %H:%M HKT"),
    bar_date=bar_date, data_fresh=yf_ok, intraday=intraday,
    market=dict(spx=usd(spx), spx_chg=(None if not spx_prev or math.isnan(spx_prev) else round(100 * (spx / spx_prev - 1), 2)),
                vix=round(vix, 2) if vix and not math.isnan(vix) else None,
                vix_chg=(None if not vix_prev or math.isnan(vix_prev) else round(vix - vix_prev, 2)),
                vix_band=vix_band, band_action=band_action, breadth=breadth, breadth_regime=breadth_regime,
                spx_sma200=round(spx_sma200, 2) if not math.isnan(spx_sma200) else None,
                mkt_state=mkt_state,
                et_note=et_note, bar_wd=bar_wd if 'bar_wd' in dir() else None),
    radar=dict(level=radar_level, reasons=radar_reasons, note=radar_note),
    verdict=dict(level=verdict_level, txt=verdict_txt, delta_single=delta_today, delta_note=delta_note),
    funnel=funnel,
    golden=gold,
    screen=top6, candidates=candidates, paper_candidates=paper_candidates,
    cum=dict(margin=usd(cum_margin), frac=(round(cum_frac * 100, 1) if cum_frac else None),
             cap_pct=CUM_CAP_FRAC * 100, cash_after=usd(NAV_USD - cum_margin)),
    nbis=nbis,
    account=dict(cash_hkd=CASH_HKD, fx=FX_USDHKD, nav_usd=round(NAV_USD, 0),
                 single_cap_usd=round(NAV_USD * SINGLE_CAP_FRAC, 0)),
    config=dict(live="基座 δ0.30/PT50/spr10/35td（拍板④）", shadow="winner δ0.28/PT60 paper 影子 2 週（11-17 無甩嘢切真倉）"),
    notes=notes,
)
json.dump(out, open(OUT_JSON, "w"), ensure_ascii=False, indent=1)
print("today.json written %s | verdict=%s | candidates=%d | breadth=%s | vix=%s" %
      (out["as_of"], verdict_level, len(candidates), breadth, out["market"]["vix"]))
