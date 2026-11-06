"""Generate neutral demo images for the public README (no personal screenshots)."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import pinwin  # noqa: E402

W, H = 900, 150
BG = (245, 246, 247)
FG = (31, 35, 40)
BAR = (154, 160, 170)
EDGE = (208, 211, 216)

EN = ("Because the sensor was used to select and correct the discrepant channels, "
      "the closer agreement with the sensor is not an independent check. "
      "We therefore also examined the hourly spectral consistency.")
ZH = ("由于该传感器被用于选择和校正不一致的通道，因此与传感器更接近的一致性并非独立检验。"
      "我们还检查了逐小时的光谱一致性。")


def wrap_words(draw, text, font, maxw):
    lines, cur = [], ""
    for word in text.split():
        candidate = (cur + " " + word).strip()
        if draw.textlength(candidate, font=font) <= maxw:
            cur = candidate
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def wrap_cjk(draw, text, font, maxw):
    lines, cur = [], ""
    for ch in text:
        candidate = cur + ch
        if draw.textlength(candidate, font=font) <= maxw:
            cur = candidate
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


# --- before: a neutral quote panel ---
before = Image.new("RGB", (W, H), (255, 255, 255))
d = ImageDraw.Draw(before)
d.rectangle([0, 0, W - 1, H - 1], fill=BG, outline=EDGE)
d.rectangle([0, 0, 3, H - 1], fill=BAR)
en_font = ImageFont.truetype("C:/Windows/Fonts/times.ttf", 19)
y = 18
for line in wrap_words(d, EN, en_font, W - 52):
    d.text((24, y), line, font=en_font, fill=FG)
    y += 27
before.save("docs/demo-before.png")

# --- after: the same panel with the Chinese translation in place ---
content = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(content)
d.rectangle([0, 0, W - 1, H - 1], fill=BG)
zh_font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 20)
y = 16
for line in wrap_cjk(d, ZH, zh_font, W - 44):
    d.text((20, y), line, font=zh_font, fill=FG)
    y += 31
out = pinwin.InPlaceWindow._decorate(content)
out.save("docs/demo-after.png")
print("before", before.size, "after", out.size)
