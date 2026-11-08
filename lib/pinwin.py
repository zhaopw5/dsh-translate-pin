#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DSH 划词/截图翻译 —— 桌面置顶工具。

职责：
  * 桌面悬浮「译」按钮 + 全局快捷键（Ctrl+Shift+S），DSH 最小化也能用；
  * 全屏框选截图；
  * 把译文直接贴在刚才框选的屏幕区域上（原位置、原大小，可拖动/复制/查看原文）；
  * 文本划词结果的置顶小窗（原来的「钉在桌面」）。

由 DSH 宿主插件（dsh-translate-pin）以子进程方式启动，通过 stdin/stdout 逐行 JSON 通信。

宿主 → 助手：
  {"id":"1","cmd":"capture"}               进入框选（完成后回 id）
  {"id":"2","cmd":"place","rect":{...},"translated":"...","original":"..."}
  {"id":"3","cmd":"loading","rect":{...}}
  {"id":"4","cmd":"notify","message":"..."}
  {"id":"5","cmd":"show","translated":...,"original":...}   文本置顶小窗
  {"id":"6","cmd":"close_all"} / {"cmd":"shutdown"} / {"cmd":"ping"}
  {"cmd":"capture_rect","x":..,"y":..,"width":..,"height":..}  调试
  {"cmd":"show_pill"} / {"cmd":"hide_pill"}

助手 → 宿主：
  {"id":"1","ok":true,"path":"...","rect":{"x":..,"y":..,"w":..,"h":..}}
  {"event":"shot","path":"...","rect":{...}}   用户从桌面浮标/快捷键发起
