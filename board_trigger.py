#!/usr/bin/python3
# 今日台自動觸發器（11-06 拍板：5分鐘探＋30分鐘兜底＋on-demand 路由三件套之自動腿）
# ET 時段每 5 分鐘平價探（^VIX band／熊門 SPX-SMA200／RSP 全市閘／雷達 level），
# 任何翻轉 → 即刻重跑 gen_today.py＋git push；verdict.level 變 → TG（獨立 dedup，60 分 cooldown，一日上限 6）。
# 30 分鐘兜底：today.json mtime 舊過 30 分鐘（任何 lane 都算——cron/fixup/on-demand 都會更新 mtime）→ 全重跑。
# 探針成本 ~3-6s（3 條 bridge yh_chart＋1 條本地檔）；gen 全鋪 ~10-30s；離 ET 時段直接退出。
import json, os, time, subprocess, datetime, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TODAY = os.path.join(HERE, "today.json")
STATE = os.path.join(HERE, "board_trigger_state.json")
LOG = os.path.join(HERE, "board_trigger.log")
RADAR_JSON = "/Users/leungkathy/quant-dashboard/kill_radar_status.json"
BRIDGE = "http://127.0.0.1:8765"
ENV_FILE = os.path.expanduser("~/.hermes/.env")
ALERT_CHAT = 726483522
TG_COOLDOWN = 3600
TG_MAX_DAY = 6

