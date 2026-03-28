"""Pygame-based controller test GUI."""

import queue
import threading

from .state import ControllerState
from .controller import PDP360Controller


def stick_knob_offset(xv: int, yv: int, radius: int) -> tuple[int, int]:
    """Map controller axes to screen-space offsets for the stick knob."""
    if radius <= 0:
        return 0, 0

    def clamp_axis(value: int) -> int:
        return max(-32767, min(32767, value))

    x = clamp_axis(xv)
    y = clamp_axis(yv)
    dx = int((x / 32767) * radius) if x else 0
    dy = int((y / 32767) * radius) if y else 0
    return dx, dy


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

    BG     = (10, 14, 20)
    PANEL  = (22, 28, 38)
    PANEL2 = (30, 38, 51)
    PANEL3 = (45, 56, 74)
    TEXT   = (236, 240, 246)
    MUTED  = (132, 144, 163)
    GREEN  = (96, 216, 160)
    BLUE   = (93, 168, 255)
    RED    = (255, 113, 113)
    YELLOW = (255, 194, 92)
    ORANGE = (255, 141, 95)

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
        shadow = r.move(0, si(5))
        pygame.draw.rect(screen, BG, shadow, border_radius=br)
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
        pygame.draw.circle(screen, BG, (ccx, ccy + si(4)), rr)
        pygame.draw.circle(screen, fill, (ccx, ccy), rr)
        pygame.draw.circle(screen, border, (ccx, ccy), rr, width=lw)
        tc = BG if pressed else TEXT
        s = _text(label, "label", tc)
        screen.blit(s, s.get_rect(center=(ccx, ccy)))

    def draw_pill(label, px, py, pw, ph, color, fg=BG):
        r = sr(px, py, pw, ph)
        pygame.draw.rect(screen, color, r, border_radius=max(1, si(ph / 2)))
        s = _text(label, "small", fg)
        screen.blit(s, s.get_rect(center=r.center))

    def draw_dpad(cx, cy, arm, gap, state_obj):
        arm_rect = sr(cx - arm / 2, cy - arm * 1.5, arm, arm * 3)
        cross_rect = sr(cx - arm * 1.5, cy - arm / 2, arm * 3, arm)
        pygame.draw.rect(screen, PANEL2, arm_rect, border_radius=max(1, si(14)))
        pygame.draw.rect(screen, PANEL2, cross_rect, border_radius=max(1, si(14)))
        up_rect = sr(cx - arm / 2, cy - arm * 1.5, arm, arm - gap)
        down_rect = sr(cx - arm / 2, cy + gap, arm, arm - gap)
        left_rect = sr(cx - arm * 1.5, cy - arm / 2, arm - gap, arm)
        right_rect = sr(cx + gap, cy - arm / 2, arm - gap, arm)
        for rect, active in (
            (up_rect, state_obj.dpad_up),
            (down_rect, state_obj.dpad_down),
            (left_rect, state_obj.dpad_left),
            (right_rect, state_obj.dpad_right),
        ):
            pygame.draw.rect(screen, GREEN if active else PANEL3, rect,
                             border_radius=max(1, si(10)))
        pygame.draw.rect(screen, MUTED, arm_rect, width=max(1, si(2)),
                         border_radius=max(1, si(14)))
        pygame.draw.rect(screen, MUTED, cross_rect, width=max(1, si(2)),
                         border_radius=max(1, si(14)))
        for label, pos in (("U", (cx, cy - arm * 1.06)),
                           ("D", (cx, cy + arm * 0.62)),
                           ("L", (cx - arm * 1.03, cy - arm * 0.05)),
                           ("R", (cx + arm * 0.83, cy - arm * 0.05))):
            surf = _text(label, "small", BG)
            screen.blit(surf, surf.get_rect(center=sc(*pos)))

    def draw_trigger(label, val, tx, ty, tw, th):
        r = sr(tx, ty, tw, th)
        br = max(1, si(16))
        pad = max(1, si(4))
        lw = max(1, si(2))
        pygame.draw.rect(screen, BG, r.move(0, si(5)), border_radius=br)
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
        pygame.draw.circle(screen, BG, (scx, scy + si(6)), sr_)
        pygame.draw.circle(screen, PANEL2, (scx, scy), sr_)
        pygame.draw.circle(screen, PANEL3, (scx, scy), max(1, sr_ - si(18)), width=lw)
        pygame.draw.circle(screen, MUTED, (scx, scy), sr_, width=lw)
        pygame.draw.line(screen, MUTED, (scx - sr_, scy), (scx + sr_, scy), 1)
        pygame.draw.line(screen, MUTED, (scx, scy - sr_), (scx, scy + sr_), 1)
        kr = sr_ - si(16)
        dx, dy = stick_knob_offset(xv, yv, kr)
        kx = scx + dx
        ky = scy + dy
        knob = si(20)
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
    motor_rumble_on = False
    trigger_rumble_on = False
    led_levels = [0, 10, 20, 35, 50]
    led_idx = 2
    prev_snap = None
    prev_size = screen.get_size()
    running = True

    def queue_rumble():
        cmd_q.put((
            "rumble",
            (
                180 if motor_rumble_on else 0,
                220 if motor_rumble_on else 0,
                255 if trigger_rumble_on else 0,
                255 if trigger_rumble_on else 0,
            ),
        ))

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
                    motor_rumble_on = not motor_rumble_on
                    queue_rumble()
                elif ev.key == pygame.K_t:
                    trigger_rumble_on = not trigger_rumble_on
                    queue_rumble()
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
        snap = (
            cur.as_tuple(),
            st_text,
            st_conn,
            st_err,
            motor_rumble_on,
            trigger_rumble_on,
            led_idx,
        )
        if snap == prev_snap:
            clock.tick(60)
            continue
        prev_snap = snap

        # -- Draw frame -------------------------------------------------------
        screen.fill(BG)

        draw_text("Taurino", "title", TEXT, (48, 28))
        draw_text("PDP Xbox Controller Monitor", "label", MUTED, (48, 62))
        draw_text("Esc quit   R body rumble   T trigger rumble   L LED", "small", MUTED, (48, 92))

        draw_pill("LIVE" if st_conn else "WAIT", 928, 32, 112, 34, GREEN if st_conn else ORANGE)
        draw_pill(f"LED {led_levels[led_idx]}", 928, 76, 112, 34, PANEL3, TEXT)

        pygame.draw.circle(screen, PANEL2, sc(1022, 118), si(118), width=max(1, si(1)))
        pygame.draw.circle(screen, PANEL2, sc(118, 690), si(150), width=max(1, si(1)))

        left_grip = sr(56, 196, 380, 430)
        right_grip = sr(664, 196, 380, 430)
        center_body = sr(228, 158, 644, 354)
        ridge = sr(292, 142, 514, 96)
        pygame.draw.ellipse(screen, PANEL, left_grip)
        pygame.draw.ellipse(screen, PANEL, right_grip)
        pygame.draw.rect(screen, PANEL, center_body, border_radius=max(1, si(70)))
        pygame.draw.rect(screen, PANEL2, ridge, border_radius=max(1, si(38)))
        pygame.draw.ellipse(screen, PANEL3, sr(130, 232, 224, 286), width=max(1, si(2)))
        pygame.draw.ellipse(screen, PANEL3, sr(746, 240, 224, 278), width=max(1, si(2)))

        draw_trigger("LT", cur.left_trigger, 120, 104, 186, 38)
        draw_trigger("RT", cur.right_trigger, 794, 104, 186, 38)
        draw_button("LB", 156, 154, 122, 42, cur.left_bumper)
        draw_button("RB", 822, 154, 122, 42, cur.right_bumper)

        draw_button("VIEW", 430, 238, 94, 42, cur.view)
        draw_button("MENU", 576, 238, 94, 42, cur.menu)
        draw_round_button("XBOX", 550, 192, 34, cur.guide, BLUE)
        draw_button("SYNC", 500, 292, 100, 40, cur.sync, BLUE)

        draw_stick("Left Stick", 286, 330, 108, cur.left_stick_x, cur.left_stick_y)
        draw_dpad(292, 520, 46, 6, cur)
        draw_button("LS", 232, 646, 112, 42, cur.left_stick_press, BLUE)

        draw_round_button("Y", 826, 306, 34, cur.y, YELLOW)
        draw_round_button("X", 760, 372, 34, cur.x, BLUE)
        draw_round_button("B", 892, 372, 34, cur.b, ORANGE)
        draw_round_button("A", 826, 438, 34, cur.a, GREEN)
        draw_stick("Right Stick", 744, 540, 108, cur.right_stick_x, cur.right_stick_y)
        draw_button("RS", 688, 646, 112, 42, cur.right_stick_press, BLUE)

        status_card = sr(404, 560, 318, 118)
        pygame.draw.rect(screen, PANEL2, status_card, border_radius=max(1, si(24)))
        pygame.draw.rect(screen, PANEL3, status_card, width=max(1, si(2)),
                         border_radius=max(1, si(24)))
        lines = [
            f"Buttons: {', '.join(cur.buttons) or '(none)'}",
            f"Status: {st_text}",
            f"Rumble: body={'on' if motor_rumble_on else 'off'}  trigger={'on' if trigger_rumble_on else 'off'}",
        ]
        for i, line in enumerate(lines):
            draw_text(line, "small", MUTED, (430, 586 + i * 24))
        draw_text("A/B/X/Y and sticks update live", "small", TEXT, (430, 652))
        if st_err:
            draw_text(st_err[:56], "small", RED, (430, 676))

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
