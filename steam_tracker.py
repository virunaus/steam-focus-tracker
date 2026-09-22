# -*- coding: utf-8 -*-
"""
Steam Focus Tracker — 桌面应用
"""
import json, subprocess, sys, threading, time, urllib.request, urllib.parse, os
from datetime import datetime, date, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from PIL import Image, ImageDraw, ImageTk
import pystray

# ---------- 路径 ----------
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
DATA_FILE = APP_DIR / "tracker_data.json"
CONFIG_FILE = APP_DIR / "config.json"
ICON_DIR = APP_DIR / "icons"
ICON_DIR.mkdir(exist_ok=True)
POLL_INTERVAL = 30
REFRESH_UI_MS = 1000
STEAMID64_BASE = 76561197960265728

# ---------- 青黑电竞配色 ----------
BG="#080c0b"; CARD="#0f1a18"; CARD_HI="#162420"; BORDER="#1a3a32"
TEXT="#e0f5f0"; MUTED="#4a7a70"
ACCENT="#00d4aa"; ACCENT2="#00b894"; GREEN="#00e5a0"; RED="#ff6b6b"; AMBER="#ffc857"
FONT=("Microsoft YaHei UI",10); FONT_B=("Microsoft YaHei UI",10,"bold")
FONT_S=("Microsoft YaHei UI",9); FONT_H=("Microsoft YaHei UI",11,"bold")
FONT_CV=("Microsoft YaHei UI",18,"bold")

# ---------- Steam 本地 ----------
def find_steam_path():
    import winreg
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for sub,vn in [(r"Software\Valve\Steam","SteamPath"),(r"SOFTWARE\WOW6432Node\Valve\Steam","InstallPath")]:
            try:
                k=winreg.OpenKey(root,sub); v,_=winreg.QueryValueEx(k,vn); winreg.CloseKey(k)
                if v and Path(v).exists(): return Path(v)
            except OSError: pass
    for p in [r"C:\Program Files (x86)\Steam",r"C:\Program Files\Steam",r"D:\Steam"]:
        if Path(p).exists(): return Path(p)
    return None

def parse_vdf(text):
    pos=0; n=len(text)
    def ws():
        nonlocal pos
        while pos<n and text[pos] in " \t\r\n":
            if text[pos:pos+2]=="//":
                while pos<n and text[pos]!="\n": pos+=1
            else: pos+=1
    def s():
        nonlocal pos; ws()
        if pos>=n or text[pos]!='"': return None
        pos+=1; buf=[]
        while pos<n and text[pos]!='"':
            if text[pos]=='\\' and pos+1<n: buf.append(text[pos+1]); pos+=2
            else: buf.append(text[pos]); pos+=1
        if pos<n and text[pos]=='"': pos+=1
        return "".join(buf)
    def block():
        nonlocal pos; o={}
        while True:
            ws()
            if pos>=n or text[pos]=='}': return o
            k=s()
            if k is None: return o
            ws()
            if pos<n and text[pos]=='{':
                pos+=1; v=block(); ws()
                if pos<n and text[pos]=='}': pos+=1
            else: v=s()
            o[k]=v
    return block()

def scan_accounts(sp):
    ud=sp/"userdata"; r={}
    if not ud.exists(): return r
    fs=[d for d in ud.iterdir() if d.is_dir() and d.name.isdigit()]
    fs.sort(key=lambda d:d.stat().st_mtime,reverse=True)
    for d in fs:
        lc=d/"config"/"localconfig.vdf"; name=d.name
        if lc.exists():
            try:
                data=parse_vdf(lc.read_text(encoding="utf-8",errors="ignore"))
                pn=data.get("UserLocalConfigStore",{}).get("friends",{}).get("PersonaName")
                if pn: name=pn
            except: pass
        r[d.name]=name
    return r

def active_account_id(sp):
    ud=sp/"userdata"; best=None; mt=-1
    if not ud.exists(): return None
    for d in ud.iterdir():
        if not d.is_dir() or not d.name.isdigit(): continue
        lc=d/"config"/"localconfig.vdf"
        if lc.exists():
            m=lc.stat().st_mtime
            if m>mt: mt=m; best=d.name
    return best

def is_steam_running():
    try:
        out=subprocess.run(["tasklist","/FI","IMAGENAME eq steam.exe","/NH","/FO","CSV"],
            capture_output=True,text=True,timeout=10,creationflags=0x08000000)
        return "steam.exe" in out.stdout.lower()
    except: return False

