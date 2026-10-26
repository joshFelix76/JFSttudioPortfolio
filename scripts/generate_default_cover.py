"""產生作品集預設封面 assets/cover.png（1200 × 750）。

一般情況不需要重新執行；若想調整預設封面設計，可修改此腳本後執行：
    python scripts/generate_default_cover.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1200, 750
OUTPUT = Path(__file__).resolve().parent.parent / "assets" / "cover.png"


def lerp(a: int, b: int, t: float) -> int:
    return round(a + (b - a) * t)


def main() -> None:
    top, bottom = (15, 23, 42), (30, 58, 138)
    img = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(img)

    # 對角漸層背景
    for y in range(HEIGHT):
        t = y / (HEIGHT - 1)
        draw.line([(0, y), (WIDTH, y)], fill=tuple(lerp(a, b, t) for a, b in zip(top, bottom)))

    # 細緻網格
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    grid = ImageDraw.Draw(overlay)
    for x in range(0, WIDTH, 50):
        grid.line([(x, 0), (x, HEIGHT)], fill=(255, 255, 255, 14))
    for y in range(0, HEIGHT, 50):
        grid.line([(0, y), (WIDTH, y)], fill=(255, 255, 255, 14))

    # 裝飾圓形
    grid.ellipse([760, -220, 1420, 440], fill=(96, 165, 250, 40))
    grid.ellipse([-200, 470, 260, 930], fill=(129, 140, 248, 30))
    img = Image.alpha_composite(img.convert("RGBA"), overlay)

    draw = ImageDraw.Draw(img)
    # 程式碼括號圖示
    draw.rounded_rectangle([100, 250, 220, 370], radius=24, fill=(37, 99, 235, 255))
    icon_font = ImageFont.load_default(size=64)
    draw.text((160, 308), "</>", font=icon_font, fill=(255, 255, 255), anchor="mm")

    title_font = ImageFont.load_default(size=86)
    sub_font = ImageFont.load_default(size=34)
    draw.text((100, 410), "JF Studio", font=title_font, fill=(248, 250, 252))
    draw.text((104, 520), "Software Engineering Portfolio", font=sub_font, fill=(165, 180, 207))
    draw.rectangle([104, 585, 224, 591], fill=(96, 165, 250))

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(OUTPUT, format="PNG", optimize=True)
    print(f"已產生 {OUTPUT}（{WIDTH}×{HEIGHT}）")


if __name__ == "__main__":
    main()
