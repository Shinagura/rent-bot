from PIL import Image, ImageDraw
import os

def create_icon(size, filename):
    img = Image.new('RGB', (size, size), color='#3b5998')
    d = ImageDraw.Draw(img)
    d.text((size//3, size//3), "🏠", fill='white', font=None)
    img.save(filename)

os.makedirs("static", exist_ok=True)
create_icon(192, "static/icon-192.png")
create_icon(512, "static/icon-512.png")
print("✅ Иконки созданы: static/icon-192.png, static/icon-512.png")