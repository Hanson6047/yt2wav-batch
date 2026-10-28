#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
音訊整理工具（改編自德玉同學的 yt2wav - time.bat）

選單：
    1. 檢查 / 整理資料夾
        掃描資料夾底下所有音訊 → 依目標（格式、時長…）列出「預計更改清單」→ 確認後才執行
        過長：修剪（保留中間 / 開頭）    格式不對：轉檔
        太短：移到別的資料夾 / 刪除 / 不處理
        結果：另存到新資料夾（原檔不動）/ 覆蓋原檔
    2. 下載音樂
        單筆：貼連結，自己指定第幾秒到第幾秒
        批量：讀 TXT（一行一個連結），開始前問一次「從第幾秒開始、抓幾秒」，全部統一
        只下載指定區段，下載後用 ffmpeg 精準修成指定長度
        同一部影片只下載一次（yt-dlp --download-archive 記錄影片 ID）

設定存在同資料夾的 settings.json（選單裡選擇儲存才會寫入），紀錄存在 logs/。
需要：python -m yt_dlp、ffmpeg、ffprobe（都要在 PATH 裡）
畫面仿 Claude Code 終端機風格（彩色、圖示）；不想要顏色可設環境變數 NO_COLOR=1

用法：雙擊「音訊工具.bat」，或 python D:/D_drive_BACKUP/Code/audio_tool/audio_tool.py
"""
import csv
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(HERE, "settings.json")
LOG_DIR = os.path.join(HERE, "logs")
AUDIO_EXT = (".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac", ".opus", ".webm")

DEFAULTS = {
    "check": {
        "last_folder": "",
        "target_ext": "wav",
        "target_seconds": 30.0,
        "tolerance": 0.05,          # 誤差容許（秒），30.04 秒算合格
        "trim_mode": "middle",      # middle = 保留中間；start = 保留開頭
        "save_mode": "overwrite",   # overwrite = 直接修改原檔；new_folder = 另存到 <資料夾>_整理後
        "short_action": "move",     # move = 移到 <資料夾>_太短；delete = 刪除；keep = 不處理
        "target_sr": 0,             # 0 = 不改取樣率
        "target_channels": 0,       # 0 = 不改聲道
    },
    "download": {
        "save_dir": "",
        "last_txt": "",
        "seconds": 30.0,            # 批量：每段長度（也是單筆不填結束時間時的長度）
        "batch_start": "0:30",      # 批量：所有連結統一從這裡開始
    },
}

LABEL = {
    "trim_mode":    {"middle": "保留中間", "start": "保留開頭"},
    "save_mode":    {"new_folder": "另存到新資料夾（原檔不動）", "overwrite": "覆蓋原檔"},
    "short_action": {"move": "移到「_太短」資料夾", "delete": "刪除", "keep": "不處理，只列出"},
}


# ================= 介面（仿 Claude Code 終端機風格） =================
if os.name == "nt":
    os.system("")                   # 開啟 Windows 主控台的 ANSI 色碼支援
COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _style(code):
    return (lambda t: f"\x1b[{code}m{t}\x1b[0m") if COLOR else str


accent = _style("38;2;215;119;87")  # Claude 橘
dim    = _style("90")
bold   = _style("1")
green  = _style("32")
yellow = _style("33")
red    = _style("31")
SPIN   = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def width(s):
    """終端機顯示寬度：中文、emoji 佔 2 格"""
    s = re.sub(r"\x1b\[[0-9;]*m", "", str(s))
    return sum(0 if unicodedata.combining(ch) or ch == "\ufe0f" else
               2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def panel(title, rows):
    """圓角框（右邊不封口，中文字寬不準也不會歪）。rows：字串，或 (標籤, 內容)"""
    kw = max((width(r[0]) for r in rows if isinstance(r, tuple)), default=0)
    print("\n" + accent("╭─ ") + bold(title) + " " + accent("─" * max(4, 34 - width(title))))
    for r in rows:
        if isinstance(r, tuple):
            r = dim(r[0] + " " * (kw - width(r[0]))) + "   " + str(r[1])
        print(accent("│") + "  " + r)
    print(accent("╰─"))


def step(msg):
    print(f"\n{accent('●')} {msg}")


def sub(msg):
    print(f"  {dim('└')} {msg}")


def tree(lines):
    for i, x in enumerate(lines):
        print(f"  {dim('└' if i == len(lines) - 1 else '├')} {x}")


def ok(msg):
    print(f"  {green('✔')} {msg}")


def warn(msg):
    print(f"  {yellow('▲')} {msg}")


def err(msg):
    print(f"  {red('✘')} {msg}")


def progress(i, n, text=""):
    w = 24
    f = w * i // n if n else w
    print(f"\r  {accent('━' * f)}{dim('━' * (w - f))} {i}/{n} {text}", end="", flush=True)


class spinner:
    """長時間工作時顯示轉圈圈＋經過秒數"""
    def __init__(self, text):
        self.text = text

    def __enter__(self):
        self.stop = threading.Event()
        self.t = threading.Thread(target=self._run, daemon=True)
        if COLOR:
            self.t.start()
        return self

    def _run(self):
        t0, i = time.time(), 0
        while not self.stop.wait(0.1):
            print(f"\r  {accent(SPIN[i % len(SPIN)])} {self.text} {dim(f'({time.time() - t0:.0f}s)')}",
                  end="", flush=True)
            i += 1

    def __exit__(self, *_):
        self.stop.set()
        if COLOR:
            self.t.join()
            print("\r\x1b[2K", end="", flush=True)


# ================= 共用 =================
def load_settings():
    s = json.loads(json.dumps(DEFAULTS))
    if os.path.exists(SETTINGS_PATH):
        try:
            saved = json.load(open(SETTINGS_PATH, encoding="utf-8"))
            for sec in s:
                s[sec].update(saved.get(sec, {}))
        except Exception as e:
            warn(f"settings.json 讀取失敗，改用預設值：{e}")
    return s


def save_settings(s):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
    ok(f"設定已儲存 {dim(SETTINGS_PATH)}")


def ask(prompt, default=""):
    """輸入；直接按 Enter 用預設值。拖曳資料夾進視窗會帶引號，順便去掉"""
    hint = " " + dim(f"[{default}]") if default != "" else ""
    v = input(f"{accent('›')} {prompt}{hint} ").strip().strip('"').strip("'")
    return v if v else str(default)


def yes(prompt, default="n"):
    return ask(f"{prompt} {dim('(y/n)')}", default).lower().startswith("y")


def choose(prompt, options, current):
    """options = {值: 說明}，回傳選到的值"""
    keys = list(options)
    print(f"\n{accent('?')} {bold(prompt)}")
    for i, k in enumerate(keys, 1):
        mark = accent("●") if k == current else dim("○")
        print(f"    {mark} {accent(i)}. {options[k]}{dim('  ← 目前') if k == current else ''}")
    v = ask("選擇", keys.index(current) + 1 if current in keys else 1)
    return keys[int(v) - 1] if v.isdigit() and 1 <= int(v) <= len(keys) else current


def menu(title, items):
    """items = [(按鍵, 圖示, 說明)]"""
    panel(title, [f"{accent(k)}  {icon}  {text}" for k, icon, text in items])


def parse_time(t):
    """'1:05' / '65' / '1:02:03' → 秒"""
    sec = 0.0
    for part in str(t).strip().split(":"):
        sec = sec * 60 + float(part)
    return sec


def fmt_time(sec):
    m, s = divmod(sec, 60)
    return f"{int(m)}:{s:05.2f}"


def probe(path):
    """用 ffprobe 讀時長、取樣率、聲道；讀不到回傳 None"""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                        "-show_entries", "format=duration:stream=sample_rate,channels",
                        "-of", "json", path], capture_output=True, text=True, encoding="utf-8")
    try:
        j = json.loads(r.stdout)
        st = j["streams"][0]
        return {"dur": float(j["format"]["duration"]), "sr": int(st["sample_rate"]), "ch": int(st["channels"])}
    except Exception:
        return None


def write_log(kind, rows, header):
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, f"{kind}_{datetime.datetime.now():%Y%m%d_%H%M%S}.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    sub(f"📄 紀錄已存 {dim(path)}")


# ================= 1. 檢查 / 整理 =================
def show_check_settings(c):
    panel("🎯 目前的目標設定", [
        ("格式", f".{c['target_ext']}"),
        ("時長", f"{c['target_seconds']:g} 秒 " + dim(f"（誤差 ±{c['tolerance']} 秒內算合格）")),
        ("過長", f"修剪，{LABEL['trim_mode'][c['trim_mode']]}"),
        ("太短", LABEL["short_action"][c["short_action"]]),
        ("結果", LABEL["save_mode"][c["save_mode"]]),
        ("取樣率", c["target_sr"] or dim("不改")),
        ("聲道", c["target_channels"] or dim("不改")),
    ])


def edit_check_settings(s):
    c = s["check"]
    c["target_ext"]      = ask("期待的檔案格式（副檔名）", c["target_ext"]).lstrip(".").lower()
    c["target_seconds"]  = float(ask("期待的時長（秒）", c["target_seconds"]))
    c["trim_mode"]       = choose("過長要怎麼修剪？", LABEL["trim_mode"], c["trim_mode"])
    c["short_action"]    = choose("比目標短的檔案要？", LABEL["short_action"], c["short_action"])
    c["save_mode"]       = choose("修改後的檔案要？", LABEL["save_mode"], c["save_mode"])
    c["target_sr"]       = int(ask("統一取樣率（Hz，0 = 不改）", c["target_sr"]))
    c["target_channels"] = int(ask("統一聲道數（1 或 2，0 = 不改）", c["target_channels"]))
    if yes("要儲存這些設定，下次沿用嗎？", "y"):
        save_settings(s)


def plan_file(path, c):
    """決定一個檔案要做什麼。回傳 (動作, 說明, 資訊)"""
    info = probe(path)
    if info is None:
        return "error", "無法讀取（檔案可能損壞）", None
    target, tol = c["target_seconds"], c["tolerance"]
    if info["dur"] < target - tol:
        return "short", f"太短 {info['dur']:.2f} 秒 → {LABEL['short_action'][c['short_action']]}", info
    fixes = []
    if not path.lower().endswith("." + c["target_ext"]):
        fixes.append(f"轉檔 {os.path.splitext(path)[1]} → .{c['target_ext']}")
    if info["dur"] > target + tol:
        fixes.append(f"修剪 {info['dur']:.2f} → {target:g} 秒（{LABEL['trim_mode'][c['trim_mode']]}）")
    if c["target_sr"] and info["sr"] != c["target_sr"]:
        fixes.append(f"取樣率 {info['sr']} → {c['target_sr']}")
    if c["target_channels"] and info["ch"] != c["target_channels"]:
        fixes.append(f"聲道 {info['ch']} → {c['target_channels']}")
    if fixes:
        return "fix", "；".join(fixes), info
    return "ok", "合格", info


def ffmpeg_fix(src, dst, info, c):
    """轉檔 + 修剪 + 取樣率 / 聲道。輸出 16-bit PCM"""
    target = c["target_seconds"]
    start = 0.0
    if info["dur"] > target + c["tolerance"] and c["trim_mode"] == "middle":
        start = (info["dur"] - target) / 2
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", src, "-t", f"{target:.3f}"]
    if c["target_sr"]:
        cmd += ["-ar", str(c["target_sr"])]
    if c["target_channels"]:
        cmd += ["-ac", str(c["target_channels"])]
    if c["target_ext"] == "wav":
        cmd += ["-c:a", "pcm_s16le"]
    tmp = dst + ".tmp." + c["target_ext"]
    r = subprocess.run(cmd + [tmp], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise RuntimeError(r.stderr.strip()[-300:])
    os.replace(tmp, dst)


def check_folder(s):
    c = s["check"]
    show_check_settings(c)
    if yes("要修改目標設定嗎？"):
        edit_check_settings(s)
        show_check_settings(c)

    print()
    root = os.path.normpath(ask("要檢查的資料夾（可直接把資料夾拖進來）", c["last_folder"]))
    if not os.path.isdir(root):
        err("找不到這個資料夾")
        return
    c["last_folder"] = root
    out_root   = root + "_整理後"
    short_root = root + "_太短"

    files = sorted(os.path.join(dp, f) for dp, _, fs in os.walk(root) for f in fs
                   if f.lower().endswith(AUDIO_EXT))
    step(f"掃描 {bold(len(files))} 個音訊檔 {dim(root)}")
    plans = []
    for i, p in enumerate(files, 1):
        plans.append((p,) + plan_file(p, c))
        progress(i, len(files))
    print()

    # ---- 預計更改清單 ----
    count = {k: sum(1 for x in plans if x[1] == k) for k in ("ok", "fix", "short", "error")}
    step(bold("預計更改清單") + "  " + "  ".join([
        green(f"✔ 合格 {count['ok']}"), yellow(f"✎ 要修改 {count['fix']}"),
        red(f"↓ 太短 {count['short']}"), dim(f"✘ 讀不到 {count['error']}")]))
    tag = {"fix": yellow("✎ 修改"), "short": red("↓ 太短"), "error": dim("✘ 錯誤")}
    tree([f"{tag[act]}  {os.path.relpath(p, root)}  {dim('→ ' + note)}"
          for p, act, note, info in plans if act != "ok"])
    if c["save_mode"] == "new_folder":
        sub(f"另存位置：{out_root} {dim('（合格的檔案也會複製過去，整理後的資料夾就是完整資料集）')}")
    if c["short_action"] == "delete" and count["short"]:
        warn(f"會刪除 {count['short']} 個太短的檔案，刪除後救不回來")
    if count["fix"] + count["short"] == 0 and c["save_mode"] == "overwrite":
        ok("全部合格，不用處理")
        return
    print()
    if not yes("確定照這個清單執行嗎？"):
        sub("已取消，沒有改動任何檔案")
        return

    # ---- 執行 ----
    step("執行中")
    log = []
    for p, act, note, info in plans:
        rel = os.path.relpath(p, root)
        new_rel = os.path.splitext(rel)[0] + "." + c["target_ext"]
        failed = False
        try:
            if act == "error":
                result = "略過"
            elif act == "short":
                if c["short_action"] == "move":
                    dst = os.path.join(short_root, rel)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.move(p, dst)
                    result = f"已移到 {dst}"
                elif c["short_action"] == "delete":
                    os.remove(p)
                    result = "已刪除"
                else:
                    result = "未處理"
            elif c["save_mode"] == "new_folder":
                dst = os.path.join(out_root, new_rel if act == "fix" else rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if act == "fix":
                    ffmpeg_fix(p, dst, info, c)
                    result = f"已修改 → {dst}"
                else:
                    shutil.copy2(p, dst)
                    result = "合格，已複製"
            else:  # overwrite
                if act == "fix":
                    dst = os.path.join(root, new_rel)
                    if dst != p and os.path.exists(dst):          # 例如 a.mp3 → a.wav 但 a.wav 已存在
                        dst = os.path.splitext(dst)[0] + "_轉檔." + c["target_ext"]
                    ffmpeg_fix(p, dst, info, c)
                    if dst != p:
                        os.remove(p)                            # 轉檔後刪掉舊格式的原檔
                    result = f"已覆蓋 → {os.path.relpath(dst, root)}"
                else:
                    result = "合格"
        except Exception as e:
            result = f"失敗：{e}"
            failed = True
        log.append([rel, act, note, f"{info['dur']:.2f}" if info else "", info["sr"] if info else "",
                    info["ch"] if info else "", result])
        if failed:
            err(f"{rel}：{result}")
        elif act != "ok":
            ok(f"{rel}  {dim(result)}")
    step(green("完成"))
    write_log("整理", log, ["檔案", "類型", "預計動作", "原時長", "原取樣率", "原聲道", "結果"])
    print()
    save_settings(s) if yes("要記住這次的資料夾嗎？", "y") else None


# ================= 2. 下載 =================
def sanitize(name):
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip().rstrip(".")[:120] or "untitled"


def download_one(url, start_sec, secs, save_dir):
    """下載 [start, start+secs] → 修成剛好 secs 秒的 wav。回傳 (狀態, 檔案路徑或訊息)"""
    tmp_dir = os.path.join(save_dir, "_tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    archive = os.path.join(save_dir, "_已下載影片ID.txt")
    cmd = [sys.executable, "-m", "yt_dlp", "--no-playlist",
           "-x", "--audio-format", "wav",
           "--download-sections", f"*{start_sec}-{start_sec + secs}",
           "--force-keyframes-at-cuts",
           "--download-archive", archive,
           "-o", os.path.join(tmp_dir, "%(title).100s.%(ext)s"),
           "--print", "after_move:filepath", "--no-simulate", "--no-warnings",
           url]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    lines = [x for x in r.stdout.splitlines() if x.strip()]
    if r.returncode != 0:
        msg = (r.stderr.strip().splitlines() or ["未知錯誤"])[-1]
        if "403" in r.stderr or "ffmpeg exited" in msg:
            msg += "  → 多半是 YouTube 改了機制，先執行：python -m pip install -U \"yt-dlp[default]\""
        elif "not available" in msg:
            msg += ("  → 影片若在瀏覽器能播放，多半是缺 deno 或解題腳本："
                    "winget install DenoLand.Deno、python -m pip install -U \"yt-dlp[default]\"")
        return "失敗", msg
    if not lines:
        return "重複略過", "這部影片之前下載過（同一部影片只取一段）"
    tmp = lines[-1].strip()
    dst = os.path.join(save_dir, sanitize(os.path.splitext(os.path.basename(tmp))[0]) + ".wav")
    if os.path.exists(dst):
        dst = os.path.splitext(dst)[0] + f"_{int(start_sec)}s.wav"
    # 精準修成 N 秒：切點不準多出來的截掉，不足的補靜音
    f = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", tmp, "-af", f"apad=whole_dur={secs}",
                        "-t", f"{secs}", "-c:a", "pcm_s16le", dst],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    os.remove(tmp)
    if f.returncode != 0:
        return "失敗", f.stderr.strip()[-200:]
    return "成功", dst


def pick_save_dir(d):
    d["save_dir"] = os.path.normpath(ask("儲存位置（可直接把資料夾拖進來）", d["save_dir"] or os.getcwd()))
    os.makedirs(d["save_dir"], exist_ok=True)


def download_menu(s):
    d = s["download"]
    menu("📥 下載音樂", [
        ("1", "🔗", "單筆下載 " + dim("貼連結，自己指定第幾秒到第幾秒")),
        ("2", "📃", "批量下載 " + dim(f"讀 TXT，上次是從 {d['batch_start']} 開始抓 {d['seconds']:g} 秒")),
        ("0", "↩ ", "返回"),
    ])
    sub(f"儲存位置：{d['save_dir'] or dim('（尚未設定）')}")
    print()
    m = ask("選擇", "1")
    if m not in ("1", "2"):
        return

    pick_save_dir(d)
    jobs = []                                   # (連結, 開始秒, 長度秒)
    try:
        if m == "1":
            url = ask("貼上連結")
            st = parse_time(ask("從第幾秒開始（例：35、1:15）", "0"))
            ed = parse_time(ask("到第幾秒結束（直接 Enter = 開始後 "
                                f"{d['seconds']:g} 秒）", fmt_time(st + float(d["seconds"]))))
            if ed <= st:
                err("結束時間要比開始時間晚")
                return
            jobs.append((url, st, ed - st))
        else:
            txt = ask("TXT 檔路徑（可直接拖進來）", d["last_txt"])
            if not os.path.isfile(txt):
                err("找不到這個檔案")
                return
            d["last_txt"] = txt
            d["batch_start"] = ask("全部從第幾秒開始（例：30、1:15）", d["batch_start"])
            d["seconds"] = float(ask("每段抓幾秒", d["seconds"]))
            st = parse_time(d["batch_start"])
            urls = []
            for line in open(txt, encoding="utf-8-sig"):
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.append(line.split()[0])    # 一行一個連結；後面多寫的字不使用
            jobs = [(u, st, float(d["seconds"])) for u in urls]
            step(f"讀到 {bold(len(jobs))} 個連結，全部從 {fmt_time(st)} 開始抓 {d['seconds']:g} 秒")
    except ValueError:
        err("時間格式看不懂，請用 35、1:15 或 1:02:03 這種寫法")
        return

    log, ok_n = [], 0
    for i, (url, st, secs) in enumerate(jobs, 1):
        step(f"{dim(f'[{i}/{len(jobs)}]')} {url}")
        with spinner(f"下載 {fmt_time(st)} ～ {fmt_time(st + secs)}（{secs:g} 秒）"):
            status, msg = download_one(url, st, secs, d["save_dir"])
        ok_n += status == "成功"
        if status == "成功":
            sub(f"{green('✔')} {os.path.basename(msg)}  {dim(f'{fmt_time(st)} ～ {fmt_time(st + secs)}')}")
        elif status == "重複略過":
            sub(f"{dim('⏭ ' + msg)}")
        else:
            sub(f"{red('✘ 失敗')} {msg}")
        log.append([url, fmt_time(st), fmt_time(st + secs), status, msg])
    shutil.rmtree(os.path.join(d["save_dir"], "_tmp"), ignore_errors=True)
    step(f"完成：{green(f'成功 {ok_n}')} / 共 {len(jobs)}")
    write_log("下載", log, ["連結", "開始", "結束", "狀態", "檔案 / 訊息"])
    print()
    if yes("要記住儲存位置、TXT 路徑和批量時間設定嗎？", "y"):
        save_settings(s)


# ================= 主選單 =================
def main():
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            warn(f"找不到 {tool}，請先安裝 ffmpeg 並加入 PATH")
    if not shutil.which("deno"):
        warn("找不到 deno：YouTube 有些影片會下載失敗（not available）。"
             "安裝：winget install DenoLand.Deno，裝完重開視窗")
    s = load_settings()
    while True:
        menu(accent("✻") + " " + bold("音訊整理工具"), [
            ("1", "🔍", "檢查 / 整理資料夾 " + dim("時長、格式")),
            ("2", "📥", "下載音樂 " + dim("YouTube 指定區段 → wav")),
            ("0", "👋", "離開"),
        ])
        print()
        m = ask("選擇", "1")
        try:
            if m == "1":
                check_folder(s)
            elif m == "2":
                download_menu(s)
            elif m == "0":
                break
        except KeyboardInterrupt:
            print()
            warn("已中斷，回到主選單")


if __name__ == "__main__":
    main()
