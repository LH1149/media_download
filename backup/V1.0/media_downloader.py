# -*- coding: utf-8 -*-
"""
网页音视频资源下载器
- 支持上千个网站（基于 yt-dlp）：YouTube、B站、Twitter/X、抖音等
- 三种模式：仅音频 / 仅视频(无声画面) / 音视频合并
- 合并与音频转码依赖 ffmpeg（打包时内置）
"""

import os
import sys
import time
import queue
import threading
import traceback

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

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
        self.geometry("760x560")
        self.minsize(680, 520)

        self.msg_q = queue.Queue()
        self.job = None
        self.ffmpeg_dir = find_ffmpeg_dir()

        self._build_ui()
        self._on_mode_change()
        self.after(120, self._poll_queue)

        if yt_dlp is None:
            messagebox.showerror("启动错误", "缺少 yt-dlp 组件，请重新获取本程序。")
            self.after(100, self.destroy)

    # ---- 界面 ----
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}
        root = ttk.Frame(self)
        root.pack(fill="both", expand=True, padx=10, pady=10)

        # URL
        row = ttk.Frame(root)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="网页地址：", width=10).pack(side="left")
        self.url_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.url_var).pack(
            side="left", fill="x", expand=True)

        # 模式
        row = ttk.LabelFrame(root, text="下载内容")
        row.pack(fill="x", **pad)
        self.mode_var = tk.StringVar(value="merge")
        for text, val in (("音视频合并（推荐）", "merge"),
                          ("仅音频", "audio"),
                          ("仅视频（无声画面）", "video")):
            ttk.Radiobutton(row, text=text, value=val,
                            variable=self.mode_var,
                            command=self._on_mode_change).pack(
                side="left", padx=10, pady=6)

        # 选项
        row = ttk.Frame(root)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="画质上限：").pack(side="left")
        self.quality_var = tk.StringVar(value="最佳画质")
        self.quality_combo = ttk.Combobox(
            row, textvariable=self.quality_var, state="readonly",
            values=list(QUALITY_MAP.keys()), width=14)
        self.quality_combo.pack(side="left", padx=(0, 16))

        ttk.Label(row, text="音频格式：").pack(side="left")
        self.audio_var = tk.StringVar(value="MP3")
        self.audio_combo = ttk.Combobox(
            row, textvariable=self.audio_var, state="readonly",
            values=list(AUDIO_MAP.keys()), width=12)
        self.audio_combo.pack(side="left", padx=(0, 16))

        self.single_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="只下载当前视频（不下载整个播放列表）",
                        variable=self.single_var).pack(side="left")

        # 输出目录
        row = ttk.Frame(root)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="保存位置：", width=10).pack(side="left")
        self.dir_var = tk.StringVar(value=default_download_dir())
        ttk.Entry(row, textvariable=self.dir_var).pack(
            side="left", fill="x", expand=True)
        ttk.Button(row, text="浏览…", command=self._choose_dir).pack(
            side="left", padx=(6, 0))
        ttk.Button(row, text="打开目录", command=self._open_dir).pack(
            side="left", padx=(6, 0))

        # 操作按钮
        row = ttk.Frame(root)
        row.pack(fill="x", **pad)
        self.start_btn = ttk.Button(row, text="开始下载", command=self._start)
        self.start_btn.pack(side="left")
        self.cancel_btn = ttk.Button(row, text="取消", command=self._cancel,
                                     state="disabled")
        self.cancel_btn.pack(side="left", padx=8)
        ff_status = "ffmpeg 已就绪" if self.ffmpeg_dir else "ffmpeg 缺失（合并/转码不可用）"
        ttk.Label(row, text=ff_status,
                  foreground="green" if self.ffmpeg_dir else "#b06a00").pack(
            side="right")

        # 进度
        row = ttk.Frame(root)
        row.pack(fill="x", **pad)
        self.progress = ttk.Progressbar(row, mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True)
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(row, textvariable=self.status_var, width=16,
                  anchor="e").pack(side="left", padx=(8, 0))

        # 日志
        self.log_box = scrolledtext.ScrolledText(root, height=12, wrap="word",
                                                 state="disabled", font=("Consolas", 9))
        self.log_box.pack(fill="both", expand=True, **pad)

    def _on_mode_change(self):
        mode = self.mode_var.get()
        audio_mode = (mode == "audio")
        self.quality_combo.configure(
            state="disabled" if audio_mode else "readonly")
        self.audio_combo.configure(
            state="readonly" if audio_mode else "disabled")

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

        self.progress.configure(mode="determinate", value=0)
        self._clear_log()
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
            self.cancel_btn.configure(state="disabled")

    def _set_running(self, running):
        self.start_btn.configure(state="disabled" if running else "normal")
        self.cancel_btn.configure(state="normal" if running else "disabled")

    # ---- 消息轮询 ----
    def _poll_queue(self):
        try:
            while True:
                msg = self.msg_q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self._append_log(msg[1])
                elif kind == "progress":
                    frac, text = msg[1], msg[2]
                    if frac < 0:
                        self.progress.configure(mode="indeterminate")
                        self.progress.start(15)
                    else:
                        self.progress.stop()
                        self.progress.configure(mode="determinate",
                                                value=frac * 100)
                    self.status_var.set(text[:60])
                elif kind == "done":
                    ok, text = msg[1], msg[2]
                    self.progress.stop()
                    self.progress.configure(mode="determinate",
                                            value=100 if ok else self.progress["value"])
                    self.status_var.set(text)
                    self._set_running(False)
                    if ok:
                        self._append_log("===== 全部完成 =====")
                        messagebox.showinfo("完成", "下载完成！")
        except queue.Empty:
            pass
        self.after(120, self._poll_queue)

    def _append_log(self, text):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
