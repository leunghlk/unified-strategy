#!/usr/bin/python3
# 📲 TG Engine — 統一台七項 TG 通知（11-04 用戶批「all」＋日報兩發制）。
# 用法：tg_engine.py session｜nbis｜weekly｜test
#   session（launchd HKT 21:00/21:30/22:00/23:00 交易日）：跟 ET 鐘自動揀 daily／fixup（DST 免疫）
#     daily（ET 08:24–09:25 開市前）：判決＋封閘逐條＋候選＋NBIS＋（週一）業績禁＋按金警
#     fixup（ET 09:45–11:00 開市後）：重跑 gen_today → 判決轉級／credit 偏差>10% 先發，冇變靜音
#   nbis（launchd 每 5 分鐘）：ET 開市時段輪詢；≥155 預警（日一次）／≥163 觸發（日一次）
#   weekly（launchd 週日 20:00 HKT）：台面狀態＋paper 週結＋NBIS＋本金
#   test：即刻發 daily＋weekly 格式實測訊息（唔寫 state）
# 規則：跟 TG 靜音三面（變級先發／複合必列邊條／fired 落 state）；state=tg_state.json
import json, os, subprocess, sys
from datetime import date, datetime
from urllib.request import Request, urlopen

DIR = os.path.dirname(os.path.abspath(__file__))
TODAY = os.path.join(DIR, "today.json")
BOOK = os.path.join(DIR, "paper_book.json")
STATE = os.path.join(DIR, "tg_state.json")
ENV_FILE = os.path.expanduser("~/.hermes/.env")
ALERT_CHAT = 726483522
NAV_USD = 49145.0
NBIS_WARN = 155.0
NBIS_TRIG_LO = 163.0


def load_token():
    for ln in open(ENV_FILE):
        if ln.startswith("TELEGRAM_BOT_TOKEN="):
            return ln.split("=", 1)[1].strip().strip('"')
    return ""


def tg(text):
    tok = load_token()
    if not tok:
        return False
    req = Request("https://api.telegram.org/bot%s/sendMessage" % tok,
                  data=json.dumps({"chat_id": ALERT_CHAT, "text": text}).encode(),
                  headers={"Content-Type": "application/json", "User-Agent": "tg-engine/1.0"})
    try:
        with urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode()).get("ok") is True
    except Exception:
        return False


def et_now():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York"))


def state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def save_state(s):
    json.dump(s, open(STATE, "w"), ensure_ascii=False, indent=1)


def nbis_price():
    try:
        import yfinance as yf
        fi = yf.Ticker("NBIS").fast_info
        p = fi.last_price or fi.lastPrice
        if p:
            return float(p)
    except Exception:
        pass
    try:
        import yfinance as yf
        h = yf.Ticker("NBIS").history(period="1d", interval="1m")
        return float(h["Close"].iloc[-1]) if len(h) else None
    except Exception:
        return None


def gates_line(td):
    m = td.get("market", {})
    r = td.get("radar", {})
    out = []
    br = m.get("breadth")
    if br is not None:
        out.append("廣度 %.1f%%（%s，<50 封）" % (br, m.get("breadth_regime", "—")))
    if r.get("level"):
        rr = "、".join((r.get("reasons") or [])[:3])
        out.append("雷達 %s（%s）" % (r["level"], rr))
    if m.get("vix") is not None:
        out.append("VIX %.2f（%s ✓）" % (m["vix"], m.get("vix_band", "—")))
    return "｜".join(out)


def cand_lines(cands):
    if not cands:
        return "候選：無（screen 全禁或封刀）"
    tot_m = 0.0
    lines = []
    for c in cands:
        tot_m += float(c.get("margin") or 0)
        lines.append("%s %s/%s %s credit %s×%s張 δ%s 按金 $%s" % (
            c["sym"], c["short_k"], c["long_k"], c["expiry"][5:], c["credit"],
            c["contracts"], c.get("delta", "—"), "{:,.0f}".format(float(c["margin"]))))
    frac = 100 * tot_m / NAV_USD
    warn = " ⚠️>65%！" if frac > 65 else "（%.0f%% NAV，≤65%% ✓）" % frac
    return "候選：\n· " + "\n· ".join(lines) + "\n總按金 $%s＝%s" % ("{:,.0f}".format(tot_m), warn)


