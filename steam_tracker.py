# -*- coding: utf-8 -*-
"""
Steam 每日在线时长追踪器（多账号 + 系统托盘 + 精致 UI）
"""

import json
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.parse
from datetime import datetime, date, timedelta
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox

from PIL import Image, ImageDraw
import pystray

# ---------- 路径与常量 ----------
if getattr(sys, "frozen", False):
    # PyInstaller 打包后：配置/数据存放在 exe 同级目录
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
DATA_FILE = APP_DIR / "tracker_data.json"
CONFIG_FILE = APP_DIR / "config.json"
POLL_INTERVAL = 30
REFRESH_UI_MS = 1000
STEAMID64_BASE = 76561197960265728

# 配色（深色高级感）
BG       = "#0f1117"
CARD     = "#1a1d27"
CARD_HI  = "#232734"
BORDER   = "#2a2e3d"
TEXT     = "#e4e6eb"
MUTED    = "#7a7f8e"
ACCENT   = "#6e8efb"
ACCENT2  = "#a78bfa"
GREEN    = "#34d399"
RED      = "#f87171"
AMBER    = "#fbbf24"

FONT     = ("Microsoft YaHei UI", 10)
FONT_B   = ("Microsoft YaHei UI", 10, "bold")
FONT_S   = ("Microsoft YaHei UI", 9)
FONT_H   = ("Microsoft YaHei UI", 11, "bold")
FONT_DISPLAY = ("Microsoft YaHei UI Light", 30, "bold")
FONT_CARD_TITLE = ("Microsoft YaHei UI", 9)
FONT_CARD_VAL = ("Microsoft YaHei UI", 18, "bold")


