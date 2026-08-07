#!/usr/bin/env python3
"""证件照（红底、有领）像素初审工具 — 无视觉模型时使用。

用法:
    <venv>/bin/python verify_id_photo.py <图片路径>

依赖: Pillow（rapidocr-onnxruntime 安装时会顺带装好，或单独 uv pip install pillow）
系统 python3 受 PEP 668 保护且无 PIL，须用 uv 建独立 venv:
    uv venv /tmp/ocr-venv
    uv pip install --python /tmp/ocr-venv/bin/python rapidocr-onnxruntime

输出: 红底判定 + 领口区域颜色网格。
网格字符: R=红底 W=白色/衣领 s=肤色 K=深色/头发 g=灰 o=其他
"""
import sys
from PIL import Image


def desc(c):
    r, g, b = c
    if r > 200 and g < 80 and b < 80:
        return 'R'          # 红底
    if r > 225 and g > 225 and b > 225:
        return 'W'          # 白/衣领
    if r < 110 and g < 110 and b < 110:
        return 'K'          # 深色/头发
    if r > 160 and 120 < g < 200 and b < 150:
        return 's'          # 肤色
    if r > 150 and g > 150 and b > 170:
        return 'g'          # 灰
    return 'o'


def grid(img, w, h):
    small = img.resize((w, h))
    px = small.load()
    return '\n'.join(''.join(desc(px[x, y]) for x in range(w)) for y in range(h))


def main():
    if len(sys.argv) < 2:
        print('用法: verify_id_photo.py <图片路径>')
        sys.exit(1)
    path = sys.argv[1]
    img = Image.open(path).convert('RGB')
    W, H = img.size
    print(f'size: {W}x{H}')

    # 红底判定: 顶部 15% 区域应为背景色, 采样 20x3=60 格
    top = img.crop((0, 0, W, int(H * 0.15))).resize((20, 3))
    px = top.load()
    red_cnt = sum(1 for y in range(3) for x in range(20) if desc(px[x, y]) == 'R')
    verdict = '红底' if red_cnt >= 40 else '非红底'
    print(f'红底判定: {verdict} (顶部红采样 {red_cnt}/60)')

    # 领口区域: 高 68%-88%, 宽 25%-75%, 放大到 40x24
    crop = img.crop((int(W * 0.25), int(H * 0.68), int(W * 0.75), int(H * 0.88))).resize((40, 24))
    print('领口区域网格 (W=衣领/白, s=肤色):')
    print(grid(crop, 40, 24))


if __name__ == '__main__':
    main()
