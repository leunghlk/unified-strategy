#!/usr/bin/python3
# 📒 Paper Book engine（winner PT60+δ0.28 四週觀察台）— 11-02 用戶令「自己背後 run，在 dashboard 顯示」。
# 每交易日 21:40 HKT launchd：
#   1. 讀 today.json paper_candidates（gen_today 21:35 先產，δ0.28 鏈 mid）
#   2. 簿級閘跟今日台 verdict：🔴 照開但標 shadow（test-only，11-04 用戶追拍——保證 2 週期滿有執行面數據）；非 🔴 正式開
#   3. open 倉每日 BS mark（IV 用 entry 時 iv_used）；PT60＝mark ≤ 40% entry credit 平倉；到期＝intrinsic
#   4. 寫 paper_book.json → git commit+push（dashboard 讀）
# 口徑：零滑價、$1.2/contract 費用（跟統一簿）、NAV 錨 US$49,145。
import json, math, os, subprocess, sys
from datetime import date, datetime

DIR = os.path.dirname(os.path.abspath(__file__))
BOOK = os.path.join(DIR, "paper_book.json")
TODAY = os.path.join(DIR, "today.json")
PT_FOLD = 0.40          # PT60：premium 收 60% → 剩 40% 平倉
FEE_PER_CT = 1.2        # 來回費用/張（統一簿口徑）
NAV_USD = 49145.0
START = "2026-11-03"    # 觀察期第一個交易日（11-02 為 setup 日）


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


def weeks_in(started):
    try:
        d0 = date.fromisoformat(started)
        return min(2, int((date.today() - d0).days / 7) + 1)
    except Exception:
        return 0


def main():
    book = dict(started=START, entries=[], closed=[], log=[])
    if os.path.exists(BOOK):
        book = json.load(open(BOOK))
    td = json.load(open(TODAY))
    today_iso = date.today().isoformat()
    v_level = (td.get("verdict") or {}).get("level", "")
    blocked = "🔴" in v_level
    acts = []

    # 1) MTM＋平倉判定（PT60／到期）
    for e in book["entries"]:
        if e["status"] != "open":
            continue
        S = spot_of(e["sym"])
        if not S:
            continue
        e["spot_last"] = S
        try:
            d0 = date.fromisoformat(e["date"])
            de = date.fromisoformat(e["expiry"])
            t_left = max((de - date.today()).days / 252.0, 1 / 252.0)
            iv = e.get("iv", 0.35)
            mark = bs_put(S, e["short_k"], t_left, 0.043, iv) - bs_put(S, e["long_k"], t_left, 0.043, iv)
            mark = max(mark, max(e["short_k"] - S - (e["short_k"] - e["long_k"]), 0.0))
        except Exception:
            continue
        e["mark"] = round(mark, 3)
        e["unrealized"] = round((e["credit"] - mark) * 100 * e["contracts"] - FEE_PER_CT * e["contracts"], 2)
        if mark <= PT_FOLD * e["credit"]:
            e["status"] = "closed"
            e["exit_date"] = today_iso
            e["exit_reason"] = "PT60（mark %.3f ≤ 40%%×credit %.2f）" % (mark, e["credit"])
            e["realized"] = round((e["credit"] - mark) * 100 * e["contracts"] - FEE_PER_CT * e["contracts"], 2)
            book["closed"].append(e)
            acts.append("PT60 平 %s %.2f→%.2f realized %+.0f" % (e["sym"], e["credit"], mark, e["realized"]))
        elif (de - date.today()).days <= 0:
            e["status"] = "closed"
            e["exit_date"] = today_iso
            e["exit_reason"] = "到期"
            e["realized"] = round((e["credit"] - mark) * 100 * e["contracts"] - FEE_PER_CT * e["contracts"], 2)
            book["closed"].append(e)
            acts.append("到期平 %s realized %+.0f" % (e["sym"], e["realized"]))

    # 2) 新倉（shadow 模式：簿級 🔴 照開標 test-only；同 sym 冇 open）
    opens = {e["sym"] for e in book["entries"] if e["status"] == "open"}
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
    book["log"] = (book.get("log") or [])[-30:] + [dict(date=today_iso, acts=acts)]

    with open(BOOK, "w") as f:
        json.dump(book, f, indent=1, ensure_ascii=False)
    print("paper_book %s | wk%s open=%d realized %.0f unreal %.0f" %
          (today_iso, book["summary"]["week"], len(opens), realized, unreal))
    for a in acts:
        print(" · " + a)
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
    sys.exit(main())