# ---------- Steam 路径探测 ----------
def find_steam_path():
    import winreg
    for root_path in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for sub, val_name in [(r"Software\Valve\Steam", "SteamPath"),
                              (r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")]:
            try:
                key = winreg.OpenKey(root_path, sub)
                val, _ = winreg.QueryValueEx(key, val_name)
                winreg.CloseKey(key)
                if val and Path(val).exists():
                    return Path(val)
            except OSError:
                pass
    for p in [r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam",
              r"D:\Steam", r"D:\Program Files (x86)\Steam"]:
        if Path(p).exists():
            return Path(p)
    return None


def parse_vdf(text):
    pos = 0
    n = len(text)

    def skip_ws():
        nonlocal pos
        while pos < n and text[pos] in " \t\r\n":
            if text[pos:pos+2] == "//":
                while pos < n and text[pos] != "\n":
                    pos += 1
            else:
                pos += 1

    def parse_str():
        nonlocal pos
        skip_ws()
        if pos >= n or text[pos] != '"':
            return None
        pos += 1
        buf = []
        while pos < n and text[pos] != '"':
            if text[pos] == '\\' and pos + 1 < n:
                buf.append(text[pos+1]); pos += 2
            else:
                buf.append(text[pos]); pos += 1
        if pos < n and text[pos] == '"':
            pos += 1
        return "".join(buf)

    def parse_block():
        nonlocal pos
        obj = {}
        while True:
            skip_ws()
            if pos >= n or text[pos] == '}':
                return obj
            k = parse_str()
            if k is None:
                return obj
            skip_ws()
            if pos < n and text[pos] == '{':
                pos += 1
                v = parse_block()
                skip_ws()
                if pos < n and text[pos] == '}':
                    pos += 1
            else:
                v = parse_str()
            obj[k] = v
    return parse_block()


def scan_accounts(steam_path):
    ud = steam_path / "userdata"
    result = {}
    if not ud.exists():
        return result
    folders = [d for d in ud.iterdir() if d.is_dir() and d.name.isdigit()]
    folders.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    for d in folders:
        lc = d / "config" / "localconfig.vdf"
        name = d.name
        if lc.exists():
            try:
                txt = lc.read_text(encoding="utf-8", errors="ignore")
                data = parse_vdf(txt)
                pname = (data.get("UserLocalConfigStore", {})
                            .get("friends", {}).get("PersonaName"))
                if pname:
                    name = pname
            except Exception:
                pass
        result[d.name] = name
    return result


def active_account_id(steam_path):
    ud = steam_path / "userdata"
    best, best_mt = None, -1
    if not ud.exists():
        return None
    for d in ud.iterdir():
        if not d.is_dir() or not d.name.isdigit():
            continue
        lc = d / "config" / "localconfig.vdf"
        if lc.exists():
            mt = lc.stat().st_mtime
            if mt > best_mt:
                best_mt = mt; best = d.name
    return best


def is_steam_running():
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq steam.exe", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=10, creationflags=0x08000000)
        return "steam.exe" in out.stdout.lower()
    except Exception:
        return False


def load_game_playtime(steam_path, userdata_dir):
    lc = userdata_dir / "config" / "localconfig.vdf"
    games = {}
    if lc.exists():
        try:
            text = lc.read_text(encoding="utf-8", errors="ignore")
            data = parse_vdf(text)
            apps = (data.get("UserLocalConfigStore", {})
                       .get("Software", {}).get("Valve", {})
                       .get("Steam", {}).get("apps", {}))
            for appid, info in apps.items():
                if not isinstance(info, dict):
                    continue
                pt = info.get("Playtime")
                if pt is None:
                    continue
                try:
                    minutes = int(pt)
                except (ValueError, TypeError):
                    continue
                if minutes <= 0:
                    continue
                last = info.get("LastPlayed")
                try: last_ts = int(last) if last else 0
                except: last_ts = 0
                pt2w = info.get("Playtime2wks")
                try: m2 = int(pt2w) if pt2w else 0
                except: m2 = 0
                games[appid] = {"minutes": minutes, "last_played": last_ts, "minutes_2w": m2}
        except Exception as e:
            print(f"parse err: {e}", file=sys.stderr)
    manifest_dir = steam_path / "steamapps"
    named = {}
    if manifest_dir.exists():
        for mf in manifest_dir.glob("appmanifest_*.acf"):
            try:
                txt = mf.read_text(encoding="utf-8", errors="ignore")
                m = parse_vdf(txt)
                inner = m.get("AppState", m) if isinstance(m, dict) else {}
                appid = str(inner.get("appid", ""))
                name = inner.get("name", "")
                if appid in games and name:
                    games[appid]["name"] = name
                    named[appid] = games[appid]
            except Exception:
                pass
    return named


# ---------- 数据 ----------
def load_data():
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                if "accounts" not in d:
                    d = {"accounts": {"_legacy": {"daily": d.get("daily", {})}}}
                return d
        except Exception:
            pass
    return {"accounts": {}}


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------- 配置（API Key） ----------
def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"api_key": ""}


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def sid32_to_64(sid32_str):
    """userdata 目录名（SteamID32）→ SteamID64"""
    try:
        return str(STEAMID64_BASE + int(sid32_str))
    except (ValueError, TypeError):
        return None


# ---------- Steam Web API ----------
def _api_get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "SteamTracker/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def api_fetch_owned_games(api_key, steamid64):
    """返回 {appid_str: {name, minutes, minutes_2w}}"""
    qs = urllib.parse.urlencode({
        "key": api_key,
        "steamid": steamid64,
        "include_appinfo": 1,
        "include_played_free_games": 1,
        "format": "json",
    })
    url = f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/?{qs}"
    data = _api_get(url)
    out = {}
    for g in data.get("response", {}).get("games", []):
        appid = str(g.get("appid", ""))
        out[appid] = {
            "name": g.get("name", f"App {appid}"),
            "minutes": g.get("playtime_forever", 0),
            "minutes_2w": g.get("playtime_2weeks", 0),
        }
    return out


def api_fetch_player_summary(api_key, steamid64):
    qs = urllib.parse.urlencode({"key": api_key, "steamids": steamid64})
    url = f"https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/?{qs}"
    data = _api_get(url)
    players = data.get("response", {}).get("players", {}).get("players", [])
    return players[0] if players else None


def fmt_dur(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h: return f"{h}h {m:02d}m"
    if m: return f"{m}m {s:02d}s"
    return f"{s}s"


def fmt_h(minutes):
    return f"{minutes/60:.1f}"


def today_str():
    return date.today().isoformat()


# ---------- 圆角卡片 Canvas ----------
def round_rect(canvas, x1, y1, x2, y2, r=10, **kw):
    points = [
        x1+r, y1, x2-r, y1, x2, y1, x2, y1+r, x2, y2-r, x2, y2,
        x2-r, y2, x1+r, y2, x1, y2, x1, y2-r, x1, y1+r, x1, y1
    ]
    return canvas.create_polygon(points, smooth=True, **kw)


# ---------- 主应用 ----------
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Steam 在线时长")
        self.root.geometry("1020x720")
        self.root.minsize(900, 640)
        self.root.configure(bg=BG)

        self.data = load_data()
        self.config = load_config()
        self.steam_path = find_steam_path()
        self.accounts = scan_accounts(self.steam_path) if self.steam_path else {}
        self.active_id = active_account_id(self.steam_path) if self.steam_path else None
        self.selected_id = self.active_id if self.active_id in self.accounts else (
            list(self.accounts.keys())[0] if self.accounts else None)
        self.games = {}
        self._stop = threading.Event()
        self._tray = None
        self._ask_on_close = True  # 点 X 是否询问

        self._build_style()
        self._build_ui()
        self._on_account_change()
        self._build_tray()
        self._start_monitor()
        self._tick_ui()

        self.root.protocol("WM_DELETE_WINDOW", self._on_x_clicked)
        # 监听最小化（点 _ 按钮）→ 收到 Unmap 时隐藏到托盘
        self.root.bind("<Unmap>", self._on_unmap)

    def _build_style(self):
        s = ttk.Style()
        try: s.theme_use("clam")
        except: pass
        s.configure("TNotebook", background=BG, borderwidth=0)
        s.configure("TNotebook.Tab", background=BG, foreground=MUTED,
                     padding=(18, 8), font=FONT_B, borderwidth=0)
        s.map("TNotebook.Tab", background=[("selected", CARD)],
              foreground=[("selected", TEXT)])
        s.configure("TFrame", background=BG)
        s.configure("TLabel", background=BG, foreground=TEXT, font=FONT)
        s.configure("TCombobox", fieldbackground=CARD, background=CARD,
                     foreground=TEXT, arrowcolor=MUTED, borderwidth=0, padding=4)
        s.map("TCombobox", fieldbackground=[("readonly", CARD)],
              foreground=[("readonly", TEXT)])
        s.configure("TButton", background=CARD_HI, foreground=TEXT,
                     borderwidth=0, padding=(12, 6), font=FONT, borderradius=6)
        s.map("TButton", background=[("active", BORDER)])
        s.configure("Treeview", background=CARD, fieldbackground=CARD,
                     foreground=TEXT, font=FONT_S, rowheight=26, borderwidth=0)
        s.configure("Treeview.Heading", background=CARD, foreground=MUTED,
                     font=FONT_B, borderwidth=0)
        s.layout("Treeview", [('Treeview.treearea', {'sticky': 'nswe'})])

    # ---------- UI ----------
    def _build_ui(self):
        # 顶部栏
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=24, pady=(18, 8))
        tk.Label(header, text="⏱  Steam 在线时长", bg=BG, fg=TEXT,
                 font=("Microsoft YaHei UI", 16, "bold")).pack(side="left")
        right = tk.Frame(header, bg=BG)
        right.pack(side="right")
        tk.Label(right, text="账号", bg=BG, fg=MUTED, font=FONT_S).pack(side="left", padx=(0, 6))
        self.acct_var = tk.StringVar()
        self.acct_box = ttk.Combobox(right, textvariable=self.acct_var, state="readonly", width=28)
        self.acct_box.pack(side="left")
        self._refresh_acct_box()
        self.acct_box.bind("<<ComboboxSelected>>", lambda e: self._on_account_change())
        ttk.Button(right, text="设置", command=self._open_settings).pack(side="left", padx=(8, 0))

        # Notebook
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=20, pady=(8, 4))
        self.tab_daily = ttk.Frame(nb)
        self.tab_games = ttk.Frame(nb)
        nb.add(self.tab_daily, text="  概览  ")
        nb.add(self.tab_games, text="  游戏时长  ")
        self._build_daily()
        self._build_games()

        # 状态栏
        self.status = tk.Label(self.root, text="", bg=BG, fg=MUTED, font=FONT_S, anchor="w")
        self.status.pack(fill="x", padx=24, pady=(2, 10))

    def _refresh_acct_box(self):
        self._acct_map = {}
        labels = []
        for sid, name in self.accounts.items():
            lbl = f"{name}"
            labels.append(lbl)
            self._acct_map[lbl] = sid
        self.acct_box["values"] = labels
        if self.selected_id:
            for lbl, sid in self._acct_map.items():
                if sid == self.selected_id:
                    self.acct_var.set(lbl); break

    def _build_daily(self):
        wrap = tk.Frame(self.tab_daily, bg=BG)
        wrap.pack(fill="both", expand=True, padx=8, pady=8)

        # 状态行
        status_row = tk.Frame(wrap, bg=BG)
        status_row.pack(fill="x", pady=(4, 12))
        self.dot = tk.Canvas(status_row, width=10, height=10, bg=BG, highlightthickness=0)
        self.dot.pack(side="left", pady=(4, 0))
        self.dot_oval = self.dot.create_oval(0, 0, 10, 10, fill=MUTED, outline="")
        self.status_lbl = tk.Label(status_row, text="检测中…", bg=BG, fg=MUTED, font=FONT_B)
        self.status_lbl.pack(side="left", padx=(8, 0))

        # 三个统计卡片
        cards = tk.Frame(wrap, bg=BG)
        cards.pack(fill="x", pady=(0, 12))
        self.card_today = self._make_card(cards, "今日在线", "--", ACCENT)
        self.card_week  = self._make_card(cards, "本周累计", "--", ACCENT2)
        self.card_avg   = self._make_card(cards, "本周日均", "--", GREEN)
        for c in (self.card_today, self.card_week, self.card_avg):
            c.pack(side="left", expand=True, fill="both", padx=6)

        # 周图表卡片
        chart_card = tk.Frame(wrap, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        chart_card.pack(fill="both", expand=True, pady=(0, 10))
        tk.Label(chart_card, text="  最近 7 天", bg=CARD, fg=MUTED, font=FONT_B).pack(anchor="w", padx=14, pady=(10, 0))
        self.chart = tk.Canvas(chart_card, bg=CARD, height=200, highlightthickness=0)
        self.chart.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # 历史表
        hist_card = tk.Frame(wrap, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        hist_card.pack(fill="x", pady=(0, 4))
        tk.Label(hist_card, text="  历史记录", bg=CARD, fg=MUTED, font=FONT_B).pack(anchor="w", padx=14, pady=(10, 0))
        cols = ("date", "dur", "h")
        self.dtree = ttk.Treeview(hist_card, columns=cols, show="headings", height=5)
        self.dtree.heading("date", text="日期")
        self.dtree.heading("dur", text="时长")
        self.dtree.heading("h", text="小时")
        self.dtree.column("date", width=160, anchor="center")
        self.dtree.column("dur", width=160, anchor="center")
        self.dtree.column("h", width=100, anchor="center")
        self.dtree.pack(fill="x", padx=10, pady=(4, 10))

        btn = tk.Frame(wrap, bg=BG)
        btn.pack(fill="x")
        ttk.Button(btn, text="+ 补记今天 30 分钟", command=self._manual_add).pack(side="left")

    def _make_card(self, parent, title, value, accent):
        c = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        # 左侧 accent 竖条
        bar = tk.Frame(c, bg=accent, width=3)
        bar.pack(side="left", fill="y")
        body = tk.Frame(c, bg=CARD)
        body.pack(side="left", fill="both", expand=True, padx=14, pady=12)
        tk.Label(body, text=title, bg=CARD, fg=MUTED, font=FONT_CARD_TITLE).pack(anchor="w")
        val_lbl = tk.Label(body, text=value, bg=CARD, fg=TEXT, font=FONT_CARD_VAL)
        val_lbl.pack(anchor="w", pady=(4, 0))
        c.val_lbl = val_lbl
        return c

    def _build_games(self):
        bar = tk.Frame(self.tab_games, bg=BG)
        bar.pack(fill="x", padx=16, pady=12)
        tk.Label(bar, text="搜索", bg=BG, fg=MUTED, font=FONT_S).pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *a: self._render_games())
        ttk.Entry(bar, textvariable=self.search_var, width=28).pack(side="left", padx=6)
        ttk.Button(bar, text="重新读取本地", command=self._refresh_games).pack(side="left", padx=6)
        self.sync_btn = ttk.Button(bar, text="☁ 从 Steam 同步", command=self._sync_from_api)
        self.sync_btn.pack(side="left", padx=6)

        cols = ("rank", "name", "total", "2w", "last")
        self.gtree = ttk.Treeview(self.tab_games, columns=cols, show="headings")
        self.gtree.heading("rank", text="#")
        self.gtree.heading("name", text="游戏")
        self.gtree.heading("total", text="累计 (小时)")
        self.gtree.heading("2w", text="近两周")
        self.gtree.heading("last", text="上次游玩")
        self.gtree.column("rank", width=50, anchor="center")
        self.gtree.column("name", width=360)
        self.gtree.column("total", width=130, anchor="center")
        self.gtree.column("2w", width=110, anchor="center")
        self.gtree.column("last", width=140, anchor="center")
        self.gtree.pack(fill="both", expand=True, padx=16, pady=(0, 6))
        self.gsum = tk.Label(self.tab_games, text="", bg=BG, fg=MUTED, font=FONT_S)
        self.gsum.pack(anchor="w", padx=16, pady=(0, 10))

    # ---------- 托盘 ----------
    def _build_tray(self):
        img = Image.new("RGB", (64, 64), BG)
        d = ImageDraw.Draw(img)
        d.ellipse((8, 8, 56, 56), fill=ACCENT)
        d.arc((16, 16, 48, 48), start=-60, end=200, fill="white", width=4)
        self._tray = pystray.Icon("steam_tracker", img, "Steam 在线时长",
            menu=pystray.Menu(
                pystray.MenuItem("显示主窗口", self._tray_show, default=True),
                pystray.MenuItem("退出", self._tray_quit),
            ))
        threading.Thread(target=self._tray.run, daemon=True).start()

    def _tray_show(self, icon=None, item=None):
        self.root.after(0, self._show_window)

    def _tray_quit(self, icon=None, item=None):
        self.root.after(0, self._real_quit)

    def _show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    # ---------- 关闭/最小化 ----------
    def _on_unmap(self, event):
        # 窗口被最小化时，自动藏到托盘
        if event.widget is self.root and self.root.state() == "iconic":
            # 延迟一下避免动画期间闪烁
            self.root.after(200, self.root.withdraw)

    def _on_x_clicked(self):
        if not self._ask_on_close:
            self.root.withdraw()
            return
        # 自定义询问
        dlg = tk.Toplevel(self.root)
        dlg.title("")
        dlg.geometry("360x180")
        dlg.configure(bg=CARD)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(False, False)
        # 居中
        self.root.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width()-360)//2
        y = self.root.winfo_y() + (self.root.winfo_height()-180)//2
        dlg.geometry(f"+{x}+{y}")

        tk.Label(dlg, text="关闭主窗口？", bg=CARD, fg=TEXT,
                 font=("Microsoft YaHei UI", 13, "bold")).pack(pady=(20, 4))
        tk.Label(dlg, text="最小化到托盘会继续后台计时；退出则停止计时。",
                 bg=CARD, fg=MUTED, font=FONT_S).pack()

        btns = tk.Frame(dlg, bg=CARD)
        btns.pack(pady=16)
        def to_tray():
            dlg.destroy()
            self.root.withdraw()
        def quit_app():
            dlg.destroy()
            self._real_quit()
        ttk.Button(btns, text="最小化到托盘", command=to_tray).pack(side="left", padx=6)
        ttk.Button(btns, text="退出程序", command=quit_app).pack(side="left", padx=6)

    def _real_quit(self):
        self._stop.set()
        save_data(self.data)
        if self._tray:
            self._tray.stop()
        self.root.destroy()

    # ---------- 数据 ----------
    def _acct_daily(self):
        if not self.selected_id:
            return {}
        acc = self.data["accounts"].setdefault(self.selected_id, {"daily": {}})
        return acc["daily"]

    def _on_account_change(self):
        lbl = self.acct_var.get()
        sid = self._acct_map.get(lbl)
        if sid:
            self.selected_id = sid
            self._refresh_games()

    def _start_monitor(self):
        def loop():
            while not self._stop.is_set():
                try:
                    if is_steam_running():
                        today = today_str()
                        sid = active_account_id(self.steam_path) or self.selected_id
                        acc = self.data["accounts"].setdefault(sid, {"daily": {}})
                        acc["daily"][today] = acc["daily"].get(today, 0) + POLL_INTERVAL
                    self.data["last_tick"] = datetime.now().isoformat(timespec="seconds")
                    save_data(self.data)
                except Exception as e:
                    print("monitor:", e, file=sys.stderr)
                for _ in range(POLL_INTERVAL):
                    if self._stop.is_set(): break
                    time.sleep(1)
        threading.Thread(target=loop, daemon=True).start()

    # ---------- 刷新 ----------
    def _tick_ui(self):
        daily = self._acct_daily()
        today = today_str()
        today_s = daily.get(today, 0)

        self.card_today.val_lbl.config(text=fmt_dur(today_s))

        # 本周（从周一到今天）
        wk_secs = 0
        now = date.today()
        monday = now - timedelta(days=now.weekday())
        for i in range(7):
            d = (monday + timedelta(days=i)).isoformat()
            if d > today: break
            wk_secs += daily.get(d, 0)
        self.card_week.val_lbl.config(text=fmt_dur(wk_secs))
        elapsed_days = (now - monday).days + 1
        avg = wk_secs / max(elapsed_days, 1)
        self.card_avg.val_lbl.config(text=fmt_dur(avg))

        # 状态灯
        running = is_steam_running()
        col = GREEN if running else RED
        self.dot.itemconfig(self.dot_oval, fill=col)
        self.status_lbl.config(text="● Steam 运行中" if running else "○ Steam 未运行",
                              fg=col if running else MUTED)

        self._draw_chart(daily)
        self._render_dtree(daily)

        sid = active_account_id(self.steam_path)
        sn = self.accounts.get(sid, "?")
        self.status.config(text=f"当前登录：{sn}   ·   {len(self.accounts)} 个账号   ·   "
                                f"后台每 {POLL_INTERVAL}s 检测一次   ·   {datetime.now():%H:%M:%S}")
        self.root.after(REFRESH_UI_MS, self._tick_ui)

    def _draw_chart(self, daily):
        c = self.chart
        c.delete("all")
        w = c.winfo_width() or 900
        h = 200
        c.configure(width=w, height=h)
        days = []
        for i in range(6, -1, -1):
            d = (date.today() - timedelta(days=i)).isoformat()
            days.append((d, daily.get(d, 0)))
        max_s = max((s for _, s in days), default=1) or 1
        base = h - 40
        slot = w / 7
        bw = min(44, slot * 0.45)
        for i, (d, s) in enumerate(days):
            cx = i*slot + slot/2
            bh = (s/max_s)*(base-30) if s > 0 else 0
            x0, x1 = cx-bw/2, cx+bw/2
            y0, y1 = base-bh, base
            if s > 0:
                round_rect(c, x0, y0, x1, y1, r=8, fill=ACCENT)
                c.create_text(cx, y0-10, text=f"{s/3600:.1f}h",
                              fill=TEXT, font=FONT_S)
            label = f"{int(d[5:7])}/{int(d[8:10])}"
            c.create_text(cx, base+16, text=label, fill=MUTED, font=FONT_S)

    def _render_dtree(self, daily):
        for it in self.dtree.get_children(): self.dtree.delete(it)
        for d, s in sorted(daily.items(), reverse=True)[:30]:
            self.dtree.insert("", "end", values=(d, fmt_dur(s), f"{s/3600:.2f}"))

    def _render_games(self):
        kw = self.search_var.get().strip().lower()
        rows = sorted(self.games.items(), key=lambda kv: -kv[1]["minutes"])
        for it in self.gtree.get_children(): self.gtree.delete(it)
        total, shown = 0, 0
        for _, g in rows:
            if kw and kw not in g["name"].lower(): continue
            last = datetime.fromtimestamp(g["last_played"]).strftime("%Y-%m-%d") if g["last_played"] else "-"
            self.gtree.insert("", "end", values=(shown+1, g["name"], fmt_h(g["minutes"]),
                                                 fmt_h(g["minutes_2w"]), last))
            total += g["minutes"]; shown += 1
        self.gsum.config(text=f"显示 {shown} 个游戏  ·  累计 {fmt_h(total)} 小时  ·  未安装的游戏已隐藏")

    def _refresh_games(self):
        self.games = {}
        if self.steam_path and self.selected_id:
            ud = self.steam_path / "userdata" / self.selected_id
            if ud.exists():
                try: self.games = load_game_playtime(self.steam_path, ud)
                except Exception as e: messagebox.showerror("读取失败", str(e))
        # 如果有 API 缓存的名字，合并进来（补全已卸载游戏名）
        api_cache = self.data.get("api_cache", {}).get(self.selected_id, {})
        for appid, info in api_cache.items():
            if appid in self.games:
                self.games[appid]["name"] = info.get("name", self.games[appid]["name"])
            elif info.get("minutes", 0) > 0:
                self.games[appid] = {
                    "name": info.get("name", f"App {appid}"),
                    "minutes": info.get("minutes", 0),
                    "minutes_2w": info.get("minutes_2w", 0),
                    "last_played": 0,
                }
        self._render_games()

    def _open_settings(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("设置")
        dlg.geometry("520x280")
        dlg.configure(bg=CARD)
        dlg.transient(self.root)
        dlg.grab_set()
        self.root.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width()-520)//2
        y = self.root.winfo_y() + (self.root.winfo_height()-280)//2
        dlg.geometry(f"+{x}+{y}")

        tk.Label(dlg, text="Steam Web API Key", bg=CARD, fg=TEXT,
                 font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=20, pady=(18, 4))
        tk.Label(dlg, text="在 https://steamcommunity.com/dev/apikey 免费申请，填到这里后可同步完整游戏库。",
                 bg=CARD, fg=MUTED, font=FONT_S, wraplength=480, justify="left").pack(anchor="w", padx=20)
        key_var = tk.StringVar(value=self.config.get("api_key", ""))
        ent = ttk.Entry(dlg, textvariable=key_var, width=60)
        ent.pack(anchor="w", padx=20, pady=(8, 4))
        ent.focus_set()

        msg = tk.Label(dlg, text="", bg=CARD, fg=MUTED, font=FONT_S)
        msg.pack(anchor="w", padx=20)

        def save():
            self.config["api_key"] = key_var.get().strip()
            save_config(self.config)
            msg.config(text="已保存", fg=GREEN)
            dlg.after(800, dlg.destroy)

        btns = tk.Frame(dlg, bg=CARD)
        btns.pack(pady=14)
        ttk.Button(btns, text="保存", command=save).pack(side="left", padx=6)
        ttk.Button(btns, text="取消", command=dlg.destroy).pack(side="left", padx=6)

    def _sync_from_api(self):
        key = self.config.get("api_key", "").strip()
        if not key:
            messagebox.showinfo("需要 API Key", "请先点右上角「设置」填入 Steam Web API Key。\n"
                                "在 https://steamcommunity.com/dev/apikey 免费申请。")
            return
        sid64 = sid32_to_64(self.selected_id)
        if not sid64:
            messagebox.showerror("错误", "无法解析当前账号 SteamID64")
            return
        self.sync_btn.config(text="同步中…", state="disabled")
        self.root.update()

        def work():
            try:
                owned = api_fetch_owned_games(key, sid64)
                cache = self.data.setdefault("api_cache", {})
                cache[self.selected_id] = owned
                save_data(self.data)
                self.root.after(0, lambda: (
                    self._refresh_games(),
                    self.sync_btn.config(text="☁ 从 Steam 同步", state="normal"),
                    messagebox.showinfo("完成", f"已从 Steam 同步 {len(owned)} 个游戏")))
            except Exception as e:
                self.root.after(0, lambda: (
                    self.sync_btn.config(text="☁ 从 Steam 同步", state="normal"),
                    messagebox.showerror("同步失败", str(e))))
        threading.Thread(target=work, daemon=True).start()

    def _manual_add(self):
        d = self._acct_daily()
        d[today_str()] = d.get(today_str(), 0) + 30*60
        save_data(self.data)
        self._tick_ui()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
