"""Pygame-based controller test GUI."""

import queue
import threading

from .state import ControllerState
from .controller import PDP360Controller


def run_gui(vendor_id: int | None = None,
            product_id: int | None = None) -> None:
    try:
        import pygame
    except ImportError:
        raise SystemExit("pygame required: pip install pygame")

    pygame.init()
    pygame.display.set_caption("Taurino — PDP Xbox Controller")

    info = pygame.display.Info()
    dw, dh = info.current_w, info.current_h

    flags = pygame.RESIZABLE
    if hasattr(pygame, "WINDOWMAXIMIZED"):
        flags |= pygame.WINDOWMAXIMIZED
    screen = pygame.display.set_mode((dw, dh), flags)
    clock = pygame.time.Clock()

    # -- Design-space constants -----------------------------------------------
    BW, BH = 1100, 760

    BG     = (14, 18, 25)
    PANEL  = (28, 34, 46)
    PANEL2 = (36, 44, 60)
    TEXT   = (233, 237, 243)
    MUTED  = (145, 156, 173)
    GREEN  = (96, 216, 160)
    BLUE   = (87, 163, 255)
    RED    = (255, 113, 113)
    YELLOW = (255, 194, 92)

    # -- Scale state (recalculated on resize) ---------------------------------
    scale = 1.0
    off_x = 0.0
    off_y = 0.0
    fonts: dict = {}
    text_cache: dict = {}

    def recalc_layout():
        nonlocal scale, off_x, off_y, fonts, text_cache
        w, h = screen.get_size()
        scale = min(w / BW, h / BH)
        off_x = (w - BW * scale) / 2
        off_y = (h - BH * scale) / 2
        fonts = {
            "title": pygame.font.SysFont("Menlo", max(8, int(28 * scale)), bold=True),
            "label": pygame.font.SysFont("Menlo", max(8, int(20 * scale))),
            "small": pygame.font.SysFont("Menlo", max(8, int(16 * scale))),
        }
        text_cache.clear()

    recalc_layout()

    # -- Helpers --------------------------------------------------------------
    def sc(x, y):
        return int(off_x + x * scale), int(off_y + y * scale)

    def si(v):
        return max(1, int(v * scale))

    def sr(x, y, w, h):
        return pygame.Rect(int(off_x + x * scale), int(off_y + y * scale),
                           max(1, int(w * scale)), max(1, int(h * scale)))

    def _text(text, fk, color):
        key = (text, fk, color)
        surf = text_cache.get(key)
        if surf is None:
            if len(text_cache) > 300:
                text_cache.clear()
            surf = fonts[fk].render(text, True, color)
            text_cache[key] = surf
        return surf

    def draw_text(text, fk, color, pos):
        screen.blit(_text(text, fk, color), sc(*pos))

    def draw_button(label, bx, by, bw, bh, pressed, active_color=None):
        col = active_color or GREEN
        fill = col if pressed else PANEL2
        border = col if pressed else MUTED
        r = sr(bx, by, bw, bh)
        br = max(1, si(14))
        lw = max(1, si(2))
        pygame.draw.rect(screen, fill, r, border_radius=br)
        pygame.draw.rect(screen, border, r, width=lw, border_radius=br)
        tc = BG if pressed else TEXT
        s = _text(label, "label", tc)
        screen.blit(s, s.get_rect(center=r.center))

    def draw_round_button(label, cx, cy, radius, pressed, active_color=None):
        col = active_color or GREEN
        fill = col if pressed else PANEL2
        border = col if pressed else MUTED
        ccx, ccy = sc(cx, cy)
        rr = si(radius)
        lw = max(1, si(2))
        pygame.draw.circle(screen, fill, (ccx, ccy), rr)
        pygame.draw.circle(screen, border, (ccx, ccy), rr, width=lw)
        tc = BG if pressed else TEXT
        s = _text(label, "label", tc)
        screen.blit(s, s.get_rect(center=(ccx, ccy)))

    def draw_dpad(cx, cy, arm, gap, state_obj):
        size = arm
        draw_button("U", cx - size / 2, cy - gap - size, size, size, state_obj.dpad_up)
        draw_button("D", cx - size / 2, cy + gap, size, size, state_obj.dpad_down)
        draw_button("L", cx - gap - size, cy - size / 2, size, size, state_obj.dpad_left)
        draw_button("R", cx + gap, cy - size / 2, size, size, state_obj.dpad_right)
        pygame.draw.circle(screen, PANEL2, sc(cx, cy), si(size * 0.28))
        pygame.draw.circle(screen, MUTED, sc(cx, cy), si(size * 0.28), width=max(1, si(2)))

    def draw_trigger(label, val, tx, ty, tw, th):
        r = sr(tx, ty, tw, th)
        br = max(1, si(12))
        pad = max(1, si(4))
        lw = max(1, si(2))
        pygame.draw.rect(screen, PANEL2, r, border_radius=br)
        pygame.draw.rect(screen, MUTED, r, width=lw, border_radius=br)
        fill_w = int((min(1023, max(0, val)) / 1023) * (r.width - 2 * pad))
        if fill_w:
            fr = pygame.Rect(r.x + pad, r.y + pad, fill_w, r.height - 2 * pad)
            pygame.draw.rect(screen, YELLOW, fr, border_radius=max(1, si(8)))
        label_s = _text(label, "label", TEXT)
        value_s = _text(str(val), "small", MUTED)
        screen.blit(label_s, label_s.get_rect(midbottom=sc(tx + tw / 2, ty - 8)))
        screen.blit(value_s, value_s.get_rect(midtop=sc(tx + tw / 2, ty + th + 8)))

    def draw_stick(label, cx, cy, rad, xv, yv):
        scx, scy = sc(cx, cy)
        sr_ = si(rad)
        lw = max(1, si(2))
        pygame.draw.circle(screen, PANEL2, (scx, scy), sr_)
        pygame.draw.circle(screen, MUTED, (scx, scy), sr_, width=lw)
        pygame.draw.line(screen, MUTED, (scx - sr_, scy), (scx + sr_, scy), 1)
        pygame.draw.line(screen, MUTED, (scx, scy - sr_), (scx, scy + sr_), 1)
        kr = sr_ - si(16)
        kx = scx + int((xv / 32767) * kr) if xv else scx
        ky = scy - int((yv / 32767) * kr) if yv else scy
        knob = si(18)
        pygame.draw.circle(screen, BLUE, (kx, ky), knob)
        pygame.draw.circle(screen, TEXT, (kx, ky), knob, width=lw)
        label_s = _text(label, "label", TEXT)
        coords_s = _text(f"x={xv:6d}  y={yv:6d}", "small", MUTED)
        screen.blit(label_s, label_s.get_rect(midtop=sc(cx, cy + rad + 18)))
        screen.blit(coords_s, coords_s.get_rect(midtop=sc(cx, cy + rad + 46)))

    # -- Controller worker thread ---------------------------------------------
    state = ControllerState()
    state_lock = threading.Lock()
    status_info = {"text": "Connecting...", "connected": False, "error": None}
    stop_ev = threading.Event()
    cmd_q: queue.Queue = queue.Queue()

    def worker():
        nonlocal state
        ctrl = None
        try:
            with state_lock:
                status_info["text"] = "Searching..."
            ctrl = PDP360Controller.find(vendor_id, product_id)
            with state_lock:
                status_info["text"] = "Initializing..."
            ctrl.open()
            with state_lock:
                status_info["text"] = "Connected"
                status_info["connected"] = True

            while not stop_ev.is_set():
                # Drain commands
                while True:
                    try:
                        c, v = cmd_q.get_nowait()
                    except queue.Empty:
                        break
                    if c == "rumble":
                        ctrl.set_rumble(*v)
                    elif c == "led":
                        ctrl.set_led(v[0])

                new = ctrl.poll(timeout_ms=8, max_packets=32)
                if new is not None:
                    with state_lock:
                        state = new
        except Exception as e:
            with state_lock:
                status_info["error"] = str(e)
                status_info["text"] = "Failed"
                status_info["connected"] = False
        finally:
            if ctrl:
                try:
                    ctrl.set_rumble(0, 0, 0, 0)
                except Exception:
                    pass
                ctrl.close()
            with state_lock:
                status_info["connected"] = False

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    # -- Main loop ------------------------------------------------------------
    rumble_on = False
    led_levels = [0, 10, 20, 35, 50]
    led_idx = 2
    prev_snap = None
    prev_size = screen.get_size()
    running = True

    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type in (pygame.VIDEORESIZE,
                             getattr(pygame, "WINDOWRESIZED", 0)):
                ns = screen.get_size()
                if ns != prev_size:
                    prev_size = ns
                    recalc_layout()
                    prev_snap = None
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    running = False
                elif ev.key == pygame.K_r:
                    rumble_on = not rumble_on
                    cmd_q.put(("rumble",
                               (180, 220, 0, 0) if rumble_on else (0, 0, 0, 0)))
                elif ev.key == pygame.K_t:
                    rumble_on = not rumble_on
                    cmd_q.put(("rumble",
                               (0, 0, 255, 255) if rumble_on else (0, 0, 0, 0)))
                elif ev.key == pygame.K_l:
                    led_idx = (led_idx + 1) % len(led_levels)
                    cmd_q.put(("led", (led_levels[led_idx],)))

        # Grab latest snapshot
        with state_lock:
            cur = state
            st_text = status_info["text"]
            st_conn = status_info["connected"]
            st_err = status_info["error"]

        # Include status in dirty check so overlay/status redraws correctly
        snap = (cur.as_tuple(), st_text, st_conn, st_err, rumble_on, led_idx)
        if snap == prev_snap:
            clock.tick(60)
            continue
        prev_snap = snap

        # -- Draw frame -------------------------------------------------------
        screen.fill(BG)

        draw_text("Taurino — PDP Xbox Controller", "title", TEXT, (40, 28))
        draw_text("Esc: quit   R: rumble   T: trigger rumble   L: LED",
                  "small", MUTED, (40, 68))

        left_grip = sr(62, 178, 356, 438)
        right_grip = sr(682, 178, 356, 438)
        center_body = sr(220, 148, 660, 370)
        pygame.draw.ellipse(screen, PANEL, left_grip)
        pygame.draw.ellipse(screen, PANEL, right_grip)
        pygame.draw.rect(screen, PANEL, center_body, border_radius=max(1, si(58)))
        pygame.draw.ellipse(screen, PANEL2, sr(152, 220, 236, 310), width=max(1, si(2)))
        pygame.draw.ellipse(screen, PANEL2, sr(712, 220, 236, 310), width=max(1, si(2)))

        # Shoulders / triggers
        draw_trigger("LT", cur.left_trigger, 126, 92, 178, 36)
        draw_trigger("RT", cur.right_trigger, 796, 92, 178, 36)
        draw_button("LB", 164, 140, 110, 42, cur.left_bumper)
        draw_button("RB", 826, 140, 110, 42, cur.right_bumper)

        # Center cluster
        draw_button("VIEW", 434, 218, 96, 42, cur.view)
        draw_button("MENU", 570, 218, 96, 42, cur.menu)
        draw_button("GUIDE", 502, 162, 96, 40, cur.guide, BLUE)
        draw_button("SYNC", 502, 272, 96, 40, cur.sync, BLUE)

        # Left side
        draw_stick("Left Stick", 274, 302, 104,
               cur.left_stick_x, cur.left_stick_y)
        draw_dpad(284, 500, 52, 4, cur)
        draw_button("LS", 220, 634, 108, 42, cur.left_stick_press)

        # Right side
        draw_round_button("Y", 812, 288, 32, cur.y, (246, 208, 84))
        draw_round_button("X", 754, 346, 32, cur.x, (93, 170, 255))
        draw_round_button("B", 870, 346, 32, cur.b, (255, 132, 96))
        draw_round_button("A", 812, 404, 32, cur.a, (96, 216, 160))
        draw_stick("Right Stick", 748, 520, 104,
               cur.right_stick_x, cur.right_stick_y)
        draw_button("RS", 694, 634, 108, 42, cur.right_stick_press)

        # Status text
        lines = [
            f"Buttons: {', '.join(cur.buttons) or '(none)'}",
            f"Status: {st_text}",
            f"Rumble: {'on' if rumble_on else 'off'}  |  LED: {led_levels[led_idx]}",
        ]
        for i, line in enumerate(lines):
            draw_text(line, "small", MUTED, (404, 586 + i * 22))
        if st_err:
            draw_text(st_err[:60], "small", RED, (404, 586 + len(lines) * 22))

        # Disconnected overlay
        if not st_conn:
            ww, wh = screen.get_size()
            # Dim overlay — use fill + set_alpha (no per-pixel alpha alloc)
            ov = pygame.Surface((ww, wh))
            ov.fill((8, 12, 16))
            ov.set_alpha(140)
            screen.blit(ov, (0, 0))
            r = sr(280, 300, 540, 120)
            pygame.draw.rect(screen, PANEL2, r, border_radius=max(1, si(20)))
            pygame.draw.rect(screen, BLUE, r, width=max(1, si(2)),
                             border_radius=max(1, si(20)))
            draw_text(st_text, "label", BLUE, (316, 340))
            if st_err:
                draw_text(st_err[:50], "small", RED, (316, 376))

        pygame.display.flip()
        clock.tick(60)

    stop_ev.set()
    t.join(timeout=1.5)
    pygame.quit()
