"""
Genera icon.ico (16/32/48/256 px) sin dependencias externas: usa el escritor
PNG nativo de Tk (tkinter.PhotoImage.write) y arma el contenedor .ico a mano
(el formato ICO moderno permite empaquetar PNG directamente, sin pasar por
BMP). Ejecutar una sola vez con: python build_icon.py
"""

import os
import struct
import tempfile
import tkinter as tk

ACCENT = "#2f6fed"
WHITE = "#ffffff"
SIZES = (16, 32, 48, 256)


def render_icon_image(size):
    cx = cy = size / 2
    outer_r = size * 0.47
    ring1_r, ring1_th = size * 0.30, max(size * 0.07, 1.2)
    ring2_r, ring2_th = size * 0.17, max(size * 0.06, 1.2)
    dot_r = size * 0.075

    img = tk.PhotoImage(width=size, height=size)
    rows = []
    transparent_px = []
    for y in range(size):
        row = []
        for x in range(size):
            dx, dy = x - cx + 0.5, y - cy + 0.5
            dist = (dx * dx + dy * dy) ** 0.5
            if dist > outer_r:
                row.append(ACCENT)
                transparent_px.append((x, y))
            elif dist <= dot_r or abs(dist - ring1_r) <= ring1_th / 2 or abs(dist - ring2_r) <= ring2_th / 2:
                row.append(WHITE)
            else:
                row.append(ACCENT)
        rows.append(row)
    img.put(rows)
    for x, y in transparent_px:
        img.transparency_set(x, y, True)
    return img


def build_ico(out_path):
    root = tk.Tk()
    root.withdraw()

    entries = []
    with tempfile.TemporaryDirectory() as tmp:
        for size in SIZES:
            img = render_icon_image(size)
            png_path = os.path.join(tmp, f"{size}.png")
            img.write(png_path, format="png")
            with open(png_path, "rb") as f:
                entries.append((size, f.read()))

    root.destroy()

    count = len(entries)
    header = struct.pack("<HHH", 0, 1, count)

    dir_entries = b""
    image_data = b""
    offset = 6 + 16 * count
    for size, data in entries:
        wh = 0 if size == 256 else size
        dir_entries += struct.pack(
            "<BBBBHHII", wh, wh, 0, 0, 1, 32, len(data), offset
        )
        image_data += data
        offset += len(data)

    with open(out_path, "wb") as f:
        f.write(header + dir_entries + image_data)


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
    build_ico(out)
    print(f"Generado: {out} ({os.path.getsize(out)} bytes)")