def load_game_playtime(sp,ud):
    lc=ud/"config"/"localconfig.vdf"; games={}
    if lc.exists():
        try:
            data=parse_vdf(lc.read_text(encoding="utf-8",errors="ignore"))
            apps=data.get("UserLocalConfigStore",{}).get("Software",{}).get("Valve",{}).get("Steam",{}).get("apps",{})
            for aid,info in apps.items():
                if not isinstance(info,dict): continue
                pt=info.get("Playtime")
                try: mn=int(pt)
                except: continue
                if mn<=0: continue
                try: lt=int(info.get("LastPlayed") or 0)
                except: lt=0
                try: m2=int(info.get("Playtime2wks") or 0)
                except: m2=0
                games[aid]={"minutes":mn,"last_played":lt,"minutes_2w":m2,"name":f"App {aid}"}
        except Exception as e: print("parse:",e,file=sys.stderr)
    md=sp/"steamapps"
    if md.exists():
        for mf in md.glob("appmanifest_*.acf"):
            try:
                m=parse_vdf(mf.read_text(encoding="utf-8",errors="ignore"))
                inner=m.get("AppState",m) if isinstance(m,dict) else {}
                aid=str(inner.get("appid","")); nm=inner.get("name","")
                if aid in games and nm:
                    games[aid]["name"]=nm
            except: pass
    return games

# ---------- 数据 ----------
def load_data():
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE,"r",encoding="utf-8") as f: return json.load(f)
        except: pass
    return {"accounts":{}}

def save_data(d):
    with open(DATA_FILE,"w",encoding="utf-8") as f: json.dump(d,f,ensure_ascii=False,indent=2)

def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE,"r",encoding="utf-8") as f: return json.load(f)
        except: pass
    return {"api_key":"","daily_goal_min":120,"goal_enabled":False,"bg_image":""}

def save_config(c):
    with open(CONFIG_FILE,"w",encoding="utf-8") as f: json.dump(c,f,ensure_ascii=False,indent=2)

def sid32_to_64(s):
    try: return str(STEAMID64_BASE+int(s))
    except: return None

# ---------- Steam API ----------
def _get(url,t=15):
    req=urllib.request.Request(url,headers={"User-Agent":"SteamTracker/1.0"})
    with urllib.request.urlopen(req,timeout=t) as r: return json.loads(r.read().decode("utf-8"))

