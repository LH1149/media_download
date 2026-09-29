# -*- coding: utf-8 -*-
"""
图标生成脚本：assets/icon.jpg（或 .png/.webp/.bmp） -> assets/icon.ico

换图流程：
  1. 用新图片替换 assets/icon.jpg（jpg/png/webp 均可，建议正方形、>=256x256）
  2. 重新运行 build.bat（会自动调用本脚本），得到的 exe 即使用新图标

生成的 ICO 包含 16~256 共 7 个标准尺寸，保证资源管理器/任务栏/高 DPI 下清晰；
图片会先居中裁剪为正方形再缩放，不会拉伸变形。
"""

import os
import sys

from PIL import Image

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
ICO_PATH = os.path.join(ASSETS_DIR, "icon.ico")
SIZES = [16, 24, 32, 48, 64, 128, 256]


def find_source():
    """在 assets 目录查找 icon 源图（ico 自身除外）。"""
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
        p = os.path.join(ASSETS_DIR, "icon" + ext)
        if os.path.isfile(p):
            return p
    return None


def center_crop_square(img):
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return img.crop((left, top, left + side, top + side))


def main():
    src = find_source()
    if not src:
        print("错误：未找到 assets/icon.jpg（或 .png/.webp/.bmp），请放入要使用的图标图片。")
        sys.exit(1)

    img = Image.open(src)
    img = center_crop_square(img)
    img = img.convert("RGBA")

    # 以 256 底图 + sizes 列表交给 Pillow 生成标准多帧 ICO
    base = img.resize((256, 256), Image.LANCZOS)

    os.makedirs(ASSETS_DIR, exist_ok=True)
    base.save(ICO_PATH, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"图标已生成：{ICO_PATH}（源图：{os.path.basename(src)}，尺寸：{SIZES}）")


if __name__ == "__main__":
    main()