def nbis_line(td):
    nb = td.get("nbis", {})
    return "NBIS %s｜觸發線 %s（剩 %std）｜%s" % (
        nb.get("spot", "—"), nb.get("trigger", "163–165"), nb.get("td_left", "—"),
        nb.get("state", ""))


def daily_text(td, tag=""):
    v = td.get("verdict", {})
    m = td.get("market", {})
    head = "🎯 統一台日報 %s %s%s\n判決：%s\n%s" % (
        date.today().strftime("%m-%d"), et_now().strftime("%H:%M ET"), tag,
        v.get("level", "—"), gates_line(td))
    st = "牛狀態（SPX %.0f ≥ SMA200 %.0f）" % (m["spx"], m["spx_sma200"]) if m.get("mkt_state") == "bull" else "熊狀態（< SMA200）"
    body = "\n%s\n%s\n%s" % (st, cand_lines(td.get("candidates") or []), nbis_line(td))
    extra = ""
    if et_now().weekday() == 0:  # ET 週一：業績禁名單
        banned = [("%s（%std）" % (r["sym"], r["earn_td"])) for r in (td.get("screen") or [])
                  if r.get("earn") == "禁" and r.get("earn_td") is not None]
        if banned:
            extra = "\n業績禁（前21td禁＋後5td冷靜）：\n· " + "\n· ".join(banned[:8])  # ⭐11-11 名實對齊（舊標籤「5td 冷靜期」列嘅係 21td 前禁名）
    return head + body + extra


def fixup_send(td, prev_v, prev_credits):
    v = td.get("verdict", {})
    lines = []
    if v.get("level") != prev_v:
        lines.append("判決轉級：%s → %s" % (prev_v, v.get("level")))
    diffs = []
    for c in (td.get("candidates") or []):
        p = prev_credits.get(c["sym"])
        if p and float(c["credit"]) > 0:
            dev = 100 * (float(c["credit"]) - p) / p
            if abs(dev) > 10:
                diffs.append("%s credit %s→%s（%+.1f%%）" % (c["sym"], p, c["credit"], dev))
    if diffs:
        lines.append("credit 偏差 >10%：\n· " + "\n· ".join(diffs))
    if not lines:
        return False
    tg("🎯 台面修正 %s %s ET\n%s" % (date.today().strftime("%m-%d"),
                                    et_now().strftime("%H:%M"), "\n".join(lines)))
    return True


def paper_week_text():
    try:
        b = json.load(open(BOOK))
        s = b.get("summary", {})
        cl = b.get("closed") or []
        clx = "、".join("%s %+d$（%s）" % (e["sym"], e.get("realized", 0), e.get("exit_reason", "").split("（")[0]) for e in cl[-5:]) or "無"
        return "📒Paper（第%s/2週）：open %s（shadow %s）｜realized %+d｜浮動 %+d｜paper NAV $%s\n已平：%s" % (
            s.get("week", 1), s.get("open_n", 0), s.get("shadow_n", 0),
            s.get("realized", 0), s.get("unrealized", 0), s.get("nav_paper", 0), clx)
    except Exception:
        return "📒Paper：讀唔到 paper_book.json"


def weekly_text():
    td = json.load(open(TODAY))
    v = td.get("verdict", {})
    m = td.get("market", {})
    ac = td.get("account", {})
    nxt = "；無甩嘢切真倉 PT60+δ0.28（11-02 追拍）"
    return ("📊 統一台週報 %s\n判決：%s｜廣度 %s%%｜VIX %s\n%s\n%s\n流動本金 %s HKD｜NAV 錨 $%s\npaper 檢定：11-17%s" % (
        date.today().strftime("%m-%d"), v.get("level", "—"),
        m.get("breadth", "—"), m.get("vix", "—"), paper_week_text(),
        nbis_line(td), "{:,.0f}".format(ac.get("cash_hkd", 0)),
        "{:,.0f}".format(ac.get("nav_usd", NAV_USD)), nxt))