"""

import ctypes
import ctypes.wintypes
import json
import math
import os
import queue
import re
import sys
import tempfile
import threading
import time
import traceback

for stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import tkinter as tk
from tkinter import font as tkfont

try:
    from PIL import Image, ImageDraw, ImageFont, ImageGrab, ImageTk
except Exception:  # pragma: no cover
    Image = ImageDraw = ImageFont = ImageTk = None
    ImageGrab = None

# ---------------------------------------------------------------- 外观
CARD = "#1b1c21"
CARD_HOVER = "#262832"
BORDER = "#35363f"
TEXT = "#eceef3"
MUTED = "#9b9da7"
FAINT = "#6f717b"
ACCENT = "#5b7cfa"
BORDER_SOFT = "#8b96ad"
BORDER_SOFT_HOVER = "#6f7d99"
BORDER_OUTER = "#9aa0aa"
BORDER_INNER = "#d9dce2"
BORDER_WIDTH = 3
BORDER_RADIUS = 0
DANGER = "#ff6b6b"
TRANSPARENT = "#ff00fe"

FONT_FAMILY = "Microsoft YaHei UI"
WIN_W = 430
PAD = 14
MAX_LINES = 15

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
WM_HOTKEY = 0x0312
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
VK_S = 0x53
VK_T = 0x54


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.GetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.restype = ctypes.c_void_p
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalAlloc.restype = ctypes.c_void_p


def log(msg):
    try:
        sys.stderr.write("[pinwin] %s\n" % msg)
        sys.stderr.flush()
    except Exception:
        pass


def virtual_screen():
    try:
        u = ctypes.windll.user32
        return (
            u.GetSystemMetrics(SM_XVIRTUALSCREEN),
            u.GetSystemMetrics(SM_YVIRTUALSCREEN),
            u.GetSystemMetrics(SM_CXVIRTUALSCREEN),
            u.GetSystemMetrics(SM_CYVIRTUALSCREEN),
        )
    except Exception:
        return (0, 0, 1280, 800)


def cursor_pos():
    try:
        pt = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return (pt.x, pt.y)
    except Exception:
        return (0, 0)


def _monitor_work_area(hmon):
    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                    ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
        r = info.rcWork
        return (r.left, r.top, r.right - r.left, r.bottom - r.top)
    return None


def monitor_work_area_at(x, y):
    try:
        MONITOR_DEFAULTTONEAREST = 2
        hmon = ctypes.windll.user32.MonitorFromPoint(
            ctypes.wintypes.POINT(int(x), int(y)), MONITOR_DEFAULTTONEAREST)
        found = _monitor_work_area(hmon)
        if found:
            return found
    except Exception:
        pass
    return (0, 0) + virtual_screen()[2:]


def monitor_work_area_at_window():
    """当前前台窗口所在显示器的工作区（点「钉住」时就是 DSH 窗口）。"""
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if hwnd:
            MONITOR_DEFAULTTONEAREST = 2
            hmon = ctypes.windll.user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
            found = _monitor_work_area(hmon)
            if found:
                return found
    except Exception:
        pass
    return monitor_work_area_at(*cursor_pos())


def foreground_pid():
    try:
        hwnd = user32.GetForegroundWindow()
        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0


def process_image_name(pid):
    try:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value or ""
            return ""
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return ""


CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def clipboard_get_text():
    try:
        opened = False
        for _ in range(6):
            if user32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.05)
        if not opened:
            return ""
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            ptr = kernel32.GlobalLock(ctypes.c_void_p(handle))
            if not ptr:
                return ""
            try:
                return ctypes.cast(ctypes.c_void_p(ptr), ctypes.c_wchar_p).value or ""
            finally:
                kernel32.GlobalUnlock(ctypes.c_void_p(handle))
        finally:
            user32.CloseClipboard()
    except Exception:
        return ""


def clipboard_set_text(text):
    try:
        data = str(text).encode("utf-16-le") + b"\x00\x00"
        if not user32.OpenClipboard(None):
            return
        try:
            user32.EmptyClipboard()
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not handle:
                return
            ptr = kernel32.GlobalLock(ctypes.c_void_p(handle))
            if not ptr:
                return
            try:
                ctypes.memmove(ptr, data, len(data))
            finally:
                kernel32.GlobalUnlock(ctypes.c_void_p(handle))
            user32.SetClipboardData(CF_UNICODETEXT, handle)
        finally:
            user32.CloseClipboard()
    except Exception:
        pass


def capture_selection():
    """复制当前前台窗口的选区；读不到返回空串，并尽量恢复剪贴板文本。"""
    saved = clipboard_get_text()
    try:
        clipboard_set_text("")
        user32.keybd_event(0x11, 0, 0, 0)          # Ctrl down
        user32.keybd_event(0x43, 0, 0, 0)          # C down
        user32.keybd_event(0x43, 0, 0x0002, 0)     # C up
        user32.keybd_event(0x11, 0, 0x0002, 0)     # Ctrl up
        time.sleep(0.14)
        text = clipboard_get_text().strip()
    except Exception:
        text = ""
    finally:
        try:
            clipboard_set_text(saved)
        except Exception:
            pass
    return text


def rounded_rect(canvas, x1, y1, x2, y2, radius, **kwargs):
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def clean_lang(tag):
    if not tag:
        return ""
    table = {
        "zh-hans": "简体中文", "zh-cn": "简体中文", "zh": "中文",
        "zh-hant": "繁體中文", "zh-tw": "繁體中文", "zh-hk": "繁體中文",
        "en": "英语", "ja": "日语", "ko": "韩语", "fr": "法语", "de": "德语",
        "es": "西班牙语", "ru": "俄语", "it": "意大利语", "pt": "葡萄牙语",
        "ar": "阿拉伯语", "auto": "自动",
    }
    return table.get(str(tag).lower(), str(tag))


def state_dir():
    home = os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh")
    path = os.path.join(home, "translate-pin")
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        path = os.path.join(tempfile.gettempdir(), "dsh-translate-pin")
        os.makedirs(path, exist_ok=True)
    return path


def load_json(name, fallback=None):
    try:
        with open(os.path.join(state_dir(), name), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return fallback


def save_json(name, value):
    try:
        with open(os.path.join(state_dir(), name), "w", encoding="utf-8") as handle:
            json.dump(value, handle)
    except Exception:
        pass


class FlatButton(tk.Label):
    """Label 模拟扁平按钮。"""

    def __init__(self, master, text, command, fg=TEXT, bg=CARD, hover=CARD_HOVER,
                 padx=8, pady=3, font=None):
        super().__init__(master, text=text, fg=fg, bg=bg, padx=padx, pady=pady,
                         cursor="hand2", font=font or (FONT_FAMILY, 9))
        self._bg = bg
        self._hover = hover
        self._command = command
        self.bind("<Enter>", lambda e: self.configure(bg=self._hover))
        self.bind("<Leave>", lambda e: self.configure(bg=self._bg))
        self.bind("<Button-1>", lambda e: self._command and self._command())

    def set_text(self, text):
        self.configure(text=text)


# ---------------------------------------------------------------- 文本置顶小窗
class PinWindow:
    def __init__(self, app, payload, index):
        self.app = app
        self.payload = payload
        light = bool(payload.get("light"))
        self.c_card = "#ffffff" if light else CARD
        self.c_hover = "#f3f4f6" if light else CARD_HOVER
        self.c_border = "#c2cad8" if light else BORDER
        self.c_text = "#1f2328" if light else TEXT
        self.c_muted = "#5b6470" if light else MUTED
        self.c_faint = "#8a93a0" if light else FAINT
        self.closed = False
        self._drag = None

        translated = (payload.get("translated") or "").strip()
        original = (payload.get("original") or "").strip()
        source = clean_lang(payload.get("source"))
        target = clean_lang(payload.get("target")) or "简体中文"
        provider = payload.get("provider") or ""
        kind = payload.get("kind") or "text"

        self.top = tk.Toplevel(app.root)
        self.top.withdraw()
        self.top.overrideredirect(True)
        self.top.configure(bg=TRANSPARENT)
        try:
            self.top.wm_attributes("-transparentcolor", TRANSPARENT)
        except Exception:
            self.top.configure(bg=self.c_card)
        self.top.attributes("-topmost", True)

        self.f_title = tkfont.Font(family=FONT_FAMILY, size=9, weight="bold")
        self.f_body = tkfont.Font(family=FONT_FAMILY, size=11)
        self.f_small = tkfont.Font(family=FONT_FAMILY, size=9)

        content_w = WIN_W - PAD * 2
        body_lines = self._estimate_lines(translated, content_w, self.f_body)
        body_h = max(3, body_lines) * 21 + 6
        header_h = 30
        footer_h = 34
        total_h = 10 + header_h + 8 + body_h + 8 + footer_h + 10

        vx, vy, vw, vh = monitor_work_area_at_window()
        self.work = (vx, vy, vw, vh)
        self.index = index
        x = vx + vw - WIN_W - 18
        y = vy + 96 + (index % 7) * 34
        if y + total_h > vy + vh - 10:
            y = vy + vh - total_h - 10
        x = max(vx + 8, x)
        near = payload.get("near")
        if isinstance(near, dict):
            try:
                x = int(near.get("x", x)) + 14
                y = int(near.get("y", y)) + 18
            except Exception:
                pass
            x = max(vx + 8, min(x, vx + vw - WIN_W - 8))
            y = max(vy + 8, min(y, vy + vh - total_h - 8))

        self.top.geometry("%dx%d+%d+%d" % (WIN_W, total_h, x, y))
        self.base_total = total_h
        self.canvas = tk.Canvas(self.top, width=WIN_W, height=total_h,
                                bg=TRANSPARENT, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        if light:
            # 浅色卡片用普通矩形边框，避免圆角 smooth 多边形在左上角留下斜线。
            self.card = self.canvas.create_rectangle(
                1, 1, WIN_W - 1, total_h - 1,
                fill=self.c_card, outline=self.c_border, width=2)
        else:
            self.card = rounded_rect(self.canvas, 1, 1, WIN_W - 1, total_h - 1, 12,
                                     fill=self.c_card, outline=self.c_border, width=1)

        body = tk.Frame(self.top, bg=self.c_card)
        self.body = body
        self.body_item = self.canvas.create_window(PAD, 10, anchor="nw", window=body,
                                                   width=content_w, height=total_h - 20)

        head = tk.Frame(body, bg=self.c_card, height=header_h)
        head.pack(fill="x", side="top")
        title = tk.Label(head, text="截图翻译" if kind == "screenshot" else "划词翻译",
                         bg=self.c_card, fg=self.c_text, font=self.f_title)
        title.pack(side="left")
        lang = tk.Label(head, text="%s → %s" % (source or "自动", target),
                        bg=self.c_card, fg=self.c_muted, font=self.f_small)
        lang.pack(side="left", padx=(8, 0))
        if provider:
            tk.Label(head, text=provider, bg=self.c_card, fg=self.c_faint,
                     font=self.f_small).pack(side="left", padx=(8, 0))

        close = FlatButton(head, "×", self.close, fg=self.c_muted, bg=self.c_card,
                           hover=DANGER, padx=6, pady=0, font=(FONT_FAMILY, 12))
        close.pack(side="right")
        self.pin_btn = FlatButton(head, "📌 置顶", self.toggle_top,
                                  fg=ACCENT, bg=self.c_card, padx=6, pady=2)
        self.pin_btn.pack(side="right", padx=(0, 4))

        for widget in (head, title, lang, close, self.pin_btn):
            widget.bind("<Button-1>", self._drag_start, add="+")
            widget.bind("<B1-Motion>", self._drag_move, add="+")

        tk.Frame(body, bg=self.c_border, height=1).pack(fill="x", pady=(6, 8))

        self.text = tk.Text(body, wrap="word", height=max(3, body_lines),
                            bg=self.c_card, fg=self.c_text, bd=0, highlightthickness=0,
                            font=self.f_body, cursor="arrow", padx=0, pady=0,
                            spacing1=2, spacing3=4, insertwidth=0,
                            selectbackground="#3a4a86", selectforeground=self.c_text)
        self.text.pack(fill="x")
        self.text.insert("1.0", translated)
        self.text.configure(state="disabled")
        self.text.bind("<MouseWheel>", self._on_wheel)
        self.text.bind("<Control-c>", lambda e: self.copy())

        self.orig_frame = None
        self.orig_btn = None
        if original:
            self.orig_frame = tk.Frame(body, bg=self.c_card)
            tk.Frame(self.orig_frame, bg=self.c_border, height=1).pack(fill="x", pady=(10, 8))
            self.orig_text = tk.Text(self.orig_frame, wrap="word", height=4,
                                     bg=self.c_card, fg=self.c_muted, bd=0, highlightthickness=0,
                                     font=self.f_small, cursor="arrow", padx=0, pady=0,
                                     spacing1=1, spacing3=2,
                                     selectbackground="#3a4a86")
            self.orig_text.pack(fill="x")
            self.orig_text.insert("1.0", original)
            self.orig_text.configure(state="disabled")
            self.orig_text.bind("<MouseWheel>", self._on_wheel)

        foot = tk.Frame(body, bg=self.c_card)
        foot.pack(fill="x", side="bottom", pady=(8, 0))
        self.copy_btn = FlatButton(foot, "复制译文", self.copy, fg=self.c_text, bg=self.c_card,
                                   hover=self.c_hover, padx=10, pady=4)
        self.copy_btn.pack(side="left")
        if original:
            self.orig_btn = FlatButton(foot, "显示原文", self.toggle_original,
                                       fg=self.c_muted, bg=self.c_card, padx=8, pady=4)
            self.orig_btn.pack(side="left", padx=(6, 0))
        self.hint = tk.Label(foot, text="可拖动 · 始终置顶", bg=self.c_card, fg=self.c_faint,
                             font=self.f_small)
        self.hint.pack(side="right")

        self.top.bind("<Escape>", lambda e: self.close())
        self.top.bind("<Control-w>", lambda e: self.close())
        self.top.deiconify()
        self.top.update_idletasks()
        self._fit_text()
        self.top.lift()

    def _fit_text(self):
        lines = self._wrap_lines(self.payload.get("translated") or "", WIN_W - PAD * 2 - 4, self.f_body)
        lines = max(3, min(MAX_LINES, lines))
        self.text.configure(height=lines)
        self.top.update_idletasks()
        total = 108 + self.text.winfo_reqheight()
        self.base_total = total
        x = self.top.winfo_x()
        vx, vy, vw, vh = self.work
        near = self.payload.get("near")
        if isinstance(near, dict):
            try:
                y = int(near.get("y", 0)) + 18
            except Exception:
                y = vy + 96
            y = max(vy + 8, min(y, vy + vh - total - 8))
        else:
            y = vy + 96 + (self.index % 7) * 34
            if y + total > vy + vh - 10:
                y = vy + vh - total - 10
            y = max(vy + 8, y)
        self.top.geometry("%dx%d+%d+%d" % (WIN_W, total, x, y))
        self._resize(total)

    @staticmethod
    def _wrap_lines(text, width, font):
        if not text:
            return 3
        lines, cur = 1, 0.0
        for ch in text:
            if ch == "\n":
                lines += 1
                cur = 0.0
                continue
            w = font.measure(ch)
            if cur + w > width:
                lines += 1
                cur = w
            else:
                cur += w
        return lines

    def _estimate_lines(self, text, width, font):
        if not text:
            return 3
        sample = text[:80]
        try:
            avg = max(6.0, font.measure(sample) / max(1, len(sample)))
        except Exception:
            avg = 8.0
        per_line = max(10, int(width / avg))
        lines = 0
        for paragraph in text.split("\n"):
            lines += max(1, math.ceil(len(paragraph) / per_line))
        return min(MAX_LINES, max(3, lines))

    def _on_wheel(self, event):
        delta = -1 if event.delta > 0 else 1
        try:
            event.widget.yview_scroll(delta * 3, "units")
        except Exception:
            pass
        return "break"

    def _drag_start(self, event):
        self._drag = (event.x_root, event.y_root,
                      self.top.winfo_x(), self.top.winfo_y())

    def _drag_move(self, event):
        if not self._drag:
            return
        sx, sy, wx, wy = self._drag
        self.top.geometry("+%d+%d" % (wx + event.x_root - sx, wy + event.y_root - sy))

    def copy(self):
        text = self.payload.get("translated") or ""
        try:
            if self.text.tag_ranges("sel"):
                text = self.text.get("sel.first", "sel.last")
        except Exception:
            pass
        try:
            self.top.clipboard_clear()
            self.top.clipboard_append(text)
            self.hint.configure(text="已复制 ✓", fg=ACCENT)
            self.top.after(1400, lambda: self.hint.configure(text="可拖动 · 始终置顶", fg=self.c_faint))
        except Exception:
            pass

    def toggle_original(self):
        if self.orig_frame is None:
            return
        if self.orig_frame.winfo_manager():
            self.orig_frame.pack_forget()
            self.orig_btn.set_text("显示原文")
            self._resize(self.base_total)
        else:
            self.orig_frame.pack(fill="x", before=self.copy_btn.master)
            self.orig_btn.set_text("隐藏原文")
            extra = self.orig_text.winfo_reqheight() + 26
            self._resize(self.base_total + extra)

    def _resize(self, total):
        total = max(150, min(760, int(total)))
        self.top.geometry("%dx%d" % (WIN_W, total))
        self.canvas.configure(height=total)
        self.canvas.coords(self.card, 1, 1, WIN_W - 1, total - 1)
        self.canvas.tag_lower(self.card)
        self.canvas.itemconfigure(self.body_item, height=total - PAD - 10)
        self.body.configure(height=total - PAD - 10)

    def toggle_top(self):
        topmost = bool(self.top.attributes("-topmost"))
        self.top.attributes("-topmost", not topmost)
        self.pin_btn.set_text("📌 已取消" if topmost else "📌 置顶")
        self.pin_btn.configure(fg=self.c_muted if topmost else ACCENT)

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.top.destroy()
        except Exception:
            pass
        self.app.windows.discard(self)


# ---------------------------------------------------------------- 原地译文窗
class InPlaceWindow:
    """把译文直接合成到截图上：抹掉原文区域，用相近字号画上译文，窗口就是原截图本身。"""

    def __init__(self, app, payload):
        self.app = app
        self.payload = payload
        self.closed = False
        self._drag = None
        self._moved = False
        self.showing_original = False

        rect = payload.get("rect") or {}
        x = int(rect.get("x", 0))
        y = int(rect.get("y", 0))
        w = max(int(rect.get("w", 0)), 40)
        h = max(int(rect.get("h", 0)), 30)

        image_path = payload.get("imagePath")
        self.crop = None
        if image_path and os.path.exists(image_path):
            try:
                self.crop = Image.open(image_path).convert("RGB")
            except Exception:
                self.crop = None
        if self.crop is None:
            self.crop = Image.new("RGB", (w, h), (255, 255, 255))
        w, h = self.crop.size

        self.bg, self.fg = self._sample_colors(self.crop)
        raw_lines = payload.get("lines") or []
        raw_blocks = payload.get("blocks") or []
        raw_table = (payload.get("table") or {}).get("cells") or []
        table_mode = bool(raw_table) and any(
            isinstance(item, dict) and str(item.get("translated") or "").strip()
            for item in raw_table)
        block_mode = bool(raw_blocks) and any(
            isinstance(item, dict) and str(item.get("translated") or "").strip()
            for item in raw_blocks)
        line_mode = bool(raw_lines) and any(
            isinstance(item, dict) and str(item.get("translated") or "").strip()
            for item in raw_lines)
        if table_mode:
            base, original_img = self._compose_table(raw_table, w, h)
        elif block_mode:
            base, original_img = self._compose_blocks(raw_blocks, w, h)
        elif line_mode:
            base, original_img = self._compose_lines(raw_lines, w, h)
        else:
            line_rects = self._line_rects(raw_lines, w, h)
            boxes = self._text_boxes(raw_lines, w, h)
            bx0, by0, bx1, by1 = boxes
            translated = (payload.get("translated") or "").strip()
            original_text = (payload.get("original") or "").strip()
            font_path = self._font_path(translated or original_text)
            line_heights = [int(item.get("h", 0)) for item in raw_lines
                            if isinstance(item, dict) and int(item.get("h", 0)) > 0]
            if line_heights:
                line_heights.sort()
                base_size = line_heights[len(line_heights) // 2]
            else:
                base_size = max(12, (by1 - by0) // 3)
            start_size = max(10, min(40, int(base_size * 1.08)))
            base, original_img, _ = self._compose(
                translated, original_text, boxes, line_rects, font_path, start_size, w, h)
        self.image_translated = self._decorate(base)
        self.image_original = self._decorate(original_img)
        self.window_w, self.window_h = self.image_translated.size

        # ---- 窗口（圆角 + 浅蓝渐变边框，画进图片里；圆角外透出桌面） ----
        self.top = tk.Toplevel(app.root)
        self.top.withdraw()
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.configure(bg=TRANSPARENT)
        try:
            self.top.wm_attributes("-transparentcolor", TRANSPARENT)
        except Exception:
            pass
        self.top.geometry("%dx%d+%d+%d" % (self.window_w, self.window_h,
                                          x - BORDER_WIDTH, y - BORDER_WIDTH))

        self.canvas = tk.Canvas(self.top, width=self.window_w, height=self.window_h,
                                highlightthickness=0, bd=0, bg=TRANSPARENT, cursor="fleur")
        self.canvas.pack(fill="both", expand=True)

        self.photo_translated = ImageTk.PhotoImage(self.image_translated)
        self.photo_original = ImageTk.PhotoImage(self.image_original)
        self.image_item = self.canvas.create_image(0, 0, anchor="nw", image=self.photo_translated)

        # 悬停时才出现的工具条（框外）：沿用译文框的底色、文字色和灰色描边
        self.pinned = True
        self._hide_toolbar_job = None
        tb_bg = self._hex(self.bg)
        tb_fg = self._hex(self.fg)
        self._tb_hover = self._hex(self._mix(self.bg, self.fg, 0.10))
        self._tb_pin_on = tb_fg
        self._tb_pin_off = self._hex(self._mix(self.bg, self.fg, 0.45))
        self.toolbar = tk.Toplevel(app.root)
        self.toolbar.withdraw()
        self.toolbar.overrideredirect(True)
        self.toolbar.attributes("-topmost", True)
        self.toolbar.configure(bg="#9aa0aa")
        inner = tk.Frame(self.toolbar, bg=tb_bg)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.pin_btn = tk.Label(inner, text="📌", bg=tb_bg, fg=self._tb_pin_on,
                                padx=9, pady=5, font=(FONT_FAMILY, 11), cursor="hand2")
        self.pin_btn.pack(side="left")
        self.pin_btn.bind("<Enter>", lambda e: self.pin_btn.configure(bg=self._tb_hover))
        self.pin_btn.bind("<Leave>", lambda e: self.pin_btn.configure(bg=tb_bg))
        self.pin_btn.bind("<Button-1>", lambda e: self.toggle_pin())
        sep = tk.Frame(inner, bg=self._hex(self._mix(self.bg, self.fg, 0.22)), width=1)
        sep.pack(side="left", fill="y", padx=(2, 3), pady=5)
        for text, command in (("复制", self.copy), ("原文", self.toggle), ("×", self.close)):
            label = tk.Label(inner, text=text, bg=tb_bg, fg=tb_fg,
                             padx=10, pady=5, font=(FONT_FAMILY, 10), cursor="hand2")
            label.pack(side="left")
            label.bind("<Enter>", lambda e, w=label: w.configure(bg=self._tb_hover))
            label.bind("<Leave>", lambda e, w=label: w.configure(bg=tb_bg))
            label.bind("<Button-1>", lambda e, c=command: c())
        self.toolbar.update_idletasks()

        self.toolbar.bind("<Enter>", lambda e: self._cancel_hide())
        self.toolbar.bind("<Leave>", lambda e: self._schedule_hide())

        self.canvas.bind("<Button-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<Double-Button-1>", lambda e: self.toggle())
        self.canvas.bind("<Button-3>", self._menu)
        self.top.bind("<Enter>", lambda e: self._hover(True))
        self.top.bind("<Leave>", lambda e: self._hover(False))
        self.top.bind("<Escape>", lambda e: self.close())
        self.top.bind("<Control-c>", lambda e: self.copy())

        self.top.deiconify()
        self.top.lift()
        self.top.update_idletasks()

    # ---------------------------------------------------------- 颜色 / 字体
    @staticmethod
    def _hex(color):
        return "#%02x%02x%02x" % tuple(max(0, min(255, int(c))) for c in color)

    @staticmethod
    def _hex_rgb(value):
        value = value.lstrip("#")
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))

    @staticmethod
    def _mix(a, b, t):
        return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))

    @classmethod
    def _decorate(cls, content):
        """给合成图加直角（或圆角）+ 由外到内变浅的灰色渐变边框。"""
        w, h = content.size
        border = BORDER_WIDTH
        radius = BORDER_RADIUS
        out = Image.new("RGBA", (w + 2 * border, h + 2 * border), (0, 0, 0, 0))
        draw = ImageDraw.Draw(out)
        outer = cls._hex_rgb(BORDER_OUTER)
        inner = cls._hex_rgb(BORDER_INNER)
        for i in range(border):
            t = i / max(1, border - 1)
            color = tuple(int(outer[c] + (inner[c] - outer[c]) * t) for c in range(3)) + (255,)
            rect = [i, i, w + 2 * border - 1 - i, h + 2 * border - 1 - i]
            if radius <= 0:
                draw.rectangle(rect, outline=color, width=1)
            else:
                draw.rounded_rectangle(rect, radius=max(2, radius - i), outline=color, width=1)
        if radius <= 0:
            out.paste(content, (border, border))
        else:
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                [0, 0, w - 1, h - 1], radius=max(2, radius - border), fill=255)
            out.paste(content, (border, border), mask)
        return out

    @staticmethod
    def _luminance(color):
        return 0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]

    @classmethod
    def _sample_colors(cls, img):
        small = img.copy()
        small.thumbnail((180, 180))
        try:
            pixels = list(small.get_flattened_data())
        except Exception:
            pixels = list(small.getdata())
        if not pixels:
            return (255, 255, 255), (0, 0, 0)
        bins = {}
        for pixel in pixels:
            key = (pixel[0] // 8, pixel[1] // 8, pixel[2] // 8)
            bins.setdefault(key, []).append(pixel)
        dominant = max(bins.values(), key=len)
        bg = tuple(int(sum(p[i] for p in dominant) / len(dominant)) for i in range(3))
        bg_lum = cls._luminance(bg)
        if bg_lum > 128:
            pool = [p for p in pixels if cls._luminance(p) < 90]
            pool.sort(key=cls._luminance)
            chosen = pool[:max(3, len(pool) // 5)] if pool else [(0, 0, 0)]
        else:
            pool = [p for p in pixels if cls._luminance(p) > 165]
            pool.sort(key=cls._luminance, reverse=True)
            chosen = pool[:max(3, len(pool) // 5)] if pool else [(255, 255, 255)]
        fg = tuple(int(sum(p[i] for p in chosen) / len(chosen)) for i in range(3))
        if abs(cls._luminance(fg) - bg_lum) < 50:
            fg = (0, 0, 0) if bg_lum > 128 else (255, 255, 255)
        return bg, fg

    @staticmethod
    def _font_path(text):
        windir = os.environ.get("WINDIR") or "C:\\Windows"
        fonts = os.path.join(windir, "Fonts")
        cjk = bool(re.search(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]", text or ""))
        names = ("msyh.ttc", "msyhl.ttc", "simhei.ttf", "simsun.ttc") if cjk else ("segoeui.ttf", "arial.ttf", "msyh.ttc")
        for name in names:
            path = os.path.join(fonts, name)
            if os.path.exists(path):
                return path
        return None

    @staticmethod
    def _line_rects(lines, width, height):
        rects = []
        for item in lines:
            if not isinstance(item, dict):
                continue
            try:
                lw = int(item.get("w", 0))
                lh = int(item.get("h", 0))
                lx = int(item.get("x", 0))
                ly = int(item.get("y", 0))
            except Exception:
                continue
            if lw <= 2 or lh <= 2:
                continue
            rects.append((max(0, lx - 2), max(0, ly - 2),
                          min(width - 1, lx + lw + 2), min(height - 1, ly + lh + 2)))
        return rects

    @staticmethod
    def _text_boxes(lines, width, height):
        boxes = []
        for item in lines:
            if not isinstance(item, dict):
                continue
            try:
                lw = int(item.get("w", 0))
                lh = int(item.get("h", 0))
                lx = int(item.get("x", 0))
                ly = int(item.get("y", 0))
            except Exception:
                continue
            if lw <= 2 or lh <= 2:
                continue
            boxes.append((lx, ly, lx + lw, ly + lh))
        if not boxes:
            return (6, 6, max(20, width - 6), max(20, height - 6))
        x0 = max(0, min(b[0] for b in boxes) - 2)
        y0 = max(0, min(b[1] for b in boxes) - 2)
        x1 = min(width, max(b[2] for b in boxes) + 2)
        y1 = min(height, max(b[3] for b in boxes) + 2)
        if x1 - x0 < 20 or y1 - y0 < 12:
            return (6, 6, max(20, width - 6), max(20, height - 6))
        return (x0, y0, x1, y1)

    @staticmethod
    def _wrap(draw, text, font, width):
        lines = []
        for paragraph in str(text).split("\n"):
            current = ""
            for ch in paragraph:
                trial = current + ch
                if draw.textlength(trial, font=font) <= width or not current:
                    current = trial
                else:
                    lines.append(current)
                    current = ch
            lines.append(current)
        return lines or [""]

    def _local_bg(self, img, rect):
        """取这一行上下空白处的底色，保留引用框/卡片的灰底和左右留白。"""
        lx, ly, rx, ry = rect
        samples = []
        cand_y = []
        if ly - 4 >= 0:
            cand_y.append(ly - 4)
        if ry + 2 < img.height:
            cand_y.append(ry + 2)
        if ly < 4 and ry + 2 < img.height:
            cand_y = [ry + 2]
        step = max(1, (rx - lx) // 24)
        for y in cand_y:
            for x in range(max(0, lx), min(img.width, rx), step):
                samples.append(img.getpixel((x, y)))
        if not samples:
            return self.bg
        bins = {}
        for pixel in samples:
            key = (pixel[0] // 8, pixel[1] // 8, pixel[2] // 8)
            bins.setdefault(key, []).append(pixel)
        dominant = max(bins.values(), key=len)
        return tuple(int(sum(p[i] for p in dominant) / len(dominant)) for i in range(3))

    @staticmethod
    def _dist(a, b):
        return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])

    def _dominant(self, pixels, fallback):
        if not pixels:
            return fallback
        bins = {}
        for pixel in pixels:
            key = (pixel[0] // 8, pixel[1] // 8, pixel[2] // 8)
            bins.setdefault(key, []).append(pixel)
        group = max(bins.values(), key=len)
        return tuple(int(sum(p[i] for p in group) / len(group)) for i in range(3))

    def _line_style(self, rect, width, height):
        """按每一行原始文字采样：底色与文字主色。"""
        x0, y0, x1, y1 = rect
        inner = self.crop.crop((x0, y0, x1, y1))
        try:
            pixels = list(inner.get_flattened_data())
        except Exception:
            pixels = list(inner.getdata())
        if not pixels:
            return {"bg": self.bg, "fg": self.fg, "bold": False}
        # 一行里大多数像素是背景色；取出现最多的颜色，避免把左侧竖线/页面底色混进来。
        bg = self._dominant(pixels, self.bg)
        mask = [p for p in pixels if self._dist(p, bg) > 70]
        if not mask:
            return {"bg": bg, "fg": self.fg, "bold": False}
        # 文字主色取“与底色差异最大的一批像素”里出现最多的颜色，避免被个别红字/抗锯齿边缘带偏。
        strong = [p for p in mask if self._dist(p, bg) > 140]
        fg = self._dominant(strong or mask, self.fg)
        w = x1 - x0
        xs, ys = [], []
        for index, pixel in enumerate(pixels):
            if self._dist(pixel, bg) > 70:
                xs.append(index % w)
                ys.append(index // w)
        bw = max(xs) - min(xs) + 1
        bh = max(ys) - min(ys) + 1
        fill = len(mask) / max(1, bw * bh)
        return {"bg": bg, "fg": fg, "bold": fill > 0.22}

    def _compose(self, translated, original, boxes, line_rects, font_path, start_size, width, height):
        rects = []
        for item in (line_rects or []):
            try:
                x0, y0, x1, y1 = [int(v) for v in item]
            except Exception:
                continue
            x0 = max(0, min(width - 1, x0))
            y0 = max(0, min(height - 1, y0))
            x1 = max(x0 + 1, min(width, x1))
            y1 = max(y0 + 1, min(height, y1))
            if x1 - x0 >= 6 and y1 - y0 >= 6:
                rects.append((x0, y0, x1, y1))
        if not rects:
            try:
                x0, y0, x1, y1 = [int(v) for v in boxes]
                rects = [(max(0, x0), max(0, y0), min(width, x1), min(height, y1))]
            except Exception:
                rects = [(4, 4, max(8, width - 4), max(8, height - 4))]
        styles = [self._line_style(r, width, height) for r in rects]

        base = self.crop.copy()
        painter = ImageDraw.Draw(base)
        # 只抹掉每一行文字本身，保留引用框的底色、左侧竖线和边框。
        for (x0, y0, x1, y1), style in zip(rects, styles):
            painter.rectangle(
                [max(0, x0 - 3), max(0, y0 - 3),
                 min(width - 1, x1 + 2), min(height - 1, y1 + 2)],
                fill=style["bg"])

        rects.sort(key=lambda r: r[1])
        block_left = min(r[0] for r in rects)
        block_right = max(r[2] for r in rects)
        block_top = min(r[1] for r in rects)
        block_bottom = max(r[3] for r in rects)
        block_w = max(40, block_right - block_left)
        block_h = max(12, block_bottom - block_top)
        heights = sorted(r[3] - r[1] for r in rects)
        base_h = heights[len(heights) // 2]
        pitch = block_h / max(1, len(rects) - 1)
        limit = int(min(base_h * 0.95, pitch * 0.66 if len(rects) > 1 else block_h * 0.85))
        limit = max(7, min(40, limit))
        bold_path = None
        if font_path:
            candidate = font_path.replace("msyh.ttc", "msyhbd.ttc")
            if os.path.exists(candidate):
                bold_path = candidate

        chosen = None
        for size in range(limit, 6, -1):
            try:
                font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
            except Exception:
                font = ImageFont.load_default()
            spacing = max(2, int(size * 0.45))
            wrapped = self._wrap(painter, translated, font, block_w) if translated else []
            if not wrapped:
                chosen = (size, font, [], spacing)
                break
            total = len(wrapped) * size + (len(wrapped) - 1) * spacing
            widest = max(painter.textlength(line, font=font) for line in wrapped)
            # 让译文行数不超过原文行数，这样每一行都能落在原始行的位置上。
            fits = len(wrapped) <= len(rects) and widest <= block_w
            if len(wrapped) < len(rects) and total > block_h - 2:
                fits = False
            if fits:
                chosen = (size, font, wrapped, spacing)
                break
        if chosen is None:
            size = 7
            try:
                font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
            except Exception:
                font = ImageFont.load_default()
            spacing = 0
            wrapped = self._wrap(painter, translated, font, block_w) if translated else []
            chosen = (size, font, wrapped, spacing)
        size, font, wrapped, spacing = chosen

        count = len(rects)
        total_lines = len(wrapped)
        first_box = rects[0]
        first_y = first_box[1] + max(0, (first_box[3] - first_box[1] - size) // 2)
        for j, line in enumerate(wrapped):
            if total_lines == count:
                # 行数一致：逐行落在原文每一行的位置上。
                k = j
                x = rects[k][0]
                y = rects[k][1] + max(0, (rects[k][3] - rects[k][1] - size) // 2)
            elif total_lines < count:
                # 译文更短：从原文第一行开始、按正常行距排，不再留出原行距的大空档。
                k = min(j, count - 1)
                x = first_box[0]
                y = first_y + j * (size + spacing)
            else:
                # 极长译文兜底：接在最后一行下面。
                k = count - 1
                x = rects[k][0]
                y = rects[k][3] + (j - count + 1) * (size + spacing)
            style = styles[k]
            # 统一使用正常字重，不跟随原文加粗。
            painter.text((x, y), line, font=font, fill=style["fg"])

        return base, self.crop.copy(), base.height

    def _compose_table(self, cells, width, height):
        """表格：逐单元格替换，保留表格线、数字和列位置。"""
        base = self.crop.copy()
        painter = ImageDraw.Draw(base)
        font_path = self._font_path("中文")
        # 表格整体用一个文字颜色，避免小字号单元格采到 ClearType 彩边（粉色/紫色）。
        valid = [cell for cell in cells if isinstance(cell, dict)]
        if not valid:
            return base, self.crop.copy()
        tx0 = min(max(0, int(cell.get("x", 0))) for cell in valid)
        ty0 = min(max(0, int(cell.get("y", 0))) for cell in valid)
        tx1 = max(min(width, int(cell.get("x", 0)) + int(cell.get("w", 0))) for cell in valid)
        ty1 = max(min(height, int(cell.get("y", 0)) + int(cell.get("h", 0))) for cell in valid)
        table_style = self._line_style((tx0, ty0, max(tx0 + 1, tx1), max(ty0 + 1, ty1)),
                                       width, height)
        table_fg = table_style["fg"]
        for cell in cells:
            if not isinstance(cell, dict):
                continue
            text = str(cell.get("translated") or "").strip()
            if cell.get("numeric"):
                # OCR 有时会在数字前后带上引号/撇号，画之前清掉。
                text = re.sub(r"^[\s'\"‘’`]+|[\s'\"‘’`]+$", "", text)
            if not text:
                continue
            try:
                x = int(cell.get("x", 0))
                y = int(cell.get("y", 0))
                cw = int(cell.get("w", 0))
                ch = int(cell.get("h", 0))
            except Exception:
                continue
            if cw < 4 or ch < 6:
                continue
            x0 = max(0, min(width - 1, x))
            y0 = max(0, min(height - 1, y))
            x1 = max(x0 + 1, min(width, x + cw))
            y1 = max(y0 + 1, min(height, y + ch))
            style = self._line_style((x0, y0, x1, y1), width, height)
            painter.rectangle(
                [max(0, x0 - 2), max(0, y0 - 2),
                 min(width - 1, x1 + 2), min(height - 1, y1 + 2)],
                fill=style["bg"])
            size = max(6, int((y1 - y0) * 0.9))
            try:
                font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
            except Exception:
                font = ImageFont.load_default()
            while size > 6 and painter.textlength(text, font=font) > (x1 - x0):
                size -= 1
                try:
                    font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
                except Exception:
                    font = ImageFont.load_default()
            text_w = painter.textlength(text, font=font)
            right_align = bool(cell.get("numeric")) or cell.get("align") == "right"
            tx = (x1 - text_w) if right_align else x0
            ty = y0 + max(0, (y1 - y0 - size) // 2)
            painter.text((tx, ty), text, font=font, fill=table_fg)
        return base, self.crop.copy()

    def _compose_blocks(self, blocks, width, height):
        """按段落块翻译：整段文字在块内重新排版，但整块落在原文段落所在的矩形里。"""
        base = self.crop.copy()
        painter = ImageDraw.Draw(base)
        font_path = self._font_path("中文")
        for block in blocks:
            if not isinstance(block, dict):
                continue
            text = str(block.get("translated") or "").strip()
            lines = [item for item in (block.get("lines") or []) if isinstance(item, dict)]
            rect = block.get("rect") or {}
            if not text or not lines:
                continue
            try:
                bx = int(rect.get("x", 0))
                by = int(rect.get("y", 0))
                bw = int(rect.get("w", 0))
                bh = int(rect.get("h", 0))
            except Exception:
                continue
            if bw < 20 or bh < 10:
                continue
            style = self._line_style(
                (max(0, bx), max(0, by), min(width, bx + bw), min(height, by + bh)),
                width, height)
            # 只抹掉这个块里每一行文字，保留引用框的底色/竖线/边框。
            for item in lines:
                try:
                    x0 = int(item.get("x", 0))
                    y0 = int(item.get("y", 0))
                    lw = int(item.get("w", 0))
                    lh = int(item.get("h", 0))
                except Exception:
                    continue
                if lw <= 0 or lh <= 0:
                    continue
                painter.rectangle(
                    [max(0, x0 - 3), max(0, y0 - 3),
                     min(width - 1, x0 + lw + 2), min(height - 1, y0 + lh + 2)],
                    fill=style["bg"])
            block_w = max(20, bw)
            block_h = max(10, bh)
            chosen = None
            for size in range(max(7, min(40, int(block_h * 0.92))), 6, -1):
                try:
                    font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
                except Exception:
                    font = ImageFont.load_default()
                spacing = max(2, int(size * 0.35))
                wrapped = self._wrap(painter, text, font, block_w)
                total = len(wrapped) * size + (len(wrapped) - 1) * spacing
                widest = max(painter.textlength(line, font=font) for line in wrapped)
                if total <= block_h - 2 and widest <= block_w:
                    chosen = (size, font, wrapped, spacing)
                    break
            if chosen is None:
                size = 7
                try:
                    font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
                except Exception:
                    font = ImageFont.load_default()
                spacing = 0
                wrapped = self._wrap(painter, text, font, block_w)
                chosen = (size, font, wrapped, spacing)
            size, font, wrapped, spacing = chosen
            total = len(wrapped) * size + max(0, len(wrapped) - 1) * spacing
            y = by + max(0, (block_h - total) // 2)
            for line in wrapped:
                painter.text((bx, y), line, font=font, fill=style["fg"])
                y += size + spacing
        return base, self.crop.copy()

    def _compose_lines(self, lines, width, height):
        """逐行替换：每条 OCR 行用自己的译文画回原来的行框，保证位置贴着原文。"""
        base = self.crop.copy()
        painter = ImageDraw.Draw(base)
        font_path = self._font_path("中文")
        for item in lines:
            if not isinstance(item, dict):
                continue
            text = str(item.get("translated") or "").strip()
            try:
                x = int(item.get("x", 0))
                y = int(item.get("y", 0))
                lw = int(item.get("w", 0))
                lh = int(item.get("h", 0))
            except Exception:
                continue
            if not text or lw < 4 or lh < 6:
                continue
            x0 = max(0, min(width - 1, x))
            y0 = max(0, min(height - 1, y))
            x1 = max(x0 + 1, min(width, x + lw))
            y1 = max(y0 + 1, min(height, y + lh))
            style = self._line_style((x0, y0, x1, y1), width, height)
            painter.rectangle(
                [max(0, x0 - 3), max(0, y0 - 3),
                 min(width - 1, x1 + 2), min(height - 1, y1 + 2)],
                fill=style["bg"])
            size = max(6, int((y1 - y0) * 0.9))
            try:
                font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
            except Exception:
                font = ImageFont.load_default()
            while size > 6 and painter.textlength(text, font=font) > (x1 - x0):
                size -= 1
                try:
                    font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
                except Exception:
                    font = ImageFont.load_default()
            draw_y = y0 + max(0, (y1 - y0 - size) // 2)
            painter.text((x0, draw_y), text, font=font, fill=style["fg"])
        return base, self.crop.copy()

    # ---------------------------------------------------------- 交互
    def _hover(self, inside):
        if inside:
            self._show_toolbar()
            self._cancel_hide()
        else:
            self._schedule_hide()

    def _show_toolbar(self):
        """工具条放在译文框外：优先上方，贴边时放下方。"""
        try:
            self.toolbar.update_idletasks()
            tw = max(60, self.toolbar.winfo_reqwidth())
            th = max(20, self.toolbar.winfo_reqheight())
            x = self.top.winfo_x()
            y = self.top.winfo_y()
            w = self.top.winfo_width()
            h = self.top.winfo_height()
            vx, vy, vw, vh = monitor_work_area_at(x, y)
            tx = min(x + w - tw, vx + vw - tw - 4)
            tx = max(vx + 4, tx)
            ty = y - th - 6
            if ty < vy + 4:
                ty = y + h + 6
                if ty + th > vy + vh - 4:
                    ty = y - th - 6
            self.toolbar.geometry("+%d+%d" % (tx, ty))
            self.toolbar.deiconify()
            self.toolbar.lift()
        except Exception:
            pass

    def _cancel_hide(self):
        if self._hide_toolbar_job:
            try:
                self.top.after_cancel(self._hide_toolbar_job)
            except Exception:
                pass
            self._hide_toolbar_job = None

    def _schedule_hide(self):
        self._cancel_hide()
        self._hide_toolbar_job = self.top.after(450, self._hide_toolbar)

    def _hide_toolbar(self):
        self._hide_toolbar_job = None
        try:
            self.toolbar.withdraw()
        except Exception:
            pass

    def toggle_pin(self):
        self.pinned = not self.pinned
        try:
            self.top.attributes("-topmost", self.pinned)
            self.toolbar.attributes("-topmost", self.pinned)
            self.pin_btn.configure(fg=self._tb_pin_on if self.pinned else self._tb_pin_off)
            if self.pinned:
                self.top.lift()
                self.toolbar.lift()
        except Exception:
            pass

    def _drag_start(self, event):
        self._moved = False
        self._drag = (event.x_root, event.y_root, self.top.winfo_x(), self.top.winfo_y())
        try:
            self.top.lift()
        except Exception:
            pass

    def _drag_move(self, event):
        if not self._drag:
            return
        sx, sy, wx, wy = self._drag
        if abs(event.x_root - sx) + abs(event.y_root - sy) > 4:
            self._moved = True
        self.top.geometry("+%d+%d" % (wx + event.x_root - sx, wy + event.y_root - sy))
        if self.toolbar.winfo_ismapped():
            self._show_toolbar()

    def _menu(self, event):
        menu = tk.Toplevel(self.top)
        menu.overrideredirect(True)
        menu.attributes("-topmost", True)
        menu.configure(bg=BORDER)
        menu.geometry("132x96+%d+%d" % (event.x_root, event.y_root))
        inner = tk.Frame(menu, bg=CARD)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        def item(text, command):
            label = tk.Label(inner, text=text, bg=CARD, fg=TEXT, anchor="w",
                             padx=10, pady=5, font=(FONT_FAMILY, 9), cursor="hand2")
            label.pack(fill="x")
            label.bind("<Enter>", lambda e: label.configure(bg=CARD_HOVER))
            label.bind("<Leave>", lambda e: label.configure(bg=CARD))
            label.bind("<Button-1>", lambda e: (menu.destroy(), command()))

        item("复制译文", self.copy)
        item("显示原文", self.toggle)
        item("关闭", self.close)
        menu.focus_force()

    def toggle(self):
        self.showing_original = not self.showing_original
        self.canvas.itemconfigure(
            self.image_item,
            image=self.photo_original if self.showing_original else self.photo_translated)
        for child in self.toolbar.winfo_children():
            if child.cget("text") == "原文":
                child.configure(text="译文")
            elif child.cget("text") == "译文":
                child.configure(text="原文")

    def copy(self):
        text = self.payload.get("original") if self.showing_original else self.payload.get("translated")
        try:
            self.top.clipboard_clear()
            self.top.clipboard_append(text or "")
        except Exception:
            pass

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.toolbar.destroy()
        except Exception:
            pass
        try:
            self.top.destroy()
        except Exception:
            pass
        self.app.inplace.discard(self)


# ---------------------------------------------------------------- 轻量提示
class LoadingWindow:
    def __init__(self, app, rect):
        self.app = app
        x = int(rect.get("x", 0))
        y = int(rect.get("y", 0))
        self.top = tk.Toplevel(app.root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.configure(bg=ACCENT)
        self.top.geometry("132x36+%d+%d" % (x + 6, y + 6))
        inner = tk.Frame(self.top, bg=CARD)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(inner, text="翻译中…", bg=CARD, fg=TEXT,
                 font=(FONT_FAMILY, 9)).pack(expand=True)

    def close(self):
        try:
            self.top.destroy()
        except Exception:
            pass


class Toast:
    def __init__(self, app, message, anchor=None):
        self.top = tk.Toplevel(app.root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.configure(bg=ACCENT)
        vx, vy, vw, vh = monitor_work_area_at(*(anchor or cursor_pos()))
        width = min(460, max(220, len(message) * 9 + 40))
        x = vx + (vw - width) // 2
        y = vy + 70
        self.top.geometry("%dx%d+%d+%d" % (width, 54, x, y))
        inner = tk.Frame(self.top, bg=CARD)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(inner, text=message, bg=CARD, fg=TEXT, wraplength=width - 30,
                 font=(FONT_FAMILY, 9), justify="left").pack(expand=True, padx=10)
        self.top.after(3200, self.top.destroy)


# ---------------------------------------------------------------- 框选
class CaptureOverlay:
    def __init__(self, app, request, source):
        self.app = app
        self.request = request
        self.source = source
        self.start = None
        self.pending = None
        self.confirm_top = None
        self.finished = False
        vx, vy, vw, vh = virtual_screen()
        self.origin = (vx, vy, vw, vh)

        self.top = tk.Toplevel(app.root)
        self.top.withdraw()
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.attributes("-alpha", 0.32)
        self.top.configure(bg="#000000")
        self.top.geometry("%dx%d+%d+%d" % (vw, vh, vx, vy))
        self.canvas = tk.Canvas(self.top, bg="#000000", highlightthickness=0, bd=0,
                                cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)

        mx, my, mw, mh = monitor_work_area_at(*cursor_pos())
        hint_x = mx - vx + mw // 2
        hint_y = my - vy + 44
        self.hint = self.canvas.create_text(
            hint_x, hint_y, text="拖拽选择要翻译的区域    ·    Esc 取消",
            fill="#ffffff", font=(FONT_FAMILY, 13))
        self.size_label = self.canvas.create_text(hint_x, hint_y + 24, text="",
                                                  fill="#cfd6ff", font=(FONT_FAMILY, 10))
        self.rect = None

        self.canvas.bind("<Button-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.top.bind("<Escape>", lambda e: self._cancel())
        self.top.deiconify()
        self.top.lift()
        try:
            self.top.focus_force()
        except Exception:
            pass

    def _press(self, event):
        if self.confirm_top is not None:
            self._destroy_confirm()
            self.pending = None
            self.canvas.itemconfigure(self.hint, text="拖拽选择要翻译的区域    ·    Esc 取消")
            self.canvas.itemconfigure(self.size_label, text="")
        self.start = (event.x, event.y)
        if self.rect is None:
            self.rect = self.canvas.create_rectangle(
                event.x, event.y, event.x, event.y, outline=ACCENT, width=2)
        else:
            self.canvas.coords(self.rect, event.x, event.y, event.x, event.y)

    def _move(self, event):
        if self.rect is None or self.start is None:
            return
        self.canvas.coords(self.rect, self.start[0], self.start[1], event.x, event.y)
        w = abs(event.x - self.start[0])
        h = abs(event.y - self.start[1])
        self.canvas.itemconfigure(self.size_label, text="%d × %d" % (w, h))
        self.canvas.coords(self.size_label, event.x + 16, event.y + 18)

    def _release(self, event):
        if self.start is None:
            return
        x1, y1 = self.start
        x2, y2 = event.x, event.y
        left, top = min(x1, x2), min(y1, y2)
        right, bottom = max(x1, x2), max(y1, y2)
        if right - left < 8 or bottom - top < 8:
            self._cancel()
            return
        self.pending = (left, top, right, bottom)
        self._show_confirm(left, top, right, bottom)

    def _show_confirm(self, left, top, right, bottom):
        """松开鼠标后先让用户确认，避免截错区域就直接翻译。"""
        vx, vy, vw, vh = self.origin
        panel = tk.Toplevel(self.app.root)
        panel.overrideredirect(True)
        panel.attributes("-topmost", True)
        panel.configure(bg="#1b1c21")
        ok = tk.Label(panel, text="✓ 翻译", bg="#1f7a5a", fg="#ffffff",
                      padx=14, pady=5, cursor="hand2", font=(FONT_FAMILY, 9))
        ask = tk.Label(panel, text="✨ 问 AI", bg="#2a3550", fg="#dbe6ff",
                       padx=14, pady=5, cursor="hand2", font=(FONT_FAMILY, 9))
        no = tk.Label(panel, text="× 取消", bg="#3a3b42", fg="#e8e9ec",
                      padx=14, pady=5, cursor="hand2", font=(FONT_FAMILY, 9))
        ok.pack(side="left", padx=(0, 6))
        ask.pack(side="left", padx=(0, 6))
        no.pack(side="left")
        ok.bind("<Enter>", lambda e: ok.configure(bg="#26956d"))
        ok.bind("<Leave>", lambda e: ok.configure(bg="#1f7a5a"))
        ask.bind("<Enter>", lambda e: ask.configure(bg="#36456a"))
        ask.bind("<Leave>", lambda e: ask.configure(bg="#2a3550"))
        no.bind("<Enter>", lambda e: no.configure(bg="#4a4b55"))
        no.bind("<Leave>", lambda e: no.configure(bg="#3a3b42"))
        ok.bind("<Button-1>", lambda e: self._confirm())
        ask.bind("<Button-1>", lambda e: self._ask_ai())
        no.bind("<Button-1>", lambda e: self._cancel())
        panel.bind("<Escape>", lambda e: self._cancel())
        panel.update_idletasks()
        pw = panel.winfo_reqwidth()
        ph = panel.winfo_reqheight()
        px = vx + right - pw
        py = vy + bottom + 8
        if py + ph > vy + vh - 6:
            py = vy + top - ph - 8
        px = max(vx + 6, min(px, vx + vw - pw - 6))
        py = max(vy + 6, min(py, vy + vh - ph - 6))
        panel.geometry("%dx%d+%d+%d" % (pw, ph, px, py))
        panel.deiconify()
        panel.lift()
        self.confirm_top = panel
        self.canvas.itemconfigure(self.hint, text="确认框选区域：✓ 翻译    ✨ 问 AI    × 取消")
        self.canvas.itemconfigure(self.size_label, text="")

    def _destroy_confirm(self):
        if self.confirm_top is not None:
            try:
                self.confirm_top.destroy()
            except Exception:
                pass
            self.confirm_top = None

    def _confirm(self):
        if self.pending is None:
            return
        rect = self.pending
        self.pending = None
        self._finish(rect)

    def _cancel(self):
        self.pending = None
        self._finish(None)

    def _ask_ai(self):
        if self.pending is None:
            return
        rect = self.pending
        self.pending = None
        if self.request is not None:
            self.app.respond(self.request, ok=False, error="asked-ai")
            self.request = None
        self._finish_for_ask(rect)

    def _finish_for_ask(self, local):
        if self.finished:
            return
        self.finished = True
        self._destroy_confirm()
        vx, vy, vw, vh = self.origin
        self.top.withdraw()
        self.top.update()
        self.app.root.update()
        left, top, right, bottom = local
        left = max(0, min(vw - 1, left))
        top = max(0, min(vh - 1, top))
        right = max(left + 1, min(vw, right))
        bottom = max(top + 1, min(vh, bottom))
        time.sleep(0.10)
        path = self.app.grab((vx + left, vy + top, vx + right, vy + bottom))
        if path is not None:
            AskWindow(self.app, path, {"x": vx + left, "y": vy + top,
                                       "w": right - left, "h": bottom - top})
        self._destroy()

    def _finish(self, local):
        if self.finished:
            return
        self.finished = True
        self._destroy_confirm()
        vx, vy, vw, vh = self.origin
        self.top.withdraw()
        self.top.update()
        self.app.root.update()
        if local is None:
            if self.request is not None:
                self.app.respond(self.request, ok=False, error="cancelled")
            self._destroy()
            return
        left, top, right, bottom = local
        left = max(0, min(vw - 1, left))
        top = max(0, min(vh - 1, top))
        right = max(left + 1, min(vw, right))
        bottom = max(top + 1, min(vh, bottom))
        time.sleep(0.10)
        path = self.app.grab((vx + left, vy + top, vx + right, vy + bottom))
        rect = {"x": vx + left, "y": vy + top, "w": right - left, "h": bottom - top}
        if path is None:
            if self.request is not None:
                self.app.respond(self.request, ok=False, error="截图失败：Pillow 不可用")
            self._destroy()
            return
        if self.request is not None:
            self.app.respond(self.request, ok=True, path=path, rect=rect)
        else:
            self.app.emit({"event": "shot", "path": path, "rect": rect,
                           "source": self.source})
        self._destroy()

    def _destroy(self):
        try:
            self.top.destroy()
        except Exception:
            pass
        self.app.overlay = None


# ---------------------------------------------------------------- 问 AI 对话
class AskWindow:
    """框选后「问 AI」：保留截图，和模型围绕这张图对话。"""

    def __init__(self, app, path, rect):
        self.app = app
        self.path = path
        self.rect = rect or {}
        self.history = []
        self.pending = False

        self.top = tk.Toplevel(app.root)
        self.top.title("问 AI · 截图")
        self.top.attributes("-topmost", True)
        self.top.configure(bg=CARD)
        self.top.minsize(460, 460)
        w, h = 580, 640
        vx, vy, vw, vh = monitor_work_area_at(
            int(self.rect.get("x", 0)) + 40, int(self.rect.get("y", 0)) + 40)
        x = min(max(vx + 16, int(self.rect.get("x", 0)) + int(self.rect.get("w", 0)) + 16),
                vx + vw - w - 16)
        y = min(max(vy + 16, int(self.rect.get("y", 0))), vy + vh - h - 16)
        self.top.geometry("%dx%d+%d+%d" % (w, h, x, y))

        head = tk.Frame(self.top, bg=CARD)
        head.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(head, text="问 AI · 截图区域", bg=CARD, fg=TEXT,
                 font=(FONT_FAMILY, 11, "bold")).pack(side="left")
        close = tk.Label(head, text="×", bg=CARD, fg=MUTED, padx=8, cursor="hand2",
                         font=(FONT_FAMILY, 12))
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.close())
        close.bind("<Enter>", lambda e: close.configure(fg=TEXT))
        close.bind("<Leave>", lambda e: close.configure(fg=MUTED))

        try:
            preview = Image.open(path)
            preview.thumbnail((520, 210))
            self.photo = ImageTk.PhotoImage(preview)
            holder = tk.Frame(self.top, bg=BORDER)
            holder.pack(padx=12, pady=4, fill="x")
            tk.Label(holder, image=self.photo, bg=CARD,
                     bd=0).pack(padx=1, pady=1)
        except Exception:
            self.photo = None

        body = tk.Frame(self.top, bg=CARD)
        body.pack(fill="both", expand=True, padx=12, pady=(4, 4))
        self.log = tk.Text(body, bg="#15161b", fg=TEXT, bd=0, wrap="word",
                           highlightthickness=1, highlightbackground=BORDER,
                           font=(FONT_FAMILY, 9), padx=8, pady=6)
        scroll = tk.Scrollbar(body, command=self.log.yview)
        scroll.configure(bg=CARD, troughcolor=CARD)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.log.tag_configure("me", foreground="#9ec1ff")
        self.log.tag_configure("ai", foreground="#dfe6ff")
        self.log.tag_configure("hint", foreground=MUTED)
        self.log.insert("end", "输入问题，AI 会读取这张截图回答。\n\n", "hint")
        self.log.configure(state="disabled")

        bar = tk.Frame(self.top, bg=CARD)
        bar.pack(fill="x", padx=12, pady=(2, 12))
        self.entry = tk.Entry(bar, bg="#15161b", fg=TEXT, insertbackground=TEXT,
                              bd=0, highlightthickness=1, highlightbackground=BORDER,
                              font=(FONT_FAMILY, 10))
        self.entry.pack(side="left", fill="x", expand=True, ipady=5)
        send = tk.Label(bar, text="发送", bg=ACCENT, fg="#ffffff", padx=14, pady=6,
                        cursor="hand2", font=(FONT_FAMILY, 9))
        send.pack(side="left", padx=(8, 0))
        send.bind("<Button-1>", lambda e: self._send())
        send.bind("<Enter>", lambda e: send.configure(bg="#4a68e0"))
        send.bind("<Leave>", lambda e: send.configure(bg=ACCENT))
        self.entry.bind("<Return>", lambda e: self._send())
        self.entry.focus_set()
        try:
            self.top.focus_force()
        except Exception:
            pass
        self.top.after(180, self._focus)
        self.top.bind("<Escape>", lambda e: self.close())
        self.top.protocol("WM_DELETE_WINDOW", self.close)

    def _focus(self):
        try:
            self.top.lift()
            self.top.focus_force()
            self.entry.focus_force()
        except Exception:
            pass

    def _append(self, who, text):
        self.log.configure(state="normal")
        self.log.insert("end", "%s：%s\n\n" % (who, text), "me" if who == "你" else "ai")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _send(self):
        if self.pending:
            return
        question = self.entry.get().strip()
        if not question:
            return
        self.entry.delete(0, "end")
        self._append("你", question)
        prior = list(self.history)
        self.history.append({"role": "user", "content": question})
        self.pending = True
        self._append("AI", "正在读图…")
        self.app.ask(self.path, question, prior, self._on_answer)

    def _on_answer(self, ok, text):
        self.pending = False
        if ok:
            self._append("AI", text)
            self.history.append({"role": "assistant", "content": text})
        else:
            self._append("AI", "出错了：%s" % text)

    def close(self):
        try:
            self.top.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------- 桌面浮标
class DesktopPill:
    def __init__(self, app):
        self.app = app
        self.dragged = False
        self._armed = False
        self.top = tk.Toplevel(app.root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.attributes("-alpha", 0.88)
        self.top.configure(bg=TRANSPARENT)
        try:
            self.top.wm_attributes("-transparentcolor", TRANSPARENT)
        except Exception:
            pass

        saved = load_json("pill.json", None)
        vx, vy, vw, vh = monitor_work_area_at(*cursor_pos())
        x = int(saved.get("x", vx + vw - 72)) if isinstance(saved, dict) else vx + vw - 72
        y = int(saved.get("y", vy + 190)) if isinstance(saved, dict) else vy + 190
        self.top.geometry("52x52+%d+%d" % (x, y))

        self.canvas = tk.Canvas(self.top, width=52, height=52, bg=TRANSPARENT,
                                highlightthickness=0, bd=0, cursor="hand2")
        self.canvas.pack()
        self.circle = self.canvas.create_oval(3, 3, 49, 49, fill=CARD, outline=ACCENT, width=2)
        self.canvas.create_text(26, 24, text="译", fill=TEXT, font=(FONT_FAMILY, 13, "bold"))
        self.canvas.create_text(26, 39, text="截图", fill=MUTED, font=(FONT_FAMILY, 6))

        self.canvas.bind("<Enter>", lambda e: self.canvas.itemconfigure(self.circle, fill=CARD_HOVER))
        self.canvas.bind("<Leave>", lambda e: self.canvas.itemconfigure(self.circle, fill=CARD))
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Button-3>", self._menu)

    def _press(self, event):
        self._armed = True
        self.dragged = False
        self._start = (event.x_root, event.y_root, self.top.winfo_x(), self.top.winfo_y())

    def _move(self, event):
        if not self._armed:
            return
        sx, sy, wx, wy = self._start
        if abs(event.x_root - sx) + abs(event.y_root - sy) > 4:
            self.dragged = True
        self.top.geometry("+%d+%d" % (wx + event.x_root - sx, wy + event.y_root - sy))

    def _release(self, event):
        if not self._armed:
            return
        self._armed = False
        if self.dragged:
            save_json("pill.json", {"x": self.top.winfo_x(), "y": self.top.winfo_y()})
        else:
            self.app.start_capture("pill")

    def _menu(self, event):
        menu = tk.Toplevel(self.top)
        menu.overrideredirect(True)
        menu.attributes("-topmost", True)
        menu.configure(bg=BORDER)
        x, y = event.x_root, event.y_root
        menu.geometry("150x112+%d+%d" % (x, y))
        inner = tk.Frame(menu, bg=CARD)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        def item(text, command):
            label = tk.Label(inner, text=text, bg=CARD, fg=TEXT, anchor="w",
                             padx=12, pady=6, font=(FONT_FAMILY, 9), cursor="hand2")
            label.pack(fill="x")
            label.bind("<Enter>", lambda e: label.configure(bg=CARD_HOVER))
            label.bind("<Leave>", lambda e: label.configure(bg=CARD))
            label.bind("<Button-1>", lambda e: (menu.destroy(), command()))

        item("截图翻译", lambda: self.app.start_capture("pill"))
        item("隐藏浮标", self.hide)
        item("退出工具", self.app.quit)
        menu.bind("<FocusOut>", lambda e: menu.destroy())
        menu.focus_force()

    def hide(self):
        self.top.withdraw()

    def show(self):
        self.top.deiconify()
        self.top.lift()

    def close(self):
        try:
            self.top.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------- 全局快捷键
class Hotkey:
    def __init__(self, app):
        self.app = app
        self.active = False
        self.queue = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            user32 = ctypes.windll.user32
            ok_s = user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, VK_S)
            ok_t = user32.RegisterHotKey(None, 2, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, VK_T)
            if not ok_s and not ok_t:
                log("RegisterHotKey failed")
                return
            self.active = True
            self.app.hotkey_active = True
            log("hotkey registered: Ctrl+Shift+S / Ctrl+Shift+T")
            msg = ctypes.wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message == WM_HOTKEY:
                    if msg.wParam == 1:
                        self.queue.put("capture")
                    elif msg.wParam == 2:
                        self.queue.put("translate_selection")
            if ok_s:
                user32.UnregisterHotKey(None, 1)
            if ok_t:
                user32.UnregisterHotKey(None, 2)
        except Exception:
            log(traceback.format_exc())

    def poll(self):
        try:
            while True:
                action = self.queue.get_nowait()
                if action == "translate_selection":
                    self.app.translate_selection_hotkey()
                else:
                    self.app.start_capture("hotkey")
        except queue.Empty:
            pass


class SelectionWatcher:
    """全局鼠标钩子：左键松开后尝试读取选区，用于在外部应用里弹出「译」。"""

    def __init__(self, app):
        self.app = app
        self.queue = queue.Queue()
        self.running = True
        self._down = None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            hookproc = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)
            self._proc = hookproc(self._hook)
            self._handle = user32.SetWindowsHookExW(14, self._proc, None, 0)
            if not self._handle:
                log("selection mouse hook failed")
                return
            msg = ctypes.wintypes.MSG()
            while self.running and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            log(traceback.format_exc())

    def _hook(self, n_code, w_param, l_param):
        if n_code == 0:
            try:
                data = ctypes.cast(ctypes.c_void_p(l_param), ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                x = int(data.pt.x)
                y = int(data.pt.y)
                if w_param == 0x0201:  # WM_LBUTTONDOWN
                    self._down = (x, y)
                    self.queue.put(("down", x, y))
                elif w_param in (0x0204, 0x0207):  # 右键 / 中键按下
                    self.queue.put(("down", x, y))
                elif w_param == 0x0202:  # WM_LBUTTONUP
                    start = self._down
                    self._down = None
                    # 只有拖动/划选才尝试复制；单击不触发。
                    if start and abs(x - start[0]) + abs(y - start[1]) >= 6:
                        self.queue.put(("drag", x, y))
            except Exception:
                pass
        return user32.CallNextHookEx(None, n_code, w_param, l_param)


class SelectionPill:
    """选字后浮在光标旁的小「译」按钮。"""

    def __init__(self, app, text, x, y):
        self.app = app
        self.text = text
        self.origin = (x, y)
        self.top = tk.Toplevel(app.root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.configure(bg=TRANSPARENT)
        try:
            self.top.wm_attributes("-transparentcolor", TRANSPARENT)
        except Exception:
            pass
        vx, vy, vw, vh = monitor_work_area_at(x, y)
        px = min(max(vx + 4, x + 10), vx + vw - 36)
        py = min(max(vy + 4, y + 8), vy + vh - 36)
        self.top.geometry("32x32+%d+%d" % (px, py))
        self.canvas = tk.Canvas(self.top, width=32, height=32, bg=TRANSPARENT,
                                highlightthickness=0, bd=0, cursor="hand2")
        self.canvas.pack()
        self.circle = self.canvas.create_oval(1, 1, 31, 31, fill=CARD,
                                              outline=ACCENT, width=1)
        self.canvas.create_text(16, 16, text="译", fill=TEXT,
                                font=(FONT_FAMILY, 11, "bold"))
        self.canvas.bind("<Enter>", lambda e: self.canvas.itemconfigure(self.circle, fill=CARD_HOVER))
        self.canvas.bind("<Leave>", lambda e: self.canvas.itemconfigure(self.circle, fill=CARD))
        self.canvas.bind("<Button-1>", lambda e: app.translate_selection_text(self.text, x, y))
        self.top.after(5000, self.close)

    def contains(self, x, y):
        try:
            px = self.top.winfo_x()
            py = self.top.winfo_y()
            pw = self.top.winfo_width()
            ph = self.top.winfo_height()
            return px - 2 <= x <= px + pw + 2 and py - 2 <= y <= py + ph + 2
        except Exception:
            return False

    def close(self):
        try:
            self.top.destroy()
        except Exception:
            pass
        if self.app.sel_pill is self:
            self.app.sel_pill = None


# ---------------------------------------------------------------- 应用
class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("dsh-translate-pin")
        self.windows = set()
        self.inplace = set()
        self.cascade = 0
        self.overlay = None
        self.pill = None
        self.hotkey = None
        self.hotkey_active = False
        self.loading = None
        self.primary = self._acquire_single_instance()
        self.tmpdir = os.path.join(tempfile.gettempdir(), "dsh-translate-pin")
        os.makedirs(self.tmpdir, exist_ok=True)
        self.capture_seq = 0
        self.asks = {}
        self.ask_seq = 0
        self.sel_pill = None
        self.watcher = None

    @staticmethod
    def _acquire_single_instance():
        try:
            handle = ctypes.windll.kernel32.CreateMutexW(None, True, "Global\\dsh-translate-pin")
            if not handle:
                return True
            if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
                return False
            return True
        except Exception:
            return True

    # ---------------------------------------------------------- IO
    def respond(self, request, **payload):
        data = {"id": request.get("id")}
        data.update(payload)
        self._write(data)

    def emit(self, payload):
        self._write(payload)

    def ask(self, path, question, history, callback):
        self.ask_seq += 1
        key = "ask-%d" % self.ask_seq
        self.asks[key] = callback
        self._write({"id": key, "cmd": "ask", "path": path,
                     "question": question, "history": history})

    def _write(self, data):
        try:
            sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception:
            pass

    def grab(self, rect):
        if ImageGrab is None:
            return None
        left, top, right, bottom = rect
        img = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
        self.capture_seq += 1
        path = os.path.join(self.tmpdir, "shot-%d-%d.png" % (int(time.time() * 1000), self.capture_seq))
        img.save(path, "PNG")
        return path

    # ---------------------------------------------------------- 命令
    def handle(self, request):
        cmd = request.get("cmd")
        try:
            if cmd == "ping":
                self.respond(request, ok=True, pid=os.getpid(),
                             hotkey=self.hotkey_active, primary=self.primary,
                             pill=self.pill is not None)
            elif cmd == "show":
                self.show_pin(request)
            elif cmd == "place":
                self.place(request)
            elif cmd == "loading":
                self.show_loading(request)
            elif cmd == "notify":
                self._clear_loading()
                rect = request.get("rect") or {}
                anchor = (int(rect.get("x", 0)), int(rect.get("y", 0))) if rect else cursor_pos()
                Toast(self, request.get("message") or "翻译失败", anchor)
                self.respond(request, ok=True)
            elif cmd == "capture":
                self.start_capture("host", request)
            elif cmd == "capture_rect":
                rect = (int(request["x"]), int(request["y"]),
                        int(request["x"]) + int(request["width"]),
                        int(request["y"]) + int(request["height"]))
                path = self.grab(rect)
                self.respond(request, ok=path is not None, path=path,
                             error=None if path else "截图失败")
            elif cmd == "close_all":
                for win in list(self.windows):
                    win.close()
                for win in list(self.inplace):
                    win.close()
                self._clear_loading()
                self.respond(request, ok=True)
            elif cmd == "show_pill":
                if self.pill:
                    self.pill.show()
                self.respond(request, ok=True)
            elif cmd == "hide_pill":
                if self.pill:
                    self.pill.hide()
                self.respond(request, ok=True)
            elif cmd == "shutdown":
                self.respond(request, ok=True)
                self.root.after(30, self.quit)
            elif cmd == "hotkey":
                self.respond(request, ok=True, active=self.hotkey_active)
            else:
                self.respond(request, ok=False, error="unknown cmd: %r" % (cmd,))
        except Exception as exc:
            log(traceback.format_exc())
            self.respond(request, ok=False, error=str(exc))

    def show_pin(self, request):
        win = PinWindow(self, request, self.cascade)
        self.windows.add(win)
        self.cascade += 1
        self.root.update_idletasks()
        self.respond(request, ok=True, x=win.top.winfo_x(), y=win.top.winfo_y(),
                     width=win.top.winfo_width(), height=win.top.winfo_height())

    def place(self, request):
        self._clear_loading()
        win = InPlaceWindow(self, request)
        self.inplace.add(win)
        self.respond(request, ok=True)

    def show_loading(self, request):
        self._clear_loading()
        self.loading = LoadingWindow(self, request.get("rect") or {})
        self.respond(request, ok=True)

    def _clear_loading(self):
        if self.loading is not None:
            try:
                self.loading.close()
            except Exception:
                pass
            self.loading = None

    def start_capture(self, source, request=None):
        if self.overlay is not None:
            if request is not None:
                self.respond(request, ok=False, error="截图已在进行中")
            return
        self._clear_loading()
        self.overlay = CaptureOverlay(self, request, source)

    def quit(self):
        try:
            if self.hotkey:
                try:
                    ctypes.windll.user32.UnregisterHotKey(None, 1)
                except Exception:
                    pass
            self.root.quit()
        except Exception:
            pass

    # ---------------------------------------------------------- 生命周期
    def run(self):
        threading.Thread(target=self._reader, daemon=True).start()
        if self.primary:
            try:
                self.pill = DesktopPill(self)
            except Exception:
                log(traceback.format_exc())
            self.hotkey = Hotkey(self)
            self.root.after(150, self._poll_hotkey)
            try:
                self.watcher = SelectionWatcher(self)
                self.root.after(150, self._poll_selection)
            except Exception:
                log(traceback.format_exc())
        self.root.mainloop()

    def _is_own_or_dsh(self):
        pid = foreground_pid()
        if not pid:
            return True
        if pid == os.getpid():
            return True
        name = process_image_name(pid).lower()
        return "deepseek" in name or "harness" in name

    def translate_selection_text(self, text, x, y):
        if self.sel_pill is not None:
            self.sel_pill.close()
            self.sel_pill = None
        text = (text or "").strip()
        if not text:
            return
        self.emit({"event": "selection", "text": text, "x": int(x), "y": int(y),
                   "source": "global"})

    def translate_selection_hotkey(self):
        # 全局热键是自己注册的，即使在 DSH 里按下也收不到页面事件；
        # 这里统一走“复制选区 → 宿主翻译 → 桌面卡片”，保证热键始终有反馈。
        if foreground_pid() == os.getpid():
            return
        text = capture_selection()
        if not text:
            Toast(self, "没有检测到选中的文字", cursor_pos())
            return
        x, y = cursor_pos()
        self.translate_selection_text(text, x, y)

    def maybe_selection(self, x, y):
        if self._is_own_or_dsh():
            return
        text = capture_selection()
        if not text or len(text) > 4000:
            return
        if not any(ch.isalpha() or "\u4e00" <= ch <= "\u9fff" for ch in text):
            return
        if self.sel_pill is not None:
            self.sel_pill.close()
        self.sel_pill = SelectionPill(self, text, x, y)

    def _poll_selection(self):
        if self.watcher is not None:
            try:
                while True:
                    event = self.watcher.queue.get_nowait()
                    if not isinstance(event, tuple) or len(event) != 3:
                        continue
                    kind, x, y = event
                    if kind == "down":
                        # 点到别处：立刻收起「译」，不等自动超时。
                        if self.sel_pill is not None and not self.sel_pill.contains(x, y):
                            self.sel_pill.close()
                    elif kind == "drag":
                        self.root.after(180, lambda x=x, y=y: self.maybe_selection(x, y))
            except queue.Empty:
                pass
        self.root.after(120, self._poll_selection)

    def _poll_hotkey(self):
        if self.hotkey:
            self.hotkey.poll()
        self.root.after(120, self._poll_hotkey)

    def _reader(self):
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except Exception:
                log("bad json: %r" % line[:200])
                continue
            if request.get("id") in self.asks and ("answer" in request or "error" in request):
                callback = self.asks.pop(request["id"])
                ok = bool(request.get("ok"))
                text = request.get("answer") if ok else request.get("error", "请求失败")
                self.root.after(0, lambda cb=callback, ok=ok, text=text: cb(ok, text))
                continue
            self.root.after(0, lambda r=request: self.handle(r))
        try:
            self.root.after(0, self.root.quit)
        except Exception:
            pass


if __name__ == "__main__":
    App().run()










