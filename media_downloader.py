# -*- coding: utf-8 -*-
"""
网页音视频资源下载器
- 支持上千个网站（基于 yt-dlp）：YouTube、B站、Twitter/X、抖音等
- 三种模式：仅音频 / 仅视频(无声画面) / 音视频合并
- 合并与音频转码依赖 ffmpeg（打包时内置）
"""

import os
import sys
import json
import time
import queue
import shutil
import threading
import traceback
import ctypes

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from PIL import Image, ImageTk, ImageDraw, ImageChops

try:
    import yt_dlp
    from yt_dlp import utils as _ydlp_utils
except Exception as _e:  # pragma: no cover
    yt_dlp = None
    _ydlp_utils = None


# ---------------------------------------------------------------- 路径与工具

def app_dir():
    """程序所在目录（打包后是 exe 所在目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def bundle_dir():
    """PyInstaller onefile 解包目录（运行期临时目录）。"""
    return getattr(sys, "_MEIPASS", app_dir())


def find_icon_path():
    """查找打包资源 / assets 目录中的 icon.ico，找不到返回 None。"""
    candidates = [
        os.path.join(bundle_dir(), "assets", "icon.ico"),
        os.path.join(bundle_dir(), "icon.ico"),
        os.path.join(app_dir(), "assets", "icon.ico"),
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return None


# ---------------------------------------------------------------- 皮肤管理

def skins_dir():
    """用户皮肤目录（位于 exe 同目录，便于跨版本保留）。"""
    return os.path.join(app_dir(), "skins")


def skin_config_path():
    return os.path.join(skins_dir(), "skin.json")


def _read_config():
    try:
        with open(skin_config_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_config(data):
    os.makedirs(skins_dir(), exist_ok=True)
    with open(skin_config_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def default_skin_path():
    """内置默认背景皮肤。"""
    for p in (os.path.join(bundle_dir(), "assets", "skin.default.jpg"),
              os.path.join(app_dir(), "assets", "skin.default.jpg")):
        if p and os.path.isfile(p):
            return p
    return None


def default_panel_path():
    """内置默认右侧面板图。"""
    for p in (os.path.join(bundle_dir(), "assets", "panel.default.jpg"),
              os.path.join(app_dir(), "assets", "panel.default.jpg")):
        if p and os.path.isfile(p):
            return p
    return None


def current_skin_path():
    """当前背景图：用户自定义优先，否则默认。"""
    custom = _read_config().get("skin_file")
    if custom:
        p = os.path.join(skins_dir(), os.path.basename(custom))
        if os.path.isfile(p):
            return p
    return default_skin_path()


def current_panel_path():
    """当前右侧面板图：用户自定义优先，否则默认。"""
    custom = _read_config().get("panel_file")
    if custom:
        p = os.path.join(skins_dir(), os.path.basename(custom))
        if os.path.isfile(p):
            return p
    return default_panel_path()


def _copy_to_skins(src_path, prefix):
    os.makedirs(skins_dir(), exist_ok=True)
    ext = os.path.splitext(src_path)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".bmp",
                   ".mp4", ".webm", ".mkv", ".mov", ".avi", ".gif"):
        ext = ".jpg"
    dst = os.path.join(skins_dir(), prefix + ext)
    shutil.copyfile(src_path, dst)
    return os.path.basename(dst)


def import_skin(src_path):
    """更换背景皮肤图片。"""
    name = _copy_to_skins(src_path, "skin_custom")
    data = _read_config()
    data["skin_file"] = name
    _write_config(data)
    return os.path.join(skins_dir(), name)


def import_panel(src_path):
    """更换右侧面板图片。"""
    name = _copy_to_skins(src_path, "panel_custom")
    data = _read_config()
    data["panel_file"] = name
    _write_config(data)
    return os.path.join(skins_dir(), name)


def reset_skin():
    """恢复默认背景皮肤。"""
    data = _read_config()
    data.pop("skin_file", None)
    _write_config(data)
    if os.path.isdir(skins_dir()):
        for f in os.listdir(skins_dir()):
            if f.lower().startswith("skin_custom"):
                try:
                    os.remove(os.path.join(skins_dir(), f))
                except OSError:
                    pass


def reset_panel():
    """恢复默认右侧面板。"""
    data = _read_config()
    data.pop("panel_file", None)
    _write_config(data)
    if os.path.isdir(skins_dir()):
        for f in os.listdir(skins_dir()):
            if f.lower().startswith("panel_custom"):
                try:
                    os.remove(os.path.join(skins_dir(), f))
                except OSError:
                    pass


def cover_resize(img, w, h):
    """等比放大并居中裁剪，铺满 w×h（类似 CSS object-fit: cover）。"""
    src_w, src_h = img.size
    scale = max(w / src_w, h / src_h)
    new_w, new_h = max(1, round(src_w * scale)), max(1, round(src_h * scale))
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - w) // 2
    top = (new_h - h) // 2
    return img.crop((left, top, left + w, top + h))


def cover_image(path, w, h, mode="RGB"):
    """读取图片并按 cover 方式缩放到 w×h，失败返回 None。"""
    try:
        img = Image.open(path).convert(mode)
    except Exception:
        return None
    return cover_resize(img, w, h)


def circle_thumb(path, size):
    """生成正方形居中裁剪后的圆形缩略图，失败返回 None。"""
    try:
        img = Image.open(path).convert("RGBA")
    except Exception:
        return None
    side = min(img.size)
    img = img.crop(((img.width - side) // 2, (img.height - side) // 2,
                    (img.width + side) // 2, (img.height + side) // 2))
    img = img.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    img.putalpha(mask)
    return img


# ---------- 视频背景支持 ----------

VIDEO_EXTS = (".mp4", ".webm", ".mkv", ".mov", ".avi", ".gif")


def is_video_path(path):
    return path and os.path.splitext(path)[1].lower() in VIDEO_EXTS


class VideoBackgroundPlayer:
    """用 ffmpeg 子进程从视频文件读取 RGB 帧，线程安全队列供主线程消费。"""

    def __init__(self, video_path, width, height, fps=20, ffmpeg_exe="ffmpeg"):
        self.path = video_path
        self.w = max(2, width)
        self.h = max(2, height)
        self.fps = fps
        self.ffmpeg = ffmpeg_exe
        self._proc = None
        self._thread = None
        self._queue = queue.Queue(maxsize=3)
        self._stop = threading.Event()

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._proc:
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None
        # 清空队列
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass

    def get_frame(self):
        """非阻塞取一帧 PIL.Image，无可用帧返回 None。"""
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def _read_loop(self):
        import subprocess
        vf = (f"scale={self.w}:{self.h}:force_original_aspect_ratio=increase,"
              f"crop={self.w}:{self.h}")
        while not self._stop.is_set():
            try:
                self._proc = subprocess.Popen(
                    [self.ffmpeg, "-i", self.path,
                     "-vf", vf, "-pix_fmt", "rgba",
                     "-r", str(self.fps), "-f", "rawvideo", "-"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    creationflags=0x08000000)  # CREATE_NO_WINDOW
            except Exception:
                return
            frame_size = self.w * self.h * 4
            while not self._stop.is_set():
                raw = self._proc.stdout.read(frame_size)
                if len(raw) < frame_size:
                    break  # 视频结束，外层循环重新启动实现循环播放
                if self._stop.is_set():
                    break
                img = Image.frombytes("RGBA", (self.w, self.h), raw)
                try:
                    self._queue.put(img, timeout=0.5)
                except queue.Full:
                    # 主线程消费慢，丢弃旧帧保持最新
                    try:
                        self._queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        self._queue.put(img, timeout=0.5)
                    except queue.Full:
                        pass
            if self._proc:
                try:
                    self._proc.kill()
                except Exception:
                    pass
                self._proc = None


def find_ffmpeg_dir():
    """依次在打包资源、程序目录、bin 子目录里寻找 ffmpeg.exe，找不到返回 None。"""
    candidates = [
        bundle_dir(),
        app_dir(),
        os.path.join(app_dir(), "bin"),
    ]
    for d in candidates:
        if d and os.path.isfile(os.path.join(d, "ffmpeg.exe")):
            return d
    return None


def human_bytes(n):
    if not n:
        return "0 B"
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} GB"


def human_eta(sec):
    if sec is None:
        return "--:--"
    sec = int(sec)
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def default_download_dir():
    d = os.path.join(os.path.expanduser("~"), "Downloads")
    return d if os.path.isdir(d) else os.path.expanduser("~")


# ---------------------------------------------------------------- 下载核心

QUALITY_MAP = {
    "最佳画质": None,
    "2160p (4K)": 2160,
    "1440p (2K)": 1440,
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
}

AUDIO_MAP = {
    "MP3": "mp3",
    "M4A (AAC)": "m4a",
    "原始格式": None,
}


def build_format_selector(mode, height):
    """根据模式与清晰度上限生成 yt-dlp format 表达式。"""
    h = f"[height<={height}]" if height else ""
    if mode == "audio":
        return "bestaudio/best"
    if mode == "video":
        # 只要画面流；若站点不提供分离视频流，则回退到带声音的普通流
        return f"bestvideo{h}/best{h}/best"
    # merge：分离的最佳视频+最佳音频，回退到一体化流
    return f"bestvideo{h}+bestaudio/best{h}/best"


class DownloadJob(threading.Thread):
    def __init__(self, url, mode, quality_label, audio_label, out_dir,
                 single_only, ffmpeg_dir, msg_q):
        super().__init__(daemon=True)
        self.url = url
        self.mode = mode
        self.quality_label = quality_label
        self.audio_label = audio_label
        self.out_dir = out_dir
        self.single_only = single_only
        self.ffmpeg_dir = ffmpeg_dir
        self.q = msg_q
        self.cancel_event = threading.Event()

    def cancel(self):
        self.cancel_event.set()

    def _log(self, text):
        self.q.put(("log", text))

    def _progress_hook(self, d):
        if self.cancel_event.is_set():
            # yt-dlp 支持在 hook 中抛出该异常来中止下载
            exc_cls = getattr(_ydlp_utils, "DownloadCancelled", None)
            if exc_cls is not None:
                raise exc_cls("用户取消下载")
            raise RuntimeError("用户取消下载")

        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes") or 0
            if total:
                frac = downloaded / total
                bar = f"{frac * 100:.1f}%"
            else:
                frac = -1.0
                bar = d.get("_percent_str", "").strip() or "下载中"
            speed = human_bytes(d.get("speed")) + "/s" if d.get("speed") else ""
            eta = human_eta(d.get("eta"))
            name = os.path.basename(d.get("filename", "") or "")
            self.q.put(("progress", frac,
                        f"下载中 {bar}  {speed}  剩余 {eta}  {name}"))
        elif status == "finished":
            self.q.put(("log", "下载完成，正在后处理（转码/合并）..."))

    def _pp_hook(self, d):
        if self.cancel_event.is_set():
            exc_cls = getattr(_ydlp_utils, "DownloadCancelled", None)
            if exc_cls is not None:
                raise exc_cls("用户取消下载")
            raise RuntimeError("用户取消下载")
        if d.get("status") == "started":
            pp = d.get("postprocessor")
            name_map = {
                "FFmpegExtractAudio": "提取/转换音频",
                "FFmpegVideoConvertor": "转换视频容器",
                "FFmpegMerger": "合并音视频",
                "Merger": "合并音视频",
                "MoveFiles": "整理文件",
            }
            self.q.put(("log", f"后处理：{name_map.get(pp, pp)} ..."))

    def run(self):
        try:
            self._run_inner()
        except Exception as e:
            name = type(e).__name__
            if "cancel" in str(e).lower() or name == "DownloadCancelled":
                self.q.put(("log", "已取消下载。"))
                self.q.put(("done", False, "已取消"))
            else:
                self.q.put(("log", "下载失败：\n" + traceback.format_exc()))
                self.q.put(("done", False, f"失败：{e}"))

    def _run_inner(self):
        height = QUALITY_MAP[self.quality_label]
        fmt = build_format_selector(self.mode, height)

        out_tmpl = os.path.join(self.out_dir, "%(title)s [%(id)s].%(ext)s")
        opts = {
            "format": fmt,
            "outtmpl": out_tmpl,
            "noplaylist": self.single_only,
            "quiet": True,
            "no_warnings": False,
            "noprogress": True,
            "ignoreerrors": False,
            # 网络容错：更多重试 + 指数退避（外层重试在 _run_inner 中实现）
            "retries": 10,
            "fragment_retries": 10,
            "retry_sleep_options": {
                "extractor": 5,
                "http": 5,
                "fragment": 5,
                "file_access": 5,
            },
            "concurrent_fragment_downloads": 1,  # 串行下载，避免触发 B站等站点并发限流
            "progress_hooks": [self._progress_hook],
            "postprocessor_hooks": [self._pp_hook],
            "logger": _QueueLogger(self.q),
        }
        if self.ffmpeg_dir:
            opts["ffmpeg_location"] = self.ffmpeg_dir

        if self.mode == "audio":
            codec = AUDIO_MAP[self.audio_label]
            if codec:
                opts["postprocessors"] = [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": codec,
                    "preferredquality": "0",
                }]
        elif self.mode == "merge":
            opts["merge_output_format"] = "mp4"
            opts["postprocessors"] = [{
                "key": "FFmpegMetadata",
            }]
        # video（仅画面）：不加后处理器，保留原始视频流

        if self.mode in ("audio", "merge") and not self.ffmpeg_dir:
            self.q.put(("log", "警告：未找到 ffmpeg，将保留原始格式且无法合并/转码。"))

        self._log(f"网址：{self.url}")
        mode_text = {"audio": "仅音频", "video": "仅视频（无声）",
                     "merge": "音视频合并"}[self.mode]
        self._log(f"模式：{mode_text}    画质：{self.quality_label}")
        self._log(f"保存到：{self.out_dir}")

        # 预取标题（不下载），用于右侧面板显示
        try:
            with yt_dlp.YoutubeDL(dict(opts, quiet=True, no_warnings=True,
                                       skip_download=True)) as ydl:
                info0 = ydl.extract_info(self.url, download=False)
            title = (info0 or {}).get("title") or self.url
            self.q.put(("info", title,
                        f"清晰度：{self.quality_label}　音频：{self.audio_label}"))
        except Exception:
            pass

        # 外层重试：yt-dlp 内部 10 次重试用尽后，等待一段时间再整体重试，
        # 应对 B站 CDN 持续 503 / 断连的情况。每轮从零开始下载。
        MAX_OUTER_RETRIES = 3
        OUTER_RETRY_WAIT = 30  # 秒

        info = None
        for attempt in range(1, MAX_OUTER_RETRIES + 1):
            if self.cancel_event.is_set():
                raise RuntimeError("用户取消下载")
            if attempt > 1:
                self._log(f"等待 {OUTER_RETRY_WAIT} 秒后重试"
                          f"（第 {attempt}/{MAX_OUTER_RETRIES} 轮）...")
                for _ in range(OUTER_RETRY_WAIT):
                    if self.cancel_event.is_set():
                        raise RuntimeError("用户取消下载")
                    time.sleep(1)

            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(self.url, download=True)
                break  # 成功，跳出重试循环
            except Exception as e:
                if self.cancel_event.is_set():
                    raise
                if attempt < MAX_OUTER_RETRIES:
                    self._log(f"第 {attempt}/{MAX_OUTER_RETRIES} 轮失败：{e}")
                else:
                    raise

        # 取得最终文件路径（兼容播放列表场景）
        paths = []
        entries = [info] if info and "entries" not in info else (info or {}).get("entries") or []
        for it in entries:
            if not it:
                continue
            p = it.get("requested_downloads")
            if p:
                paths.extend(x.get("filepath", "") for x in p)
            elif it.get("filepath"):
                paths.append(it["filepath"])
        paths = [p for p in paths if p]
        if paths:
            self._log("生成文件：\n  " + "\n  ".join(paths))
        self.q.put(("progress", 1.0, "完成"))
        self.q.put(("done", True, "下载完成"))


class _QueueLogger:
    """把 yt-dlp 的日志转发到消息队列。"""
    def __init__(self, q):
        self.q = q

    def debug(self, msg):
        # yt-dlp 的普通 info 也走 debug 通道，过滤掉纯调试信息
        if msg and not msg.startswith("[debug]"):
            self.q.put(("log", msg))

    def info(self, msg):
        if msg:
            self.q.put(("log", msg))

    def warning(self, msg):
        if msg:
            self.q.put(("log", "警告：" + msg))

    def error(self, msg):
        if msg:
            self.q.put(("log", "错误：" + msg))


# ---------------------------------------------------------------- GUI

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("网页音视频下载器")
        self.geometry("1080x720+80+60")
        self.minsize(920, 620)

        # 窗口标题栏 / 任务栏图标
        icon = find_icon_path()
        if icon:
            try:
                self.iconbitmap(default=icon)
            except Exception:
                pass

        self.msg_q = queue.Queue()
        self.job = None
        self.ffmpeg_dir = find_ffmpeg_dir()

        # 无边框圆角窗口相关状态
        self._win_mode = None       # "move" / "resize" / None
        self._drag_start = None
        self._maximized = False
        self._saved_geom = None
        # 视频背景播放器
        self._video_player = None
        self._video_after = None
        self._glass_overlay = None     # 预渲染的玻璃覆盖层 RGBA
        self._glass_photo = None       # 玻璃覆盖层 PhotoImage（视频模式用）
        self._glass_rgb = None         # 玻璃 RGB 通道（视频帧合成用）
        self._glass_alpha = None       # 玻璃 alpha 通道（视频帧合成用）
        self._corner_mask = None       # 圆角遮罩（视频帧合成用）
        self._video_mode = False       # 是否处于视频背景模式

        self._build_ui()
        self._on_mode_change()
        self.after(120, self._poll_queue)
        self.after(60, self._enable_frameless)

        if yt_dlp is None:
            messagebox.showerror("启动错误", "缺少 yt-dlp 组件，请重新获取本程序。")
            self.after(100, self.destroy)

    LEFT_W = 180
    RIGHT_W = 330
    AVATAR_SIZE = 84
    TITLE_H = 48          # 自定义标题栏高度
    WIN_RADIUS = 24       # 窗口外圆角半径
    TRANS_KEY = "#ff00fe"  # 透明色键（窗口四角用）

    # iOS 玻璃配色
    GLASS_FILL = (255, 255, 255, 148)     # 白色半透明玻璃（透出背景）
    GLASS_EDGE = (255, 255, 255, 120)     # 顶部高光边
    TEXT_DARK = "#1d2230"
    TEXT_GREY = "#5c6478"
    FIELD_BG = "#ffffff"
    FIELD_BD = "#d3d8e2"
    IOS_BLUE = "#0a84ff"
    IOS_PINK = "#ff375f"

    # ---- 主题 ----
    def _setup_theme(self):
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        fg = self.TEXT_DARK
        field = self.FIELD_BG
        border = self.FIELD_BD
        # ttk 控件背景与玻璃面板一致（白），视觉上融入
        glass_hex = "#eef1f7"
        s.configure(".", background=glass_hex, foreground=fg,
                    font=("Microsoft YaHei UI", 10))
        s.configure("TFrame", background=glass_hex)
        s.configure("TLabel", background=glass_hex, foreground=fg)
        s.configure("TEntry", fieldbackground=field, foreground=fg,
                    insertcolor=fg, bordercolor=border, lightcolor=border,
                    darkcolor=border, padding=5)
        s.configure("TCombobox", fieldbackground=field, foreground=fg,
                    background=field, bordercolor=border, lightcolor=border,
                    darkcolor=border, arrowcolor="#5c6478", padding=3)
        s.map("TCombobox",
              fieldbackground=[("readonly", field)],
              foreground=[("readonly", fg)],
              bordercolor=[("focus", self.IOS_BLUE)])
        # 下拉列表配色
        self.option_add("*TCombobox*Listbox.background", field)
        self.option_add("*TCombobox*Listbox.foreground", fg)
        self.option_add("*TCombobox*Listbox.selectBackground", self.IOS_BLUE)
        self.option_add("*TCombobox*Listbox.selectForeground", "white")

    # ---- 界面构建 ----
    def _build_ui(self):
        self._setup_theme()
        self._bg_photo = None
        self._avatar_photo = None
        self._bg_after = None
        self._prog_rect = None          # 进度条几何 (x,y,w,h)
        self._seg_geom = None           # 分段控件几何
        self._chk_rect = None           # 勾选框几何
        self._progress_frac = 0.0
        self._btn_imgs = {}             # 自绘按钮图片引用（防 GC）

        self.configure(bg=self.TRANS_KEY)
        self.bg_canvas = tk.Canvas(self, highlightthickness=0,
                                   bg=self.TRANS_KEY, bd=0)
        self.bg_canvas.pack(fill="both", expand=True)
        self.bg_canvas.bind("<Configure>", self._on_bg_configure)
        # 自定义标题栏：拖动移动 / 右下角缩放 / 双击最大化
        self.bg_canvas.bind("<ButtonPress-1>", self._win_press)
        self.bg_canvas.bind("<B1-Motion>", self._win_drag)
        self.bg_canvas.bind("<ButtonRelease-1>", self._win_release)
        self.bg_canvas.bind("<Double-Button-1>", self._win_double)
        self.bg_canvas.bind("<Motion>", self._win_hover)

        # ---------- 中心控件（仅保留原生输入控件，按钮全部自绘） ----------
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(self.bg_canvas, textvariable=self.url_var,
                                   font=("Microsoft YaHei UI", 10))

        self.mode_var = tk.StringVar(value="merge")

        self.quality_var = tk.StringVar(value="最佳画质")
        self.quality_combo = ttk.Combobox(
            self.bg_canvas, textvariable=self.quality_var, state="readonly",
            values=list(QUALITY_MAP.keys()), width=12)
        self.audio_var = tk.StringVar(value="MP3")
        self.audio_combo = ttk.Combobox(
            self.bg_canvas, textvariable=self.audio_var, state="readonly",
            values=list(AUDIO_MAP.keys()), width=10)
        # 强制初始值写入，避免部分主题下首帧空白
        self.quality_combo.set("最佳画质")
        self.audio_combo.set("MP3")

        self.single_var = tk.BooleanVar(value=True)

        self.dir_var = tk.StringVar(value=default_download_dir())
        self.dir_entry = ttk.Entry(self.bg_canvas, textvariable=self.dir_var)

        # ---------- 右侧面板状态 ----------
        self.title_var = tk.StringVar(value="等待解析视频信息…")
        self.fmt_var = tk.StringVar(value="清晰度：—　音频格式：—")
        self.action_var = tk.StringVar(value="就绪")
        self.status_var = self.action_var

        self.after(10, self._layout)

    # ---- 背景渲染 ----
    def _render_background(self, W, H, lx, ly, lw, lh, rx, ry, rw, rh):
        skin = current_skin_path()
        self._video_mode = skin is not None and is_video_path(skin)
        # 预渲染玻璃覆盖层（含圆角遮罩），供视频帧和静态图共用
        self._glass_overlay = self._render_glass_overlay(
            W, H, lx, ly, lw, lh, rx, ry, rw, rh)

        if skin and is_video_path(skin):
            # 视频背景：停掉旧播放器，启动新的
            self._stop_video_bg()
            self._start_video_bg(skin, W, H)
        else:
            # 静态图片背景：停掉视频，一次性渲染
            self._stop_video_bg()
            img = cover_image(skin, W, H) if skin else None
            if img is None:
                img = Image.new("RGB", (W, H), "#23283a")
            img = img.convert("RGBA")
            img = Image.alpha_composite(img, self._glass_overlay)
            self._bg_photo = ImageTk.PhotoImage(img)
            self.bg_canvas.delete("bg")
            self.bg_canvas.create_image(
                0, 0, anchor="nw", image=self._bg_photo, tags="bg")
            self.bg_canvas.tag_lower("bg")

    def _render_glass_overlay(self, W, H, lx, ly, lw, lh, rx, ry, rw, rh):
        """生成含玻璃面板 + 圆角遮罩的 RGBA 覆盖层。"""
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        radius = 26
        panels = [(lx, ly, lx + lw, ly + lh),
                  (rx, ry, rx + rw, ry + rh)]
        cx0 = lx + lw + 16
        cx1 = rx - 16
        if cx1 > cx0 + 2:
            panels.append((cx0, ly, cx1, ly + lh))
        for box in panels:
            od.rounded_rectangle(box, radius=radius, fill=self.GLASS_FILL)
            od.arc([box[0], box[1], box[0] + 2 * radius, box[1] + 2 * radius],
                   start=180, end=270, fill=self.GLASS_EDGE, width=2)
            od.line([box[0] + radius, box[1], box[2] - radius, box[1]],
                    fill=self.GLASS_EDGE, width=2)
            od.arc([box[2] - 2 * radius, box[1], box[2], box[1] + 2 * radius],
                   start=270, end=360, fill=self.GLASS_EDGE, width=2)
        # 圆角遮罩：窗口四角透明
        wr = min(self.WIN_RADIUS, W // 2, H // 2)
        mask = Image.new("L", (W, H), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, W - 1, H - 1], radius=wr, fill=255)
        # 将遮罩与玻璃 alpha 相乘（四角透明 + 玻璃面板半透明）
        overlay.putalpha(ImageChops.multiply(overlay.split()[3], mask))
        # 预计算供视频帧合成使用的通道
        self._glass_rgb = overlay.convert("RGB")
        self._glass_alpha = overlay.split()[3]
        self._corner_mask = mask
        return overlay

    # ---- 视频背景 ----
    def _start_video_bg(self, path, W, H):
        ffmpeg_exe = "ffmpeg"
        if self.ffmpeg_dir:
            ffmpeg_exe = os.path.join(self.ffmpeg_dir, "ffmpeg.exe")
        self._video_player = VideoBackgroundPlayer(path, W, H, fps=30,
                                                   ffmpeg_exe=ffmpeg_exe)
        self._video_player.start()
        self._update_video_frame()

    def _stop_video_bg(self):
        if self._video_after:
            self.after_cancel(self._video_after)
            self._video_after = None
        if self._video_player:
            self._video_player.stop()
            self._video_player = None
        self._bg_photo = None
        self._glass_photo = None
        self.bg_canvas.delete("bg_video")
        self.bg_canvas.delete("bg_overlay")

    def _update_video_frame(self):
        """从视频播放器取帧，仅加圆角 alpha 后更新画布底层。
        玻璃覆盖层作为独立 canvas image 叠在上方，由 Tk 原生做 alpha 混合。"""
        if not self._video_player or not self._corner_mask:
            return
        frame = self._video_player.get_frame()
        if frame is not None:
            frame.putalpha(self._corner_mask)  # 直接替换 alpha 通道
            if self._bg_photo is None:
                self._bg_photo = ImageTk.PhotoImage(frame)
                self.bg_canvas.create_image(
                    0, 0, anchor="nw", image=self._bg_photo, tags="bg_video")
                # 玻璃覆盖层叠在视频之上（仅创建一次）
                self._glass_photo = ImageTk.PhotoImage(self._glass_overlay)
                self.bg_canvas.create_image(
                    0, 0, anchor="nw", image=self._glass_photo, tags="bg_overlay")
                self.bg_canvas.tag_lower("bg_video")
                self.bg_canvas.tag_lower("bg_overlay")
            else:
                self._bg_photo.paste(frame)
        self._video_after = self.after(20, self._update_video_frame)

    def _set_text(self, tag, x, y, text, **kw):
        """在 canvas 上放置/更新一个文字项（透明背景）。"""
        self.bg_canvas.delete(tag)
        kw.setdefault("anchor", "w")
        kw.setdefault("fill", self.TEXT_DARK)
        kw.setdefault("font", ("Microsoft YaHei UI", 10))
        self.bg_canvas.create_text(x, y, text=text, tags=tag, **kw)

    def _update_text(self, tag, text):
        """仅更新已有文字项内容，不重渲染背景。"""
        items = self.bg_canvas.find_withtag(tag)
        if items:
            self.bg_canvas.itemconfigure(items[0], text=text[:80])

    # ---- 自绘圆角控件 ----
    def _rounded_img(self, w, h, radius, fill, outline=None, ow=1):
        """生成一张圆角矩形 RGBA 图片。fill/outline 为 (r,g,b,a)。"""
        scale = 2  # 超采样抗锯齿
        im = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        box = [ow * scale, ow * scale,
               w * scale - ow * scale - 1, h * scale - ow * scale - 1]
        d.rounded_rectangle(box, radius=radius * scale, fill=fill,
                            outline=outline, width=ow * scale)
        return im.resize((w, h), Image.LANCZOS)

    def _draw_button(self, tag, x, y, w, h, text, kind="glass",
                     command=None, enabled=True):
        """在主画布上绘制 iOS 风格圆角按钮。kind: blue/glass/gray/ghost/dark。"""
        self.bg_canvas.delete(tag)
        if not enabled:
            fill = (209, 214, 224, 200)
            tcolor = "#9aa2b2"
            outline = None
        elif kind == "dark":
            # 标题栏深色半透明胶囊；关闭按钮悬停变红
            if tag == "b_close":
                fill = (255, 55, 95, 235)
            else:
                fill = (18, 20, 28, 150)
            tcolor = "#ffffff"
            outline = (255, 255, 255, 70)
        elif kind == "blue":
            fill = (10, 132, 255, 255)
            tcolor = "#ffffff"
            outline = None
        elif kind == "gray":
            fill = (228, 231, 237, 235)
            tcolor = self.TEXT_DARK
            outline = None
        elif kind == "ghost":
            fill = (255, 255, 255, 60)
            tcolor = self.TEXT_DARK
            outline = (255, 255, 255, 160)
        elif kind == "ghost_blue":
            fill = (10, 132, 255, 90)
            tcolor = "#ffffff"
            outline = (10, 132, 255, 200)
        else:  # glass：浅色玻璃按钮
            fill = (255, 255, 255, 150)
            tcolor = self.TEXT_DARK
            outline = (255, 255, 255, 200)
        im = self._rounded_img(w, h, h // 2, fill, outline)
        photo = ImageTk.PhotoImage(im)
        self._btn_imgs[tag] = photo
        cid = self.bg_canvas.create_image(x, y, anchor="nw", image=photo,
                                          tags=(tag, "ctrl"))
        tid = self.bg_canvas.create_text(
            x + w // 2, y + h // 2, text=text, tags=(tag, "ctrl"),
            font=("Microsoft YaHei UI", 10, "bold" if kind == "blue" else "normal"),
            fill=tcolor)
        if command and enabled:
            for i in (cid, tid):
                self.bg_canvas.tag_bind(i, "<Button-1>",
                                        lambda _e, c=command: c())
                self.bg_canvas.tag_bind(i, "<Enter>",
                                        lambda _e, t=tag: self.bg_canvas.configure(cursor="hand2"))
                self.bg_canvas.tag_bind(i, "<Leave>",
                                        lambda _e: self.bg_canvas.configure(cursor=""))

    def _draw_ghost_field(self, tag, x, y, w, h, textvar, widget, is_combo=False):
        """视频模式：用 ghost 样式 canvas 替代 ttk 控件，点击时弹出真实控件编辑。"""
        self.bg_canvas.delete(tag)
        # ghost 半透明背景 + 白色边框（同"打开皮肤文件夹"按钮风格）
        im = self._rounded_img(w, h, min(h // 2, 12),
                               (255, 255, 255, 60), (255, 255, 255, 160))
        photo = ImageTk.PhotoImage(im)
        self._btn_imgs[tag + "_g"] = photo
        bg_id = self.bg_canvas.create_image(
            x, y, anchor="nw", image=photo, tags=(tag, "ctrl"))
        # 文字
        tid = self.bg_canvas.create_text(
            x + 12, y + h // 2, text=textvar.get(), anchor="w",
            tags=(tag, "ctrl"), font=("Microsoft YaHei UI", 10),
            fill=self.TEXT_DARK)
        items = [bg_id, tid]
        if is_combo:
            aid = self.bg_canvas.create_text(
                x + w - 14, y + h // 2, text="\u25be", tags=(tag, "ctrl"),
                font=("Microsoft YaHei UI", 10), fill=self.TEXT_GREY)
            items.append(aid)

        def on_click(_e):
            widget.place(x=x, y=y, width=w, height=h)
            widget.focus_set()
            if is_combo:
                widget.event_generate("<Button-1>")
            for it in items:
                self.bg_canvas.itemconfigure(it, state="hidden")

        def on_done(_e=None):
            try:
                widget.place_forget()
            except Exception:
                pass
            try:
                self.bg_canvas.itemconfig(tid, text=textvar.get())
            except Exception:
                pass
            for it in items:
                try:
                    self.bg_canvas.itemconfigure(it, state="normal")
                except Exception:
                    pass

        for it in items:
            self.bg_canvas.tag_bind(it, "<Button-1>", on_click)
            self.bg_canvas.tag_bind(it, "<Enter>",
                                    lambda _e: self.bg_canvas.configure(cursor="xterm"))
            self.bg_canvas.tag_bind(it, "<Leave>",
                                    lambda _e: self.bg_canvas.configure(cursor=""))
        if is_combo:
            widget.bind("<<ComboboxSelected>>", on_done)
        widget.bind("<FocusOut>", on_done)

    def _draw_segmented(self, tag, x, y, w, h, options, variable, command=None):
        """iOS 分段控件。options: [(显示文字, 值), ...]，画在主画布上。"""
        self.bg_canvas.delete(tag)
        self._seg_geom = (x, y, w, h, options, variable, command)
        scale = 2
        im = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        # 容器
        d.rounded_rectangle([0, 0, w * scale - 1, h * scale - 1],
                            radius=h * scale // 2, fill=(120, 128, 145, 55))
        # 选中滑块
        n = len(options)
        sw = w / n
        sel = variable.get()
        idx = next((i for i, (_, v) in enumerate(options) if v == sel), 0)
        pad = 3 * scale
        sx0 = idx * sw * scale + pad
        sx1 = (idx + 1) * sw * scale - pad
        if self._video_mode:
            d.rounded_rectangle([sx0, pad, sx1, h * scale - pad - 1],
                                radius=(h - 3) * scale // 2,
                                fill=(255, 255, 255, 60),
                                outline=(255, 255, 255, 160), width=scale)
        else:
            d.rounded_rectangle([sx0, pad, sx1, h * scale - pad - 1],
                                radius=(h - 3) * scale // 2,
                                fill=(255, 255, 255, 255))
        photo = ImageTk.PhotoImage(im.resize((w, h), Image.LANCZOS))
        self._btn_imgs[tag] = photo
        iid = self.bg_canvas.create_image(x, y, anchor="nw", image=photo,
                                          tags=(tag, "ctrl"))
        text_ids = []
        for i, (label, val) in enumerate(options):
            active = (val == sel)
            tid = self.bg_canvas.create_text(
                x + int((i + 0.5) * sw), y + h // 2, text=label,
                tags=(tag, "ctrl"),
                font=("Microsoft YaHei UI", 9, "bold" if active else "normal"),
                fill=self.TEXT_DARK if active else self.TEXT_GREY)
            text_ids.append(tid)
        # 整块区域（图片 + 每个文字）统一按点击 X 坐标判定分段，
        # 不能只绑定文字笔画——否则点中滑块空白处不会切换
        handler = lambda e: self._segment_click(
            x, w, options, variable, command, e)
        self.bg_canvas.tag_bind(iid, "<Button-1>", handler)
        for tid in text_ids:
            self.bg_canvas.tag_bind(tid, "<Button-1>", handler)

    def _select_segment(self, value, variable, command):
        variable.set(value)
        if command:
            command()

    def _segment_click(self, x, w, options, variable, command, event):
        n = len(options)
        sw = w / n
        idx = min(n - 1, max(0, int((event.x - x) / sw)))
        self._select_segment(options[idx][1], variable, command)

    def _layout(self):
        W = max(1, self.winfo_width())
        H = max(1, self.winfo_height())
        if W < 200 or H < 200:
            self.after(100, self._layout)
            return

        M = 16
        top = self.TITLE_H + 4          # 面板起始 Y（让出标题栏）
        lw = self.LEFT_W
        rw = self.RIGHT_W
        lx, ly = M, top
        lh = H - top - M
        rx = W - rw - M
        ry = top
        rh = H - top - M
        cx = lx + lw + 16
        cw = rx - cx - 16

        self._render_background(W, H, lx, ly, lw, lh, rx, ry, rw, rh)
        self._place_titlebar(W)
        self._place_left(lx, ly, lw, lh)
        self._place_center(cx, ly, cw, lh)
        self._place_right(rx, ry, rw, rh)

    # ---- 自定义标题栏 ----
    def _place_titlebar(self, W):
        self.bg_canvas.delete("titlebar")
        # 小图标
        icon = find_icon_path()
        if icon:
            img = circle_thumb(icon, 26)
            if img is not None:
                self._tb_icon = ImageTk.PhotoImage(img)
                self.bg_canvas.create_image(
                    18, 11, anchor="nw", image=self._tb_icon,
                    tags="titlebar")
        # 标题文字（阴影 + 白字，保证在任意背景上可读）
        self.bg_canvas.create_text(
            54, 24, text="网页音视频下载器", anchor="w",
            font=("Microsoft YaHei UI", 10, "bold"),
            fill="#000000", tags="titlebar")
        self.bg_canvas.create_text(
            53, 23, text="网页音视频下载器", anchor="w",
            font=("Microsoft YaHei UI", 10, "bold"),
            fill="#ffffff", tags="titlebar")
        # 最小化 / 关闭按钮（深色半透明胶囊）
        self._draw_button("b_min", W - 104, 10, 44, 28, "—",
                          "dark", self._win_minimize)
        self._draw_button("b_close", W - 52, 10, 40, 28, "✕",
                          "dark", self._win_close)

    def _enable_frameless(self):
        """去掉原生边框，开启透明色键，并让窗口出现在任务栏。"""
        try:
            self.overrideredirect(True)
            self.attributes("-transparentcolor", self.TRANS_KEY)
        except tk.TclError:
            return
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            GWL_EXSTYLE = -20
            WS_EX_APPWINDOW = 0x00040000
            cur = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE, cur | WS_EX_APPWINDOW)
            # 恢复无边框窗口的阴影
            class _MARGINS(ctypes.Structure):
                _fields_ = [("cxLeftWidth", ctypes.c_int),
                            ("cxRightWidth", ctypes.c_int),
                            ("cyTopHeight", ctypes.c_int),
                            ("cyBottomHeight", ctypes.c_int)]
            m = _MARGINS(1, 1, 1, 1)
            try:
                ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(
                    hwnd, ctypes.byref(m))
            except Exception:
                pass
        except Exception:
            pass

    def _win_press(self, e):
        W, H = self.winfo_width(), self.winfo_height()
        # 右下角缩放热区
        if e.x >= W - 14 and e.y >= H - 14:
            self._win_mode = "resize"
            self._drag_start = (e.x_root, e.y_root, W, H)
            return
        # 标题栏拖动区（避开右上角按钮）
        if e.y <= self.TITLE_H and e.x < W - 116 and not self._maximized:
            self._win_mode = "move"
            self._drag_start = (e.x_root - self.winfo_x(),
                                e.y_root - self.winfo_y())

    def _win_drag(self, e):
        if not self._win_mode or not self._drag_start:
            return
        if self._win_mode == "move":
            dx, dy = self._drag_start
            self.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")
        elif self._win_mode == "resize":
            sx, sy, sw, sh = self._drag_start
            nw = max(920, sw + e.x_root - sx)
            nh = max(620, sh + e.y_root - sy)
            self.geometry(f"{nw}x{nh}")

    def _win_release(self, _e):
        self._win_mode = None
        self._drag_start = None

    def _win_hover(self, e):
        W, H = self.winfo_width(), self.winfo_height()
        if e.x >= W - 14 and e.y >= H - 14:
            self.bg_canvas.configure(cursor="size_nw_se")
        else:
            self.bg_canvas.configure(cursor="")

    def _win_double(self, e):
        if e.y <= self.TITLE_H and e.x < self.winfo_width() - 116:
            self._toggle_maximize()

    def _toggle_maximize(self):
        if not self._maximized:
            self._saved_geom = (self.winfo_x(), self.winfo_y(),
                                self.winfo_width(), self.winfo_height())

            class _RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
            r = _RECT()
            ctypes.windll.user32.SystemParametersInfoW(0x0030, 0,
                                                       ctypes.byref(r), 0)
            self.geometry(
                f"{r.right - r.left}x{r.bottom - r.top}+{r.left}+{r.top}")
            self._maximized = True
        else:
            x, y, w, h = self._saved_geom
            self.geometry(f"{w}x{h}+{x}+{y}")
            self._maximized = False

    def _win_minimize(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            ctypes.windll.user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        except Exception:
            pass

    def _win_close(self):
        self._stop_video_bg()
        self.destroy()

    def _place_left(self, x, y, w, h):
        bw = w - 32
        # 头像
        ax = x + (w - self.AVATAR_SIZE) // 2
        ay = y + 22
        self._draw_avatar(ax, ay)
        by = ay + self.AVATAR_SIZE + 22
        gap_b = 12
        self._draw_button("b_avatar", x + 16, by, bw, 38, "替换头像",
                          "glass", self._choose_avatar)
        self._draw_button("b_skin", x + 16, by + (38 + gap_b), bw, 38,
                          "更换背景皮肤", "glass", self._choose_skin)
        self._draw_button("b_reset", x + 16, by + 2 * (38 + gap_b), bw, 38,
                          "恢复默认", "glass", self._reset_all)
        self._draw_button("b_open", x + 16, by + 3 * (38 + gap_b), bw, 36,
                          "打开皮肤文件夹", "ghost", self._open_skins_dir)

    def _draw_avatar(self, x, y):
        self.bg_canvas.delete("avatar")
        src = self._avatar_path()
        img = circle_thumb(src, self.AVATAR_SIZE) if src else None
        if img is None:
            return
        self._avatar_photo = ImageTk.PhotoImage(img)
        self.bg_canvas.create_image(x, y, anchor="nw", image=self._avatar_photo,
                                    tags="avatar")

    def _place_center(self, x, y, w, h):
        pad = 20
        # 标题
        self._set_text("c_title", x + pad, y + 26, "网页音视频下载器",
                       font=("Microsoft YaHei UI", 16, "bold"),
                       fill=self.TEXT_DARK)
        self._set_text("c_sub", x + pad, y + 52,
                       "支持 YouTube / B站 / 抖音等上千个站点",
                       font=("Microsoft YaHei UI", 9), fill=self.TEXT_GREY)

        # URL 行（输入框占满）
        uy = y + 84
        url_w = max(120, w - 2 * pad)
        if self._video_mode:
            self.url_entry.place_forget()
            self._draw_ghost_field("g_url", x + pad, uy, url_w, 38,
                                   self.url_var, self.url_entry)
        else:
            self.url_entry.place(x=x + pad, y=uy, width=url_w, height=38)

        # 下载模式 —— iOS 分段控件
        my = uy + 62
        self._set_text("c_mode", x + pad, my - 18, "下载模式",
                       font=("Microsoft YaHei UI", 9), fill=self.TEXT_GREY)
        seg_w = min(360, w - 2 * pad)
        self._draw_segmented(
            "seg_mode", x + pad, my, seg_w, 36,
            [("音视频合并", "merge"), ("仅音频", "audio"), ("仅视频", "video")],
            self.mode_var, command=self._on_mode_change)

        # 画质 / 音频
        qy = my + 56
        self._set_text("c_q", x + pad, qy + 15, "画质上限",
                       font=("Microsoft YaHei UI", 9), fill=self.TEXT_GREY)
        if self._video_mode:
            self.quality_combo.place_forget()
            self._draw_ghost_field("g_qual", x + pad + 80, qy, 130, 34,
                                   self.quality_var, self.quality_combo, is_combo=True)
        else:
            self.quality_combo.place(x=x + pad + 80, y=qy, width=130, height=34)
        self._set_text("c_a", x + pad + 228, qy + 15, "音频格式",
                       font=("Microsoft YaHei UI", 9), fill=self.TEXT_GREY)
        if self._video_mode:
            self.audio_combo.place_forget()
            self._draw_ghost_field("g_audio", x + pad + 302, qy, 120, 34,
                                   self.audio_var, self.audio_combo, is_combo=True)
        else:
            self.audio_combo.place(x=x + pad + 302, y=qy, width=120, height=34)

        # 只下载当前视频（独立一行，iOS 粉色勾选）
        cy = qy + 48
        self._chk_rect = (x + pad, cy, w - 2 * pad)
        self._draw_single_check(x + pad, cy, w - 2 * pad)

        # 保存位置（窄窗时按钮自动换到下一行）
        dy = cy + 40
        self._set_text("c_save", x + pad, dy + 16, "保存位置",
                       font=("Microsoft YaHei UI", 9), fill=self.TEXT_GREY)
        ex = x + pad + 80
        avail = w - 2 * pad - 80
        need = 72 + 88 + 20  # 两个按钮 + 间距
        if avail - need - 16 >= 150:
            # 一行：输入框 + 浏览 + 打开目录
            dir_w = avail - need - 16
            if self._video_mode:
                self.dir_entry.place_forget()
                self._draw_ghost_field("g_dir", ex, dy, dir_w, 34,
                                       self.dir_var, self.dir_entry)
            else:
                self.dir_entry.place(x=ex, y=dy, width=dir_w, height=34)
            self._draw_button("b_browse", ex + dir_w + 8, dy - 1,
                              72, 36, "浏览…",
                              "ghost" if self._video_mode else "gray",
                              self._choose_dir)
            self._draw_button("b_open_dir", ex + dir_w + 88, dy - 1,
                              88, 36, "打开目录",
                              "ghost" if self._video_mode else "gray",
                              self._open_dir)
            by2 = dy + 56
        else:
            # 两行：输入框占满，按钮右对齐到下一行
            if self._video_mode:
                self.dir_entry.place_forget()
                self._draw_ghost_field("g_dir", ex, dy, avail, 34,
                                       self.dir_var, self.dir_entry)
            else:
                self.dir_entry.place(x=ex, y=dy, width=avail, height=34)
            row2 = dy + 44
            self._draw_button("b_browse", x + w - pad - 160, row2 - 1,
                              72, 36, "浏览…",
                              "ghost" if self._video_mode else "gray",
                              self._choose_dir)
            self._draw_button("b_open_dir", x + w - pad - 88, row2 - 1,
                              88, 36, "打开目录",
                              "ghost" if self._video_mode else "gray",
                              self._open_dir)
            by2 = row2 + 48

        # 操作按钮 + ffmpeg 状态
        running = bool(self.job and self.job.is_alive())
        self._draw_button("b_start", x + pad, by2, 120, 40, "开始下载",
                          "ghost_blue" if self._video_mode else "blue",
                          self._start, enabled=not running)
        self._draw_button("b_cancel", x + pad + 136, by2, 90, 40, "取消",
                          "ghost" if self._video_mode else "gray",
                          self._cancel, enabled=running)
        ff = "ffmpeg 已就绪" if self.ffmpeg_dir else "ffmpeg 缺失（合并/转码不可用）"
        self._set_text("c_ff", x + pad + 244, by2 + 20, ff, anchor="w",
                       fill="#1d9e52" if self.ffmpeg_dir else "#c77700",
                       font=("Microsoft YaHei UI", 9))

    def _place_right(self, x, y, w, h):
        pad = 20
        # 标题
        self._set_text("r_label", x + pad, y + 24, "下载信息",
                       font=("Microsoft YaHei UI", 14, "bold"),
                       fill=self.TEXT_DARK)
        # 视频标题（最多两行）
        self._set_text("r_title", x + pad, y + 62, self.title_var.get(),
                       font=("Microsoft YaHei UI", 12, "bold"),
                       fill=self.TEXT_DARK, width=w - 2 * pad)
        # 格式信息
        self._set_text("r_fmt", x + pad, y + 110, self.fmt_var.get(),
                       font=("Microsoft YaHei UI", 9), fill=self.TEXT_GREY)
        # 进度标题行
        py = y + 172
        pw = w - 2 * pad
        ph = 14
        self._set_text("r_proglabel", x + pad, py - 24, "下载进度", anchor="w",
                       font=("Microsoft YaHei UI", 10, "bold"),
                       fill=self.TEXT_DARK)
        pct = int(self._progress_frac * 100)
        self._set_text("r_pct", x + w - pad, py - 24, f"{pct}%", anchor="e",
                       font=("Microsoft YaHei UI", 13, "bold"),
                       fill=self.TEXT_DARK)
        # 进度条（直接画在主画布上，胶囊形）
        self._prog_rect = (x + pad, py, pw, ph)
        self._render_progress()
        # 当前动作
        self._set_text("r_action", x + pad, py + 34, self.action_var.get(),
                       font=("Microsoft YaHei UI", 9), fill=self.TEXT_GREY,
                       width=w - 2 * pad)

    def _render_progress(self):
        """在主画布上绘制胶囊渐变进度条（无独立黑框 Canvas）。"""
        self.bg_canvas.delete("prog")
        if not self._prog_rect:
            return
        x, y, w, h = self._prog_rect
        if w <= 1:
            return
        frac = max(0.0, min(1.0, self._progress_frac))
        scale = 2
        im = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        # 轨道：浅色半透明胶囊（融入玻璃，无黑框）
        d.rounded_rectangle(
            [0, 0, w * scale - 1, h * scale - 1],
            radius=h * scale // 2, fill=(120, 128, 145, 45))
        if frac > 0:
            fw = max(h * scale, int(w * scale * frac))
            grad = Image.new("RGBA", (fw, h * scale), (0, 0, 0, 0))
            gp = grad.load()
            for px in range(fw):
                t = px / max(1, fw - 1)
                r = int(10 + (255 - 10) * t)     # iOS 蓝 -> 粉
                g = int(132 + (55 - 132) * t)
                b = int(255 + (95 - 255) * t)
                for py2 in range(h * scale):
                    gp[px, py2] = (r, g, b, 255)
            mask = Image.new("L", (fw, h * scale), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                [0, 0, fw - 1, h * scale - 1],
                radius=h * scale // 2, fill=255)
            im.paste(grad, (0, 0), mask)
        self._prog_photo = ImageTk.PhotoImage(im.resize((w, h), Image.LANCZOS))
        self.bg_canvas.create_image(x, y, anchor="nw", image=self._prog_photo,
                                    tags="prog")
        self.bg_canvas.tag_raise("prog")

    def _set_progress(self, frac):
        """更新进度值并重绘。"""
        self._progress_frac = max(0.0, min(1.0, frac))
        self.after_idle(self._render_progress)

    def _draw_single_check(self, x, y, max_w):
        """iOS 风格勾选框：未选圆角空心框，选中粉色填充 + 白色 √。"""
        self.bg_canvas.delete("chk")
        box = 20
        gap = 10
        checked = self.single_var.get()
        scale = 2
        im = Image.new("RGBA", (box * scale, box * scale), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        r = 6 * scale
        if checked:
            d.rounded_rectangle(
                [0, 0, box * scale - 1, box * scale - 1],
                radius=r, fill=self.IOS_PINK)
            # 白色 √
            d.line([(4 * scale, 10 * scale), (8 * scale, 14 * scale),
                    (15 * scale, 5 * scale)],
                   fill="white", width=2 * scale, joint="curve")
        else:
            d.rounded_rectangle(
                [0, 0, box * scale - 1, box * scale - 1],
                radius=r, outline=(110, 118, 138, 220), width=2 * scale)
        self._chk_photo = ImageTk.PhotoImage(im.resize((box, box), Image.LANCZOS))
        iid = self.bg_canvas.create_image(x, y, anchor="nw",
                                          image=self._chk_photo, tags="chk")
        tid = self.bg_canvas.create_text(
            x + box + gap, y + box // 2,
            text="只下载当前视频（不下载整个列表）", anchor="w",
            font=("Microsoft YaHei UI", 9), fill=self.TEXT_DARK,
            width=max(60, max_w - box - gap), tags="chk")
        for i in (iid, tid):
            self.bg_canvas.tag_bind(i, "<Button-1>", self._toggle_single)

    def _toggle_single(self, _event=None):
        self.single_var.set(not self.single_var.get())
        if self._chk_rect:
            x, y, mw = self._chk_rect
            self._draw_single_check(x, y, mw)

    def _on_bg_configure(self, _event):
        if self._bg_after:
            self.after_cancel(self._bg_after)
        self._bg_after = self.after(120, self._layout)

    # ---- 头像 ----
    def _avatar_path(self):
        custom = os.path.join(skins_dir(), "avatar.png")
        if os.path.isfile(custom):
            return custom
        return find_icon_path() or current_skin_path()

    def _choose_avatar(self):
        path = filedialog.askopenfilename(
            title="选择头像图片",
            filetypes=[("图片文件", "*.jpg *.jpeg *.png *.webp *.bmp"),
                       ("所有文件", "*.*")])
        if not path:
            return
        try:
            img = Image.open(path).convert("RGBA")
            img = cover_resize(img, 256, 256)
            os.makedirs(skins_dir(), exist_ok=True)
            img.save(os.path.join(skins_dir(), "avatar.png"), "PNG")
        except Exception as e:
            messagebox.showerror("更换头像失败", f"无法使用该图片：\n{e}")
            return
        self._layout()

    # ---- 皮肤 / 面板 ----
    def _choose_skin(self):
        path = filedialog.askopenfilename(
            title="选择背景皮肤（图片或视频）",
            filetypes=[("图片和视频", "*.jpg *.jpeg *.png *.webp *.bmp "
                                 "*.mp4 *.webm *.mkv *.mov *.avi *.gif"),
                       ("图片文件", "*.jpg *.jpeg *.png *.webp *.bmp"),
                       ("视频文件", "*.mp4 *.webm *.mkv *.mov *.avi *.gif"),
                       ("所有文件", "*.*")])
        if not path:
            return
        try:
            import_skin(path)
        except Exception as e:
            messagebox.showerror("更换皮肤失败", f"无法使用该文件：\n{e}")
            return
        self._layout()

    def _reset_all(self):
        reset_skin()
        reset_panel()
        av = os.path.join(skins_dir(), "avatar.png")
        if os.path.isfile(av):
            try:
                os.remove(av)
            except OSError:
                pass
        self._layout()

    def _ask_image(self, title):
        return filedialog.askopenfilename(
            title=title,
            filetypes=[("图片文件", "*.jpg *.jpeg *.png *.webp *.bmp"),
                       ("所有文件", "*.*")])

    def _open_skins_dir(self):
        d = skins_dir()
        os.makedirs(d, exist_ok=True)
        os.startfile(d)  # noqa: S606  (Windows)

    def _on_mode_change(self):
        # 两个下拉框始终可用，已选值永不丢失；只重绘分段滑块
        if self._seg_geom:
            x, y, w, h, options, variable, command = self._seg_geom
            self._draw_segmented("seg_mode", x, y, w, h, options,
                                 variable, command=command)

    def _choose_dir(self):
        d = filedialog.askDirectory(initialdir=self.dir_var.get()) \
            if hasattr(filedialog, "askDirectory") else \
            filedialog.askdirectory(initialdir=self.dir_var.get())
        if d:
            self.dir_var.set(d)

    def _open_dir(self):
        d = self.dir_var.get()
        if os.path.isdir(d):
            os.startfile(d)  # noqa: S606  (Windows)
        else:
            messagebox.showwarning("提示", "目录不存在。")

    # ---- 下载控制 ----
    def _start(self):
        url = self.url_var.get().strip()
        out_dir = self.dir_var.get().strip()
        if not url:
            messagebox.showwarning("提示", "请输入网页地址。")
            return
        if not out_dir or not os.path.isdir(out_dir):
            messagebox.showwarning("提示", "保存位置不存在，请重新选择。")
            return
        if self.job and self.job.is_alive():
            return

        self._set_progress(0.0)
        self._set_running(True)
        self.status_var.set("准备中…")

        self.job = DownloadJob(
            url=url,
            mode=self.mode_var.get(),
            quality_label=self.quality_var.get(),
            audio_label=self.audio_var.get(),
            out_dir=out_dir,
            single_only=self.single_var.get(),
            ffmpeg_dir=self.ffmpeg_dir,
            msg_q=self.msg_q,
        )
        self.job.start()

    def _cancel(self):
        if self.job and self.job.is_alive():
            self.job.cancel()
            self.status_var.set("正在取消…")
            self._update_text("r_action", "正在取消…")

    def _set_running(self, running):
        # 重绘开始/取消按钮的可用状态
        self._layout()

    # ---- 消息轮询 ----
    def _poll_queue(self):
        try:
            while True:
                msg = self.msg_q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    pass  # 日志不再显示在界面上
                elif kind == "info":
                    self.title_var.set(msg[1])
                    self.fmt_var.set(msg[2])
                    self._update_text("r_title", msg[1])
                    self._update_text("r_fmt", msg[2])
                elif kind == "progress":
                    frac, text = msg[1], msg[2]
                    if frac < 0:
                        # 不确定进度：显示一个小条在动
                        self._set_progress(0.0)
                    else:
                        self._set_progress(frac)
                    self.status_var.set(text[:60])
                    self._update_text("r_action", text)
                    self._update_text("r_pct",
                                      f"{int(max(0, frac) * 100)}%")
                elif kind == "done":
                    ok, text = msg[1], msg[2]
                    if ok:
                        self._set_progress(1.0)
                        self._update_text("r_pct", "100%")
                    self.status_var.set(text)
                    self._update_text("r_action", text)
                    self._set_running(False)
                    if ok:
                        messagebox.showinfo("完成", "下载完成！")
        except queue.Empty:
            pass
        self.after(120, self._poll_queue)



def main():
    # Windows 任务栏分组 ID：设置后任务栏才会显示自定义图标而非默认 Python 图标
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "LH1149.MediaDownloader.App")
        except Exception:
            pass
    App().mainloop()


if __name__ == "__main__":
    main()
