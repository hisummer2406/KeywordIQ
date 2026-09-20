"""由 logo 合成 PWA 安装图标（frontend/public/ 下的四个 PNG）。

现有 logo（frontend/src/assets/logo-icon.png）是 510×423 的非正方形白底图，不能直接
当图标用：浏览器要 192 / 512 的正方形，且 maskable 图标会被系统裁成圆形/圆角，内容
必须收在中心「安全区」内，否则商标被切掉。

Pillow 不是项目依赖，按需带上跑：

    uv run --with pillow python scripts/make_pwa_icons.py

换了 logo 素材就重跑一遍，把产物一并提交。**产物入库、脚本只在换素材时用**——构建流程
不依赖它，CI 也不跑它。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "frontend/src/assets/logo-icon.png"
OUT_DIR = ROOT / "frontend/public"

BACKGROUND = (255, 255, 255)

# (文件名, 边长, logo 宽度占边长的比例)
#
# maskable 的安全区是以中心为圆心、直径为边长 80% 的圆。logo 裁掉白边后宽高比约 1.1，
# 对角线半长要落在半径 40% 之内，宽度占 60% 是留了余量的取值。
# any 用途的图标没有被裁剪的风险，放大到 72% 让小尺寸下更清晰。
ICONS = [
    ("icon-192.png", 192, 0.72),
    ("icon-512.png", 512, 0.72),
    ("icon-maskable-512.png", 512, 0.60),
    ("apple-touch-icon.png", 180, 0.70),
]


def crop_to_content(img: Image.Image) -> Image.Image:
    """裁掉四周的白边。源图是无透明通道的白底 RGB，逐像素找「不够白」的最小外接框。"""
    rgb = img.convert("RGB")
    # 反相后非白像素变亮，getbbox 找非零区域；阈值 12 抹掉抗锯齿与压缩噪点的近白像素
    mask = rgb.point(lambda v: 255 if v < 255 - 12 else 0).convert("L")
    box = mask.getbbox()
    if box is None:
        raise SystemExit(f"{SOURCE} 全白，无法提取 logo 内容")
    return whiten_near_white(rgb.crop(box))


def whiten_near_white(img: Image.Image) -> Image.Image:
    """把近白像素（三通道都 ≥ 246）归一成纯白。

    源图的「白底」并不纯：有大片 253~254 的近白噪点。它们放到纯白画布上会显出一圈
    肉眼可辨的浅灰方框边缘，在 maskable 图标上尤其明显。
    """
    out = img.copy()
    px = out.load()
    for y in range(out.height):
        for x in range(out.width):
            if min(px[x, y]) >= 246:
                px[x, y] = (255, 255, 255)
    return out


def compose(logo: Image.Image, size: int, width_ratio: float) -> Image.Image:
    canvas = Image.new("RGB", (size, size), BACKGROUND)
    target_w = round(size * width_ratio)
    target_h = round(logo.height * target_w / logo.width)
    resized = logo.resize((target_w, target_h), Image.LANCZOS)
    canvas.paste(resized, ((size - target_w) // 2, (size - target_h) // 2))
    return canvas


def main() -> None:
    logo = crop_to_content(Image.open(SOURCE))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, size, ratio in ICONS:
        compose(logo, size, ratio).save(OUT_DIR / name, optimize=True)
        print(f"wrote {OUT_DIR / name} ({size}×{size})")


if __name__ == "__main__":
    main()
