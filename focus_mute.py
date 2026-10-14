# focus_mute.py
# Windows 전용 / Python 3.10+ 권장
# 의존성: psutil pycaw pywin32 comtypes pystray pillow
# 설치: python -m pip install psutil pycaw pywin32 comtypes pystray pillow

import os, sys, time, threading, configparser
import psutil

import tkinter as tk
from tkinter import ttk, messagebox

# Win32 (포커스/윈도우)
import win32gui
import win32process
import win32con

# 오디오 세션
from pycaw.pycaw import AudioUtilities
import comtypes

# 트레이
import pystray
from PIL import Image, ImageTk, ImageDraw

# 자동 시작(레지스트리)
import winreg

APP_TITLE = "Focus Mute"
POLL_MS = 200  # 감시주기(ms)

AUTOSTART_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_VALUE_NAME = "FocusMute"

# ---------------- COM 초기화 (스레드별) ----------------
_tls = threading.local()

def ensure_com_initialized():
    """pycaw 호출 전에 각 스레드에서 반드시 호출"""
    if getattr(_tls, "com_inited", False):
        return
    try:
        comtypes.CoInitialize()   # 스레드별 COM 초기화
        _tls.com_inited = True
    except Exception:
        pass

# ---------------- 경로/INI ----------------
def app_dir():
    if getattr(sys, 'frozen', False):  # PyInstaller 단일 exe
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

INI_PATH = os.path.join(app_dir(), "focusmute.ini")

def read_cfg():
    cfg = configparser.ConfigParser()
    if os.path.exists(INI_PATH):
        cfg.read(INI_PATH, encoding="utf-8")
    return cfg

def write_cfg(cfg: configparser.ConfigParser):
    with open(INI_PATH, "w", encoding="utf-8") as f:
        cfg.write(f)

def ensure_ini_exists():
    cfg = read_cfg()
    if "watch" not in cfg:
        cfg["watch"] = {"apps": ""}  # 쉼표 구분 exe 목록
    if "options" not in cfg:
        cfg["options"] = {"autostart": "0", "start_in_tray": "0"}
    write_cfg(cfg)

def load_watch():
    ensure_ini_exists()
    cfg = read_cfg()
    apps = cfg.get("watch", "apps", fallback="")
    vals = [x.strip().lower() for x in apps.split(",") if x.strip()]
    return set(vals)

def save_watch(s):
    ensure_ini_exists()
    cfg = read_cfg()
    cfg["watch"] = {"apps": ",".join(sorted(s))}
    # options 섹션은 유지
    if "options" not in cfg:
        cfg["options"] = {"autostart": "0", "start_in_tray": "0"}
    write_cfg(cfg)

def load_options():
    ensure_ini_exists()
    cfg = read_cfg()
    autostart = cfg.getboolean("options", "autostart", fallback=False)
    start_in_tray = cfg.getboolean("options", "start_in_tray", fallback=False)
    return autostart, start_in_tray

def save_options(autostart: bool, start_in_tray: bool):
    ensure_ini_exists()
    cfg = read_cfg()
    if "options" not in cfg:
        cfg["options"] = {}
    cfg["options"]["autostart"] = "1" if autostart else "0"
    cfg["options"]["start_in_tray"] = "1" if start_in_tray else "0"
    write_cfg(cfg)

# ---------------- 포커스/윈도우 ----------------
def get_foreground_hwnd():
    try:
        return win32gui.GetForegroundWindow()
    except Exception:
        return None

def get_hwnd_pid(hwnd):
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid
    except Exception:
        return None

def get_process_name_by_pid(pid):
    try:
        return psutil.Process(pid).name().lower()
    except Exception:
        return None

def get_foreground_exe():
    hwnd = get_foreground_hwnd()
    if not hwnd:
        return None
    pid = get_hwnd_pid(hwnd)
    if not pid:
        return None
    return get_process_name_by_pid(pid)

def enum_top_windows_for_pid(pid):
    results = []
    def cb(hwnd, _):
        try:
            if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindow(hwnd, win32con.GW_OWNER) == 0:
                _, p = win32process.GetWindowThreadProcessId(hwnd)
                if p == pid:
                    results.append(hwnd)
        except Exception:
            pass
        return True
    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass
    return results

