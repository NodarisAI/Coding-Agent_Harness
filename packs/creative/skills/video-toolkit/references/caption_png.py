from PIL import Image, ImageDraw, ImageFont
W, H = 1280, 720
for i, t in enumerate(["First caption line.", "Second caption line."], 1):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    f = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 44)
    x0, y0, x1, y1 = d.textbbox((0, 0), t, font=f); w, h = x1 - x0, y1 - y0
    x, y = (W - w) // 2, H - h - 80
    d.rounded_rectangle((x - 24, y - 16, x + w + 24, y + h + 24), radius=12, fill=(0, 0, 0, 170))
    d.text((x, y - y0), t, font=f, fill=(255, 255, 255, 255))
    im.save(f"cap{i}.png")
