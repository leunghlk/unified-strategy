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
SAT_DELTA = 0.30               # 🛰️ 衛星台（10td+PT50+δ0.30，2026-11-10 三路研究拍板 11-10 verdict）
WIDTH = 0.10                   # spr10
TENOR_TD = 35                  # 35td（band 30-45）
C96_JSON = os.path.join(HERE, "c96_universe.json")   # 板塊頭部池（11-05 拍板：局部殺戮真倉換池用；研究原裝）
POOL117_JSON = os.path.join(HERE, "universe_pool117.json")   # ⭐11-10 欽定池（ownable45+fleet73−ATKR；用戶令 117+C96 都入衛星台）
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


# ---------- ⭐ 11-05 三層定價：bridge（Futu OpenD 真鏈）→ yf 鏈 mid → BS 兜底 ----------
# ⭐secret 唔准入公開 repo（11-05 分類器攔截實錄）——env FUTU_BRIDGE_SECRET 或 ~/.futu_bridge_secret（600）
def _bridge_secret():
    _s = os.environ.get("FUTU_BRIDGE_SECRET")
    if _s:
        return _s
    try:
        with open(os.path.expanduser("~/.futu_bridge_secret")) as _f:
            return _f.read().strip()
    except Exception:
        return ""


BRIDGE_SECRET = _bridge_secret()


_BRIDGE_FAIL_NOTED = [False]


def bridge_chain(s, exp_str):
    """Futu OpenD 真實 bid/ask（127.0.0.1 bridge，本地 only——公開 repo 頁面唔會見 secret）。
    bridge /options/chain 必須帶 &expiry=——OpenD full-chain 有 row cap 會截走遠月
    （11-05 實錘：TMO 全鏈只返 786 rows 截到 12-04，12-18 月選完全唔見）。
    ⭐bridge wedge（OpenD stall，11-05 21:35 實錄）時唔好靜默跌層——note 一次俾 dashboard 見到。"""
    try:
        import urllib.request as _ur
        _u = ("http://127.0.0.1:8765/options/chain?code=US.%s&expiry=%s&secret=%s"
              % (s, exp_str, BRIDGE_SECRET))
        _d = json.loads(_ur.urlopen(_u, timeout=25).read().decode("utf-8", "replace"))
        if not _d.get("ok"):
            if not _BRIDGE_FAIL_NOTED[0]:
                _BRIDGE_FAIL_NOTED[0] = True
                note("bridge 鏈價 ok=False（%s）——候選定價跌落 yf 鏈 mid/BS 層" % str(_d.get("error"))[:60])
            return None
        for _e in _d.get("expiries") or []:
            if _e.get("expiry") != exp_str:
                continue
            # ⭐11-05: 0/0 stub 行唔算有價（開市初頭 OpenD snapshot 全表 0/0 實錄 22:30 HKT）
            _rows = sorted((_r for _r in (_e.get("rows") or [])
                            if str(_r.get("type", "")).upper() == "PUT"
                            and _r.get("strike") is not None
                            and ((_r.get("bid") or 0) > 0 or (_r.get("ask") or 0) > 0)),
                           key=lambda r: -r["strike"])
            if _rows:
                return {"spot": _d.get("spot"), "puts": _rows}
            if not _BRIDGE_FAIL_NOTED[0]:
                _BRIDGE_FAIL_NOTED[0] = True
                note("bridge 真鏈無 live 報價（0/0 stub，開市初頭/OpenD 未 ready）——候選定價跌落 yf 鏈 mid/BS 層")
    except Exception as _ex:
        if not _BRIDGE_FAIL_NOTED[0]:
            _BRIDGE_FAIL_NOTED[0] = True
            note("bridge 鏈價攞唔到（%s）——候選定價跌落 yf 鏈 mid/BS 層" % _ex.__class__.__name__)
    return None


def price_spread(S, T, delta_tgt, iv, exp_str, sym=None, use_bridge=True):
    """短 put spread 定價三層：①bridge 真鏈（真 bid/ask mid、真 δ、全部 listed strikes）
    → ②yf 鏈 mid（延遲價，BS 行 K 貼最近 listed）→ ③BS 兜底。
    → (Ks, Kl, credit, src, iv_out, credit_worst)；credit=mid 口徑（收掛 mid 嘅理想值），
    credit_worst=sell@bid−buy@ask（市價單立即成交嘅最差可收值，⭐11-06 用戶令顯示）；
    iv_out=鏈價時用該腿真鏈 IV（None=用輸入 iv）。三層同一 δ 語意（行落 OTM 第一隻 |δ|≤delta_tgt）。"""
    if sym and use_bridge:
        # ⭐11-10：use_bridge=False 俾衛星台 189 名掃描用（⭐11-11 +SPCX/AMKR）——OpenD 30s/10 限額，全掃逐名打真鏈會燒光配額，
        # 令之後真倉 validation 全 403 假「無鏈」（實錘）。screening 用 yf/BS 夠；真鏈覆核係 satellite_d10.py 責任
        _bc = bridge_chain(sym, exp_str)
        if _bc:
            try:
                _ks = _kl = None
                for _r in _bc["puts"]:
                    if _r["strike"] < S * 0.6:
                        break
                    _dl = _r.get("delta")
                    _dl = abs(float(_dl)) if _dl is not None else -put_delta(S, _r["strike"], T, BS_R, iv)
                    if _dl <= delta_tgt:
                        _ks = _r
                        break
                if _ks is not None and (_ks["ask"] or 0) > 0:
                    _kt = _ks["strike"] * (1 - WIDTH)
                    for _r in _bc["puts"]:
                        if _r["strike"] <= _kt:
                            _kl = _r
                            break
                if _ks is not None and _kl is not None and (_kl["ask"] or 0) > 0:
                    _ms = (_ks["bid"] + _ks["ask"]) / 2.0
                    _ml = (_kl["bid"] + _kl["ask"]) / 2.0
                    if _ms > 0:
                        _iv_out = _ks.get("iv")
                        _iv_out = float(_iv_out) if _iv_out else None
                        _cw = (_ks["bid"] or 0) - (_kl["ask"] or 0)
                        return (float(_ks["strike"]), float(_kl["strike"]),
                                max(_ms - _ml, 0.01), "鏈 mid（Futu OpenD）", _iv_out,
                                round(max(_cw, 0.01), 2))
            except Exception:
                pass
    Ks = S
    for k in range(0, 240):
        K = round(S * (1.0 - 0.005 * k), 0)
        Ks = K
        if -put_delta(S, K, T, BS_R, iv) <= delta_tgt or K < S * 0.6:
            break
    Kl = round(Ks * (1 - WIDTH), 0)
    credit = bs_put(S, Ks, T, BS_R, iv) - bs_put(S, Kl, T, BS_R, iv)
    src = "BS(RV21×1.10)"
    _cw = None
    try:
        puts = yf.Ticker(sym).option_chain(exp_str).puts if sym else None
        if puts is not None and len(puts):
            rs = puts.iloc[(puts["strike"] - Ks).abs().argmin()]
            rl = puts.iloc[(puts["strike"] - Kl).abs().argmin()]
            if float(rs["bid"]) > 0 and float(rs["ask"]) > 0 and float(rl["ask"]) > 0:
                credit = (float(rs["bid"]) + float(rs["ask"])) / 2 - (float(rl["bid"]) + float(rl["ask"])) / 2
                _cw = float(rs["bid"]) - float(rl["ask"])
                Ks, Kl = float(rs["strike"]), float(rl["strike"])
                src = "鏈 mid（延遲價）"
    except Exception:
        pass
    return float(Ks), float(Kl), max(credit, 0.01), src, None, (round(_cw, 2) if _cw else None)


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


# ⭐11-09 聖誕五事故：30-45td 窗撞 2026-12-25（假期五，冇期權）→ bridge/yf 兩層都搵唔到 12-25 鏈
# → 全部候選跌 BS＋蓋「⚠️ 無鏈」，OpenD 開市都唔會自癒。揀期必須對真到期日曆。
_HOLIDAY_FRIDAYS = {"12-25", "01-01"}  # 固定日期假期五（探唔到曆嘅最後防線）
_EXP_CAL = [None]


def listed_expiries():
    """SPY 真到期日曆：yf options API（收市都有全曆，34 個）→ bridge 鏈兜底 → None。
    有 cache（一次 run 只探一次）。"""
    if _EXP_CAL[0] is not None:
        return _EXP_CAL[0]
    _out = None
    try:
        import yfinance as _yf
        _out = set(_yf.Ticker("SPY").options)
    except Exception:
        pass
    if not _out:
        try:
            import urllib.request as _ur
            _d = json.loads(_ur.urlopen("http://127.0.0.1:8765/options/chain?code=US.SPY&secret=%s"
                                        % BRIDGE_SECRET, timeout=25).read().decode("utf-8", "replace"))
            if _d.get("ok") and (_d.get("expiries") or []):
                _out = {e.get("expiry") for e in _d["expiries"] if e.get("expiry")}
        except Exception:
            pass
    _EXP_CAL[0] = _out
    return _out


def pick_exp_d(today):
    """tenor 30-45td 揀期，但必須係真到期日；窗內冇真日 → 揀距窗口邊最近嘅真日
    （11-09：12-18=29td 差一日出窗，12-25=34td 係聖誕五——29td 真鏈好過假期五永遠無鏈）。"""
    _real = listed_expiries()
    _exps = next_fridays(today, 10)
    if _real is not None:
        _exps = [d for d in _exps if d.strftime("%Y-%m-%d") in _real]
    else:
        _exps = [d for d in _exps if d.strftime("%m-%d") not in _HOLIDAY_FRIDAYS]
    _tgt = [d for d in _exps if 30 <= td_between(today, d) <= 45]
    if _tgt:
        return _tgt[0]
    if not _exps:
        return next_fridays(today, 10)[min(5, 9)]
    return min(_exps, key=lambda d: min(abs(td_between(today, d) - 30),
                                        abs(td_between(today, d) - 45)))