def is_iconic(hwnd):
    try:
        return win32gui.IsIconic(hwnd)
    except Exception:
        return False

def is_exe_foreground(exe_name: str) -> bool:
    exe_name = (exe_name or "").lower()
    try:
        pids = [p.pid for p in psutil.process_iter(["name"]) if (p.info.get("name") or "").lower() == exe_name]
        if not pids:
            return False
        fg = get_foreground_hwnd()
        if not fg:
            return False
        fg_pid = get_hwnd_pid(fg)
        if not fg_pid:
            return False
        if fg_pid in pids:
            return True
        for pid in pids:
            for hwnd in enum_top_windows_for_pid(pid):
                if hwnd == fg:
                    return True
        return False
    except Exception:
        return False

# ---------------- 오디오 제어 ----------------
def set_app_mute_by_name(exe_name: str, mute: bool) -> bool:
    ensure_com_initialized()  # COM 보장
    exe_name = (exe_name or "").lower()
    ok = False
    for s in AudioUtilities.GetAllSessions():
        try:
            proc = s.Process
            if proc and (proc.name() or "").lower() == exe_name:
                vol = s.SimpleAudioVolume
                if vol.GetMute() != mute:
                    vol.SetMute(mute, None)
                ok = True
        except Exception:
            pass
    return ok

# ---------------- 트레이 아이콘 ----------------
def build_tray_icon_image():
    with Image.open(resource_path("assets/icon.png")) as img:
        return img.convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)

def resource_path(name):
    return os.path.join(getattr(sys, "_MEIPASS", app_dir()), name)

def style_ui(root):
    root.configure(bg="#F5F7FA")
    root.iconbitmap(resource_path("icon.ico"))
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TFrame", background="#F5F7FA")
    style.configure("TLabel", background="#F5F7FA", foreground="#223746", font=("맑은 고딕", 10))
    style.configure("Title.TLabel", font=("Segoe UI", 21, "bold"), foreground="#142D3B")
    style.configure("Muted.TLabel", foreground="#71808C", font=("맑은 고딕", 9))
    style.configure("Section.TLabel", font=("맑은 고딕", 10, "bold"))
    style.configure("TButton", font=("맑은 고딕", 10), padding=(12, 8), background="#FFFFFF", foreground="#344956", borderwidth=1, relief="flat")
    style.map("TButton", background=[("active", "#E8EEF2"), ("pressed", "#DAE4EA")])
    style.configure("Accent.TButton", background="#147F73", foreground="#FFFFFF", borderwidth=0)
    style.map("Accent.TButton", background=[("active", "#106D63"), ("pressed", "#0D5D55")])
    style.configure("TCheckbutton", font=("맑은 고딕", 10), background="#F5F7FA", foreground="#455966", padding=(0, 4))
    style.map("TCheckbutton", background=[("active", "#F5F7FA")])
    style.configure("TEntry", padding=8, fieldbackground="#FFFFFF", bordercolor="#DDE5EB")
    style.configure("TSeparator", background="#DFE6EC")
    style.configure("Vertical.TScrollbar", background="#D8E2E8", troughcolor="#F5F7FA", borderwidth=0, arrowsize=12, arrowcolor="#71808C")
    # Image elements keep native ttk mouse, keyboard, and variable behavior.
    root._theme_images = []
    def shape(size, fill, outline=None, radius=4, checked=False):
        scale = 4
        w, h = size
        img = Image.new("RGBA", (w * scale, h * scale))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((2, 2, w * scale - 3, h * scale - 3), radius=radius * scale,
                               fill=fill, outline=outline, width=scale)
        if checked:
            draw.line([(4 * scale, 9 * scale), (7 * scale, 12 * scale), (13 * scale, 5 * scale)],
                      fill="#FFFFFF", width=2 * scale, joint="curve")
        photo = ImageTk.PhotoImage(img.resize(size, Image.Resampling.LANCZOS))
        root._theme_images.append(photo)
        return photo

    for name, colors, border in [("Minimal", ("#FFFFFF", "#EAF0F4", "#DFE8EE"), "#E0E7ED"),
                                  ("Accent", ("#147F73", "#106D63", "#0D5D55"), None)]:
        normal, hover, pressed = [shape((32, 32), color, border) for color in colors]
        focused = shape((32, 32), colors[0], "#53B5A6")
        element = name + ".Button.surface"
        style.element_create(element, "image", normal, ("pressed", pressed), ("active", hover),
                             ("focus", focused), border=6, sticky="nsew")
        button_style = "TButton" if name == "Minimal" else "Accent.TButton"
        style.layout(button_style, [(element, {"sticky": "nsew", "children": [
            ("Button.padding", {"sticky": "nsew", "children": [
                ("Button.label", {"sticky": "nsew"})]})]})])

    unchecked = shape((18, 18), "#FFFFFF", "#B7C7D1", radius=3)
    check_hover = shape((18, 18), "#EEF8F5", "#147F73", radius=3)
    checked = shape((18, 18), "#147F73", radius=3, checked=True)
    check_focus = shape((18, 18), "#FFFFFF", "#147F73", radius=3)
    style.element_create("Minimal.Check.indicator", "image", unchecked,
                         ("selected", checked), ("active", check_hover), ("focus", check_focus),
                         width=24, sticky="w")
    style.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nsew", "children": [
        ("Minimal.Check.indicator", {"side": "left", "sticky": "w"}),
        ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
            ("Checkbutton.label", {"sticky": "w"})]})]})])
    thumb = shape((10, 28), "#C5D1DA", radius=5)
    thumb_hover = shape((10, 28), "#9CAFBF", radius=5)
    track = shape((10, 28), "#F5F7FA", radius=5)
    style.element_create("Minimal.Scroll.thumb", "image", thumb,
                         ("pressed", thumb_hover), ("active", thumb_hover), border=5, sticky="nsew")
    style.element_create("Minimal.Scroll.trough", "image", track, border=5, sticky="nsew")
    style.layout("Vertical.TScrollbar", [("Minimal.Scroll.trough", {"sticky": "ns", "children": [
        ("Minimal.Scroll.thumb", {"sticky": "nsew"})]})])

