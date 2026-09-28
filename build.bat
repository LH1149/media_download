@echo off
chcp 65001 >nul
REM ============================================================
REM 网页音视频下载器 - 一键打包脚本
REM 前置：pip install yt-dlp pyinstaller imageio-ffmpeg
REM ============================================================

if not exist bin\ffmpeg.exe (
    echo [准备] 从 imageio-ffmpeg 复制 ffmpeg.exe 到 bin ...
    if not exist bin mkdir bin
    python -c "import imageio_ffmpeg, shutil; shutil.copy(imageio_ffmpeg.get_ffmpeg_exe(), r'bin\ffmpeg.exe')"
)

python -m PyInstaller --noconfirm --onefile --windowed ^
  --name MediaDownloader ^
  --add-binary "bin\ffmpeg.exe;." ^
  --collect-all yt_dlp ^
  media_downloader.py

echo.
echo 打包完成：dist\MediaDownloader.exe
pause