def pick_exp_d_sat(today):
    """🛰️ 衛星台 10td 揀期（8-14td 窗，真到期日曆同規矩——11-09 聖誕五事故：假期五硬禁、
    窗邊最近真日 fallback）。2026-11-10 短 tenor 三路研究終判：10td 係唯一有證據格。"""
    _real = listed_expiries()
    _exps = next_fridays(today, 10)
    if _real is not None:
        _exps = [d for d in _exps if d.strftime("%Y-%m-%d") in _real]
    else:
        _exps = [d for d in _exps if d.strftime("%m-%d") not in _HOLIDAY_FRIDAYS]
    _tgt = [d for d in _exps if 8 <= td_between(today, d) <= 14]
    if _tgt:
        return _tgt[0]
    if not _exps:
        return next_fridays(today, 10)[min(3, 9)]
    return min(_exps, key=lambda d: abs(td_between(today, d) - 10))


# ---------- 1. 本地 engine 結構數據 ----------
rows = list(csv.DictReader(open(DAILY_CSV)))
loc_dates = [r["date"] for r in rows]


def loc_col(c):
    out = []
    for r in rows:
        try:
            out.append(float(r[c]))
        except (TypeError, ValueError, KeyError):  # ⭐11-11 KeyError 補：新入池名（SPCX/AMKR）引擎 CSV 未有欄→NaN，yf 新鮮價兜
            out.append(float("nan"))
    return out


mkt_spx = loc_col("spx")
mkt_vix = loc_col("vix")
mkt_breadth = loc_col("breadth_pct12")
last_loc = loc_dates[-1]
import numpy as np
# ⭐11-11 用戶令：+SPCX（SpaceX 2026-06-12 上市）/+AMKR（Amkor OSAT）——標準程序全面同步
names = ["NVDA","AMD","AVGO","MRVL","ARM","QCOM","ADI","MPWR","VICR","AMBA","SITM","TSM","INTC","GFS","UMC","TSEM","STM","AMAT","LRCX","KLAC","ASML","TER","ONTO","ENTG","UCTT","CDNS","MU","SNDK","WDC","STX","FN","COHR","LITE","CIEN","AAOI","ALAB","CRDO","ANET","CSCO","NTGR","FFIV","HPE","GLW","APH","TEL","UI","VST","CEG","TLN","GEV","ETN","PWR","HUBB","NVT","POWL","BE","LEU","CCJ","NEE","SO","DUK","D","AEP","EXC","XEL","ETR","VRT","DELL","MOD","DLR","EQIX","IRM","MSFT","GOOGL","AMZN","META","IBM","NOW","CRM","ADBE","INTU","WDAY","SHOP","DDOG","SNOW","NET","PLTR","APP","TTD","U","RBLX","DUOL","UBER","DASH","ABNB","MELI","CRWD","PANW","ZS","S","FTNT","OKTA","TENB","QLYS","GEN","RPD","CHKP","NBIS","CIFR","CORZ","APLD","TEM","SOUN","BBAI","IONQ","MBLY","PONY","SPCX","AMKR"]

# 板塊頭部 C96（11 行業 × top-8 ＋ 8 隻半導體/軟件補充——breadth_design_research_20261105 原裝池）
# 用途唔係廣度閘（實證 16 假響輸 A117 7/8），係局部殺戮時嘅可做池：AI 封咗都有醫療/能源/工業/公用好做。
try:
    C96 = json.load(open(C96_JSON))["all"]
except Exception as ex:
    C96 = []
    note("c96_universe.json 讀唔到（%s）——板塊頭部池停用" % ex.__class__.__name__)

# ⭐11-10 欽定池 pool117（ownable45+fleet73−ATKR）——衛星台同 C96 全掃（用戶令「117+c96 都入晒」）
try:
    POOL117 = sorted(json.load(open(POOL117_JSON))["names_meta"].keys())
except Exception as ex:
    POOL117 = []
    note("universe_pool117.json 讀唔到（%s）——衛星台只掃 C96" % ex.__class__.__name__)
_C96SET, _P117SET = set(C96), set(POOL117)

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
    dl = yf.download(names + C96 + ["^GSPC", "^VIX", "RSP"], period="2y", interval="1d",
                     group_by="column", auto_adjust=False, progress=False, threads=True)
    last_close, prev_close = {}, {}
    for t in names + C96 + ["^GSPC", "^VIX", "RSP"]:
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

# ---------- 3. 廣度雙閘（⭐11-05 拍板「雙閘全套」；實證 breadth_design_research_20261105） ----------
# 118 池＝AI 戰場廣度（10月段唯一捉到嘅先知鐘，7/8）；RSP＝全市閘（26-03 陰跌唯一領先 +1）；
# 速度鐘＝池廣度 5 日跌 ≥20pct（⚠️10-30 舊註解「80→50/5日」係 AI12 %SMA20 口徑，SMA50 下 0/8 全啞——實證後改正）。
# 局部殺戮：池<50 而 RSP 企穩 → AI 單名封、真倉建議面轉 C96 板塊頭部、指數腿照 VIX floor（11-05 用戶令：封 AI 都要有嘢好做）。
breadth = None
breadth_5d = None
mom = {}
rsp_above = None            # 全市閘：RSP 收市 ≥ SMA50
rsp_below_recent5 = False   # 全市閘：近 5 日曾跌穿（陰跌 catch）
speed_bell = False
try:
    import numpy as _np
    above = above5 = tot = 0
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
            if len(arr) >= 56 and arr[-6] > arr[-56:-6].mean():
                above5 += 1
            r63 = arr[-1] / arr[-64] - 1.0 if len(arr) > 64 else float("nan")
            mom[t] = r63
        except Exception:
            pass
    breadth = round(100.0 * above / max(tot, 1), 1)
    breadth_5d = round(100.0 * above5 / max(tot, 1), 1)
    if breadth is not None and breadth_5d is not None and breadth_5d - breadth >= 20.0:
        speed_bell = True
except Exception:
    breadth = float(mkt_breadth[-1]) if mkt_breadth else None
    note("廣度用本地 breadth_pct12（%s）" % last_loc)
try:
    _rsp = series("RSP")
    if _rsp is not None and len(_rsp) > 60:
        _rsma = _rsp.rolling(50).mean()
        rsp_above = bool(_rsp.iloc[-1] >= _rsma.iloc[-1])
        rsp_below_recent5 = bool((_rsp.tail(5) < _rsma.tail(5)).any())
except Exception:
    note("RSP 全市閘數據攞唔到——全市閘今日停用（其餘閘照行，fail-closed 已有四層）")

# 廣度 regime（雙閘版：全市閘凌駕 → 硬封刀；池<50 或速度鐘響而全市企穩 → 局部殺戮）
mkt_gate_bad = None if rsp_above is None else (not rsp_above or rsp_below_recent5)
breadth_regime = "normal"
if mkt_gate_bad:
    breadth_regime = "collapse"                     # 全市閘響 → 硬封刀（全市塌，RSP 曾係最接近嘅領先指標）
elif breadth is not None and breadth < 50:
    breadth_regime = "partial_kill"                 # 局部殺戮：AI 戰場塌、全市企穩
elif speed_bell:
    breadth_regime = "partial_kill"                 # 速度鐘響（池 5 日急崩未穿 50）→ 先封 AI 戰場
elif breadth is None:
    breadth_regime = "nan"
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
    # ⭐ 11-09 用戶質問「XSP？你琴日唔係禁左咩」——band_action 冇理 RED 閘自相矛盾，補上
    if vix_band == "20-32":
        band_action = "XSP 帶位開窗——惟雷達 RED：指數腿停新開（roadmap B2 封）；C96 豁免腿照規則"
elif radar_level == "YEL":
    delta_today = round(LIVE_DELTA - 0.02, 2)
    radar_note = "🟡 YEL——單名照做，δ 縮深至 %.2f（10-18 判決）" % delta_today
    delta_note = "δ%.2f＝基座 %.2f −0.02（雷達 YEL 縮深，唔係 winner）" % (delta_today, LIVE_DELTA)
else:
    radar_note = "🟢 正常"

# credit break-glass（⭐11-05 拍板 (b)：雷達豁免 C96 腿嘅保險——真信用事件三條件齊先封 C96：
# OAS≥3.20 ＋ 5日+15bp ＋ RSP 跌穿 SMA50。得 OAS 擴而 RSP 企穩＝AI 發債體推動，唔封防守池）
credit_glass = False
try:
    _hist = json.load(open("/Users/leungkathy/quant-dashboard/kill_radar_history.json")).get("rows", [])
    if _hist:
        _r0 = _hist[-1]
        _oas, _d5 = _r0.get("oas"), _r0.get("oas_d5")
        credit_glass = bool(_oas is not None and _oas >= 3.20
                            and _d5 is not None and _d5 >= 0.15
                            and rsp_above is False)
        if credit_glass:
            note("credit break-glass 觸發（OAS %.2f／5日%+.0fbp＋RSP 跌穿 SMA50）——C96 腿照封（11-05 拍板）" % (_oas, _d5 * 100))
except Exception:
    pass

# ---------- 6. 今日 screen：mom63 top-6＋閘 ----------
today = datetime.date.today()
# ⭐11-10 根修：10-28 凍結 CSV 錨日 rebase——earnings_days 係「錨日至下季業績 td」快照，食舊數會假禁
# （TEM ed=1 真實已出；INTU ed=18 真實 8td＝衛星 11-20 到期日穿 print 事故）。rv21 分位同步轉 yf 新鮮現算
# （RBLX 13.5→92.3／APP 9.5→89.6 假死亡區同根）。
try:
    _csv_ed_drift = td_between(datetime.date.fromisoformat(str(last_loc)[:10]), today)
except Exception:
    _csv_ed_drift = 0
if _csv_ed_drift >= 5:
    note("⚠️ 引擎 CSV 錨日 %s 已 %dtd 舊——earnings_days 已 rebase、rv21_pctl 已轉新鮮 yf 現算（11-10 根修）" % (last_loc, _csv_ed_drift))