def do_session():
    et = et_now()
    if et.weekday() >= 5:
        return
    t = et.hour + et.minute / 60.0
    s = state()
    today = date.today().isoformat()
    if 8.4 <= t < 9.42:  # daily
        d = s.get("daily", {})
        if d.get("date") == today:
            return
        td = json.load(open(TODAY))
        tg(daily_text(td))
        d.update(date=today, verdict=(td.get("verdict") or {}).get("level", ""),
                 credits={c["sym"]: float(c["credit"]) for c in (td.get("candidates") or [])})
        s["daily"] = d
        save_state(s)
    elif 9.75 <= t <= 11.0:  # fixup
        f = s.get("fixup", {})
        if f.get("date") == today:
            return
        d = s.get("daily", {})
        prev_v, prev_c = d.get("verdict", ""), d.get("credits", {})
        if not prev_v:
            return  # 冇 daily 基準（例如漏跑）——唔亂發
        gen = subprocess.run(["/usr/bin/python3", "gen_today.py"], cwd=DIR,
                             capture_output=True, text=True, timeout=900)
        if gen.returncode != 0:
            tg("⚠️ 台面修正跑失敗 %s：%s" % (date.today().strftime("%m-%d"), (gen.stderr or "")[-200:]))
            f["date"] = today
            s["fixup"] = f
            save_state(s)
            return
        subprocess.run(["git", "add", "today.json", "gen_today.log"], cwd=DIR, check=False)
        subprocess.run(["git", "commit", "-q", "-m", "today.json fixup %s" % today], cwd=DIR, check=False)
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=DIR, check=False)
        td = json.load(open(TODAY))
        fixup_send(td, prev_v, prev_c)
        f["date"] = today
        s["fixup"] = f
        save_state(s)


def do_nbis():
    et = et_now()
    if et.weekday() >= 5:
        return
    t = et.hour + et.minute / 60.0
    if not (9.5 <= t <= 16.05):
        return
    p = nbis_price()
    if not p:
        return
    s = state()
    today = date.today().isoformat()
    n = s.get("nbis", {})
    if p >= NBIS_TRIG_LO:
        if n.get("trig_date") != today:
            tg("🚨 NBIS 觸發線！現價 %.2f（≥%.0f）\n決策窗開：接受被 call @165（實現 −$7,000）／roll 170@11-20 收 credit\n（拍板⑤）" % (p, NBIS_TRIG_LO))
            n["trig_date"] = today
            n["level"] = "trig"
            save_state(s)
    elif p >= NBIS_WARN:
        if n.get("warn_date") != today:
            tg("⚠️ NBIS 預警：現價 %.2f（入 %.0f–%.0f 預警區）\n觸發線 %.0f–165｜plan A 照企，觸發先揀" % (p, NBIS_WARN, NBIS_TRIG_LO, NBIS_TRIG_LO))
            n["warn_date"] = today
            n["level"] = "warn"
            save_state(s)


def do_weekly():
    today = date.today().isoformat()
    s = state()
    if s.get("weekly") == today:
        return
    tg(weekly_text())
    s["weekly"] = today
    save_state(s)


def do_test():
    td = json.load(open(TODAY))
    ok1 = tg(daily_text(td, tag="｜測試發送") + "\n（以上＝每朝 ET 09:00 格式）")
    ok2 = tg("🧪 NBIS 監察已部署：現價 %s｜預警線 ≥%.0f｜觸發線 ≥%.0f（ET 開市 5 分鐘輪詢）" % (
        nbis_price() or "—", NBIS_WARN, NBIS_TRIG_LO))
    ok3 = tg(weekly_text() + "\n（以上＝週日 20:00 格式）")
    print("test sent: daily=%s nbis=%s weekly=%s" % (ok1, ok2, ok3))


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "session":
        do_session()
    elif mode == "nbis":
        do_nbis()
    elif mode == "weekly":
        do_weekly()
    elif mode == "test":
        do_test()
    else:
        print("usage: tg_engine.py session|nbis|weekly|test")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
