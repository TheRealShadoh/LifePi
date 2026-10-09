# Browser entry point for pygbag (WebAssembly). web/build.py copies this next to lifepi.py.
# pygbag decides which WebAssembly libraries to load from *this* file's imports, so pygame
# must be imported here, not only inside lifepi. (No "# /// script" dependency block: that
# makes pygbag look for wheels on its own dev server instead of its CDN.)
import asyncio

import pygame  # noqa: F401

import lifepi

asyncio.run(lifepi.main_async())
