#!/usr/bin/env python3
"""证件照初审辅助脚本：无视觉工具时，用像素分析判定红底/人像/有领特征。

用法:
    uv run --with pillow python verify_photo.py <图片路径>

(系统无 pip、PEP 668 保护，必须用 uv run --with pillow；如需读取 PDF 可加 --with pymupdf)

输出:
    1. 文件 magic bytes 与尺寸（ffd8ffe1 = JPEG；WeCom 上传的证件照常存为 .bin 后缀）
    2. 边缘主色 top5 → 红底判定（红底 RGB 通常 ≈ (200-210, 10-20, 15-25)）
    3. 中央区域肤色占比 → 人像判定（证件照通常 >50%）
    4. 整体构图色块图 (24x32) → 判断是证件照还是护照页/文档扫描
    5. 领口区域色块图 (40x18, y 0.62-0.80) → 有领判定（白色 V 形领 = 白衬衫领）

图例: R=红底  W=白色(衬衫/纸)  S=肤色  K=深色(头发/深色服装)  .=其他

典型红底有领证件照特征:
    - 边缘主色以红色为主
    - 中央肤色占比高
    - 整体色块图: 上部/两侧红底，中间人像，领口区出现 W 领 + 中间 S 脖子
    - 肩部以下可能整行变 W（白衬衫占满画面），只要顶部/两侧红底清晰即可
"""
import sys
from collections import Counter


def classify(p):
    r, g, b = p
    if r > 150 and g < 90 and b < 90:
        return 'R'          # 红底
    if r > 200 and g > 200 and b > 200:
        return 'W'          # 白色（衬衫/纸张）
    if r > 95 and g > 40 and b > 20 and r > g and r > b and (r - min(g, b)) > 15:
        return 'S'          # 肤色
    if r < 90 and g < 90 and b < 90:
        return 'K'          # 深色（头发/深色服装）
    return '.'              # 其他


def block_map(img, cols, rows):
    thumb = img.resize((cols, rows))
    return [''.join(classify(thumb.getpixel((x, y))) for x in range(cols)) for y in range(rows)]


def main(path):
    from PIL import Image
    with open(path, 'rb') as f:
        magic = f.read(8)
    kind = 'JPEG' if magic[:2] == b'\xff\xd8' else \
           'PNG' if magic[:4] == b'\x89PNG' else \
           'PDF' if magic[:4] == b'%PDF' else '未知'
    print('magic:', magic.hex(), '->', kind)

    img = Image.open(path).convert('RGB')
    w, h = img.size
    print('size:', img.size, 'mode:', img.mode)

    # 1) 边缘主色 → 红底判定
    edges = []
    for x in range(0, w, 10):
        for y in range(0, h, 10):
            if x < w // 10 or x > w * 9 // 10 or y < h // 10 or y > h * 9 // 10:
                edges.append(img.getpixel((x, y)))
    print('边缘主色 top5:', Counter(edges).most_common(5))

    # 2) 中央区域肤色占比 → 人像判定
    region = img.crop((int(w * 0.25), int(h * 0.35), int(w * 0.75), int(h * 0.75)))
    skin = total = 0
    for x in range(0, region.width, 4):
        for y in range(0, region.height, 4):
            p = region.getpixel((x, y))
            total += 1
            r, g, b = p
            if r > 95 and g > 40 and b > 20 and r > g and r > b and (r - min(g, b)) > 15:
                skin += 1
    print('中央区域肤色占比: %.1f%%' % (skin / total * 100))

    # 3) 整体构图色块图
    print('--- 整体构图 (24x32) ---')
    for i, line in enumerate(block_map(img, 24, 32)):
        print('%02d %s' % (i, line))

    # 4) 领口区域 (y 0.62-0.80) → 有领判定
    crop = img.crop((int(w * 0.2), int(h * 0.62), int(w * 0.8), int(h * 0.80)))
    print('--- 领口区域 (40x18) ---')
    for i, line in enumerate(block_map(crop, 40, 18)):
        print('%02d %s' % (i, line))


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('用法: uv run --with pillow python verify_photo.py <图片路径>')
        sys.exit(1)
    main(sys.argv[1])
