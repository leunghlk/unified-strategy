#!/usr/bin/python3
# 🛰️ 衛星台（10td+PT50+δ0.30）— 2026-11-10 短 tenor 三路研究拍板（用戶令「開」）。
# 研究 verdict：10td 全樣本 PF 2.23／MDD −11.8%（d30 六成）／崩盤窗唯一 PF>1 短 tenor；
# 3-5td 三線全滅。同 paper_book.py 已驗證骨架，四個分別：
#   ①PT_FOLD 0.50（PT50：mark ≤ 50% credit 平倉袋差價）
#   ②dte≤3 硬平倉線（wheel320 實倉兩單災難全部 dte=1 撞 10-15 裂口——短 tenor 尾端零緩衝
#     係結構死穴，研究中明確「dte≥3 硬平倉」拍板）
#   ③候選源 = today.json sat_candidates（δ0.30、8-14td 真到期曆、業績閘已喺 gen 過濾）
#   ④簿檔 paper_satellite_d10.json（dashboard 🛰️ 面板 fetch）
# 口徑同 📒：真鏈 mid mark（snap 最近 listed）→ BS 兜底、零滑價、$1.2/張來回費、NAV 錨 US$49,145。
# 「浮動」＝(credit−mark)×100×張數−來回費。
# ⭐11-12 回血第三輪拍板（用戶令「全做」）四刀：
#   ①MAX_NIGHTLY 5→7＋PT50 贏腿即日回收——mark 時段折咗贏腿→即晚用 freed margin 補開
#     （每日開倉總數＝full＋回收共用 7 張，book.day 計數）
#   ②hiIV 部署閘 pilot：rv21_pctl≥90（gen 今日台已傳每名 rv21_pctl；缺值＝fail-closed 唔開；
#     11-17 衛星生死判決同驗，屆時決定去留）
#   ③credit_min 閘全面改 executable-worst（短腿 bid−長腿 ask）——mid 只准用嚟 mark 估值
#     （OKTA 灌水教訓：board mid 1.64 vs 真鏈 worst 0.54 呢種單唔准再開）
#   ④w6 pilot 腿：spot≥$150 名（71/117），同 δ0.30 短腿、長腿＝短−6（真曆無 $6 格就限 [5,8]
#     內最近 listed），credit_min＝max(10%×width,$1.50) 同樣食 worst；獨立每晚 3 張＋$6k 按金
#     sub-cap（唔食主簿 40% NAV cap），track="w6" 落簿，11-17 對比 std 腿判去留。
#   🐛順手修：開倉前業績二重閘 datetime.date.fromisoformat 在 from-import 口徑係 AttributeError
#     ＝閘靜默失效（INTU 級事故温床）——改用已 import 嘅 date。
import json, math, os, subprocess, sys
from datetime import date, datetime, timedelta

DIR = os.path.dirname(os.path.abspath(__file__))
BOOK = os.path.join(DIR, "paper_satellite_d10.json")
TODAY = os.path.join(DIR, "today.json")
PT_FOLD = 0.50          # PT50：premium 收 50% → 剩 50% 平倉（研究最強組合）
FORCE_DTE = 3           # dte≤3 硬平倉（wheel320 dte=1 教訓——唔抱到期尾）
FEE_PER_CT = 1.2        # 來回費用/張（統一簿口徑）
NAV_USD = 49145.0
MAX_NIGHTLY = 7         # ⭐11-12 拍板①：5→7（std 腿每日 full＋PT50 回收共用；11-10 原值 5）
MARGIN_CAP_USD = NAV_USD * 0.40   # 簿內總按金上限 40% NAV（≈$19,658）
# ⭐11-12 拍板②④：w6+hiIV pilot（11-17 生死判決同驗）
HIIV_PILOT = True       # rv21_pctl≥90 部署閘（fail-closed：缺值唔開）
HIIV_PCTL = 90.0
W6_PILOT = True         # spot≥$150 名另開 w6 試驗腿（真鏈 strict、獨立 sub-cap）
W6_MIN_SPOT = 150.0
W6_MAX_NIGHTLY = 3      # w6 腿獨立每晚上限
W6_MARGIN_CAP = 6000.0  # w6 腿獨立按金 sub-cap
CREDIT_MIN_FLOOR = 1.50
START = None            # 首個 full run 自動寫入


