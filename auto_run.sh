#!/bin/bash
# 📒 統一台自動產生器 wrapper（launchd 07:30+21:35 HKT 交易日）：
# gen_today.py → today.json → git commit+push（Pages 即時更新）
R=/Users/leungkathy/shortput_research/unified_dashboard_20261102/unified-strategy
DOW=$(date +%u)
[ "$DOW" -ge 6 ] && exit 0
cd "$R" || exit 1
/usr/bin/python3 gen_today.py >> gen_today.log 2>&1
# log 防肥
[ -f gen_today.log ] && [ "$(wc -c < gen_today.log)" -gt 400000 ] && tail -c 100000 gen_today.log > gen_today.tmp && mv gen_today.tmp gen_today.log
git add today.json gen_today.log 2>/dev/null
git commit -q -m "today.json auto $(date '+%Y-%m-%d %H:%M')" 2>/dev/null
git push -q origin main 2>/dev/null
