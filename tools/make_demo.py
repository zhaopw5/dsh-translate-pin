"""Generate neutral demo images for the public README (no personal screenshots)."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import pinwin  # noqa: E402

W, H = 860, 170
BG = (253, 246, 227)
FG = (26, 79, 180)
BAR = (47, 111, 219)

before = Image.new("RGB", (W, H), BG)
draw = ImageDraw.Draw(before)
draw.rectangle([0, 0, 5, H - 1], fill=BAR)
font = ImageFont.truetype("C:/Windows/Fonts/timesbd.ttf", 21)
lines = [
    "Because the sensor was used to select and correct",
    "the discrepant channels, the closer agreement is",
    "not an independent check.",
]
rects = []
y = 22
for line in lines:
    draw.text((22, y), line, font=font, fill=FG)
    bbox = draw.textbbox((22, y), line, font=font)
    rects.append((bbox[0] - 1, bbox[1] - 1, bbox[2] + 1, bbox[3] + 1))
    y += 46
before.save("docs/demo-before.png")

obj = object.__new__(pinwin.InPlaceWindow)
obj.crop = before
obj.bg = BG
obj.fg = FG
zh = "由于传感器被用于选择和校正不一致的通道，因此与传感器更接近的一致性并非独立检验。"
block = {
    "rect": {"x": 22, "y": 22, "w": 800, "h": 128},
    "lines": [{"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0} for x0, y0, x1, y1 in rects],
    "translated": zh,
}
after, _ = obj._compose_blocks([block], W, H)
after.save("docs/demo-after.png")
print("before", before.size, "after", after.size)

