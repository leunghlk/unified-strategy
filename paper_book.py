#!/usr/bin/python3
# 📒 Paper Book engine（winner PT60+δ0.28 四週觀察台）— 11-02 用戶令「自己背後 run，在 dashboard 顯示」。
# 兩個模式（launchd）：
#   full（每交易日 21:40 HKT）：
#   1. 讀 today.json paper_candidates（gen_today 21:35 先產，δ0.28 鏈 mid）
#   2. 簿級閘跟今日台 verdict：🔴 照開但標 shadow（test-only，11-04 用戶追拍——保證 2 週期滿有執行面數據）；非 🔴 正式開
#   3. open 倉 mark＝bridge Futu OpenD 真鏈 mid（⭐11-05 用戶令「全部都用 Futu OpenD 期權鏈」；
#      必帶 &expiry=——OpenD full-chain row cap 截走遠月）→ BS（IV 用 entry 時 iv_used）兜底；
#      PT60＝mark ≤ 40% entry credit 平倉；到期＝intrinsic
#   4. 寫 paper_book.json → git commit+push（dashboard 讀）
#   mark（ET 開市時段每 5 分鐘，11-04 用戶問 real-time P&L 後加）：
#   - 純 MTM＋PT60/到期判定，唔開新倉；有意義狀態（marks/P&L/倉數）冇變就唔寫唔 push
#   - .pb_lock 防同 21:40 全跑相撞
# 口徑：零滑價、$1.2/contract 費用（跟統一簿）、NAV 錨 US$49,145。
# 「浮動」＝未實現 P&L＝(credit−mark)×100×張數−來回費（即以 mid 平倉袋到幾多）。
import json, math, os, subprocess, sys
from datetime import date, datetime, timedelta

DIR = os.path.dirname(os.path.abspath(__file__))
BOOK = os.path.join(DIR, "paper_book.json")
TODAY = os.path.join(DIR, "today.json")
PT_FOLD = 0.40          # PT60：premium 收 60% → 剩 40% 平倉
FEE_PER_CT = 1.2        # 來回費用/張（統一簿口徑）
NAV_USD = 49145.0
START = "2026-11-03"    # 觀察期第一個交易日（11-02 為 setup 日）


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
    """⭐11-05: bridge Futu OpenD 真鏈 bid/ask mid（本地 127.0.0.1；公開 repo 頁唔會見 secret）。
    必帶 &expiry=——OpenD full-chain 有 row cap 會截走遠月（11-05 TMO 12-18 實錘）。
    舊 BS 入場嘅 non-listed strikes（如 ZS 201/181）→ snap 最近 listed（tol=max(5%,2.5)，
    跟 bridge weekly_scan 死 strike 口徑）。→ (mid, src, ks_used, kl_used)；snap 唔到／
    任一腿 bid/ask 缺＝(None, None, ks, kl)（fallback BS）。"""
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


def weeks_in(started):
    try:
        d0 = date.fromisoformat(started)
        return min(2, int((date.today() - d0).days / 7) + 1)
    except Exception:
        return 0


def et_session():
    # ET 開市時段 guard（mark 模式 5 分鐘輪詢用）
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
    lock = os.path.join(DIR, ".pb_lock")
    try:
        os.mkdir(lock)
    except Exception:
        return 0  # 上一輪未跑完（21:40 全跑撞 5 分鐘 mark）——讓路
    try:
        return _run(mode)
    finally:
        try:
            os.rmdir(lock)
        except Exception:
            pass


