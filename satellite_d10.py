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
import json, math, os, subprocess, sys
from datetime import date, datetime, timedelta

DIR = os.path.dirname(os.path.abspath(__file__))
BOOK = os.path.join(DIR, "paper_satellite_d10.json")
TODAY = os.path.join(DIR, "today.json")
PT_FOLD = 0.50          # PT50：premium 收 50% → 剩 50% 平倉（研究最強組合）
FORCE_DTE = 3           # dte≤3 硬平倉（wheel320 dte=1 教訓——唔抱到期尾）
FEE_PER_CT = 1.2        # 來回費用/張（統一簿口徑）
NAV_USD = 49145.0
MAX_NIGHTLY = 5         # ⭐11-10 擴池配套：每晚最多開 5 倉（credit 大排先；池 11→189 名後唔 cap 會一晚十幾倉（⭐11-11 +SPCX/AMKR））
MARGIN_CAP_USD = NAV_USD * 0.40   # 簿內總按金上限 40% NAV（≈$19,658）
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


def chain_mid(sym, expiry, ks, kl):
    """真鏈 bid/ask mid（本地 127.0.0.1；公開 repo 頁唔會見 secret）。
    必帶 &expiry=——OpenD full-chain row cap 截走遠月（11-05 實錘）。
    snap 最近 listed（tol=max(5%,2.5)）→ (mid, src, ks_used, kl_used)；攞唔到＝(None,...)。"""
    try:
        import urllib.request as _ur
        _u = ("http://127.0.0.1:8765/options/chain?code=US.%s&expiry=%s&secret=%s"
              % (sym, expiry, BRIDGE_SECRET))
        _d = json.loads(_ur.urlopen(_u, timeout=20).read().decode("utf-8", "replace"))
        if not _d.get("ok"):
            return None, None, ks, kl
        _mids = {}
        for _e in (_d.get("expiries") or []):
            if _e.get("expiry") != expiry:
                continue
            for _r in (_e.get("rows") or []):
                if str(_r.get("type", "")).upper() != "PUT":
                    continue
                _k = _r.get("strike")
                _b, _a = _r.get("bid"), _r.get("ask")
                if _k is not None and _b is not None and _a is not None and _a > 0:
                    _mids[float(_k)] = (_b + _a) / 2.0

        def _pick(k):
            if k in _mids:
                return k
            _tol = max(k * 0.05, 2.5)
            _cands = [x for x in _mids if abs(x - k) <= _tol]
            return min(_cands, key=lambda x: abs(x - k)) if _cands else None

        _ku, _lu = _pick(float(ks)), _pick(float(kl))
        if _ku is not None and _lu is not None and _mids[_ku] > 0:
            return round(_mids[_ku] - _mids[_lu], 3), "Futu", _ku, _lu
    except Exception:
        pass
    return None, None, ks, kl


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
                    config="satellite 10td+PT50+δ0.30（2026-11-10 三路研究拍板）")
    old_fp = book.get("fp")
    td = json.load(open(TODAY))
    today_iso = date.today().isoformat()
    v_level = (td.get("verdict") or {}).get("level", "")
    # 簿級閘同 📒 同款：🔴 或 AI 池被封 → 照開標 shadow 🧪（test-only 保證有執行面數據）
    blocked = "🔴" in v_level or bool((td.get("verdict") or {}).get("ai_sealed"))
    acts = []

    # 1) MTM＋平倉判定（PT50／dte≤3 硬平倉／到期）
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
            acts.append("PT50 平 %s %.2f→%.2f（%s mark）realized %+.0f" % (e["sym"], e["credit"], mark, mark_src, e["realized"]))
        elif e["dte_td"] <= FORCE_DTE:
            # ⭐ dte≤3 硬平倉線——wheel320 實倉 dte=1 兩單災難（10-15 裂口日 −$16,480）教訓；
            #   短 tenor 尾端零緩衝，唔抱到期尾，mark 照真鏈／BS 出場
            e["status"] = "closed"
            e["exit_date"] = today_iso
            e["exit_reason"] = "dte≤3 硬平倉（mark %.3f）" % e["mark"]
            e["realized"] = round((e["credit"] - e["mark"]) * 100 * e["contracts"] - FEE_PER_CT * e["contracts"], 2)
            book["closed"].append(e)
            acts.append("dte≤3 硬平 %s realized %+.0f（wheel320 教訓閘）" % (e["sym"], e["realized"]))
        elif (de - date.today()).days <= 0:
            e["status"] = "closed"
            e["exit_date"] = today_iso
            e["exit_reason"] = "到期"
            e["realized"] = round((e["credit"] - e["mark"]) * 100 * e["contracts"] - FEE_PER_CT * e["contracts"], 2)
            book["closed"].append(e)
            acts.append("到期平 %s realized %+.0f" % (e["sym"], e["realized"]))

    # 2) 新倉（full 模式先開；mark 模式純 MTM；同 sym 冇 open 才開）
    opens_syms = {e["sym"] for e in book["entries"] if e["status"] == "open"}
    if mode == "full":
        _margin_now = sum(float(e.get("margin") or 0) for e in book["entries"] if e["status"] == "open")
        _n_tonight = 0
        _skip_min, _skip_w, _skip_no_real = 0, 0, 0   # ⭐聚合統計——189 名逐名出「跳過」會轟炸 TG（11-10 擴池配套）
        _skip_eg = []
        for c in (td.get("sat_candidates") or []):
            if c["sym"] in opens_syms:
                continue
            # ⭐三條線紀律（11-10 用戶拍板）：未過 credit_min 唔好開＋width≥15（研究拍板）
            if float(c.get("credit_min") or 0) > 0 and float(c["credit"]) < float(c["credit_min"]):
                _skip_min += 1
                if len(_skip_eg) < 3:
                    _skip_eg.append("%s %.2f<%.2f" % (c["sym"], c["credit"], c["credit_min"]))
                continue
            if float(c.get("short_k") or 0) - float(c.get("long_k") or 0) < 15:
                _skip_w += 1
                continue
            # ⭐11-10 防線②（INTU 漏閘事故）：開倉前 yf live 再驗業績窗——gen screen 食過凍結 CSV 話 ed=18，
            # 真實 INTU 11-20＝到期日。呢道閘用新鮮日曆，唔信 gen 個 gate
            try:
                import yfinance as _yf
                _cal2 = _yf.Ticker(c["sym"]).calendar
                _eds2 = (_cal2 or {}).get("Earnings Date")
                _d02 = _eds2[0] if isinstance(_eds2, (list, tuple)) and len(_eds2) else _eds2
                if isinstance(_d02, str):
                    _d02 = datetime.date.fromisoformat(str(_d02)[:10])
                elif _d02 is not None and hasattr(_d02, "date"):
                    _d02 = _d02.date()
                # ⭐11-11 蟲修（agent C 實錘：td_between past→0＋`0<=0`＝已出＝無限期禁）＋拍板⑥補齊：
                # 已出≤5td→禁（冷靜）；>5td→放行（下季未公佈＝唔知窗，同 gen earn_passed 口徑）
                if _d02 and _d02 < date.today():
                    _g2 = td_between(_d02, date.today())
                    if _g2 <= 5:
                        acts.append("跳過 %s 業績 %s 剛出 %std——後 5td 冷靜（拍板⑥，二重閘）" % (c["sym"], _d02, _g2))
                        continue
                else:
                    _ed2 = td_between(date.today(), _d02) if _d02 else None
                    if _ed2 is not None and _ed2 <= int(c.get("dte_td") or 10) + 1:
                        acts.append("跳過 %s 業績 %std 落喺窗口（引擎二重閘，11-10 INTU 根修）" % (c["sym"], _ed2))
                        continue
            except Exception:
                pass
            # ⭐11-10 擴池配套：每晚 ≤5 倉＋總按金 ≤40% NAV（池 11→189 名後唔 cap 會一晚爆十幾倉；
            #    gen 已 credit 大排先，行到呢度就係最肥先開）
            if _n_tonight >= MAX_NIGHTLY:
                acts.append("每晚 %d 倉已滿——%s 之後嘅候選留下一晚" % (MAX_NIGHTLY, c["sym"]))
                break
            if _margin_now + float(c.get("margin") or 0) > MARGIN_CAP_USD:
                acts.append("按金上限 $%.0f 到頂——%s（要 $%.0f）唔開" % (MARGIN_CAP_USD, c["sym"], float(c.get("margin") or 0)))
                continue
            # ⭐落單前真鏈覆核（11-10 用戶令「要攞真價」）：gen 時段（07:30/21:35 HKT＝ET 盤前/盤後）
            #    OpenD 有機會俾唔到真鏈（0/0 snapshot 窗）——BS 合成價唔准開倉（OKTA 灌水教訓：
            #    board mid 1.64 vs 真鏈 worst 0.54）。開倉嗰刻重新向 Futu 攞真 mid，攞唔到＝跳過。
            _rm, _rsrc, _ku, _lu = chain_mid(c["sym"], c["expiry"], c["short_k"], c["long_k"])
            if _rm is None:
                _skip_no_real += 1
                if len(_skip_eg) < 3:
                    _skip_eg.append("%s(無真鏈)" % c["sym"])
                continue
            _credit, _ks, _kl = float(_rm), float(_ku), float(_lu)
            if _ks != float(c["short_k"]) or _kl != float(c["long_k"]):
                pass   # snap 落 listed strike（chain_mid tol 5%）——照用 snapped
            _width = _ks - _kl
            if _width < 15:
                _skip_w += 1
                continue
            if float(c.get("credit_min") or 0) > 0 and _credit < float(c["credit_min"]):
                _skip_min += 1
                if len(_skip_eg) < 3:
                    _skip_eg.append("%s 真鏈%.2f<%.2f" % (c["sym"], _credit, float(c["credit_min"])))
                continue
            _margin = round(_width * 100 - _credit * 100, 2)
            if _margin_now + _margin > MARGIN_CAP_USD:
                acts.append("按金上限 $%.0f 到頂——%s（要 $%.0f）唔開" % (MARGIN_CAP_USD, c["sym"], _margin))
                continue
            try:
                iv = float(c.get("iv_used") or 35) / 100.0
            except Exception:
                iv = 0.35
            sh = bool(blocked)   # 簿級 🔴 test-only 開關（shadow 池已剷——11-10 用戶令淨返兩個池）
            book["entries"].append(dict(
                date=today_iso, sym=c["sym"], spot=c["spot"], short_k=_ks, long_k=_kl,
                expiry=c["expiry"], credit=_credit, contracts=int(c["contracts"]),
                margin=_margin, iv=iv, status="open", shadow=sh, mark=_credit, mark_src=_rsrc,
                dte_td=c.get("dte_td"), pool=c.get("pool", "C96"), config="satellite 10td+PT50+δ0.30 池=117+C96"))
            _margin_now += _margin
            _n_tonight += 1
            acts.append(("🧪 簿級 test-only 開倉" if sh else "開倉") +
                        "（%s·真鏈） %s %s/%s %s credit %.2f×%d張（dte %s）" % (c.get("pool", "C96"), c["sym"], _ks, _kl, c["expiry"], _credit, c["contracts"], c.get("dte_td")))
        if _skip_min or _skip_w or _skip_no_real:
            acts.append("跳過合計 %d（未過最低線 %d〔例：%s〕／width<15 %d／開倉時無真鏈 %d）——寧願不做" %
                        (_skip_min + _skip_w + _skip_no_real, _skip_min,
                         "、".join(_skip_eg) if _skip_eg else "—", _skip_w, _skip_no_real))

    # 3) 匯總
    opens = [e for e in book["entries"] if e["status"] == "open"]
    realized = sum(e.get("realized", 0.0) for e in book["closed"])
    unreal = sum(e.get("unrealized", 0.0) for e in opens)
    book["summary"] = dict(as_of=datetime.now().strftime("%Y-%m-%d %H:%M"),
                           open_n=len(opens), realized=round(realized, 2),
                           unrealized=round(unreal, 2), total=round(realized + unreal, 2),
                           nav_paper=round(NAV_USD + realized + unreal, 2),
                           shadow_n=len([e for e in opens if e.get("shadow")]),
                           margin_used=round(sum(e["margin"] for e in opens), 2),
                           closed_n=len(book["closed"]))
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
    print("satellite_d10 %s | open=%d realized %.0f unreal %.0f" %
          (today_iso, len(opens), realized, unreal))
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
