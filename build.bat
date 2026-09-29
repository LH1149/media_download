@echo off
chcp 65001 >nul
REM ============================================================
REM 网页音视频下载器 - 一键打包脚本
REM 前置：pip install yt-dlp pyinstaller imageio-ffmpeg pillow
REM
REM 换图标：用新图片替换 assets\icon.jpg（jpg/png/webp 均可），
REM         重新运行本脚本即可，图标会自动转换并嵌入 exe。
REM ============================================================

if not exist bin\ffmpeg.exe (
    echo [准备] 从 imageio-ffmpeg 复制 ffmpeg.exe 到 bin ...
    if not exist bin mkdir bin
    python -c "import imageio_ffmpeg, shutil; shutil.copy(imageio_ffmpeg.get_ffmpeg_exe(), r'bin\ffmpeg.exe')"
)

echo [图标] 由 assets\icon.jpg 生成多尺寸 icon.ico ...
python make_icon.py
if errorlevel 1 (
    echo 图标生成失败，请确认 assets\icon.jpg 存在
    pause
    exit /b 1
)

python -m PyInstaller --noconfirm --onefile --windowed ^
  --name MediaDownloader ^
  --icon "assets\icon.ico" ^
  --add-binary "bin\ffmpeg.exe;." ^
  --add-data "assets\icon.ico;assets" ^
  --add-data "assets\skin.default.jpg;assets" ^
  --hidden-import PIL._tkinter_finder ^
  --collect-all yt_dlp ^
  media_downloader.py

echo.
echo 打包完成：dist\MediaDownloader.exe
pause