def _run(mode):
    book = dict(started=START, entries=[], closed=[], log=[])
    if os.path.exists(BOOK):
        book = json.load(open(BOOK))
    old_fp = book.get("fp")
    td = json.load(open(TODAY))
    today_iso = date.today().isoformat()
    v_level = (td.get("verdict") or {}).get("level", "")
    # blocked＝唔准正式開：簿級 🔴，或 AI 池被封（局部殺戮／熊狀態門，⭐11-05 雙閘——ai_sealed）
    blocked = "🔴" in v_level or bool((td.get("verdict") or {}).get("ai_sealed"))
    acts = []

    # 1) MTM＋平倉判定（PT60／到期）
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
            # ⭐11-05: bridge Futu 真鏈 mid 優先——PT60 判定唔再食 BS 假價；
            # snap 過 strike 會記 mark_ks/mark_kl（audit 用）
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
        if mark <= PT_FOLD * e["credit"]:
            e["status"] = "closed"
            e["exit_date"] = today_iso
            e["exit_reason"] = "PT60（mark %.3f ≤ 40%%×credit %.2f）" % (mark, e["credit"])
            e["realized"] = round((e["credit"] - mark) * 100 * e["contracts"] - FEE_PER_CT * e["contracts"], 2)
            book["closed"].append(e)
            acts.append("PT60 平 %s %.2f→%.2f（%s mark）realized %+.0f" % (e["sym"], e["credit"], mark, mark_src, e["realized"]))
        elif (de - date.today()).days <= 0:
            e["status"] = "closed"
            e["exit_date"] = today_iso
            e["exit_reason"] = "到期"
            e["realized"] = round((e["credit"] - mark) * 100 * e["contracts"] - FEE_PER_CT * e["contracts"], 2)
            book["closed"].append(e)
            acts.append("到期平 %s realized %+.0f" % (e["sym"], e["realized"]))

    # 2) 新倉（shadow 模式：簿級 🔴 照開標 test-only；同 sym 冇 open）
    #    mark 模式（ET 開市 5 分鐘輪詢）只 MTM＋平倉判定，唔開新倉
    opens = {e["sym"] for e in book["entries"] if e["status"] == "open"}
    if mode == "full":
        for c in (td.get("paper_candidates") or []):
            if c["sym"] in opens:
                continue
            try:
                iv = float(c.get("iv_used") or 35) / 100.0
            except Exception:
                iv = 0.35
            sh = bool(blocked)
            book["entries"].append(dict(
                date=today_iso, sym=c["sym"], spot=c["spot"], short_k=c["short_k"], long_k=c["long_k"],
                expiry=c["expiry"], credit=float(c["credit"]), contracts=int(c["contracts"]),
                margin=float(c["margin"]), iv=iv, status="open", shadow=sh, config="winner PT60+δ0.28"))
            acts.append(("shadow 開倉（簿級 🔴 test-only）" if sh else "開 paper 倉") +
                        " %s %s/%s credit %s×%d張" % (c["sym"], c["short_k"], c["long_k"], c["credit"], c["contracts"]))

    # 3) 匯總
    opens = [e for e in book["entries"] if e["status"] == "open"]
    realized = sum(e.get("realized", 0.0) for e in book["closed"])
    unreal = sum(e.get("unrealized", 0.0) for e in opens)
    book["summary"] = dict(as_of=datetime.now().strftime("%Y-%m-%d %H:%M"),
                           week=weeks_in(book.get("started", START)),
                           open_n=len(opens), realized=round(realized, 2),
                           unrealized=round(unreal, 2), total=round(realized + unreal, 2),
                           nav_paper=round(NAV_USD + realized + unreal, 2),
                           shadow_n=len([e for e in opens if e.get("shadow")]),
                           margin_used=round(sum(e["margin"] for e in opens), 2))
    # mark 模式：有意義狀態冇變（marks/P&L/倉數）就唔寫唔 push（防 288 commit/日）
    fp = json.dumps([round(e.get("mark", -1), 3) for e in book["entries"]] +
                    [round(unreal, 2), round(realized, 2), len(opens), len(book["closed"])], sort_keys=True)
    if mode == "mark" and fp == old_fp and not acts:
        print("mark no-change")
        return 0
    book["fp"] = fp
    if acts or mode == "full":
        book["log"] = (book.get("log") or [])[-30:] + [dict(date=today_iso, acts=acts)]

    with open(BOOK, "w") as f:
        json.dump(book, f, indent=1, ensure_ascii=False)
    print("paper_book %s | wk%s open=%d realized %.0f unreal %.0f" %
          (today_iso, book["summary"]["week"], len(opens), realized, unreal))
    for a in acts:
        print(" · " + a)
    # 4) TG 通知（11-04 用戶批：paper 台交易即報）
    if acts:
        try:
            from tg_engine import tg
            tg("📒 Paper 台 %s\n· %s" % (today_iso, "\n· ".join(acts)))
        except Exception as ex:
            print("tg skip: %s" % ex.__class__.__name__)
    # 4) push（dashboard 讀）
    try:
        subprocess.run(["git", "add", "paper_book.json"], cwd=DIR, check=False)
        r = subprocess.run(["git", "commit", "-q", "-m", "paper book auto %s" % today_iso],
                           cwd=DIR, capture_output=True, text=True)
        if r.returncode == 0:
            subprocess.run(["git", "push", "-q", "origin", "main"], cwd=DIR, check=True)
            print("pushed")
    except Exception as ex:
        print("push skip: %s" % ex.__class__.__name__)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "full"))