def rv21_pctl_ser(sub):
    """rv21 分位＝2y 日收對數差 21 日窗滾動 σ 對自身歷史百分位（11-10 由 7b 移上嚟——screen 閘都食新鮮值）。"""
    import numpy as _np
    arr = _np.asarray(sub, dtype=float)
    if len(arr) < 80:
        return None
    lr = _np.diff(_np.log(arr))
    rvs = [float(lr[i - 21:i].std(ddof=1) * _np.sqrt(252.0)) for i in range(21, len(lr) + 1)]
    if not rvs:
        return None
    return 100.0 * sum(1 for v in rvs if v <= rvs[-1]) / len(rvs)


screen = []
for s in names:
    m63 = mom.get(s, loc[s]["mom63"])
    if not m63 or (isinstance(m63, float) and math.isnan(m63)) or m63 <= 0:
        continue
    try:
        _sub_s = series(s)
        rv_p = rv21_pctl_ser(_sub_s) if (yf_ok and _sub_s is not None) else None
    except Exception:
        rv_p = None
    if rv_p is None or rv_p != rv_p:
        rv_p = loc[s]["rv21_pctl"]
    ed = loc[s]["earnings_days"]
    if ed is not None and ed == ed:
        ed = ed - _csv_ed_drift
    # ⭐11-11 根修（agent C 實錘 UI 假禁）：CSV 投影話禁（−5..21）嘅先做 fresh yf 覆核——UI 11-06 已出、
    # CSV 投影 11-18 → 假禁 3-5td（同 rv21 假死亡區同根：凍結錨）。已出≤5td→禁（冷靜）；>5td→ok 放行；
    # fresh fail→照 CSV 判決（唔信邪亦唔錯放）。只覆核禁區名，配額友好。
    _earn_post = None  # ⭐11-11 顯示修：fresh 覆核確認「已出 Ntd」→ 0td 唔好同「今晚出」混
    if yf_ok and ed is not None and ed == ed and -5 <= ed <= 10:  # ⭐11-11 拍板：前禁 21→10td（pre10+p5 數據最優）
        try:
            _cal = yf.Ticker(s).calendar
            _eds = (_cal or {}).get("Earnings Date")
            _d0 = _eds[0] if isinstance(_eds, (list, tuple)) and len(_eds) else _eds
            if isinstance(_d0, str):
                _d0 = datetime.date.fromisoformat(str(_d0)[:10])
            elif hasattr(_d0, "date"):
                _d0 = _d0.date()
            if _d0 is not None:
                if _d0 >= today:
                    _fetd = td_between(today, _d0)
                    if _fetd is not None:
                        ed = _fetd  # 真未來日——可能比 CSV 投影更近（假放）或更遠（假禁），一律用真日
                else:
                    _fgap = td_between(_d0, today)
                    if _fgap is not None:
                        ed = 0 if _fgap <= 5 else None  # ≤5td 冷靜禁；>5td 放行（下季未公佈＝唔知窗）
                        if ed == 0:
                            _earn_post = _fgap  # 已出 _fgap td，冷靜中——顯示用，唔入閘數學
        except Exception:
            pass
    r1d = (px.get(s, loc[s]["close"]) / prev_close.get(s, loc[s]["close"]) - 1.0) if s in px and s in prev_close else float("nan")
    gates = dict(
        iv=("死亡區" if (rv_p is not None and not math.isnan(rv_p) and rv_p < 25) else ("半" if (rv_p is not None and not math.isnan(rv_p) and rv_p < 50) else "全")),
        earn=("禁" if (ed is not None and not math.isnan(ed) and -5 <= ed <= 10) else "ok"),  # ⭐11-11 拍板：前10td禁（21→10，pre10+p5）＋後5td冷靜（拍板⑥：−5..0=已出5td內）
        gap=("禁" if (not math.isnan(r1d) and r1d > 0.08) else "ok"),
    )
    # ⭐11-11 顯示修：「禁（0td）」兩義——前禁（未出）vs 後冷靜（已出）分開寫，用戶唔會再誤讀
    _earn_label = None
    if gates["earn"] == "禁":
        if _earn_post is not None:
            _earn_label = "已出%std 冷靜中" % int(_earn_post)
        elif ed is not None and not math.isnan(ed):
            _earn_label = ("已出%std 冷靜中" % int(-ed)) if ed < 0 else ("前%std" % int(ed))
    screen.append(dict(sym=s, mom63=round(100 * m63, 1), close=usd(px.get(s, loc[s]["close"])),
                       rv21_pctl=(None if rv_p is None or math.isnan(rv_p) else round(rv_p, 0)),
                       earn_td=(None if ed is None or math.isnan(ed) else int(ed)),
                       earn_label=_earn_label,
                       chg1d=(None if math.isnan(r1d) else round(100 * r1d, 2)),
                       **gates))
screen.sort(key=lambda x: -x["mom63"])
top6 = screen[:6]
tradeable = [x for x in top6 if x["iv"] != "死亡區" and x["earn"] == "ok" and x["gap"] == "ok"]

# ownable45 正選池快照→live（⭐11-05 用戶令：宇宙 tab TOP6 榜唔好再食 10-30 快照）
OWNABLE45 = ["APP","TSM","ADI","ASML","AVGO","CCJ","INTU","LRCX","MRVL","STX","ADBE","AMD","CDNS","CSCO","FFIV","GFS","GLW","KLAC","MU","OKTA","PLTR","QLYS","SNDK","VICR","WDAY","AMAT","AMZN","APH","ARM","CRDO","DELL","DLR","FTNT","GEV","GOOGL","MSFT","NVT","QCOM","RBLX","STM","TSEM","TTD","UBER","WDC","IRM"]
own_scr = [x for x in screen if x["sym"] in OWNABLE45][:6]
ownable6 = [dict(sym=x["sym"], mom63=x["mom63"], close=x["close"], earn_td=x["earn_td"],
                 earn_window=("撞窗" if x["earn_td"] is not None and -5 <= x["earn_td"] <= 10  # ⭐11-11 拍板 21→10td＋補下限：已出（負數）唔係撞未來窗（agent C 實錘顯示蟲）
                              else ("清窗" if x["earn_td"] is not None else "?"))) for x in own_scr]