def clean_listbox(master, **kwargs):
    return tk.Listbox(master, bg="#FFFFFF", fg="#223746", bd=0,
                      highlightthickness=1, highlightbackground="#DFE6EC",
                      highlightcolor="#147F73", selectbackground="#DCF3EE",
                      selectforeground="#12665D", activestyle="none",
                      font=("Segoe UI", 11), exportselection=False, **kwargs)

class ProcPicker(tk.Toplevel):
    def __init__(self, master, items: list[str]):
        super().__init__(master)
        self.title("앱 추가")
        self.configure(bg="#F5F7FA")
        self.iconbitmap(resource_path("icon.ico"))
        self.geometry("400x480")
        self.minsize(360, 400)
        self.transient(master)
        self.grab_set()

        ttk.Label(self, text="실행 중인 앱", style="Section.TLabel").pack(anchor="w", padx=20, pady=(20, 0))
        ttk.Label(self, text="이름으로 검색하고 추가할 앱을 선택하세요.", style="Muted.TLabel").pack(anchor="w", padx=20, pady=(4, 8))
        self.var = tk.StringVar()
        self.ent = ttk.Entry(self, textvariable=self.var)
        self.ent.pack(fill=tk.X, padx=20, pady=4)
        self.ent.focus_set()

        list_frame = ttk.Frame(self)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=8)
        self.listbox = clean_listbox(list_frame)
        scroll = ttk.Scrollbar(list_frame, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        btns = ttk.Frame(self)
        btns.pack(fill=tk.X, padx=20, pady=(0, 20))
        ttk.Button(btns, text="추가", style="Accent.TButton", command=self.on_ok).pack(side=tk.LEFT)
        ttk.Button(btns, text="취소", command=self.on_cancel).pack(side=tk.RIGHT)

        self._all = sorted(items)
        self._filtered = []
        self.result = None

        self.ent.bind("<KeyRelease>", self.on_filter)
        self.listbox.bind("<Double-Button-1>", lambda e: self.on_ok())

        self.populate(self._all)

    def populate(self, arr):
        self.listbox.delete(0, tk.END)
        for it in arr:
            self.listbox.insert(tk.END, it)
        self._filtered = arr

    def on_filter(self, *_):
        q = self.var.get().lower().strip()
        if not q:
            self.populate(self._all)
            return
        arr = [x for x in self._all if q in x.lower()]
        self.populate(arr)

    def on_ok(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        text = self.listbox.get(sel[0])
        name = text.split(" (pid:")[0].strip().lower()
        if name:
            self.result = name
        self.destroy()

    def on_cancel(self):
        self.result = None
        self.destroy()

# ---------------- 자동 시작(레지스트리) ----------------
def _autostart_command() -> str:
    if getattr(sys, 'frozen', False):
        # 단일 exe
        return f'"{sys.executable}"'
    else:
        # 파이썬으로 직접 실행 중
        return f'"{sys.executable}" "{os.path.abspath(__file__)}"'

def is_autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_PATH, 0, winreg.KEY_READ) as k:
            _val, _typ = winreg.QueryValueEx(k, AUTOSTART_VALUE_NAME)
            return True
    except FileNotFoundError:
        return False
    except Exception:
        return False

def set_autostart(enable: bool) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_PATH, 0, winreg.KEY_ALL_ACCESS) as k:
            if enable:
                winreg.SetValueEx(k, AUTOSTART_VALUE_NAME, 0, winreg.REG_SZ, _autostart_command())
            else:
                try:
                    winreg.DeleteValue(k, AUTOSTART_VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception as e:
        # UI가 준비되기 전일 수도 있으니 메시지 박스는 안전하게 처리
        try:
            messagebox.showerror("자동 시작 오류", str(e))
        except Exception:
            pass
        return False

# ---------------- 메인 앱 ----------------
class AppState:
    def __init__(self):
        self.watch = load_watch()            # set of exe names
        self.stop_event = threading.Event()  # 종료 이벤트

class FocusMuteApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.state = AppState()
        self.focus_text = tk.StringVar(value="포커스: 초기화 중... | 감시 앱: 0")
        self.tray_icon = None

        # 옵션 로드 (autostart는 레지스트리 실시간 기준으로 보정)
        ini_autostart, ini_start_in_tray = load_options()
        self.var_autostart = tk.BooleanVar(value=is_autostart_enabled() or ini_autostart)
        self.var_start_tray = tk.BooleanVar(value=ini_start_in_tray)

        self.build_ui()
        # X 버튼은 '종료'
        self.root.protocol("WM_DELETE_WINDOW", self.cleanup_and_quit)
        # 최소화할 때만 트레이로
        self.root.bind("<Unmap>", self.on_minimize)

        # 실행 즉시 모니터 시작
        self.monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self.monitor_thread.start()

        # 시작 시 라벨 업데이트
        self.set_focus_label(f"포커스: {get_foreground_exe() or '없음'} | 감시 앱: {len(self.state.watch)}")

        # 옵션: 시작 시 트레이로 숨기기
        if self.var_start_tray.get():
            # Tk 초기 표시 직후 트레이로
            self.root.after(200, self.hide_to_tray)

    def build_ui(self):
        style_ui(self.root)
        self.root.geometry("480x500")
        frm = ttk.Frame(self.root, padding=24)
        frm.pack(fill=tk.BOTH, expand=True)
        header = ttk.Frame(frm)
        header.pack(fill=tk.X)
        with Image.open(resource_path("assets/icon.png")) as img:
            self.header_icon = ImageTk.PhotoImage(img.resize((48, 48), Image.Resampling.LANCZOS))
        ttk.Label(header, image=self.header_icon).pack(side=tk.LEFT, padx=(0, 12))
        titles = ttk.Frame(header)
        titles.pack(side=tk.LEFT)
        ttk.Label(titles, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(titles, text="집중하는 앱의 소리만 켜두세요.", style="Muted.TLabel").pack(anchor="w")

        section = ttk.Frame(frm)
        section.pack(fill=tk.X, pady=(24, 8))
        ttk.Label(section, text="감시할 앱", style="Section.TLabel").pack(side=tk.LEFT)
        ttk.Label(section, text="포커스를 벗어나면 자동 음소거", style="Muted.TLabel").pack(side=tk.RIGHT)
        list_frame = ttk.Frame(frm)
        list_frame.pack(fill=tk.BOTH, expand=True)
        self.listbox = clean_listbox(list_frame, height=5)
        scroll = ttk.Scrollbar(list_frame, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        for exe in sorted(self.state.watch):
            self.listbox.insert(tk.END, exe)
        btnf = ttk.Frame(frm)
        btnf.pack(fill=tk.X, pady=(10, 18))
        ttk.Button(btnf, text="＋ 앱 추가", style="Accent.TButton", command=self.add_from_running).pack(side=tk.LEFT)
        ttk.Button(btnf, text="선택 삭제", command=self.remove_selected).pack(side=tk.LEFT, padx=8)
        ttk.Button(btnf, text="저장", command=self.save_now).pack(side=tk.RIGHT)
        ttk.Separator(frm).pack(fill=tk.X)
        opts = ttk.Frame(frm)
        opts.pack(fill=tk.X, pady=(12, 12))
        chk1 = ttk.Checkbutton(opts, text="Windows 시작 시 자동 실행", variable=self.var_autostart, command=self.on_toggle_autostart)
        chk2 = ttk.Checkbutton(opts, text="실행 시 시스템 트레이로 시작", variable=self.var_start_tray, command=self.on_toggle_start_tray)
        chk1.pack(anchor="w")
        chk2.pack(anchor="w")
        footer = ttk.Frame(frm)
        footer.pack(fill=tk.X)
        ttk.Button(footer, text="세션 검사", command=self.inspect_sessions).pack(side=tk.RIGHT, padx=(8, 0))
        self.status_label = ttk.Label(footer, textvariable=self.focus_text, style="Muted.TLabel", wraplength=310)
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        footer.bind("<Configure>", lambda e: self.status_label.configure(wraplength=max(160, e.width - 110)))
        self.root.minsize(460, 460)

    # ------ UI 헬퍼 ------
    def set_focus_label(self, text: str):
        self.root.after(0, self.focus_text.set, text)

    # ------ 옵션 핸들러 ------
    def on_toggle_autostart(self):
        enabled = self.var_autostart.get()
        ok = set_autostart(enabled)
        if not ok:
            # 실패 시 체크 되돌림
            self.var_autostart.set(is_autostart_enabled())
        # INI 저장(상태 동기화)
        save_options(self.var_autostart.get(), self.var_start_tray.get())

    def on_toggle_start_tray(self):
        save_options(self.var_autostart.get(), self.var_start_tray.get())

    # ------ 실행중 프로세스 추가 ------
    def add_from_running(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("프로세스 수집 중…")
        dlg.geometry("280x110")
        ttk.Label(dlg, text="실행중 프로세스를 수집하고 있어요...").pack(expand=True, padx=12, pady=16)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.update_idletasks()

        items_box = {"items": None}

        def worker():
            names = set()
            try:
                for p in psutil.process_iter(["name", "pid"]):
                    n = (p.info.get("name") or "").strip()
                    if not n:
                        continue
                    names.add(f"{n} (pid:{p.info.get('pid')})")
            except Exception:
                pass
            items_box["items"] = sorted(names)
            self.root.after(0, on_ready)

        def on_ready():
            try:
                dlg.destroy()
            except Exception:
                pass
            items = items_box["items"] or []
            if not items:
                messagebox.showinfo("안내", "표시할 프로세스가 없어요.")
                return
            picker = ProcPicker(self.root, items)
            self.root.wait_window(picker)
            if picker.result:
                exe = picker.result
                if not exe.endswith(".exe"):
                    exe = exe + ".exe"
                if exe not in self.state.watch:
                    self.state.watch.add(exe)
                    self.listbox.insert(tk.END, exe)
                    self.set_focus_label(f"포커스: {get_foreground_exe() or '없음'} | 감시 앱: {len(self.state.watch)}")

        threading.Thread(target=worker, daemon=True).start()

    # ------ 삭제(먼저 음소거 해제) ------
    def remove_selected(self):
        indices = list(self.listbox.curselection())
        if not indices:
            return
        for idx in reversed(indices):
            exe = self.listbox.get(idx)
            # 먼저 해제 시도
            set_app_mute_by_name(exe, False)
            # 목록에서 제거
            self.listbox.delete(idx)
            self.state.watch.discard(exe)
        self.set_focus_label(f"포커스: {get_foreground_exe() or '없음'} | 감시 앱: {len(self.state.watch)}")

    def save_now(self):
        save_watch(self.state.watch)
        # 옵션도 함께 저장(체크 상태 반영)
        save_options(self.var_autostart.get(), self.var_start_tray.get())
        messagebox.showinfo("저장", f"설정이 저장됐어요.\n{INI_PATH}")

    # ------ 도구 ------
    def inspect_sessions(self):
        ensure_com_initialized()  # COM 보장
        lines = []
        for s in AudioUtilities.GetAllSessions():
            try:
                pname = (s.Process.name() or "").lower() if s.Process else "<no-process>"
            except Exception:
                pname = "<error>"
            try:
                muted = s.SimpleAudioVolume.GetMute()
            except Exception:
                muted = "?"

            star = "*" if pname in self.state.watch else " "
            lines.append(f"{star} {pname:<24} | muted={muted}")
        if not lines:
            lines = ["세션이 감지되지 않았어요."]
        msg = "\n".join(lines)
        messagebox.showinfo("세션 검사", msg)

    # ------ 모니터 루프(지속 적용) ------
    def monitor_loop(self):
        ensure_com_initialized()  # 이 스레드도 COM 초기화
        while not self.state.stop_event.is_set():
            try:
                fg_exe = get_foreground_exe()
                display_name = fg_exe if fg_exe else "없음"

                # 리스트에 저장된 각 exe: 포커스 아니면 뮤트, 포커스면 해제
                for exe in list(self.state.watch):
                    foreground = (exe == (fg_exe or "")) or is_exe_foreground(exe)

                    # 보정: exe의 최상위 창이 최소화면 무조건 뮤트
                    if foreground:
                        for pid in [p.pid for p in psutil.process_iter(["name"]) if (p.info.get("name") or "").lower() == exe]:
                            for hwnd in enum_top_windows_for_pid(pid):
                                if is_iconic(hwnd):
                                    foreground = False
                                    break
                            if not foreground:
                                break

                    set_app_mute_by_name(exe, mute=not foreground)

                self.root.after(0, self.focus_text.set, f"포커스: {display_name} | 감시 앱: {len(self.state.watch)}")
            except Exception as e:
                self.root.after(0, self.focus_text.set, f"포커스 확인 오류: {e}")
            time.sleep(POLL_MS / 1000.0)

    # ------ 트레이 (최소화시에만) ------
    def on_minimize(self, event):
        if self.root.state() == "iconic":
            self.hide_to_tray()

    def hide_to_tray(self):
        try:
            self.root.withdraw()
            # 이미 떠 있으면 재생성 방지
            if self.tray_icon is None:
                img = build_tray_icon_image()
                menu = pystray.Menu(
                    pystray.MenuItem("열기", self.tray_open, default=True),  # 더블클릭 = 열기
                    pystray.MenuItem("종료", self.tray_exit)
                )
                self.tray_icon = pystray.Icon(APP_TITLE, img, APP_TITLE, menu)
                self.tray_icon.run_detached()
        except Exception as e:
            try:
                messagebox.showerror("트레이 오류", str(e))
            except Exception:
                pass

    def tray_open(self, icon=None, item=None):
        try:
            self.root.deiconify()
            self.root.state("normal")
            self.root.lift()
            self.root.focus_force()
            if self.tray_icon:
                self.tray_icon.stop()
                self.tray_icon = None
        except Exception:
            pass

    def tray_exit(self, icon=None, item=None):
        self.cleanup_and_quit()

    # ------ 종료 ------
    def cleanup_and_quit(self):
        try:
            # 종료 전에 현재 감시 목록의 앱들 '모두 음소거 해제' (리스트는 유지)
            ensure_com_initialized()
            for exe in list(self.state.watch):
                try:
                    set_app_mute_by_name(exe, False)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            save_watch(self.state.watch)
            save_options(self.var_autostart.get(), self.var_start_tray.get())
        except Exception:
            pass
        # 트레이 정리
        try:
            if self.tray_icon:
                self.tray_icon.stop()
                self.tray_icon = None
        except Exception:
            pass
        # 스레드 종료
        self.state.stop_event.set()
        # 창 파괴
        self.root.after(50, self.root.destroy)

# ---------------- main ----------------
def main():
    ensure_ini_exists()
    # DPI 보정(있으면 적용)
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = tk.Tk()
    app = FocusMuteApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
