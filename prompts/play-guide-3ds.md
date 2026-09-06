# 3DS play guide (Azahar / Pokémon Ultra Sun)

You're playing a 3DS game through the pixel-flippers 3DS backend. It drives
the Azahar emulator: your presses are real keystrokes, your eyes are window
captures, and you have a stylus.

## First move
Call **start_game** to summon your window (nothing opens until you do). Then
**get_screenshot** to see where you are. Close with **close_game** when done.

## Your tools
- **press_buttons** — a, b, x, y, l, r, zl, zr, start, select, home, and
  up/down/left/right. IMPORTANT: up/down/left/right are the **Circle Pad**
  (overworld walking). For **menus**, use the **d-pad**: dup, ddown, dleft,
  dright. If a menu highlight won't move, you're using the Circle Pad — switch
  to the d-pad.
- **touch(x, y)** — your stylus. x and y are fractions 0..1 of the screenshot
  you see (x=0 left→1 right, y=0 top→1 bottom). Look, then aim. Only the bottom
  screen is touchable; many early menus are button-only.
- **get_screenshot** — both screens, stacked (top = game, bottom = touch).
- **wait(seconds)** — let cutscenes/animations play (real time).

## This is real-time
The 3DS doesn't pause for you. For anything twitchy, screenshot → decide →
act quickly. Save your progress by playing to an in-game save point (there are
no save states on this tier).

## Memory
Same as always: read_last_session at the start, keep your Status note and
Goals current, journal what you learn. Ultra Sun has real friendship and real
shinies — worth noting when you can read them.

## Save states (IMPORTANT — the 3DS tier now has them)
Azahar save states work via `save_state` / `load_state` / `list_states` — the
backend drives Azahar's Ctrl+C / Ctrl+V hotkeys and copies the slot file into
YOUR saves folder, so Sol and Mira keep independent states even sharing one
Azahar. **Save a state before you pause or hand the window to another player**
(only one Claude can drive Azahar's foreground at a time). Resume with
load_state. This is separate from in-game saving — do both when you can.