# ---------- 7. 候選倉：δ_today put spread spr10，tenor 30-45td（真鏈價優先，BS 兜底） ----------
BS_R = 0.043
candidates = []
paper_candidates = []
if yf_ok:
    try:
        exp_d = pick_exp_d(today)
        exp_str = exp_d.strftime("%Y-%m-%d")
        T = max(td_between(today, exp_d), 1) / 252.0
        for x in tradeable[:5]:
            s = x["sym"]
            S = px.get(s)
            if not S:
                continue
            # 候補 strike：δ_today（三層定價 11-05：bridge 真鏈 → yf 鏈 mid → BS）
            rv21 = loc[s]["rv21_pctl"]
            iv = float(loc[s].get("rv21_pctl") or 0)  # pctl 唔係 iv；正確 iv 用本地 rv21 欄
            try:
                iv = float(loc_col(s + "_rv21")[-1]) * 1.10
            except Exception:
                iv = 0.35
            iv = max(min(iv, 1.5), 0.12)
            Ks, Kl, credit, src, iv_out, cw = price_spread(S, T, delta_today, iv, exp_str, s)
            credit = max(credit, 0.01)
            max_loss_pc = (Ks - Kl) * 100 - credit * 100
            cap = NAV_USD * SINGLE_CAP_FRAC
            n_ct = max(int(cap // max_loss_pc), 1) if max_loss_pc > 0 else 1
            # 📒 Paper 台（winner PT60+δ0.28）：同批數據/鏈價，δ 強制 0.28（雷達唔縮深）
            Ks_p, Kl_p, credit_p, src_p, iv_out_p, cw_p = price_spread(S, T, PAPER_DELTA, iv, exp_str, s)
            credit_p = max(credit_p, 0.01)
            ml_p = (Ks_p - Kl_p) * 100 - credit_p * 100
            n_p = max(int(cap // ml_p), 1) if ml_p > 0 else 1
            paper_candidates.append(dict(
                sym=s, spot=usd(S), short_k=Ks_p, long_k=Kl_p, expiry=exp_str,
                dte_td=td_between(today, exp_d), credit=usd(credit_p), credit_worst=cw_p,
                contracts=int(n_p), premium_in=usd(credit_p * 100 * n_p),
                margin=usd(ml_p * n_p), iv_used=round((iv_out_p or iv) * 100, 1), src=src_p,
                delta=PAPER_DELTA, pt="PT60"))
            candidates.append(dict(
                sym=s, spot=usd(S), short_k=Ks, long_k=Kl, expiry=exp_str,
                dte_td=td_between(today, exp_d), credit=usd(credit), credit_worst=cw, width_pct=10,
                credit_min=usd(max((Ks - Kl) * 0.10, 1.50)),
                contracts=int(n_ct), max_loss=usd(max_loss_pc * n_ct / 100.0 * 100 / 100.0) if False else usd(max_loss_pc * n_ct),
                premium_in=usd(credit * 100 * n_ct),
                margin=usd(max_loss_pc * n_ct),
                cash_after=usd(CASH_HKD / FX_USDHKD + credit * 100 * n_ct),
                iv_used=round((iv_out or iv) * 100, 1), src=src, delta=delta_today,
                rv21_pctl=x["rv21_pctl"], earn_td=x["earn_td"]))
    except Exception as ex:
        note("候選倉定價失敗：%s %s" % (ex.__class__.__name__, ex))
else:
    note("無新鮮鏈價——候選倉只出 screen 唔出定價")

# ---------- 7b. 板塊頭部 C96 screen＋局部殺戮換池定價（⭐11-05 用戶令：廣度封 AI 後都要有嘢好做） ----------
# （rv21_pctl_ser 已移上去 section 6 前面——11-10 根修，screen 閘都食新鮮 yf 現算；earn 閘只查 top-8 慳 calls）


c96_screen = []
for s in C96:
    try:
        sub = series(s)
        if sub is None or len(sub) < 60:
            continue
        last_px = float(sub.iloc[-1])
        m63 = last_px / float(sub.iloc[-64]) - 1.0 if len(sub) > 64 else None
        if not m63 or m63 <= 0:
            continue
        rv_p = rv21_pctl_ser(sub)
        r1d = (px.get(s, last_px) / prev_close.get(s, last_px) - 1.0) if s in px and s in prev_close else float("nan")
        c96_screen.append(dict(
            sym=s, mom63=round(100 * m63, 1), close=usd(last_px),
            rv21_pctl=(None if rv_p is None else round(rv_p, 0)),
            chg1d=(None if math.isnan(r1d) else round(100 * r1d, 2)),
            iv=("死亡區" if (rv_p is not None and rv_p < 25) else ("半" if (rv_p is not None and rv_p < 50) else "全")),
            gap=("禁" if (not math.isnan(r1d) and r1d > 0.08) else "ok"),
            earn="?"))
    except Exception:
        pass
c96_screen.sort(key=lambda x: -x["mom63"])
c96_top = c96_screen[:8]
if yf_ok and c96_top:
    # earn 閘：只為 top-8 拉 earnings calendar（≤10td 禁，⭐11-11 拍板 21→10）
    for x in c96_top:
        try:
            _cal = yf.Ticker(x["sym"]).calendar
            _eds = (_cal or {}).get("Earnings Date")
            _d0 = _eds[0] if isinstance(_eds, (list, tuple)) and len(_eds) else _eds
            if isinstance(_d0, str):
                _d0 = datetime.date.fromisoformat(str(_d0)[:10])
            elif hasattr(_d0, "date"):
                _d0 = _d0.date()
            if _d0 is None:
                continue
            if _d0 < today:
                # ⭐11-10：yf 會回上一季已出日子（ABBV 10-30/LLY 10-29）——唔好 clamp 0 當禁；
                # ⭐11-11 拍板⑥補齊：剛出 ≤5td＝後冷靜禁；>5td＝放行
                _gap = td_between(_d0, today)
                x["earn"], x["earn_td"] = ("禁" if _gap <= 5 else "ok"), None
                continue
            ed = td_between(today, _d0)
            x["earn"] = "禁" if ed <= 10 else "ok"  # ⭐11-11 拍板 21→10td（前禁；後5td 冷靜已喺上面 past 分支）
            x["earn_td"] = ed
        except Exception:
            pass

c96_candidates = []
if breadth_regime == "partial_kill" and not credit_glass and yf_ok and c96_top:
    # ⭐11-05 拍板 (b)：雷達 RED 豁免 C96 腿——唔停手但 δ 縮深 0.02（底 0.25），跟 10-18 判決同款
    delta_c96 = round(max(delta_today - 0.02, 0.25), 2) if radar_level == "RED" else delta_today
    if radar_level == "RED":
        note("雷達 RED 但 C96 腿豁免封（11-05 拍板 b）——C96 候選 δ 縮深至 %.2f" % delta_c96)
    c96_ok = [x for x in c96_top if x["iv"] != "死亡區" and x["gap"] == "ok" and x["earn"] != "禁"]
    try:
        _exp_d = pick_exp_d(today)
        _exp_str = _exp_d.strftime("%Y-%m-%d")
        _T = max(td_between(today, _exp_d), 1) / 252.0
        _cap = NAV_USD * SINGLE_CAP_FRAC
        for x in c96_ok[:5]:
            s = x["sym"]
            S = px.get(s)
            if not S:
                continue
            iv = 0.35
            # C96 唔喺本地 engine——IV 用自身 21 日實現波動（年化）×1.10，唔係分位
            try:
                import numpy as _np
                _arr = _np.asarray(series(s), dtype=float)
                _lr = _np.diff(_np.log(_arr))
                iv = float(_lr[-21:].std(ddof=1) * _np.sqrt(252.0)) * 1.10
            except Exception:
                pass
            iv = max(min(iv, 1.5), 0.12)
            Ks, Kl, credit, src, iv_out, cw = price_spread(S, _T, delta_c96, iv, _exp_str, s)
            credit = max(credit, 0.01)
            ml = (Ks - Kl) * 100 - credit * 100
            n_ct = max(int(_cap // ml), 1) if ml > 0 else 1
            c96_candidates.append(dict(
                sym=s, spot=usd(S), short_k=Ks, long_k=Kl, expiry=_exp_str,
                dte_td=td_between(today, _exp_d), credit=usd(credit), credit_worst=cw, width_pct=10,
                credit_min=usd(max((Ks - Kl) * 0.10, 1.50)),
                contracts=int(n_ct), max_loss=usd(ml * n_ct), premium_in=usd(credit * 100 * n_ct),
                margin=usd(ml * n_ct), cash_after=usd(CASH_HKD / FX_USDHKD + credit * 100 * n_ct),
                iv_used=round((iv_out or iv) * 100, 1), src=src, delta=delta_c96,
                rv21_pctl=x["rv21_pctl"], earn_td=x.get("earn_td"), pool="C96"))
    except Exception as ex:
        note("C96 候選定價失敗：%s %s" % (ex.__class__.__name__, ex))

# ---------- 🛰️ 衛星台候選（10td+PT50+δ0.30，2026-11-10 三路研究拍板）----------
# ⭐11-10 shadow 池已剷（用戶令：淨返 pool117+C96 兩個池；BE/ALAB/CRDO 本來就喺 pool117）
# 研究 verdict：10td 全樣本 PF 2.23／MDD −11.8%（d30 六成）／崩盤窗唯一 PF>1 短 tenor；
# 3-5td 三線全滅（PF 1.57-1.83＋崩盤窗 0.49-0.70＋單名成本 60-100%）。
# 池＝C96 板塊頭部（研究：短 tenor 成本要 mega-liquid——AI 池 OKTA 10td 實測 worst 33% mid 亮紅燈）。
# 業績閘＝研究②Q4 口徑：只 skip 業績落喺窗口內（earn_td ≤ sat_td+1）——唔用 C96 前10td 禁（⭐11-11 拍板 21→10）
# （嗰個對 10td 窗過嚴會全滅）。verdict 🔴/ai_sealed 由 satellite_d10.py 照 📒 規矩處理（照開標 🧪）。
sat_candidates = []
sat_skips = []  # ⭐11-10 剷走留痕——逐個 skip 原因入 today.json（審計＋「點解某股唔見咗」有得答）
# ⭐11-10 擴池（用戶令「117+c96 都入晒衛星台」）：C96 全池＋pool117 欽定池去重統一掃描（⭐11-11 實數 189 名：pool117 119＋C96 96−overlap 26），
# shadow 池剷除（BE/ALAB/CRDO 本來就喺 pool117 入面，唔再需要獨立觀察組）。定價照 price_spread
# 三層——第一層已經係 bridge 真鏈（Futu OpenD 真 bid/ask，測得 0.2s/名、189 名 ≈ 45s）。
# 閘不挑池：mom63>0／死亡區 rv<25／gap chg1d>8%／業績 ≤sat_td+1 禁
# （pool117 用本地 earnings_days，C96-only 用 yf calendar 現拉）。
if yf_ok:
    try:
        _sat_exp = pick_exp_d_sat(today)
        _sat_str = _sat_exp.strftime("%Y-%m-%d")
        _sat_td = td_between(today, _sat_exp)
        _sat_T = max(_sat_td, 1) / 252.0
        _sat_cap = NAV_USD * SINGLE_CAP_FRAC
        _seen = set()
        _scan = []
        for s in C96 + POOL117:
            if s in _seen:
                continue
            _seen.add(s)
            _ic, _ip = s in _C96SET, s in _P117SET
            _scan.append((s, "C96+117" if (_ic and _ip) else ("C96" if _ic else "117")))
        for s, _pool in _scan:
            try:
                sub = series(s)
                if sub is None or len(sub) < 60:
                    continue
                S = px.get(s) or float(sub.iloc[-1])
                m63 = float(sub.iloc[-1]) / float(sub.iloc[-64]) - 1.0 if len(sub) > 64 else None
                if not m63 or m63 <= 0:
                    sat_skips.append(dict(sym=s, pool=_pool, reason="mom", note="mom63 %.1f%%" % (100 * m63)))
                    continue  # mom63>0（同 c96_screen 口徑）
                rv_p = rv21_pctl_ser(sub)  # ⭐11-10 新鮮 yf 現算先——凍結 CSV 令 RBLX/APP/MSFT 假死亡區
                if rv_p is None or rv_p != rv_p:
                    rv_p = loc.get(s, {}).get("rv21_pctl")
                if rv_p is not None and rv_p < 25:
                    sat_skips.append(dict(sym=s, pool=_pool, reason="iv_death", note="rv21分位 %.0f" % rv_p))
                    continue  # 死亡區
                r1d = (px.get(s, S) / prev_close.get(s, S) - 1.0) if s in px and s in prev_close else float("nan")
                if not math.isnan(r1d) and r1d > 0.08:
                    sat_skips.append(dict(sym=s, pool=_pool, reason="gap", note="chg1d %.1f%%" % (100 * r1d)))
                    continue  # gap 禁
                # ⭐11-10 根修（INTU 漏閘事故）：業績日 yf live 先、凍結 CSV 只做 rebase 後備；
                # 兩邊都唔明＝fail-closed 唔做（寧願無單，唔准穿 print）；
                # ⭐11-11 拍板⑥補齊：calendar 回舊季日＝已出——後 5td 冷靜禁、>5td 放行（唔再誤當 unknown fail-closed）
                _etd = None
                _earn_past_ok = False
                try:
                    _cal = yf.Ticker(s).calendar
                    _eds = (_cal or {}).get("Earnings Date")
                    _d0 = _eds[0] if isinstance(_eds, (list, tuple)) and len(_eds) else _eds
                    if isinstance(_d0, str):
                        _d0 = datetime.date.fromisoformat(str(_d0)[:10])
                    elif hasattr(_d0, "date"):
                        _d0 = _d0.date()
                    if _d0 is not None:
                        if _d0 >= today:
                            _etd = td_between(today, _d0)
                        else:
                            _gap = td_between(_d0, today)
                            if _gap <= 5:
                                sat_skips.append(dict(sym=s, pool=_pool, reason="earn_cooloff",
                                                      note="業績 %s 剛出 %std——後 5td 冷靜（拍板⑥）" % (_d0, _gap)))
                                continue
                            _earn_past_ok = True
                            sat_skips.append(dict(sym=s, pool=_pool, reason="earn_passed",
                                                  note="業績已出 %std（%s）、下季未公佈——放行" % (_gap, _d0)))
                except Exception:
                    _etd = None
                if _etd is None and not _earn_past_ok:
                    _edc = loc.get(s, {}).get("earnings_days")
                    if _edc is not None and _edc == _edc:
                        _edr = int(_edc) - _csv_ed_drift
                        if _edr > 0:
                            _etd = _edr
                # ⭐11-11 修蟲：呢度原本係 `if _etd is None:`——earn_passed 放行嗰批（_etd=None＋_earn_past_ok=True）
                # 跌落嚟再被 earnings_unknown 殺（27 名 earn_passed＋earnings_unknown 雙旗全滅＝run4 sat_cand 空嘅真兇）
                if _etd is None and not _earn_past_ok:
                    sat_skips.append(dict(sym=s, pool=_pool, reason="earnings_unknown",
                                          note="業績日唔明（yf fail＋CSV 錨已過）——fail-closed"))
                    continue
                # ⭐11-11 修蟲：已出>5td 放行時 _etd=None，float(None) 原本會 TypeError 俾 per-name except 靜默食掉
                if _etd is not None and float(_etd) <= _sat_td + 1:
                    sat_skips.append(dict(sym=s, pool=_pool, reason="earnings",
                                          note="業績 %dtd 落喺 %dtd 窗口" % (int(_etd), int(_sat_td))))
                    continue
                iv_s = 0.35
                try:
                    import numpy as _np
                    _arr = _np.asarray(series(s), dtype=float)
                    _lr = _np.diff(_np.log(_arr))
                    iv_s = float(_lr[-21:].std(ddof=1) * _np.sqrt(252.0)) * 1.10
                except Exception:
                    pass
                iv_s = max(min(iv_s, 1.5), 0.12)
                # ⭐11-11 根修（MPWR 11-27 幻影 tenor，用戶 app 實錘「得 11-20/12-18/65天」＝月選名）：
                # pick_exp_d_sat 係投影曆——個股未必有嗰個週選；Futu scoped chain 撞唔到 tenor 唔報錯、
                # 靜靜返鄰月（實測 MPWR 11-27 → 返 11-20/12-18），令幻影 row 著住「真鏈」外衣。
                # per-name 真曆覆核（yf options 全到期列表）：揀日唔喺真曆 → 8–14td 帶內最近真日替換；
                # 帶內冇真日 → skip（fail-closed：月選名唔啱短 tenor 衛星台，唔好硬夾）。
                try:
                    _reals = []
                    for _re in (yf.Ticker(s).options or ()):
                        try:
                            _rd = datetime.date.fromisoformat(str(_re)[:10])
                            _rt = td_between(today, _rd)
                            if _rt is not None:
                                _reals.append((_re, _rt))
                        except Exception:
                            continue
                    if _reals and not any(_x[0] == _sat_str for _x in _reals):
                        _band = [x for x in _reals if 8 <= x[1] <= 14]
                        if _band:
                            _pick = min(_band, key=lambda x: abs(x[1] - _sat_td))
                            _sat_str, _sat_td = _pick[0], _pick[1]
                            _sat_T = max(_sat_td, 1) / 252.0
                        else:
                            sat_skips.append(dict(sym=s, pool=_pool, reason="tenor_phantom",
                                                  note="pick %s 唔喺真曆＋8–14td 帶內冇真 tenor——skip" % _sat_str))
                            continue
                except Exception:
                    pass
                Ks_s, Kl_s, credit_s, src_s, iv_s_out, cw_s = price_spread(S, _sat_T, SAT_DELTA, iv_s, _sat_str, s, use_bridge=False)
                credit_s = max(credit_s, 0.01)
                ml_s = (Ks_s - Kl_s) * 100 - credit_s * 100
                n_s = max(int(_sat_cap // ml_s), 1) if ml_s > 0 else 1
                sat_candidates.append(dict(
                    sym=s, spot=usd(S), short_k=Ks_s, long_k=Kl_s, expiry=_sat_str,
                    dte_td=_sat_td, credit=usd(credit_s), credit_worst=cw_s,
                    credit_min=usd(max((Ks_s - Kl_s) * 0.10, 1.50)),
                    contracts=int(n_s), premium_in=usd(credit_s * 100 * n_s),
                    margin=usd(ml_s * n_s), iv_used=round((iv_s_out or iv_s) * 100, 1), src=src_s,
                    delta=SAT_DELTA, pt="PT50", pool=_pool))
            except Exception:
                continue
        # credit 大排先——衛星 engine 每晚 cap 內揀最肥
        sat_candidates.sort(key=lambda c: -float(c["credit"]))
    except Exception as ex:
        note("衛星候選定價失敗：%s %s" % (ex.__class__.__name__, ex))

# 局部殺戮換池（⭐11-05）：真倉建議面 AI→C96；paper_candidates 照 AI 池 shadow（2 週試驗唔混池，11-17 檢討）
if breadth_regime == "partial_kill" and c96_candidates:
    note("局部殺戮——真倉候選由 AI 池（%d 隻）轉板塊頭部 C96（%d 隻）；paper 台照 AI 池 shadow 唔混池" % (len(candidates), len(c96_candidates)))
    candidates = c96_candidates
elif breadth_regime == "partial_kill":
    candidates = []
    note("局部殺戮——AI 池封、C96 冇過閘候選，真倉建議面空")

cum_margin = sum(c["margin"] for c in candidates)
cum_frac = cum_margin / NAV_USD if NAV_USD else None
cum_dropped = []
if cum_frac and cum_frac > CUM_CAP_FRAC and candidates:
    keep, tot = [], 0.0
    for c in candidates:
        if tot + c["margin"] <= NAV_USD * CUM_CAP_FRAC:
            keep.append(c)
            tot += c["margin"]
    cum_dropped = [c["sym"] for c in candidates if c not in keep]
    dropped = len(candidates) - len(keep)
    candidates = keep
    note("累計 12%% NAV 閘裁走 %d 個候選（%s；12%% 上限=%.0f 美元）" % (dropped, "、".join(cum_dropped), NAV_USD * CUM_CAP_FRAC))
# ⭐11-06 修蟲：裁閘後重計——之前顯示嘅係裁前舊數（15400/31.3%）誤導用戶以為爆閘
cum_margin = sum(c["margin"] for c in candidates)
cum_frac = cum_margin / NAV_USD if NAV_USD else None

# ---------- 8. NBIS 倒數（已移除——11-07 episode 結清：200 股 @127 沽出＋165C 過期收袋，帳戶全清） ----------

# ⭐11-07 用戶令：每條候選 leg 番歷史勝率（per-symbol WR；spr10_2026 優先→v4g naked；樣本少照畀、UI 顯示 n 筆）
try:
    _wrdb = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "per_symbol_wr.json")))
except Exception:
    _wrdb = {}
for _c in candidates + c96_candidates:
    _w = _wrdb.get(str(_c.get("sym") or ""))
    if _w:
        _c["wr"], _c["wr_n"], _c["wr_src"] = _w.get("wr"), _w.get("n"), _w.get("src")

# ⭐11-08 用戶令（板面作廢事件）：候選腿三層狀態閘——①手寫判決檔 leg_verdicts.json ②live 鏈流動性閘
#   （腿 mid spread >35%＝mid 價 fiction，MAX_SPREAD_FRAC 0.35）③可執行 credit 對比（sell@bid−buy@ask
#   < 50% 票面 mid＝票面灌水）。教訓：V 405/360 票面 mid 5.13 係 fiction（可執行 2.90＝−43%），
#   判決以前只喺研究報告度、板面照顯示舊票面——用戶跟板做會踩中已作廢腿。判決層由今日起直接上板。
def _leg_verdicts_load():
    try:
        return json.load(open("/Users/leungkathy/quant_model/leg_verdicts.json"))
    except Exception:
        return {}

_LEG_VERDICTS = _leg_verdicts_load()

def _kfmt(_x):
    """strike key 正規化：405.0→405，避免手寫判決檔 key 對唔上 float strike"""
    try:
        _f = float(_x)
        return str(int(_f)) if _f == int(_f) else str(_f)
    except Exception:
        return str(_x)

def _validate_legs(cands):
    for _c in cands:
        _key_t = "%s %s/%s" % (_c.get("sym"), _kfmt(_c.get("short_k")), _kfmt(_c.get("long_k")))
        _v = _LEG_VERDICTS.get(_key_t) or _LEG_VERDICTS.get(str(_c.get("sym") or ""))
        if _v and _v.get("status"):
            _c["status"], _c["status_why"] = _v["status"], "%s（%s）" % (_v.get("reason", ""), _v.get("date", ""))
            continue
        _st = _why = None
        _exec = None
        try:
            _bc = bridge_chain(_c.get("sym"), _c.get("expiry"))
            if not _bc:
                _st, _why = "⚠️ 無鏈", "bridge 攞唔到報價——fail-closed 當封，唔好落"
            else:
                _ls = _ll = None
                for _r in _bc.get("puts", []):
                    if abs(float(_r["strike"]) - float(_c["short_k"])) < 0.01:
                        _ls = _r
                    if abs(float(_r["strike"]) - float(_c["long_k"])) < 0.01:
                        _ll = _r
                def _frac(r):
                    if not r:
                        return None
                    _b, _a = r.get("bid") or 0, r.get("ask") or 0
                    _m = (_b + _a) / 2.0
                    return (_a - _b) / _m if _m > 0 else None
                _fs, _fl = _frac(_ls), _frac(_ll)
                if _ls and _ll:
                    _exec = round((_ls.get("bid") or 0) - (_ll.get("ask") or 0), 2)
                    _c["exec_credit"] = _exec
                if _fs is not None and _fs > 0.35:
                    _st, _why = "⛔ 流動性閘", "短腿（賣）mid spread %.0f%% >35%%——收價 fiction，掛 mid 都收唔到（MAX_SPREAD_FRAC 0.35）" % round(_fs * 100)
                else:
                    _warn = ""
                    if _fl is not None and _fl > 0.35:
                        _warn = "；長腿（買）mid spread %.0f%% 闊——只准 combo 限價落，唔准逐腿市價" % round(_fl * 100)
                    if _exec is not None and _exec > 0:
                        try:
                            _tick = float(str(_c["credit"]).replace("US$", "").replace("$", "").replace(",", ""))
                        except Exception:
                            _tick = None
                        if _tick and _exec < _tick * 0.5:
                            _st, _why = "❌ 票面灌水", "可執行 %.2f 對票面 mid %.2f（−%.0f%%）——實收腰斬，票要重生成" % (_exec, _tick, (1 - _exec / _tick) * 100)
                        elif _tick:
                            _st, _why = "✅ 可落", "短腿 %.0f%% 過閘%s；可執行 $%.2f 對 mid $%.2f（−%.0f%%）" % (round(_fs * 100) if _fs is not None else 0, _warn, _exec, _tick, (1 - _exec / _tick) * 100)
                        else:
                            _st, _why = "✅ 可落", "短腿 %.0f%% 過閘%s；可執行 $%.2f" % (round(_fs * 100) if _fs is not None else 0, _warn, _exec)
                    else:
                        _st, _why = "⚠️ 無報價", "鏈內搵唔到齊 bid/ask 嘅對應 strike——fail-closed 當封"
        except Exception as _ex:
            _st, _why = "⚠️ 複檢失敗", "%s——fail-closed 當封" % _ex.__class__.__name__
        _c["status"], _c["status_why"] = _st, _why

_validate_legs(candidates)
_validate_legs(c96_candidates)


# ---------- 8b. 我的倉／戶口實數／風控行（⭐11-06 round-2；round-2b 蟲修＝真數源） ----------
# ⭐ round-2b 蟲修：options_rec portfolio／monitor history 嗰啲 equity 係「引擎模擬簿」
#   （700k 重基合成分母＋paper 模擬單 −90,945 溝埋），唔係用戶戶口——用戶鬧「d數唔岩」。
#   戶口實數改由 bridge OpenD 直讀：/real_funds（帳戶總覽）＋/real_positions（逐倉盈虧）
#   ＋/trade_history（88日 Futu 上限窗，Futu 自家成交記錄計已平倉淨數）。
#   engine 簿數（ytd）保留但板面降級做「模擬簿」灰字。
#   密鑰規矩：env FUTU_BRIDGE_SECRET → ~/.futu_bridge_secret，唔准寫死喺 repo。
def _bridge_secret():
    _s = os.environ.get("FUTU_BRIDGE_SECRET")
    if not _s:
        try:
            with open(os.path.expanduser("~/.futu_bridge_secret")) as _f:
                _s = _f.read().strip()
        except Exception:
            _s = None
    return _s

def _bridge_get(_path, _timeout=9):
    _s = _bridge_secret()
    if not _s:
        return None
    import urllib.request as _ur, urllib.parse as _up
    try:
        _url = "http://127.0.0.1:8765%s%ssecret=%s" % (_path, "&" if "?" in _path else "?", _up.quote(_s))
        with _ur.urlopen(_url, timeout=_timeout) as _r:
            return json.loads(_r.read().decode())
    except Exception:
        return None

import re as _re
_OPTCODE = _re.compile(r"^[A-Z]+\d+[CP]\d+$")

positions_out = []
positions_paper = []
ytd = dict(real_realized_hkd=None, paper_realized_hkd=None, unrealized_hkd=None, base_hkd=None,
           portfolio_return_pct=None, as_of=None)
account = dict(total_hkd=None, equity_usd=None, us_cash_power_usd=None, long_mv_hkd=None,
               fund_assets_hkd=None, init_margin_hkd=None, maint_margin_hkd=None, real_cash_hkd=None,
               anchor_hkd=None, anchor_delta_pct=None, legs=[], opt_legs=[],
               unreal_stock_hkd=None, unreal_opt_hkd=None,
               closed_88d_usd=None, closed_88d_hkd=None, closed_codes={},
               fill_unds=[], ts=None)
risk = dict(obligation_usd=None, equity_usd_live=None, oblig_cash_ratio=None,
            margin_used_pct=None)
_fill_unds = set()
_real_stock_tickers = set()
try:
    _funds = _bridge_get("/real_funds")
    _rpos = _bridge_get("/real_positions")
    _thist = _bridge_get("/trade_history?days=88", _timeout=15)
    if _funds and _funds.get("ok"):
        account.update(total_hkd=round(_funds.get("total_hkd") or 0),
                       equity_usd=round(_funds.get("equity_usd") or 0, 1),
                       us_cash_power_usd=round(_funds.get("us_cash_power_usd") or 0),
                       long_mv_hkd=round(_funds.get("long_mv_hkd") or 0),
                       fund_assets_hkd=round(_funds.get("fund_assets_hkd") or 0),
                       init_margin_hkd=round(_funds.get("init_margin_hkd") or 0),
                       maint_margin_hkd=round(_funds.get("maint_margin_hkd") or 0),
                       real_cash_hkd=round(_funds.get("cash_hkd") or 0),
                       anchor_hkd=round(CASH_HKD),
                       anchor_delta_pct=(round(((_funds.get("total_hkd") or 0) - CASH_HKD) / CASH_HKD * 100, 1) if CASH_HKD else None),
                       ts=_funds.get("ts"))
        if account["init_margin_hkd"] and account["total_hkd"]:
            risk["margin_used_pct"] = round(account["init_margin_hkd"] / account["total_hkd"] * 100, 1)
    for _rp in ((_rpos or {}).get("positions") or []):
        if _rp.get("kind") == "stock":
            _real_stock_tickers.add(_rp.get("ticker"))
            account["legs"].append(dict(ticker=_rp.get("ticker"), shares=_rp.get("shares"),
                                        mkt_val_hkd=_rp.get("mkt_val_hkd"), pnl_hkd=_rp.get("pnl_hkd"),
                                        pnl_pct=_rp.get("pnl_pct")))
        elif _rp.get("kind") == "option":
            _oc = str(_rp.get("code", "")).split(".", 1)[-1]
            _om = _re.search(r"(\d{6})[CP]", _oc)
            _oexp = _om.group(1) if _om else ""
            if _oexp and _oexp < datetime.date.today().strftime("%y%m%d"):
                continue  # 11-07：過期未結算 Futu row（例 NBIS 261106C165000）唔入帳面——同義務閘一致
            account["opt_legs"].append(dict(ticker=_rp.get("ticker"), code=_rp.get("code"),
                                            shares=_rp.get("shares"), strike=_rp.get("avg_cost"),
                                            pnl_hkd=_rp.get("pnl_hkd"), expiry=str(_rp.get("code", ""))[-7:-5]))
    account["unreal_stock_hkd"] = sum((l.get("pnl_hkd") or 0) for l in account["legs"])
    account["unreal_opt_hkd"] = sum((l.get("pnl_hkd") or 0) for l in account["opt_legs"])
    if _thist and _thist.get("ok"):
        _flow = {}
        for _d in (_thist.get("deals") or []):
            _raw = _d.get("code", "")
            _und = _raw.split(".", 1)[-1] if "." in _raw else _raw
            _isopt = bool(_OPTCODE.match(_und))
            _base = _re.sub(r"\d+[CP]\d+$", "", _und) if _isopt else _und
            _mult = 100 if _isopt else 1
            _sign = 1 if str(_d.get("side", "")).startswith("SELL") else -1
            _flow[_base] = _flow.get(_base, 0.0) + _sign * (_d.get("qty") or 0) * (_d.get("price") or 0) * _mult
            _fill_unds.add(_base)
        account["fill_unds"] = sorted(_fill_unds)
        _closed = {k: round(v) for k, v in sorted(_flow.items(), key=lambda kv: kv[1])
                   if k not in _real_stock_tickers}
        account["closed_codes"] = _closed
        account["closed_88d_usd"] = round(sum(_closed.values()))
        account["closed_88d_hkd"] = round(account["closed_88d_usd"] * FX_USDHKD)
except Exception as _e:
    note("戶口實數讀取失敗：%s" % _e)
try:
    _rec = json.load(open("/Users/leungkathy/quant_model/options_rec.json"))
    _pf = _rec.get("portfolio") or {}
    ytd = dict(real_realized_hkd=_pf.get("real_realized_hkd"),
               paper_realized_hkd=_pf.get("paper_realized_hkd"),
               unrealized_hkd=_pf.get("real_unrealized_hkd"),
               base_hkd=_pf.get("base_hkd"),
               portfolio_return_pct=_pf.get("return_pct"),
               as_of=_rec.get("generated"))
    positions_paper = []
    for _p in (_rec.get("positions") or []):
        if _p.get("status") != "open":
            continue
        _ep = _p.get("entry_prem") or 0
        _cp = _p.get("cur_prem")
        _rem = (_cp / _ep) if (_ep and _cp is not None) else None
        _d = dict(
            ticker=_p.get("ticker"), side=_p.get("side"), expiry=_p.get("expiry"), dte=_p.get("dte"),
            strike=_p.get("strike"), long_k=_p.get("long_k"), contracts=_p.get("contracts"),
            entry_prem=_ep, cur_prem=_cp,
            pt_remaining=(int(round(_rem * 100)) if _rem is not None else None),
            margin_req=_p.get("margin_req"), pnl_usd=_p.get("pnl_usd"), spot=_p.get("spot"),
            pt_zone=("可平倉" if (_rem is not None and _rem <= 0.5) else ("接近" if (_rem is not None and _rem <= 0.7) else "未到")),
            id=_p.get("id"))
        if _p.get("real"):
            positions_out.append(_d)
        else:
            _d["book"] = _p.get("book") or "paper"
            positions_paper.append(_d)
    # verified 驗證：Futu 88日成交或現持倉搵唔到＝幽靈倉（IAU 11-06 實錘）→ 唔入義務計
    for _po in positions_out:
        _po["verified"] = bool(_po.get("ticker") in _fill_unds or _po.get("ticker") in _real_stock_tickers)
except Exception as _e:
    note("我的倉讀取失敗：%s" % _e)
# 用戶指示 paper 簿（bridge /paper_order 落單 → user_paper_orders.json；⭐11-07 button 用）
# ⭐ 11-08：open 單已 import 入 engine 簿（options_income import_paper_orders）→ rec positions 有該 id
# 就唔喺度重複顯示（避免雙倉）——entry_prem 首標回寫呢個檔照舊，engine 每 pass 同步返入簿。
_eng_ids = {(_p.get("id")) for _p in (_rec.get("positions") or [])}
try:
    import yfinance as _yf3
    _ords = json.load(open("/Users/leungkathy/quant_model/user_paper_orders.json"))
except Exception:
    _ords = []
for _o in (_ords if isinstance(_ords, list) else []):
    if _o.get("status") != "open":
        continue
    _d = dict(ticker=_o.get("ticker"), side=_o.get("side"), expiry=_o.get("expiry"),
              dte=None, strike=_o.get("short_k"), long_k=_o.get("long_k"),
              contracts=_o.get("contracts"), entry_prem=_o.get("entry_prem"),
              cur_prem=None, pt_remaining=None, pt_zone="—",
              pnl_usd=None, book=_o.get("book") or "user_paper", id=_o.get("id"))
    try:
        _edd = datetime.date.fromisoformat(str(_o.get("expiry")))
        _d["dte"] = (_edd - datetime.date.today()).days
    except Exception:
        pass
    try:
        _ep = _o.get("entry_prem")
        _ch = _yf3.Ticker(str(_o.get("ticker"))).option_chain(str(_o.get("expiry"))).puts
        _mm = {}
        for _r in _ch.itertuples():
            if _r.strike in (float(_o.get("short_k")), float(_o.get("long_k"))):
                _mm[float(_r.strike)] = ((_r.bid + _r.ask) / 2 if _r.ask else _r.lastPrice)
        if _mm.get(float(_o.get("short_k"))) and _mm.get(float(_o.get("long_k"))) is not None:
            _cur = round(_mm[float(_o.get("short_k"))] - _mm[float(_o.get("long_k"))], 2)
            _d["cur_prem"] = _cur
            if not (_ep and _ep > 0):
                # ⭐ 11-07：button 落單冇填 credit → 首次 mark 自動做入場基準（寫返入 orders json）
                _ep = _cur
                _o["entry_prem"] = _ep
                try:
                    with open("/Users/leungkathy/quant_model/user_paper_orders.json", "w") as _f2:
                        json.dump(_ords, _f2, ensure_ascii=False, indent=1)
                except Exception:
                    pass
            _rem = _cur / _ep
            _d["entry_prem"] = _ep
            _d["pt_remaining"] = int(round(_rem * 100))
            _d["pt_zone"] = ("可平倉" if _rem <= 0.5 else ("接近" if _rem <= 0.7 else "未到"))
            _d["pnl_usd"] = round((_ep - _cur) * 100 * (_o.get("contracts") or 1), 0)
    except Exception as _e4:
        note("user paper mark 失敗 %s：%s" % (_o.get("ticker"), _e4.__class__.__name__))
    # ⭐ 11-08 已 import 入 engine 簿嘅單：mark／entry 首標回寫照行（上面），顯示跳過（engine rec 有）
    if _o.get("id") in _eng_ids:
        continue
    positions_paper.append(_d)
try:
    _mon = json.load(open("/Users/leungkathy/quant_model/options_monitor.json"))
    _sp = {x.get("id"): x.get("spot") for x in (_mon.get("positions") or [])}
    for _po in positions_out:
        if _po.get("spot") is None and _po.get("id") in _sp:
            _po["spot"] = _sp[_po.get("id")]
except Exception as _e:
    note("倉位 spot 併合失敗：%s" % _e)
try:
    # 義務＝真倉（Futu 驗證過）short strike notional；⭐ 2026-11-10: spread 淨義務＝width
    #   （接貨即刻行使長腿 590P 對沖，淨成本 7k 而唔係 66k——舊式會將 TMO 當裸 put，
    #    義務/現金 1.53 🔴 誤報封刀）
    _oblig = sum((((p.get("strike") or 0) - (p.get("long_k") or 0)) if p.get("long_k")
                  else (p.get("strike") or 0)) * 100 * (p.get("contracts") or 0)
                 for p in positions_out if p.get("verified"))
    risk["obligation_usd"] = round(_oblig)
    _eq = account.get("equity_usd")
    risk["equity_usd_live"] = _eq
    risk["cash_usd"] = round(account.get("us_cash_power_usd") or 0)
    risk["oblig_cash_ratio"] = (round(_oblig / _eq, 2) if _eq else None)
except Exception as _e:
    note("義務比計算失敗：%s" % _e)

# ---------- 9. 總判決（⭐11-02 熊狀態門＋⭐11-05 雙閘＋⭐11-05 拍板(b)：雷達 RED 豁免 C96 腿） ----------
# 分腿制：雷達只封 AI 池＋指數腿；C96 腿閘＝RSP 全市閘＋VIX band＋credit break-glass，雷達 RED 淨縮 δ0.02。
verdict_level, verdict_txt = "封", ""
ai_sealed = False          # AI 池新倉封咗未（局部殺戮／熊狀態門）——paper 台照開但強制 shadow
if vix_band == ">32" or breadth_regime == "collapse":
    ai_sealed = True
    _why = []
    if vix_band == ">32":
        _why.append("VIX>32")
    if breadth_regime == "collapse" and mkt_gate_bad:
        _why.append("全市閘（RSP%s）" % ("跌穿 SMA50" if rsp_above is False else "近5日曾穿 SMA50"))
    elif breadth_regime == "collapse" and breadth is not None:
        _why.append("廣度 %.1f<50" % breadth)
    verdict_level, verdict_txt = "🔴 封刀", "／".join(_why) + "——今日唔開新倉，現有腿照 PT50/roll 規則行"
elif breadth_regime == "partial_kill":
    ai_sealed = True
    if credit_glass:
        verdict_level, verdict_txt = "🔴 封刀（局部殺戮＋credit break-glass）", "OAS/信用三條件齊（真信用事件保險，⭐11-05 拍板）→ C96 腿照封；現有腿照 PT50/roll"
    elif vix_band == "20-32":
        verdict_level, verdict_txt = "🟡 半開（局部殺戮）", "AI 池廣度 %.1f<50（5日前 %.1f）但全市閘企穩（RSP ≥ SMA50）→ AI 單名封（10月段結構）；真倉建議面轉板塊頭部 C96（%d 隻過閘）照 δ%.2f、spread 闊度 10%%（賣 put 買低 10%% 嘅 put，收 credit）；指數腿（XSP）照 VIX floor 規則開；現有腿照 PT50/roll" % (breadth, breadth_5d if breadth_5d is not None else breadth, len(c96_candidates), delta_today)
    elif vix_band == "<20" and mkt_state == "bull":
        _red_note = "；雷達 RED 唔封 C96 腿（⭐11-05 拍板 b，實證 RED 日 C96 f10 +1.13%% 對 QQQ −0.96%% 方向相反）——C96 δ 縮深 0.02 至 %.2f（底 0.25）" % max(delta_today - 0.02, 0.25) if radar_level == "RED" else ""
        verdict_level, verdict_txt = "🟡 半開（局部殺戮，熊狀態門放行）", "AI 池廣度 %.1f<50 → AI 單名封；但 VIX %.1f<20＋牛狀態（SPX %.0f≥SMA200 %.0f，11-02 拍板 20bull 實錘）→ 板塊頭部 C96（%d 隻過閘）照 δ spr10 可開%s；指數腿照封（floor20 唔郁）；現有腿照 PT50/roll" % (breadth, vix, spx, spx_sma200, len(c96_candidates), _red_note)
    else:
        verdict_level, verdict_txt = "🔴 封刀（局部殺戮）", "AI 池廣度 %.1f<50 且 VIX band（%s）唔放行單名／指數腿 → 全封；現有腿照 PT50/roll" % (breadth, vix_band)
elif radar_level == "RED":
    ai_sealed = True
    verdict_level, verdict_txt = "🔴 封刀", "雷達 RED——AI 池＋指數腿唔開新倉（C96 腿只喺局部殺戮 regime 存在，唔受影響）；現有腿照 PT50/roll 規則行"
elif vix_band == "<20" and mkt_state == "bull":
    verdict_level, verdict_txt = "🟡 半開（熊狀態門放行）", "VIX %.1f <20 但牛狀態（SPX %.0f ≥ SMA200 %.0f）→ 單名照 screen 過閘開（⭐11-02 拍板，七窗 OOS 20bull 實錘）；XSP 照封（指數腿 floor20 唔郁）；現有腿照 PT50/roll" % (vix, spx, spx_sma200)
elif vix_band == "<20":
    ai_sealed = True
    verdict_level, verdict_txt = "🟡 全封（熊狀態門）", "VIX %.1f <20 且熊狀態（SPX %.0f < SMA200 %.0f 或攞唔到）→ XSP＋單名新倉一律唔開（fail-closed）；現有腿照 PT50/roll" % (vix, spx, spx_sma200)
elif radar_level == "YEL":
    verdict_level, verdict_txt = "🟡 半開", "雷達 YEL：單名照做 δ 縮深 0.02；XSP 照 band 規則"
else:
    verdict_level, verdict_txt = "🟢 開倉窗", "band＋雙閘＋雷達全過——跟 top-6 screen 開 spr10"

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

# ---------- 10. ROADMAP 決策樹（⭐11-08 ROADMAP_決策路徑：A狀態閘→B結構→C篩選→D sizing→E出場→F出事） ----------
_r_vix_ok = bool(vix) and not math.isnan(vix)
_r_collapse = (breadth_regime == "collapse")
_r_bear = (mkt_state != "bull")
_r_red = (radar_level == "RED")
_r_state, _r_txt = "🟢 正常", "全行：指數腿＋單名＋C96 照三層閘"
if (_r_collapse) or (not _r_vix_ok) or (_r_vix_ok and vix > 32):
    _r_state, _r_txt = "⛔ 全停", "VIX>32／廣度塌陷／數據 fail-closed——唔開新倉，現有倉入 break-glass"
elif _r_red:
    _r_state, _r_txt = "🟠 半停（RED）", "AI 池封＋指數腿停新開；C96 板塊頭部豁免（δ縮深0.02＋credit 下限）"
elif _r_bear:
    _r_state, _r_txt = "🔴 熊門（半停）", "單名全封；指數腿照行 δ 縮深＋唔加張"
elif _r_vix_ok and vix >= 25:
    _r_state, _r_txt = "🟡 高波動", "照行：δ縮深 0.28-0.32＋唔加張"
_sp = None
try:
    _sp = json.load(open("/Users/leungkathy/quant_model/stop_protocol_state.json"))
except Exception:
    pass
_roadmap = dict(
    state=_r_state, state_txt=_r_txt,
    steps=[
      dict(id="A", name="①狀態閘｜今日行唔行？（由上而下，中咗就停）", checks=[
        dict(q="VIX > 32？", v=("VIX %.1f" % vix) if _r_vix_ok else "VIX 讀唔到", hit=((not _r_vix_ok) or vix > 32), yes="⛔全停", no="→A2"),
        dict(q="廣度塌陷（RSP 全市閘／-20pct 速度鐘）？", v="regime=%s" % breadth_regime, hit=_r_collapse, yes="⛔全停", no="→A3"),
        dict(q="SPX < SMA200（熊門）？", v="SPX %s vs SMA200 %s＝%s" % (usd(spx), usd(spx_sma200) if not math.isnan(spx_sma200) else "?", mkt_state), hit=_r_bear, yes="🔴熊門：單名封指數照", no="→A4"),
        dict(q="殺倉雷達 RED？", v=radar_level, hit=_r_red, yes="🟠半停：AI池封＋C96豁免", no="→A5"),
        dict(q="VIX 帶位？", v=vix_band or "?", hit=False, yes="", no=(_r_state)),
      ]),
      dict(id="B", name="②結構矩陣｜行邊種？", checks=[
        dict(q="CSP naked？", v="帳戶≈$%dk：單腿5%%NAV按金閘→0-1張位" % round(NAV_USD/1000), hit=None, yes="", no="❌ 冇得揀全部 PCS——CSP 要帳戶翻 700k 先有合法位（11-08 判決②）"),
        dict(q="指數腿（SPX/XSP PCS）？", v="VIX %.1f＋regime %s＋RED=%s（floor20：VIX≥20 先開）" % (vix if _r_vix_ok else 0, breadth_regime, _r_red), hit=((not _r_vix_ok) or vix < 20 or _r_collapse or _r_red), yes="封（VIX<20 冇肉／塌陷／RED 停新開）", no="✅ 開（δ 錨 0.29／部署底 0.25）"),
        dict(q="單名 AI 池？", v="regime=%s／熊門=%s／RED=%s" % (breadth_regime, _r_bear, _r_red), hit=(_r_bear or _r_red or _r_collapse), yes="封（熊門/RED/塌陷）", no="✅ screen 過閘"),
        dict(q="C96 板塊頭部？", v="RED=%s%s" % (_r_red, "／δ縮深0.02" if _r_red else ""), hit=False, yes="", no=("✅ RED 豁免腿：δ縮深0.02＋credit 下限" if _r_red else "✅ 照篩（RED 響時做豁免腿）")),
      ]),
      dict(id="C", name="③揀票篩選鏈（gen_today 自動逐關斬）", checks=[
        dict(q="池→δ→DTE 30-45→credit 下限→三層閘", v="今日 %d 候選過三層閘（AI %d＋C96 %d）" % (len(candidates)+len(c96_candidates), len(candidates), len(c96_candidates)), hit=None, yes="", no="斬條件：判決檔❌／短腿 spread>35%% ⛔／可執行<50%% 票面❌"),
      ]),
      dict(id="D", name="④sizing cap（防 margin call 唯一真防線）", checks=[
        dict(q="單腿 ≤5% NAV｜cum ≤12% NAV", v="今日 cum margin=%s（cap %d%%=%s）" % (usd(cum_margin), round(CUM_CAP_FRAC*100), usd(NAV_USD*CUM_CAP_FRAC)), hit=None, yes="", no="超＝唔開（裂口日唯一防線，止蝕盤防唔到單夜×4）"),
      ]),
      dict(id="E", name="⑤出場尺（engine 自動盯＋🛑三次確認止蝕）", checks=[
        dict(q="PT50 止賺", v="市價跌到 credit 一半→自動落袋", hit=None, yes="", no="✅ sealed"),
        dict(q="輸邊硬規：spread≥2×credit／短腿觸及", v="🛑三次確認：6min連續bid穿＋3次TG＋30min無取消自動落盤；彈返即重置", hit=None, yes="", no="✅ 三次確認協議 11-08 起"),
        dict(q="earnings／assignment 防線", v="財報前必平／DTE≤5＋S<K＋S<0.95×MA50", hit=None, yes="", no="✅ 現行"),
        dict(q="MTM1× 指數腿＋PCS≥3×", v="拍板①未批／6樣本 paper 4週", hit=None, yes="", no="⏳ 等拍板"),
      ]),
      dict(id="F", name="⑥出事", checks=[
        dict(q="熊門／塌陷鐘響→N× 硬閘武裝", v="拍板②未批；硬規嚴執行照行", hit=None, yes="", no="⏳"),
        dict(q="break-glass", v="enabled", hit=None, yes="", no="✅"),
      ]),
    ],
    stop_protocol=(dict(active=_sp.get("active"), leg=_sp.get("leg_key"), t0=_sp.get("t0"), polls=_sp.get("polls"), note="🛑 三次確認進行中") if (_sp and _sp.get("active")) else None),
)

def _sat_margin_gate():
    """⭐11-11（用戶令「每隻候選寫埋按金過唔過閘」）：衛星簿按金實況——cap 錨 40%×$49,145
    （同 satellite_d10.py NAV_USD/MARGIN_CAP_USD 同源），used＝paper_satellite_d10.json summary。"""
    _cap = 49145.0 * 0.40
    try:
        _st = json.load(open(os.path.join(HERE, "paper_satellite_d10.json")))
        _used = float((_st.get("summary") or {}).get("margin_used") or 0.0)
    except Exception:
        _used = 0.0
    return dict(cap=round(_cap), used=round(_used), headroom=round(max(_cap - _used, 0.0)))


out = dict(
    roadmap=_roadmap,
    as_of=datetime.datetime.now().strftime("%Y-%m-%d %H:%M HKT"),
    bar_date=bar_date, data_fresh=yf_ok, intraday=intraday,
    market=dict(spx=usd(spx), spx_chg=(None if not spx_prev or math.isnan(spx_prev) else round(100 * (spx / spx_prev - 1), 2)),
                vix=round(vix, 2) if vix and not math.isnan(vix) else None,
                vix_chg=(None if not vix_prev or math.isnan(vix_prev) else round(vix - vix_prev, 2)),
                vix_band=vix_band, band_action=band_action, breadth=breadth, breadth_regime=breadth_regime,
                breadth_5d=breadth_5d, speed_bell=speed_bell,
                rsp_above=rsp_above, rsp_below_recent5=rsp_below_recent5, mkt_gate_bad=mkt_gate_bad,
                spx_sma200=round(spx_sma200, 2) if not math.isnan(spx_sma200) else None,
                mkt_state=mkt_state,
                et_note=et_note, bar_wd=bar_wd if 'bar_wd' in dir() else None),
    radar=dict(level=radar_level, reasons=radar_reasons, note=radar_note),
    verdict=dict(level=verdict_level, txt=verdict_txt, delta_single=delta_today, delta_note=delta_note,
                 ai_sealed=ai_sealed, credit_glass=credit_glass),
    funnel=funnel,
    golden=gold,
    screen=top6, candidates=candidates, paper_candidates=paper_candidates,
    sat_candidates=sat_candidates,
    sat_margin=_sat_margin_gate(),
    sat_skips=sat_skips[:300], csv_asof=str(last_loc), csv_ed_drift=_csv_ed_drift,
    c96_screen=c96_screen[:12], c96_candidates=c96_candidates,
    ownable6=ownable6,
    cum=dict(margin=usd(cum_margin), frac=(round(cum_frac * 100, 1) if cum_frac else None),
             cap_pct=CUM_CAP_FRAC * 100, cash_after=usd(NAV_USD - cum_margin),
             dropped=cum_dropped),
    positions=positions_out, positions_paper=positions_paper, ytd=ytd, risk=risk,
    account=dict(cash_hkd=CASH_HKD, fx=FX_USDHKD, nav_usd=round(NAV_USD, 0),
                 single_cap_usd=round(NAV_USD * SINGLE_CAP_FRAC, 0), **account),
    config=dict(live="基座 δ0.30/PT50/spr10/35td（拍板④）", shadow="winner δ0.28/PT60 paper 影子 2 週（11-17 無甩嘢切真倉）"),
    notes=notes,
)
json.dump(out, open(OUT_JSON, "w"), ensure_ascii=False, indent=1)
print("today.json written %s | verdict=%s | cand=%d(AI%d+C96%d) | breadth=%s(5d %s bell=%s RSP%s) | vix=%s" %
      (out["as_of"], verdict_level, len(candidates),
       len([c for c in candidates if not c.get("pool")]), len(c96_candidates),
       breadth, breadth_5d, speed_bell, rsp_above, out["market"]["vix"]))