def log(s):
    try:
        with open(LOG, "a") as f:
            f.write("%s %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), s))
        if os.path.getsize(LOG) > 400000:
            with open(LOG) as f:
                tail = f.readlines()[-500:]
            with open(LOG, "w") as f:
                f.writelines(tail)
    except Exception:
        pass

def bridge_secret():
    v = os.environ.get("FUTU_BRIDGE_SECRET")
    if v:
        return v
    try:
        return open(os.path.expanduser("~/.futu_bridge_secret")).read().strip()
    except Exception:
        return ""

SECRET = bridge_secret()

def yh(sym, rng, itv):
    import urllib.parse
    q = urllib.parse.quote(sym)
    u = "%s/yh_chart?s=%s&r=%s&i=%s&secret=%s" % (BRIDGE, q, rng, itv, SECRET)
    with urllib.request.urlopen(u, timeout=20) as r:
        d = json.loads(r.read().decode())
    res = (d.get("chart") or {}).get("result") or []
    if not res:
        return None, []
    q0 = res[0]["indicators"]["quote"][0]
    pairs = [(t, c) for t, c in zip(res[0].get("timestamp") or [], q0.get("close") or []) if c]
    closes = [c for _, c in pairs]
    return (res[0].get("meta") or {}).get("chartPreviousClose"), closes

def vix_band(v):
    if v is None:
        return None
    if v > 32:
        return ">32"
    if v < 20:
        return "<20"
    return "20-32"

def probe():
    """平價探：vix_band / mkt_state / mkt_gate_bad / radar level。任一攞唔到→None（fail-closed 唔觸發）。"""
    out = {}
    _, cl = yh("^VIX", "5d", "5m")
    out["vix_band"] = vix_band(cl[-1] if cl else None)
    _, spx = yh("^GSPC", "2y", "1d")
    if spx and len(spx) >= 200:
        out["mkt_state"] = "bull" if spx[-1] >= sum(spx[-200:]) / 200.0 else "bear"
    else:
        out["mkt_state"] = None
    _, rsp = yh("RSP", "2y", "1d")
    if rsp and len(rsp) >= 55:
        sma = [sum(rsp[i - 50:i]) / 50.0 for i in range(50, len(rsp) + 1)]
        last5 = rsp[-5:]
        sma_at = sma[-5:]
        out["mkt_gate_bad"] = bool(last5[-1] < sma[-1] or any(l < s for l, s in zip(last5, sma_at)))
    else:
        out["mkt_gate_bad"] = None
    try:
        with open(RADAR_JSON) as f:
            out["radar"] = json.load(f).get("level")
    except Exception:
        out["radar"] = None
    return out

def stored():
    try:
        with open(TODAY) as f:
            d = json.load(f)
        m = d.get("market") or {}
        return {"vix_band": m.get("vix_band"), "mkt_state": m.get("mkt_state"),
                "mkt_gate_bad": m.get("mkt_gate_bad"), "radar": (d.get("radar") or {}).get("level")}, d
    except Exception:
        return {}, {}

def gen_run(reason):
    r = subprocess.run(["/usr/bin/python3", "gen_today.py"], cwd=HERE, timeout=180,
                       capture_output=True, text=True)
    ok = r.returncode == 0
    log("gen(%s) rc=%s %s" % (reason, r.returncode, (r.stderr or "")[-200:] if not ok else ""))
    if ok:
        subprocess.run(["/bin/bash", "-c",
                        'git add today.json && git commit -m "board trigger auto $(date +%%H:%%M)" -q && git push -q origin main'],
                       cwd=HERE, timeout=90, capture_output=True, text=True)
    return ok

def load_token():
    try:
        for ln in open(ENV_FILE):
            if ln.startswith("TELEGRAM_BOT_TOKEN="):
                return ln.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return ""

def tg(text):
    tok = load_token()
    if not tok:
        return False
    req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % tok,
                                 data=json.dumps({"chat_id": ALERT_CHAT, "text": text}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode()).get("ok") is True
    except Exception:
        return False

def et_now():
    from zoneinfo import ZoneInfo
    return datetime.datetime.now(ZoneInfo("America/New_York"))

def main():
    et = et_now()
    if et.weekday() >= 5 or not (datetime.time(9, 30) <= et.time() <= datetime.time(16, 5)):
        return   # 離 ET 時段靜默退出（csp_lights 同款守衛）
    # 單飛：lockfile 防同刻重入（launchd 重疊觸發）
    lockf = os.path.join(HERE, ".board_trigger.lock")
    if os.path.exists(lockf) and time.time() - os.path.getmtime(lockf) < 600:
        return
    try:
        open(lockf, "w").write(str(time.time()))
    except Exception:
        return
    try:
        cur, old_doc = stored()
        if not cur:
            log("stored read fail — skip")
            return
        flips = []
        p = probe()
        for k in ("vix_band", "mkt_state", "mkt_gate_bad", "radar"):
            if p.get(k) is not None and cur.get(k) is not None and p[k] != cur[k]:
                flips.append("%s %s→%s" % (k, cur[k], p[k]))
        age = time.time() - os.path.getmtime(TODAY)
        fallback = age >= 1800
        if not flips and not fallback:
            return
        reason = "flip" if flips else "fallback30m"
        log("觸發 %s：%s" % (reason, "；".join(flips) if flips else "mtime %.0fmin" % (age / 60)))
        old_level = (old_doc.get("verdict") or {}).get("level")
        if gen_run(reason):
            _, new_doc = stored()
            new_level = (new_doc.get("verdict") or {}).get("level")
            # TG dedup：verdict.level 變＋cooldown＋日上限
            st = {}
            try:
                with open(STATE) as f:
                    st = json.load(f)
            except Exception:
                pass
            now = time.time()
            sent_today = [t for t in st.get("tg_ts", []) if now - t < 86400]
            cooldown_ok = now - st.get("last_tg", 0) >= TG_COOLDOWN
            if new_level and old_level and new_level != old_level and cooldown_ok and len(sent_today) < TG_MAX_DAY:
                m = new_doc.get("market") or {}
                tg("🎯 [今日台觸發] verdict %s → %s\nVIX %s band %s｜廣度 %s｜雷達 %s｜mkt %s\n%s" % (
                    old_level, new_level, m.get("vix"), m.get("vix_band"), m.get("breadth"),
                    (new_doc.get("radar") or {}).get("level"), m.get("mkt_state"),
                    "；".join(flips) if flips else "30 分兜底"))
                st["last_tg"] = now
                st["tg_ts"] = sent_today + [now]
                log("TG：%s→%s" % (old_level, new_level))
            st["last_gen"] = now
            with open(STATE, "w") as f:
                json.dump(st, f)
    except Exception as e:
        log("fail %s" % e)
    finally:
        try:
            os.remove(lockf)
        except Exception:
            pass

if __name__ == "__main__":
    main()