def api_owned(key,sid64):
    qs=urllib.parse.urlencode({"key":key,"steamid":sid64,"include_appinfo":1,
        "include_played_free_games":1,"format":"json"})
    d=_get(f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/?{qs}")
    out={}
    for g in d.get("response",{}).get("games",[]):
        aid=str(g.get("appid",""))
        out[aid]={"name":g.get("name",f"App {aid}"),"minutes":g.get("playtime_forever",0),
                  "minutes_2w":g.get("playtime_2weeks",0),
                  "icon_hash":g.get("img_icon_url","")}
    return out

def download_icon(appid,icon_hash):
    fp=ICON_DIR/f"{appid}.jpg"
    if not fp.exists() and icon_hash:
        try:
            url=f"https://media.steampowered.com/steamcommunity/public/images/apps/{appid}/{icon_hash}.jpg"
            urllib.request.urlretrieve(url,fp)
        except: return None
    if fp.exists():
        try:
            img=Image.open(fp).resize((32,32),Image.LANCZOS)
            return ImageTk.PhotoImage(img)
        except: return None
    return None

# ---------- 工具 ----------
def fmt_dur(s):
    s=int(s); h,r=divmod(s,3600); m,ss=divmod(r,60)
    if h: return f"{h}h {m:02d}m"
    if m: return f"{m}m {ss:02d}s"
    return f"{ss}s"
def fmt_h(m): return f"{m/60:.1f}"
def today(): return date.today().isoformat()

def round_rect(c,x1,y1,x2,y2,r=8,**kw):
    pts=[x1+r,y1,x2-r,y1,x2,y1,x2,y1+r,x2,y2-r,x2,y2,x2-r,y2,x1+r,y2,x1,y2,x1,y2-r,x1,y1+r,x1,y1]
    return c.create_polygon(pts,smooth=True,**kw)

# ---------- 主应用 ----------
class App:
    def __init__(self,root):
        self.root=root
        root.title("Steam Focus Tracker")
        root.geometry("1280x820"); root.minsize(1100,720)
        self.data=load_data(); self.config=load_config()
        self.sp=find_steam_path()
        self.accounts=scan_accounts(self.sp) if self.sp else {}
        self.aid=active_account_id(self.sp) if self.sp else None
        self.selected=self.aid if self.aid in self.accounts else (list(self.accounts)[0] if self.accounts else None)
        self.games={}; self._icons={}; self._icon_worker=None; self._render_rows=[]
        self._stop=threading.Event(); self._tray=None
        self._bg_photo=None; self._bg_image=None
        self._build_bg()
        self._style(); self._ui(); self._on_acct(); self._tray_build(); self._monitor(); self._tick()
        root.protocol("WM_DELETE_WINDOW",self._on_x)
        root.bind("<Unmap>",self._on_unmap)
        root.bind("<Configure>",lambda e:self.root.after(100,self._redraw_bg))

    def _build_bg(self):
        """创建背景 Canvas"""
        self.bg_canvas=tk.Canvas(self.root,bg=BG,highlightthickness=0)
        self.bg_canvas.pack(fill="both",expand=True)
        self._load_bg_image()

    def _load_bg_image(self):
        p=self.config.get("bg_image","")
        self._bg_image=None
        if p and Path(p).exists():
            try: self._bg_image=Image.open(p)
            except: self._bg_image=None
        self._redraw_bg()

    def _redraw_bg(self):
        w=self.bg_canvas.winfo_width() or 1280; h=self.bg_canvas.winfo_height() or 820
        self.bg_canvas.delete("all")
        if self._bg_image:
            try:
                img=self._bg_image.resize((w,h),Image.LANCZOS)
                self._bg_photo=ImageTk.PhotoImage(img)
                self.bg_canvas.create_image(0,0,image=self._bg_photo,anchor="nw")
                # 暗色遮罩
                self.bg_canvas.create_rectangle(0,0,w,h,fill="#080c0b",stipple="gray50",outline="")
            except: pass

    def _style(self):
        s=ttk.Style()
        try: s.theme_use("clam")
        except: pass
        s.configure("TNotebook",background=BG,borderwidth=0)
        s.configure("TNotebook.Tab",background=BG,foreground=MUTED,padding=(20,8),font=FONT_B,borderwidth=0)
        s.map("TNotebook.Tab",background=[("selected",CARD)],foreground=[("selected",ACCENT)])
        s.configure("TFrame",background=BG)
        s.configure("TCombobox",fieldbackground=CARD,background=CARD,foreground=TEXT,arrowcolor=ACCENT,borderwidth=0,padding=4)
        s.map("TCombobox",fieldbackground=[("readonly",CARD)],foreground=[("readonly",TEXT)])
        s.configure("TButton",background=CARD_HI,foreground=TEXT,borderwidth=0,padding=(10,6),font=FONT)
        s.map("TButton",background=[("active",ACCENT)],foreground=[("active",BG)])
        s.configure("Treeview",background=CARD,fieldbackground=CARD,foreground=TEXT,font=FONT_S,rowheight=30,borderwidth=0)
        s.configure("Treeview.Heading",background=CARD,foreground=MUTED,font=FONT_B,borderwidth=0)
        s.layout("Treeview",[('Treeview.treearea',{'sticky':'nswe'})])

    def _ui(self):
        self.main=tk.Frame(self.root,bg=BG)
        self.main.place(x=0,y=0,relwidth=1,relheight=1)
        h=tk.Frame(self.main,bg=BG); h.pack(fill="x",padx=24,pady=(16,6))
        tk.Label(h,text="◈  Steam Focus Tracker",bg=BG,fg=ACCENT,
                 font=("Microsoft YaHei UI",16,"bold")).pack(side="left")
        r=tk.Frame(h,bg=BG); r.pack(side="right")
        tk.Label(r,text="账号",bg=BG,fg=MUTED,font=FONT_S).pack(side="left",padx=(0,6))
        self.av=tk.StringVar(); self.acb=ttk.Combobox(r,textvariable=self.av,state="readonly",width=24)
        self.acb.pack(side="left"); self._accts(); self.acb.bind("<<ComboboxSelected>>",lambda e:self._on_acct())
        ttk.Button(r,text="设置",command=self._settings).pack(side="left",padx=(8,0))

        nb=ttk.Notebook(self.main); nb.pack(fill="both",expand=True,padx=20,pady=(6,4))
        self.t_ov=ttk.Frame(nb); self.t_hm=ttk.Frame(nb); self.t_gm=ttk.Frame(nb)
        nb.add(self.t_ov,text="  概览  "); nb.add(self.t_hm,text="  热力图  "); nb.add(self.t_gm,text="  游戏  ")
        self._ov(); self._hm(); self._gm()
        self.status=tk.Label(self.main,text="",bg=BG,fg=MUTED,font=FONT_S,anchor="w")
        self.status.pack(fill="x",padx=24,pady=(2,8))

    def _accts(self):
        self._amap={}; self.acb["values"]=list(self.accounts.values())
        if self.selected:
            for sid,nm in self.accounts.items():
                if sid==self.selected: self.av.set(nm)

    def _ov(self):
        w=tk.Frame(self.t_ov,bg=BG); w.pack(fill="both",expand=True,padx=8,pady=8)
        sr=tk.Frame(w,bg=BG); sr.pack(fill="x",pady=(4,8))
        self.dot=tk.Canvas(sr,width=10,height=10,bg=BG,highlightthickness=0); self.dot.pack(side="left",pady=(4,0))
        self.do=self.dot.create_oval(0,0,10,10,fill=MUTED,outline="")
        self.sl=tk.Label(sr,text="检测中…",bg=BG,fg=MUTED,font=FONT_B); self.sl.pack(side="left",padx=8)

        top=tk.Frame(w,bg=BG); top.pack(fill="x",pady=(0,10))
        self.ring_card=tk.Frame(top,bg=CARD,highlightthickness=1,highlightbackground=ACCENT)
        tk.Label(self.ring_card,text="  今日目标",bg=CARD,fg=MUTED,font=FONT_B).pack(anchor="w",padx=12,pady=(8,0))
        self.ring=tk.Canvas(self.ring_card,width=160,height=160,bg=CARD,highlightthickness=0); self.ring.pack(padx=10,pady=(0,8))
        rc=tk.Frame(top,bg=BG); rc.pack(side="left",fill="both",expand=True)
        self.c_today=self._card(rc,"今日在线","--",ACCENT)
        self.c_week=self._card(rc,"本周累计","--",ACCENT2)
        self.c_avg=self._card(rc,"本周日均","--",GREEN)
        for c in (self.c_today,self.c_week,self.c_avg): c.pack(side="left",expand=True,fill="both",padx=4)
        self._apply_goal_visibility()

        cc=tk.Frame(w,bg=CARD,highlightthickness=1,highlightbackground=BORDER); cc.pack(fill="both",expand=True,pady=(0,8))
        tk.Label(cc,text="  最近 7 天",bg=CARD,fg=MUTED,font=FONT_B).pack(anchor="w",padx=14,pady=(8,0))
        self.ch=tk.Canvas(cc,bg=CARD,height=180,highlightthickness=0); self.ch.pack(fill="both",expand=True,padx=10,pady=(0,8))

        b=tk.Frame(w,bg=BG); b.pack(fill="x")
        ttk.Button(b,text="+ 补记 30 分钟",command=self._add).pack(side="left")

    def _card(self,p,t,v,a):
        c=tk.Frame(p,bg=CARD,highlightthickness=1,highlightbackground=BORDER)
        tk.Frame(c,bg=a,width=3).pack(side="left",fill="y")
        bd=tk.Frame(c,bg=CARD); bd.pack(side="left",fill="both",expand=True,padx=12,pady=10)
        tk.Label(bd,text=t,bg=CARD,fg=MUTED,font=FONT_S).pack(anchor="w")
        l=tk.Label(bd,text=v,bg=CARD,fg=TEXT,font=FONT_CV); l.pack(anchor="w",pady=(2,0))
        c.l=l; return c

    def _hm(self):
        w=tk.Frame(self.t_hm,bg=BG); w.pack(fill="both",expand=True,padx=16,pady=12)
        tk.Label(w,text="  在线热力图（最近 18 周）",bg=BG,fg=TEXT,font=("Microsoft YaHei UI",13,"bold")).pack(anchor="w")
        tk.Label(w,text="点击日期格子查看当天详情",bg=BG,fg=MUTED,font=FONT_S).pack(anchor="w",pady=(0,8))
        self.hm=tk.Canvas(w,bg=CARD,highlightthickness=1,highlightbackground=BORDER); self.hm.pack(fill="both",expand=True)
        self.hm.bind("<Button-1>",self._hm_click)
        self.hm_info=tk.Label(w,text="",bg=BG,fg=TEXT,font=FONT_B,anchor="w")
        self.hm_info.pack(fill="x",pady=(8,0))
        self._hm_cells={}

    def _gm(self):
        b=tk.Frame(self.t_gm,bg=BG); b.pack(fill="x",padx=16,pady=10)
        tk.Label(b,text="搜索",bg=BG,fg=MUTED,font=FONT_S).pack(side="left")
        self.sv=tk.StringVar(); self.sv.trace_add("write",lambda *a:self._render_gm())
        ttk.Entry(b,textvariable=self.sv,width=24).pack(side="left",padx=6)
        ttk.Button(b,text="本地重读",command=self._load_games).pack(side="left",padx=4)
        self.sb=ttk.Button(b,text="☁ 同步",command=self._sync); self.sb.pack(side="left",padx=4)
        cols=("nm","tot","2w","last")
        self.gt=ttk.Treeview(self.t_gm,columns=cols,show="tree headings")
        self.gt.heading("#0",text="")
        self.gt.heading("nm",text="游戏")
        self.gt.heading("tot",text="累计(h)"); self.gt.heading("2w",text="两周(h)"); self.gt.heading("last",text="上次")
        self.gt.column("#0",width=56,anchor="center",stretch=False)
        self.gt.column("nm",width=420)
        self.gt.column("tot",width=100,anchor="center"); self.gt.column("2w",width=90,anchor="center")
        self.gt.column("last",width=120,anchor="center")
        self.gt.pack(fill="both",expand=True,padx=16,pady=(0,4))
        self.gs=tk.Label(self.t_gm,text="",bg=BG,fg=MUTED,font=FONT_S); self.gs.pack(anchor="w",padx=16,pady=(0,8))

    # ---------- 托盘 ----------
    def _tray_build(self):
        img=Image.new("RGB",(64,64),BG); d=ImageDraw.Draw(img)
        d.ellipse((6,6,58,58),fill=ACCENT); d.arc((14,14,50,50),start=-60,end=200,fill="white",width=4)
        self._tray=pystray.Icon("st",img,"Steam Focus Tracker",menu=pystray.Menu(
            pystray.MenuItem("显示",lambda i,it:self.root.after(0,self._show),default=True),
            pystray.MenuItem("退出",lambda i,it:self.root.after(0,self._quit))))
        threading.Thread(target=self._tray.run,daemon=True).start()
    def _show(self): self.root.deiconify(); self.root.lift(); self.root.focus_force()
    def _on_unmap(self,e):
        if e.widget is self.root and self.root.state()=="iconic":
            self.root.after(200,self.root.withdraw)
    def _on_x(self):
        dlg=tk.Toplevel(self.root); dlg.title(""); dlg.geometry("360x170"); dlg.configure(bg=CARD)
        dlg.transient(self.root); dlg.grab_set(); dlg.resizable(False,False)
        self.root.update_idletasks()
        dlg.geometry(f"+{self.root.winfo_x()+(self.root.winfo_width()-360)//2}+{self.root.winfo_y()+(self.root.winfo_height()-170)//2}")
        tk.Label(dlg,text="关闭窗口？",bg=CARD,fg=TEXT,font=("Microsoft YaHei UI",13,"bold")).pack(pady=(18,4))
        tk.Label(dlg,text="最小化到托盘继续计时；退出则停止。",bg=CARD,fg=MUTED,font=FONT_S).pack()
        bf=tk.Frame(dlg,bg=CARD); bf.pack(pady=14)
        ttk.Button(bf,text="最小化到托盘",command=lambda:(dlg.destroy(),self.root.withdraw())).pack(side="left",padx=6)
        ttk.Button(bf,text="退出",command=lambda:(dlg.destroy(),self._quit())).pack(side="left",padx=6)
    def _quit(self):
        self._stop.set(); save_data(self.data)
        if self._tray: self._tray.stop()
        self.root.destroy()

    # ---------- 数据 ----------
    def _daily(self):
        if not self.selected: return {}
        return self.data["accounts"].setdefault(self.selected,{"daily":{}})["daily"]
    def _on_acct(self):
        for sid,nm in self.accounts.items():
            if nm==self.av.get(): self.selected=sid; break
        self._load_games()

    def _monitor(self):
        def loop():
            while not self._stop.is_set():
                try:
                    if is_steam_running():
                        sid=active_account_id(self.sp) or self.selected
                        acc=self.data["accounts"].setdefault(sid,{"daily":{}})
                        acc["daily"][today()]=acc["daily"].get(today(),0)+POLL_INTERVAL
                    save_data(self.data)
                except Exception as e: print("m:",e,file=sys.stderr)
                for _ in range(POLL_INTERVAL):
                    if self._stop.is_set(): break
                    time.sleep(1)
        threading.Thread(target=loop,daemon=True).start()

    # ---------- 刷新 ----------
    def _tick(self):
        d=self._daily(); t=today(); ts=d.get(t,0)
        self.c_today.l.config(text=fmt_dur(ts))
        now=date.today(); mon=now-timedelta(days=now.weekday()); wk=0
        for i in range(7):
            dd=(mon+timedelta(days=i)).isoformat()
            if dd>t: break
            wk+=d.get(dd,0)
        self.c_week.l.config(text=fmt_dur(wk))
        self.c_avg.l.config(text=fmt_dur(wk/max((now-mon).days+1,1)))
        r=is_steam_running(); col=GREEN if r else RED
        self.dot.itemconfig(self.do,fill=col)
        self.sl.config(text="● Steam 运行中" if r else "○ Steam 未运行",fg=col if r else MUTED)
        self._ring(ts); self._bar(d); self._hm_draw(d)
        self.status.config(text=f"当前：{self.accounts.get(active_account_id(self.sp),'?')}  ·  {len(self.accounts)} 账号  ·  {datetime.now():%H:%M:%S}")
        self.root.after(REFRESH_UI_MS,self._tick)

    def _apply_goal_visibility(self):
        if self.config.get("goal_enabled",False):
            self.ring_card.pack(side="left",padx=(0,8),before=self.c_today.master)
        else:
            self.ring_card.pack_forget()

    def _ring(self,today_secs):
        if not self.config.get("goal_enabled",False): return
        c=self.ring; c.delete("all")
        goal=int(self.config.get("daily_goal_min",120))*60
        pct=min(today_secs/max(goal,1),1.0)
        cx,cy,R=80,80,60; sw=10
        c.create_oval(cx-R,cy-R,cx+R,cy+R,outline=BORDER,width=sw)
        if pct>0:
            col=GREEN if today_secs<goal else RED
            c.create_arc(cx-R,cy-R,cx+R,cy+R,start=-90,extent=pct*360,style="arc",outline=col,width=sw)
        remaining=max(0,goal-today_secs)
        c.create_text(cx,cy-12,text=fmt_dur(today_secs),fill=TEXT,font=("Microsoft YaHei UI",14,"bold"))
        c.create_text(cx,cy+12,text=f"还可 {fmt_dur(remaining)}",fill=MUTED,font=FONT_S)

    def _bar(self,d):
        c=self.ch; c.delete("all"); w=c.winfo_width() or 900; h=180
        days=[(date.today()-timedelta(days=i)).isoformat() for i in range(6,-1,-1)]
        sv=[d.get(x,0) for x in days]; mx=max(sv,default=1) or 1
        base=h-36; slot=w/7; bw=min(42,slot*0.45)
        for i,(dd,s) in enumerate(zip(days,sv)):
            cx=i*slot+slot/2; bh=(s/mx)*(base-24) if s>0 else 0
            x0,x1=cx-bw/2,cx+bw/2
            if s>0:
                round_rect(c,x0,base-bh,x1,base,r=8,fill=ACCENT)
                c.create_text(cx,base-bh-10,text=f"{s/3600:.1f}h",fill=TEXT,font=FONT_S)
            c.create_text(cx,base+14,text=f"{int(dd[5:7])}/{int(dd[8:10])}",fill=MUTED,font=FONT_S)

    def _hm_draw(self,d):
        c=self.hm; c.delete("all"); self._hm_cells={}
        w=c.winfo_width() or 900; h=420
        c.configure(width=w,height=h)
        weeks=18; cell=18; gap=4
        today_d=date.today()
        start=today_d-timedelta(days=today_d.weekday()+1)
        start=start-timedelta(weeks=weeks-1)
        max_s=max((d.get((start+timedelta(days=i)).isoformat(),0) for i in range(weeks*7)),default=1) or 1
        x0=60; y0=30
        def color(secs):
            if secs==0: return "#1a2e28"
            r=secs/max_s
            if r<0.2: return "#0f4a3a"
            if r<0.4: return "#117a5a"
            if r<0.6: return "#00a878"
            if r<0.8: return ACCENT
            return "#00ffcc"
        for wki in range(weeks):
            for di in range(7):
                dt=start+timedelta(days=wki*7+di)
                if dt>today_d: continue
                secs=d.get(dt.isoformat(),0)
                x=x0+wki*(cell+gap); y=y0+di*(cell+gap)
                rid=c.create_rectangle(x,y,x+cell,y+cell,fill=color(secs),outline="")
                self._hm_cells[rid]=(dt,secs)
        for di,nm in [(0,"日"),(3,"三"),(6,"六")]:
            c.create_text(20,y0+di*(cell+gap)+cell/2,text=nm,fill=MUTED,font=FONT_S)
        lx=x0; ly=y0+7*(cell+gap)+15
        c.create_text(lx,ly,text="少",fill=MUTED,font=FONT_S)
        for i,colr in enumerate(["#1a2e28","#0f4a3a","#117a5a","#00a878",ACCENT,"#00ffcc"]):
            c.create_rectangle(lx+20+i*22,ly-8,lx+36+i*22,ly+8,fill=colr,outline="")
        c.create_text(lx+20+i*22+22,ly,text="多",fill=MUTED,font=FONT_S)

    def _hm_click(self,e):
        c=self.hm
        item=c.find_closest(e.x,e.y)
        if not item: return
        iid=item[0]
        info=self._hm_cells.get(iid)
        if not info: return
        dt,secs=info
        wd=["一","二","三","四","五","六","日"][dt.weekday()]
        if secs>0:
            self.hm_info.config(text=f"📅 {dt.strftime('%Y-%m-%d')}（周{wd}）  ·  在线 {fmt_dur(secs)}")
        else:
            self.hm_info.config(text=f"📅 {dt.strftime('%Y-%m-%d')}（周{wd}）  ·  无记录")

    # ---------- 游戏 ----------
    def _load_games(self):
        self.games={}
        if self.sp and self.selected:
            ud=self.sp/"userdata"/self.selected
            if ud.exists():
                try: self.games=load_game_playtime(self.sp,ud)
                except Exception as e: messagebox.showerror("err",str(e))
        cache=self.data.get("api_cache",{}).get(self.selected,{})
        for aid,info in cache.items():
            if aid in self.games:
                self.games[aid]["name"]=info.get("name",self.games[aid]["name"])
                self.games[aid]["icon_hash"]=info.get("icon_hash","")
            elif info.get("minutes",0)>0:
                self.games[aid]={"name":info.get("name",f"App {aid}"),"minutes":info.get("minutes",0),
                                 "minutes_2w":info.get("minutes_2w",0),"last_played":0,
                                 "icon_hash":info.get("icon_hash","")}
        self._render_gm()

    def _render_gm(self):
        q=self.sv.get().strip().lower()
        rows=sorted(self.games.items(),key=lambda kv:-kv[1]["minutes"])
        self._render_rows=[]
        for it in self.gt.get_children(): self.gt.delete(it)
        total=0; shown=0
        for aid,g in rows:
            if q and q not in g["name"].lower(): continue
            last=datetime.fromtimestamp(g["last_played"]).strftime("%Y-%m-%d") if g["last_played"] else "-"
            iid=self.gt.insert("","end",values=(g["name"],fmt_h(g["minutes"]),fmt_h(g["minutes_2w"]),last))
            self._render_rows.append((aid,iid))
            total+=g["minutes"]; shown+=1
        self.gs.config(text=f"{shown} 个游戏  ·  累计 {fmt_h(total)} 小时")
        if self._icon_worker:
            self._icon_worker.cancel() if hasattr(self._icon_worker,'cancel') else None
        self._icon_worker=threading.Thread(target=self._load_icons_bg,daemon=True)
        self._icon_worker.start()

    def _load_icons_bg(self):
        for aid,iid in self._render_rows[:50]:
            if self._stop.is_set(): break
            g=self.games.get(aid,{})
            ic=download_icon(aid,g.get("icon_hash",""))
            if ic:
                self._icons[aid]=ic
                self.root.after(0,lambda a=aid,i=iid,ph=ic:self._update_icon(a,i,ph))

    def _update_icon(self,aid,iid,ph):
        try:
            if self.gt.exists(iid):
                self.gt.item(iid,image=ph)
        except: pass

    def _settings(self):
        dlg=tk.Toplevel(self.root); dlg.title("设置"); dlg.geometry("560x520"); dlg.configure(bg=CARD)
        dlg.transient(self.root); dlg.grab_set(); dlg.resizable(False,False)
        self.root.update_idletasks()
        dlg.geometry(f"+{self.root.winfo_x()+(self.root.winfo_width()-560)//2}+{self.root.winfo_y()+(self.root.winfo_height()-520)//2}")
        tk.Label(dlg,text="Steam Web API Key",bg=CARD,fg=TEXT,font=("Microsoft YaHei UI",12,"bold")).pack(anchor="w",padx=20,pady=(18,2))
        tk.Label(dlg,text="https://steamcommunity.com/dev/apikey 免费申请",bg=CARD,fg=MUTED,font=FONT_S).pack(anchor="w",padx=20)
        kv=tk.StringVar(value=self.config.get("api_key",""))
        ttk.Entry(dlg,textvariable=kv,width=60).pack(anchor="w",padx=20,pady=(6,12))

        en=tk.BooleanVar(value=self.config.get("goal_enabled",False))
        ttk.Checkbutton(dlg,text="启用每日限时（显示进度环）",variable=en).pack(anchor="w",padx=20,pady=(0,4))
        tk.Label(dlg,text="每日游戏上限（分钟）",bg=CARD,fg=TEXT,font=("Microsoft YaHei UI",12,"bold")).pack(anchor="w",padx=20)
        gv=tk.StringVar(value=str(self.config.get("daily_goal_min",120)))
        ttk.Entry(dlg,textvariable=gv,width=10).pack(anchor="w",padx=20,pady=(6,12))

        # 背景图
        tk.Label(dlg,text="背景图片",bg=CARD,fg=TEXT,font=("Microsoft YaHei UI",12,"bold")).pack(anchor="w",padx=20,pady=(4,0))
        bgi=tk.StringVar(value=self.config.get("bg_image",""))
        brow=tk.Frame(dlg,bg=CARD); brow.pack(anchor="w",padx=20,pady=(6,12))
        def pick():
            p=filedialog.askopenfilename(filetypes=[("图片","*.jpg *.jpeg *.png *.bmp")])
            if p: bgi.set(p)
        ttk.Button(brow,text="选择图片",command=pick).pack(side="left",padx=(0,6))
        tk.Label(brow,text=bgi.get() or "（无，使用纯色背景）",bg=CARD,fg=MUTED,font=FONT_S).pack(side="left")
        def clear_bg(): bgi.set("")
        ttk.Button(brow,text="清除",command=clear_bg).pack(side="left",padx=6)

        msg=tk.Label(dlg,text="",bg=CARD,fg=GREEN,font=FONT_S); msg.pack(anchor="w",padx=20)
        def save():
            try: g=int(gv.get())
            except: g=120
            self.config["api_key"]=kv.get().strip(); self.config["daily_goal_min"]=g
            self.config["goal_enabled"]=bool(en.get())
            self.config["bg_image"]=bgi.get().strip()
            save_config(self.config)
            self._apply_goal_visibility(); self._load_bg_image()
            msg.config(text="已保存"); dlg.after(800,dlg.destroy)
        bf=tk.Frame(dlg,bg=CARD); bf.pack(pady=10)
        ttk.Button(bf,text="保存",command=save).pack(side="left",padx=6)
        ttk.Button(bf,text="取消",command=dlg.destroy).pack(side="left",padx=6)

    def _sync(self):
        key=self.config.get("api_key","").strip()
        if not key: messagebox.showinfo("需要 Key","先在设置里填 API Key"); return
        s64=sid32_to_64(self.selected)
        if not s64: return
        self.sb.config(text="同步中…",state="disabled"); self.root.update()
        def work():
            try:
                owned=api_owned(key,s64)
                self.data.setdefault("api_cache",{})[self.selected]=owned
                save_data(self.data)
                self.root.after(0,lambda:(self._load_games(),self.sb.config(text="☁ 同步",state="normal"),
                    messagebox.showinfo("完成",f"已同步 {len(owned)} 个游戏")))
            except Exception as e:
                self.root.after(0,lambda:(self.sb.config(text="☁ 同步",state="normal"),messagebox.showerror("失败",str(e))))
        threading.Thread(target=work,daemon=True).start()

    def _add(self):
        d=self._daily(); d[today()]=d.get(today(),0)+1800; save_data(self.data); self._tick()

def main():
    root=tk.Tk(); App(root); root.mainloop()
if __name__=="__main__": main()