def td_between(d0, d1):
    # 跟 gen_today 同款（星期一至五數，唔計假期）——MTM T 口徑必須同開倉定價一致
    d, n = d0, 0
    while d < d1:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def bs_put(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0:
        return max(K - S, 0.0)
    from math import log, sqrt, exp
    from statistics import NormalDist
    d1 = (log(S / K) + (r + sigma * sigma / 2) * T) / (sigma * sqrt(T))
    d2 = d1 - sigma * sqrt(T)
    return K * exp(-r * T) * NormalDist().cdf(-d2) - S * NormalDist().cdf(-d1)


def spot_of(sym):
    import yfinance as yf
    h = yf.Ticker(sym).history(period="5d")
    return float(h["Close"].iloc[-1]) if len(h) else None


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


def chain_quotes(sym, expiry):
    """真鏈 PUT bid/ask 表 {strike:(bid,ask)}（本地 127.0.0.1；公開 repo 頁唔會見 secret）。
    必帶 &expiry=——OpenD full-chain row cap 截走遠月（11-05 實錘）。攞唔到＝None。"""
    try:
        import urllib.request as _ur
        _u = ("http://127.0.0.1:8765/options/chain?code=US.%s&expiry=%s&secret=%s"
              % (sym, expiry, BRIDGE_SECRET))
        _d = json.loads(_ur.urlopen(_u, timeout=20).read().decode("utf-8", "replace"))
        if not _d.get("ok"):
            return None
        _q = {}
        for _e in (_d.get("expiries") or []):
            if _e.get("expiry") != expiry:
                continue
            for _r in (_e.get("rows") or []):
                if str(_r.get("type", "")).upper() != "PUT":
                    continue
                _k, _b, _a = _r.get("strike"), _r.get("bid"), _r.get("ask")
                if _k is not None and _b is not None and _a is not None and _a > 0:
                    _q[float(_k)] = (float(_b), float(_a))
        return _q or None
    except Exception:
        return None


def _snap(k, qs, tol=None):
    if k in qs:
        return k
    _t = tol if tol is not None else max(k * 0.05, 2.5)
    _c = [x for x in qs if abs(x - k) <= _t]
    return min(_c, key=lambda x: abs(x - k)) if _c else None


def chain_pair(sym, expiry, ks, kl, strict=False):
    """真鏈一對 strike：回 (mid_spread, worst_credit, ks_used, kl_used)。
    worst＝executable-worst＝短腿 bid − 長腿 ask（⭐11-12 拍板③：credit_min 閘食 worst 唔食 mid）。
    strict=True（w6 腿）：ks 精確／±$1 snap，kl 限 [ks−8, ks−5] 內最接近 −6 嘅 listed——
    湊唔到真 width 格＝None（唔准幻影 width）。"""
    qs = chain_quotes(sym, expiry)
    if not qs:
        return None, None, ks, kl
    if strict:
        ku = ks if ks in qs else _snap(ks, qs, tol=1.0)
        if ku is None:
            return None, None, ks, kl
        _t = [x for x in qs if 5.0 <= ku - x <= 8.0]
        lu = min(_t, key=lambda x: abs((ku - x) - 6.0)) if _t else None
        if lu is None:
            return None, None, ks, kl
    else:
        ku, lu = _snap(float(ks), qs), _snap(float(kl), qs)
        if ku is None or lu is None:
            return None, None, ks, kl
    b_s, a_s = qs[ku]
    b_l, a_l = qs[lu]
    mid = (b_s + a_s) / 2.0 - (b_l + a_l) / 2.0
    worst = b_s - a_l
    return round(mid, 3), round(worst, 3), ku, lu


def chain_mid(sym, expiry, ks, kl):
    """（MTM 舊接口）真鏈 bid/ask mid（snap 最近 listed，tol=max(5%,2.5)）。"""
    _m, _w, ku, lu = chain_pair(sym, expiry, ks, kl)
    if _m is None:
        return None, None, ks, kl
    return _m, "Futu", ku, lu


def et_session():
    try:
        from zoneinfo import ZoneInfo
        et = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return False
    if et.weekday() >= 5:
        return False
    t = et.hour + et.minute / 60.0
    return 9.5 <= t <= 16.05


def _earn_within(sym, dte_td):
    """開倉前 yf live 業績二重閘（11-10 INTU 根修）。回 (skip, msg)。
    🐛11-12 修：原本 datetime.date.fromisoformat 係 AttributeError（from-import 口徑冇呢個
    attribute）→ except 靜默食掉＝閘從來無行過。改用頂部已 import 嘅 date。"""
    try:
        import yfinance as _yf
        _cal2 = _yf.Ticker(sym).calendar
        _eds2 = (_cal2 or {}).get("Earnings Date")
        _d02 = _eds2[0] if isinstance(_eds2, (list, tuple)) and len(_eds2) else _eds2
        if isinstance(_d02, str):
            _d02 = date.fromisoformat(str(_d02)[:10])
        elif _d02 is not None and hasattr(_d02, "date"):
            _d02 = _d02.date()
        # 已出≤5td→禁（冷靜）；>5td→放行（下季未公佈＝唔知窗，同 gen earn_passed 口徑）
        if _d02 and _d02 < date.today():
            _g2 = td_between(_d02, date.today())
            if _g2 <= 5:
                return True, "業績 %s 剛出 %std——後 5td 冷靜（拍板⑥，二重閘）" % (_d02, _g2)
        else:
            _ed2 = td_between(date.today(), _d02) if _d02 else None
            if _ed2 is not None and _ed2 <= int(dte_td or 10) + 1:
                return True, "業績 %std 落喺窗口（引擎二重閘，11-10 INTU 根修）" % _ed2
    except Exception:
        pass
    return False, ""


def _open_one(book, c, ks, kl, expiry, mid, worst, width, credit_min, track, blocked, rv, acts, tag):
    """共用落簿：已過閘嘅一張 spread。回 margin（加落 counter 用）。"""
    margin = round(width * 100 - float(mid) * 100, 2)
    try:
        iv = float(c.get("iv_used") or 35) / 100.0
    except Exception:
        iv = 0.35
    sh = bool(blocked)   # 簿級 🔴 test-only 開關
    book["entries"].append(dict(
        date=date.today().isoformat(), sym=c["sym"], spot=c["spot"], short_k=ks, long_k=kl,
        expiry=expiry, credit=float(mid), credit_worst=float(worst),
        contracts=int(c["contracts"]), margin=margin, iv=iv, status="open",
        shadow=sh, mark=float(mid), mark_src="Futu", track=track,
        dte_td=c.get("dte_td"), pool=c.get("pool", "C96"),
        config="satellite 10td+PT50+δ0.30 池=117+C96", rv21_pctl=rv))
    _pre = "🧪 簿級 test-only" if sh else ""
    if track == "w6":
        acts.append("🧪w6 pilot 開倉（%s·真鏈 strict） %s %s/%s %s worst %.2f≥min %.2f mid %.2f×%d張（dte %s，%s）"
                    % (c.get("pool", "C96"), c["sym"], ks, kl, expiry, float(worst), credit_min,
                       float(mid), c["contracts"], c.get("dte_td"), tag))
    else:
        acts.append("%s%s開倉（%s·真鏈·worst %.2f） %s %s/%s %s mid %.2f×%d張（dte %s）"
                    % (_pre, " " if _pre else "", c.get("pool", "C96"), float(worst),
                       c["sym"], ks, kl, expiry, float(mid), c["contracts"], c.get("dte_td")))
    return margin


def _opens(book, td, blocked, day, acts):
    """開倉主體（full 開新＋mark 贏腿回收共用）。std 腿＝gen strikes（w≥15）；
    w6 pilot 腿＝spot≥$150 名 strict −6。全部：真鏈必到、credit_min 食 executable-worst、
    hiIV pilot 閘 rv21_pctl≥90（fail-closed）、業績二重閘。"""
    opens_syms = {e["sym"] for e in book["entries"] if e["status"] == "open" and e.get("track", "std") == "std"}
    w6_syms = {e["sym"] for e in book["entries"] if e["status"] == "open" and e.get("track") == "w6"}
    margin_now = sum(float(e.get("margin") or 0) for e in book["entries"] if e["status"] == "open")
    w6_margin_now = sum(float(e.get("margin") or 0) for e in book["entries"]
                        if e["status"] == "open" and e.get("track") == "w6")
    _skip_min, _skip_w, _skip_no_real, _skip_hiiv = 0, 0, 0, 0
    _skip_eg = []
    for c in (td.get("sat_candidates") or []):
        sym = c["sym"]
        # ⭐hiIV 部署閘（⭐11-12 拍板②，11-17 生死判決同驗）：rv21 pctl≥90；缺值＝fail-closed
        rv = c.get("rv21_pctl")
        if HIIV_PILOT and (rv is None or float(rv) != float(rv) or float(rv) < HIIV_PCTL):
            _skip_hiiv += 1
            if len(_skip_eg) < 3:
                _skip_eg.append("%s hiIV %s<%.0f" % (sym, ("—" if rv is None else "%.0f" % float(rv)), HIIV_PCTL))
            continue
        if sym in opens_syms:
            continue
        # 業績二重閘（新鮮 yf 日曆，唔信 gen 個 gate）——std 腿先查（w6 同 sym 同曆）
        _skip, _msg = _earn_within(sym, c.get("dte_td"))
        if _skip:
            acts.append("跳過 %s %s" % (sym, _msg))
            continue
        # ---- std 腿 ----
        if day["std"] < MAX_NIGHTLY:
            c_min = float(c.get("credit_min") or 0)
            _mid, _worst, ku, lu = chain_pair(sym, c["expiry"], c["short_k"], c["long_k"])
            if _mid is None:
                _skip_no_real += 1
                if len(_skip_eg) < 3:
                    _skip_eg.append("%s(無真鏈)" % sym)
            else:
                _width = ku - lu
                if _width < 15:
                    _skip_w += 1
                elif c_min > 0 and float(_worst) < c_min:
                    _skip_min += 1
                    if len(_skip_eg) < 3:
                        _skip_eg.append("%s worst%.2f<%.2f" % (sym, float(_worst), c_min))
                else:
                    _m = round(_width * 100 - float(_mid) * 100, 2)
                    if margin_now + _m > MARGIN_CAP_USD:
                        acts.append("按金上限 $%.0f 到頂——%s（要 $%.0f）唔開" % (MARGIN_CAP_USD, sym, _m))
                    else:
                        margin_now += _open_one(book, c, ku, lu, c["expiry"], _mid, _worst,
                                                _width, c_min, "std", blocked, rv, acts, "std")
                        day["std"] += 1
        # ---- w6 pilot 腿（同 sym std 已開照可開——A/B 對比用；per-sym w6 唯一）----
        if W6_PILOT and float(c.get("spot") or 0) >= W6_MIN_SPOT and day["w6"] < W6_MAX_NIGHTLY and sym not in w6_syms:
            _ks0 = float(c["short_k"])
            _m6, _w6c, ku6, lu6 = chain_pair(sym, c["expiry"], _ks0, _ks0 - 6.0, strict=True)
            if _m6 is None:
                _skip_no_real += 1
                if len(_skip_eg) < 3:
                    _skip_eg.append("%s w6(無真 width 格)" % sym)
            else:
                _w6w = ku6 - lu6
                _min6 = round(max(_w6w * 0.10, CREDIT_MIN_FLOOR), 2)
                if float(_w6c) < _min6:
                    _skip_min += 1
                    if len(_skip_eg) < 3:
                        _skip_eg.append("%s w6 worst%.2f<%.2f" % (sym, float(_w6c), _min6))
                else:
                    _m6m = round(_w6w * 100 - float(_m6) * 100, 2)
                    if w6_margin_now + _m6m > W6_MARGIN_CAP:
                        acts.append("w6 sub-cap $%.0f 到頂——%s（要 $%.0f）唔開" % (W6_MARGIN_CAP, sym, _m6m))
                    else:
                        w6_margin_now += _open_one(book, c, ku6, lu6, c["expiry"], _m6, _w6c,
                                                   _w6w, _min6, "w6", blocked, rv, acts,
                                                   "width %g" % _w6w)
                        day["w6"] += 1
    if _skip_min or _skip_w or _skip_no_real or _skip_hiiv:
        acts.append("跳過合計 %d（hiIV閘 %d〔例：%s〕／未過最低線(worst) %d／width<15 %d／無真鏈 %d）——寧願不做" %
                    (_skip_min + _skip_w + _skip_no_real + _skip_hiiv, _skip_hiiv,
                     "、".join(_skip_eg) if _skip_eg else "—", _skip_min, _skip_w, _skip_no_real))


def main(mode="full"):
    if mode == "mark" and not et_session():
        return 0
    lock = os.path.join(DIR, ".sat_lock")
    try:
        os.mkdir(lock)
    except Exception:
        return 0  # 上一輪未跑完——讓路（同 paper_book .pb_lock 完全隔離）
    try:
        return _run(mode)
    finally:
        try:
            os.rmdir(lock)
        except Exception:
            pass


def _run(mode):
    book = None
    if os.path.exists(BOOK):
        try:
            book = json.load(open(BOOK))
        except Exception:
            book = None
    if book is None or not isinstance(book, dict) or "entries" not in book:
        book = dict(started=date.today().isoformat(), entries=[], closed=[], log=[],
                    config="satellite 10td+PT50+δ0.30（2026-11-10 三路研究拍板＋11-12 w6+hiIV pilot）")
    old_fp = book.get("fp")
    td = json.load(open(TODAY))
    today_iso = date.today().isoformat()
    v_level = (td.get("verdict") or {}).get("level", "")
    # 簿級閘同 📒 同款：🔴 或 AI 池被封 → 照開標 shadow 🧪（test-only 保證有執行面數據）
    blocked = "🔴" in v_level or bool((td.get("verdict") or {}).get("ai_sealed"))
    acts = []

    # 每日開倉預算（⭐11-12 拍板①：full＋回收共用 std 7／w6 3）
    day = book.get("day") or {}
    if day.get("date") != today_iso:
        day = dict(date=today_iso, std=0, w6=0)
        book["day"] = day

    # 1) MTM＋平倉判定（PT50／dte≤3 硬平倉／到期）
    n_closed_today = 0
    for e in book["entries"]:
        if e["status"] != "open":
            continue
        S = spot_of(e["sym"])
        if not S:
            continue
        e["spot_last"] = S
        mark_src = "BS"
        try:
            d0 = date.fromisoformat(e["date"])
            de = date.fromisoformat(e["expiry"])
            t_left = max(td_between(date.today(), de), 1) / 252.0
            iv = e.get("iv", 0.35)
            mark = bs_put(S, e["short_k"], t_left, 0.043, iv) - bs_put(S, e["long_k"], t_left, 0.043, iv)
            _bm, _bs_rc, _ku, _lu = chain_mid(e["sym"], e["expiry"], e["short_k"], e["long_k"])
            if _bm is not None:
                mark, mark_src = _bm, "Futu"
                if _ku != e["short_k"] or _lu != e["long_k"]:
                    e["mark_ks"], e["mark_kl"] = _ku, _lu
                # ⭐11-13 用戶令：平倉最貴＝短腿 ask−長腿 bid（同真倉表口徑）——mark=mid 係靚價
                _q = chain_quotes(e["sym"], e["expiry"]) or {}
                _sq = _q.get(_ku)
                _lq = _q.get(_lu)
                if _sq and _lq and _sq[1] is not None:
                    e["close_worst"] = round(_sq[1] - (_lq[0] or 0), 2)
            mark = max(mark, max(e["short_k"] - S - (e["short_k"] - e["long_k"]), 0.0))
        except Exception:
            continue
        e["mark"] = round(mark, 3)
        e["mark_src"] = mark_src
        e["unrealized"] = round((e["credit"] - mark) * 100 * e["contracts"] - FEE_PER_CT * e["contracts"], 2)
        e["dte_td"] = max(td_between(date.today(), de), 0)
        if mark <= PT_FOLD * e["credit"]:
            e["status"] = "closed"
            e["exit_date"] = today_iso
            e["exit_reason"] = "PT50（mark %.3f ≤ 50%%×credit %.2f）" % (mark, e["credit"])
            e["realized"] = round((e["credit"] - mark) * 100 * e["contracts"] - FEE_PER_CT * e["contracts"], 2)
            book["closed"].append(e)
            n_closed_today += 1
            acts.append("PT50 平 %s %.2f→%.2f（%s mark）realized %+.0f" % (e["sym"], e["credit"], mark, mark_src, e["realized"]))
        elif e["dte_td"] <= FORCE_DTE:
            # ⭐ dte≤3 硬平倉線——wheel320 實倉 dte=1 兩單災難（10-15 裂口日 −$16,480）教訓；
            #   短 tenor 尾端零緩衝，唔抱到期尾，mark 照真鏈／BS 出場
            e["status"] = "closed"
            e["exit_date"] = today_iso
            e["exit_reason"] = "dte≤3 硬平倉（mark %.3f）" % e["mark"]
            e["realized"] = round((e["credit"] - e["mark"]) * 100 * e["contracts"] - FEE_PER_CT * e["contracts"], 2)
            book["closed"].append(e)
            n_closed_today += 1
            acts.append("dte≤3 硬平 %s realized %+.0f（wheel320 教訓閘）" % (e["sym"], e["realized"]))
        elif (de - date.today()).days <= 0:
            e["status"] = "closed"
            e["exit_date"] = today_iso
            e["exit_reason"] = "到期"
            e["realized"] = round((e["credit"] - e["mark"]) * 100 * e["contracts"] - FEE_PER_CT * e["contracts"], 2)
            book["closed"].append(e)
            n_closed_today += 1
            acts.append("到期平 %s realized %+.0f" % (e["sym"], e["realized"]))

    # 2) 新倉：full 模式照開；mark 模式今日有贏腿折→即晚回收補開（⭐11-12 拍板①PT50 即日回收，
    #    要 ET 場內（有真鏈）＋每日預算未滿）
    if mode == "full":
        _opens(book, td, blocked, day, acts)
    elif mode == "mark" and n_closed_today > 0 and et_session():
        _pre_n = len(acts)
        acts.append("↻ 贏腿即日回收：今日折 %d 張，freed margin 補開（day std %d/%d、w6 %d/%d）"
                    % (n_closed_today, day["std"], MAX_NIGHTLY, day["w6"], W6_MAX_NIGHTLY))
        _opens(book, td, blocked, day, acts)
        if len(acts) == _pre_n + 1:
            acts.pop()   # 無嘢補開就唔留呢行

    # 3) 匯總
    opens = [e for e in book["entries"] if e["status"] == "open"]
    realized = sum(e.get("realized", 0.0) for e in book["closed"])
    unreal = sum(e.get("unrealized", 0.0) for e in opens)
    w6_opens = [e for e in opens if e.get("track") == "w6"]
    book["summary"] = dict(as_of=datetime.now().strftime("%Y-%m-%d %H:%M"),
                           open_n=len(opens), realized=round(realized, 2),
                           unrealized=round(unreal, 2), total=round(realized + unreal, 2),
                           nav_paper=round(NAV_USD + realized + unreal, 2),
                           shadow_n=len([e for e in opens if e.get("shadow")]),
                           margin_used=round(sum(e["margin"] for e in opens), 2),
                           closed_n=len(book["closed"]),
                           std_open_n=len(opens) - len(w6_opens), w6_open_n=len(w6_opens),
                           w6_realized=round(sum(e.get("realized", 0.0) for e in book["closed"] if e.get("track") == "w6"), 2),
                           w6_unrealized=round(sum(e.get("unrealized", 0.0) for e in w6_opens), 2),
                           day=day)
    fp = json.dumps([round(e.get("mark", -1), 3) for e in book["entries"]] +
                    [round(unreal, 2), round(realized, 2), len(opens), len(book["closed"])], sort_keys=True)
    if mode == "mark" and fp == old_fp and not acts:
        print("sat mark no-change")
        return 0
    book["fp"] = fp
    if acts or mode == "full":
        book["log"] = (book.get("log") or [])[-30:] + [dict(date=today_iso, acts=acts)]

    with open(BOOK, "w") as f:
        json.dump(book, f, indent=1, ensure_ascii=False)
    print("satellite_d10 %s | open=%d(w6 %d) realized %.0f unreal %.0f" %
          (today_iso, len(opens), len(w6_opens), realized, unreal))
    for a in acts:
        print(" · " + a)
    # 4) TG 通知（📒 同款：paper 台交易即報）
    if acts:
        try:
            from tg_engine import tg
            tg("🛰️ 衛星台 %s\n· %s" % (today_iso, "\n· ".join(acts)))
        except Exception as ex:
            print("tg skip: %s" % ex.__class__.__name__)
    # 5) push（dashboard 讀）
    try:
        subprocess.run(["git", "add", "paper_satellite_d10.json"], cwd=DIR, check=False)
        r = subprocess.run(["git", "commit", "-q", "-m", "satellite d10 auto %s" % today_iso],
                           cwd=DIR, capture_output=True, text=True)
        if r.returncode == 0:
            subprocess.run(["git", "push", "-q", "origin", "main"], cwd=DIR, check=True)
            print("pushed")
    except Exception as ex:
        print("push skip: %s" % ex.__class__.__name__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "full"))
