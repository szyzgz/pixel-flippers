"""Standalone frame viewer, run as a subprocess.

macOS only allows GUI event handling on a process's main thread, and MCP tool
calls run on worker threads — so the emulator process can't own a window.
This tiny process does nothing but read raw RGB frames from stdin and show
them; the backend pipes frames in. If it dies, the game plays on headless.

Usage: python -m pixel_flippers.viewer_proc <width> <height> <scale> [title]
"""

from __future__ import annotations

import sys


def main() -> None:
    width, height, scale = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    title = sys.argv[4] if len(sys.argv) > 4 else "PIXEL FLIPPERS"

    import pygame

    pygame.init()
    screen = pygame.display.set_mode((width * scale, height * scale))
    pygame.display.set_caption(title)

    frame_bytes = width * height * 3
    stdin = sys.stdin.buffer
    while True:
        data = stdin.read(frame_bytes)
        if len(data) < frame_bytes:  # EOF — parent closed us
            break
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return
        surface = pygame.image.frombuffer(data, (width, height), "RGB").convert()
        pygame.transform.scale(surface, screen.get_size(), screen)
        pygame.display.flip()


if __name__ == "__main__":
    main()
