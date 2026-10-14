#!/usr/bin/env python3
"""LifePi's launcher icons and splash screens for the Android app, drawn by web/build.py's
draw_icon. Re-run after changing the icon:  python mobile/make_icons.py"""
import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
import pygame                     # noqa: E402
from build import draw_icon       # noqa: E402

RES = ROOT / "mobile" / "android" / "app" / "src" / "main" / "res"
DARK = (18, 18, 24)
DENSITIES = {"mdpi": 1, "hdpi": 1.5, "xhdpi": 2, "xxhdpi": 3, "xxxhdpi": 4}


def round_mask(img):
    size = img.get_width()
    S = 4
    big = pygame.Surface((size * S, size * S), pygame.SRCALPHA)
    pygame.draw.circle(big, (255, 255, 255, 255), (size * S // 2, size * S // 2), size * S // 2)
    mask = pygame.transform.smoothscale(big, (size, size))
    out = img.copy()
    out.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    return out


def main():
    pygame.display.init()
    for name, k in DENSITIES.items():
        d = RES / f"mipmap-{name}"
        d.mkdir(parents=True, exist_ok=True)
        n = round(48 * k)                                        # legacy icon, 48 dp
        pygame.image.save(draw_icon(n, max(1, round(n * 0.06))), str(d / "ic_launcher.png"))
        pygame.image.save(round_mask(draw_icon(n, round(n * 0.16))), str(d / "ic_launcher_round.png"))
        f = round(108 * k)                                       # adaptive foreground: 108 dp,
        pygame.image.save(draw_icon(f, round(f * 23 / 108), background=None),   # art in the 66 dp middle
                          str(d / "ic_launcher_foreground.png"))
    (RES / "values" / "ic_launcher_background.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
        '    <color name="ic_launcher_background">#121218</color>\n</resources>\n')
    # splash: the icon on LifePi's dark background, every size Capacitor ships
    for png in RES.glob("drawable*/splash.png"):
        w, h = pygame.image.load(str(png)).get_size()
        s = pygame.Surface((w, h))
        s.fill(DARK)
        side = min(w, h) // 3
        icon = draw_icon(side, round(side * 0.04), background=None)
        s.blit(icon, icon.get_rect(center=(w // 2, h // 2)))
        pygame.image.save(s, str(png))
    print("icons and splash screens written under", RES)


if __name__ == "__main__":
    main()
