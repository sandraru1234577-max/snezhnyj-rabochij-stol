# -*- coding: utf-8 -*-
"""
Снежный рабочий стол — оптимизированная версия.
Исправлены баги с F8, колесиком, снегоуборщиком и зависаниями.

Управление:
  зажать ↑            — снежок у курсора появился и растёт
  зажать ↓            — снежок уменьшается/исчезает
  клик ЛКМ            — положить снежок здесь (3 рядом → снеговик ⛄)
  колесико мышки      — запасной способ растить снежок
  ← →                 — ветер
  F8                  — убрать всё и вся
  ESC                 — выключить снег и выйти
"""
import ctypes
import math
import random
import sys
import threading
from ctypes import wintypes

import pygame
import win32api

# ---------------- Защита от дублей: один снег на экране ----------------
# Если копия уже запущена — вторая тихо выходит, а не рисует поверх первую
_kernel32 = ctypes.windll.kernel32
_kernel32.CreateMutexW(None, False, "SnezhnyRabochijStol_Mutex")
if _kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
    sys.exit(0)

# ---------------- Лог ошибок для exe ----------------
# При запуске из exe (нет консоли) — все ошибки и выезды трактора пишем
# в %APPDATA%\snezhnyj_stol\snow.log, чтобы «тихая смерть» была видна
if getattr(sys, 'frozen', False):
    try:
        import os
        _logdir = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')),
                               'snezhnyj_stol')
        os.makedirs(_logdir, exist_ok=True)
        _logf = open(os.path.join(_logdir, 'snow.log'), 'w', buffering=1)
        sys.stderr = _logf
        sys.stdout = _logf
    except Exception:
        pass

import win32con
import win32gui
import pywintypes

# ---------------- Настройки экрана и окна ----------------
W = win32api.GetSystemMetrics(0)
H = win32api.GetSystemMetrics(1)
MAGENTA = (255, 0, 255)
SNOW_COLOR = (240, 248, 255)
SNOW_EDGE = (200, 220, 240)

pygame.init()
pygame.display.set_caption("Снежный рабочий стол")
screen = pygame.display.set_mode((W, H), pygame.NOFRAME)
hwnd = pygame.display.get_wm_info()["window"]

GWL_EXSTYLE = -20
WS_EX = 0x80000 | 0x20 | 0x8 | 0x80
win32gui.SetWindowLong(hwnd, GWL_EXSTYLE,
                       win32gui.GetWindowLong(hwnd, GWL_EXSTYLE) | WS_EX)
win32gui.SetLayeredWindowAttributes(hwnd, win32api.RGB(*MAGENTA), 0, 0x01)
win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                      win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)

user32 = ctypes.windll.user32

# ---------------- Модель снега ----------------
COL_W = 4
NCOLS = W // COL_W
heights = [0.0] * NCOLS
MAX_H = 110
ACC = 0.9          # быстрее наметает (было 0.6)
BASE_H = 14        # базовая полоска снега — есть всегда, минимум под каждым сугробом

def clamp_to_base():
    """Снег внизу никогда не исчезает совсем: минимум BASE_H по всей ширине."""
    for c in range(NCOLS):
        if heights[c] < BASE_H:
            heights[c] = BASE_H

wind = 0.4

def new_flake():
    return [
        random.uniform(-80, W + 80),
        random.uniform(-H * 0.6, -5),
        random.uniform(1.2, 3.2),
        random.uniform(1, 3),
        random.uniform(0, 6.28),
    ]

FRESH_DROP = [0.0]   # снег, свеже долепленный падающими комьями (с вкладок)

def deposit(c, amount):
    """Равномерный намёт: часть снега уходит соседям с плавным спадом —
    вместо кочки в одной колонке получается пологий холмик."""
    weights = [(0, 0.5), (-1, 0.22), (1, 0.22), (-2, 0.03), (2, 0.03)]
    for dc, k in weights:
        cc = c + dc
        if 0 <= cc < NCOLS:
            heights[cc] = min(MAX_H, heights[cc] + amount * k)
    FRESH_DROP[0] += amount   # свежий снег «с вкладок» — триггер для трактора

N_FLAKES = 80    # экономия CPU: комки крупнее и рисуются каждый кадр —
flakes = [new_flake() for _ in range(N_FLAKES)]  # плотность на глаз почти та же

# ---------------- Хук колесика мыши ----------------
wheel_clicks = [0]
_wheel_lock = threading.Lock()

WH_MOUSE_LL = 14
HC_ACTION = 0
WM_MOUSEWHEEL = 0x020A

def _wheel_listener():
    def _hook(n_code, w_param, l_param):
        if n_code == HC_ACTION and w_param == WM_MOUSEWHEEL:
            delta = ctypes.c_short(w_param >> 16 & 0xFFFF).value
            with _wheel_lock:
                wheel_clicks[0] += delta // 120
        # lParam указателей больше диапазона c_int — только так не переполняется
        return user32.CallNextHookEx(None, n_code, w_param, ctypes.c_void_p(l_param))

    HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    cb = HOOKPROC(_hook)
    handle = user32.SetWindowsHookExW(WH_MOUSE_LL, cb, None, 0)
    
    if not handle:
        print("Warning: Wheel hook failed", file=sys.stderr)
        return
        
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
        
    user32.UnhookWindowsHookEx(handle)

_wheel_thread = threading.Thread(target=_wheel_listener, daemon=True)
_wheel_thread.start()

# ---------------- Поток-сборщик окон (чтобы снег не зависал на чужих окнах) ----------------
import queue
WIN_QUEUE = queue.Queue()

def PYGAME_HWND_INIT():
    return pygame.display.get_wm_info()["window"]
PYGAME_HWND = PYGAME_HWND_INIT()

def _window_scanner():
    import time
    while True:
        fresh = []
        def _cb(h, _):
            # Своё полноэкранное окно не считаем — иначе все окна «перекрыты»
            if h == PYGAME_HWND:
                return
            try:
                if win32gui.IsWindowVisible(h) and not win32gui.IsIconic(h):
                    t = win32gui.GetWindowText(h)
                    r = win32gui.GetWindowRect(h)
                    if t and r[2]-r[0]>250 and r[3]-r[1]>150 and t != "Program Manager":
                        fresh.append((h, r))
            except Exception:
                pass  # окно умерло между проверками — не беда
        try:
            win32gui.EnumWindows(_cb, None)
            # Панель задач и виджеты Windows: заголовка нет и высота мала,
            # обычный фильтр их отбрасывает — ловим по классу окна
            for cls in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
                try:
                    h = win32gui.FindWindow(cls, None)
                    if h and win32gui.IsWindowVisible(h):
                        fresh.append((h, win32gui.GetWindowRect(h)))
                except Exception:
                    pass
            keep = []
            for i, (h, r) in enumerate(fresh):
                area = (r[2]-r[0])*(r[3]-r[1])
                covered = 0.0
                for h2, r2 in fresh[:i]:
                    ox = max(0, min(r[2], r2[2]) - max(r[0], r2[0]))
                    oy = max(0, min(r[3], r2[3]) - max(r[1], r2[1]))
                    covered += (ox*oy)/max(1, area)
                if covered < 0.7: keep.append((h, r))
            # В очереди всегда максимум один свежий список; старый выкидываем
            try:
                WIN_QUEUE.get_nowait()
            except Exception:
                pass
            WIN_QUEUE.put(keep[:5])
        except Exception:
            pass
        time.sleep(1)   # отклик окон быстрее: задержка максимум 1 секунда

_win_thread = threading.Thread(target=_window_scanner, daemon=True)
_win_thread.start()

# ---------------- Игровые объекты ----------------
balls = []
snowmen = []
plow = None
PLOW_THRESHOLD = 45
PLOW_EVERY = 16 * 120  # тики до выезда снегоуборщика: 16 FPS * 120 c = ровно 2 минуты
plow_timer = [0]  # копится с нуля до PLOW_EVERY (строка выше)

win_states = []
window_drift = {}
FALLING = []   # сугробы с закрытых окон: [y верх, высота, born-tick, x0, x1]
EDGE_DROPS = []  # комки, скатывающиеся с краёв живых окон: [x, y, vy, born, size]
CLOSED_RECT = {}  # hwnd → (x0, x1): ширина окна, чтобы комки падали по его ширине

# Предварительно рисуем спрайты для оптимизации
def create_snowball_sprite(r, color, edge_color):
    surf = pygame.Surface((r*2+4, r*2+4), pygame.SRCALPHA)
    center = (r+2, r+2)
    pygame.draw.circle(surf, color, center, int(r))
    pygame.draw.circle(surf, edge_color, center, int(r), 2)
    return surf

def create_snowman_sprite(scale):
    r3, r2, r1 = 8*scale, 12*scale, 16*scale
    # Ширины/высоты хватает под нос-морковку (справа) и на всю голову (сверху)
    width = int(2*r1 + 2.6*r3 + 12)
    height = int(2*r1 + 1.7*r2 + 1.9*r3 + 8)
    surf = pygame.Surface((width, height), pygame.SRCALPHA)

    cx = width // 2
    cy_bottom = height - r1 - 2
    
    # Нижний шар
    pygame.draw.circle(surf, SNOW_COLOR, (cx, cy_bottom), int(r1))
    pygame.draw.circle(surf, SNOW_EDGE, (cx, cy_bottom), int(r1), 2)
    
    # Средний: верх нижнего + сдвиг внахлёст, но не выше середины шара
    cy_mid = max(cy_bottom - r1 - r2 + int(r2*0.3), cy_bottom - r1 - int(r2*0.75))
    pygame.draw.circle(surf, SNOW_COLOR, (cx, cy_mid), int(r2))
    pygame.draw.circle(surf, SNOW_EDGE, (cx, cy_mid), int(r2), 2)
    
    # Голова: тоже с запасом, центр не выше края поверхности
    cy_head = max(cy_mid - r2 - r3 + int(r3*0.2), cy_mid - r2 - int(r3*0.9))
    pygame.draw.circle(surf, SNOW_COLOR, (cx, cy_head), int(r3))
    pygame.draw.circle(surf, SNOW_EDGE, (cx, cy_head), int(r3), 2)
    
    # Глаза
    ex = int(r3 * 0.4)
    ey = int(r3 * 0.25)
    pygame.draw.circle(surf, (30, 30, 40), (cx - ex, cy_head - ey), 2)
    pygame.draw.circle(surf, (30, 30, 40), (cx + ex, cy_head - ey), 2)
    
    # Нос
    pygame.draw.polygon(surf, (255, 140, 0),
                        [(cx, cy_head), (cx + int(r3*1.2), cy_head+2), (cx, cy_head+4)])

    # Руки-палочки: из-под среднего шара вверх в стороны
    arm_y = cy_mid - int(r2 * 0.3)
    arm_len = int(r1 * 1.4)
    for sgn in (-1, 1):
        ax, ay = cx + sgn * int(r2 * 0.8), arm_y
        bx, by = cx + sgn * (int(r2 * 0.8) + arm_len), arm_y - int(r2 * 0.8)
        pygame.draw.line(surf, (120, 80, 40), (ax, ay), (bx, by), max(2, int(scale*2)))
        # «варежка»-сучок на конце
        pygame.draw.circle(surf, (120, 80, 40), (bx, by), max(2, int(scale*1.5)))

    # Шарф на шее (между головой и средним шаром): кольцо + свисающий конец
    neck_y = (cy_head + r3 + cy_mid - r2) // 2
    sw = int(r3 * 0.8)
    sh = max(3, int(r3 * 0.45))
    pygame.draw.rect(surf, (200, 30, 60), (cx - sw, neck_y - sh // 2, sw * 2, sh),
                    border_radius=3)
    # свисающий хвост шарфа — линия вниз со ступенькой
    tail_x = cx + int(sw * 0.5)
    pygame.draw.line(surf, (200, 30, 60), (tail_x, neck_y),
                     (tail_x, neck_y + int(r2 * 1.2)), max(3, int(r3 * 0.5)))
    pygame.draw.line(surf, (160, 20, 50),
                     (tail_x, neck_y + int(r2 * 1.2)),
                     (tail_x - int(r3 * 0.6), neck_y + int(r2 * 1.4)),
                     max(2, int(r3 * 0.4)))

    # Ведро сверху (вместо шляпы-каска): серый цилиндр, чуть нахлобучен
    bw = int(r3 * 1.6)
    bh = int(r3 * 0.9)
    bx0 = cx - bw // 2
    by0 = cy_head - r3 + int(r3 * 0.25)   # низ ведра чуть ниже макушки
    pygame.draw.rect(surf, (110, 110, 125), (bx0, by0 - bh, bw, bh + 4),
                     border_radius=3)
    pygame.draw.rect(surf, (70, 70, 85), (bx0, by0 - bh, bw, bh + 4), 2, border_radius=3)
    # блик
    pygame.draw.line(surf, (150, 150, 165), (bx0 + 3, by0 - bh + 3),
                     (bx0 + 3, by0 - 2), 2)
    return surf

# Кэш спрайтов
snowball_cache = {}
snowman_cache = {}

def get_snowball_surf(r):
    key = int(r)
    if key not in snowball_cache:
        snowball_cache[key] = create_snowball_sprite(key, SNOW_COLOR, SNOW_EDGE)
    return snowball_cache[key]

def get_snowman_surf(scale):
    key = round(scale, 1)
    if key not in snowman_cache:
        snowman_cache[key] = create_snowman_sprite(key)
    return snowman_cache[key]

def spawn_plow():
    # Выезжает с той стороны, где снега БОЛЬШЕ
    left = sum(heights[: NCOLS // 2])
    right = sum(heights[NCOLS // 2:])
    if left >= right:
        return [-140.0, 1]
    return [float(W + 140), -1]

def draw_plow(surf, x, dir_, tick_):
    # Едет ПО СНЕГУ: гусеницы (низ на y+18) садятся точно на поверхность
    c = max(0, min(NCOLS - 1, int(x) // COL_W))
    hs = heights[max(0, c-3): c+4]
    y = int(H - (sum(hs) / len(hs)) - 18)
    x = int(x)
    body = (200, 30, 30)      # красный трактор
    dark = (120, 20, 20)
    
    pygame.draw.rect(surf, (60, 60, 70), (x - 30, y + 4, 60, 14), border_radius=7)
    for wx in (-18, 0, 18):
        pygame.draw.circle(surf, (35, 35, 45), (x + wx, y + 11), 6)
        
    pygame.draw.rect(surf, body, (x - 22, y - 16, 44, 20), border_radius=4)
    pygame.draw.rect(surf, dark, (x - 22, y - 16, 44, 20), 2, border_radius=4)
    pygame.draw.rect(surf, (170, 220, 255), (x - 14, y - 13, 20, 12), border_radius=2)
    
    pygame.draw.rect(surf, dark, (x + 10 * dir_, y - 26, 6, 12))
    if tick_ % 20 < 10:
        pygame.draw.circle(surf, (200, 200, 210), (int(x + 13 * dir_), y - 30), 4)
        pygame.draw.circle(surf, (230, 230, 240), (int(x + 15 * dir_), y - 36), 3)
        
    pygame.draw.circle(surf, (255, 240, 120), (x + 21 * dir_, y - 6), 3)
    
    bx = x + 34 * dir_
    pts = [(bx, y + 16), (bx + 14 * dir_, y + 10),
           (bx + 14 * dir_, y - 12), (bx, y - 16)]
    pygame.draw.polygon(surf, (180, 190, 205), pts)
    pygame.draw.polygon(surf, (140, 150, 170), pts, 2)

    # Валик собранного снега перед отвалом — комки «катятся»: подпрыгивают
    # и смещаются с разными фазами, будто снег перекатывается вперёд
    fx = int(x + 48 * dir_)
    fy = y + 18
    for i, rr in enumerate((14, 10, 7)):
        # каждый комок живёт со своей фазой: bob — вертикальный подскок,
        # wob — лёгкий горизонтальный «перекат»
        phase = tick_ * 0.3 + i * 1.7
        bob = abs(math.sin(phase)) * (4 - i)
        wob = math.sin(phase * 0.7 + i) * 3
        cx_ = fx + i * 8 * dir_ + int(wob * dir_)
        cy_ = fy - rr * 0.4 - int(bob)
        pygame.draw.circle(surf, SNOW_COLOR, (cx_, cy_), rr)
        pygame.draw.circle(surf, SNOW_EDGE, (cx_, cy_), rr, 2)

def sweep(cx, cy, radius, power):
    c0 = max(0, int((cx - radius) // COL_W))
    c1 = min(NCOLS - 1, int((cx + radius) // COL_W))
    for c in range(c0, c1 + 1):
        if heights[c] <= 0: continue
        x = c * COL_W + COL_W / 2
        h = heights[c]
        if math.hypot(x - cx, (H - h / 2) - cy) < radius + h / 4:
            heights[c] = max(0.0, heights[c] - power)
            
    for f in flakes:
        if math.hypot(f[0] - cx, f[1] - cy) < radius:
            dx = f[0] - cx or 0.01
            f[0] += dx * 0.08
            f[1] += 1

def make_snowball(x, y, r):
    global balls
    near = [b for b in balls if math.hypot(b[0] - x, b[1] - y) < 140]
    if len(near) >= 2:
        bx = (near[0][0] + near[1][0] + x) / 3
        by = max(near[0][1], near[1][1], y)
        # Размер снеговика — от снежков И случайный разброс: бывают мелкие и крупные
        base = (near[0][2] + near[1][2] + r) / 3 / 14
        scale = min(1.8, max(0.4, base + random.uniform(-0.35, 0.35)))
        snowmen.append([bx, by, scale])
        for b in near:
            if b in balls: balls.remove(b)
        return
    balls.append([x, y, r])

# ---------------- Главный цикл ----------------
roll_r = 0.0
prev_lmb = False
mouse_prev = None
tick = 0
CLOCK = pygame.time.Clock()
RUNNING = True

while RUNNING:
    for e in pygame.event.get():
        if e.type == pygame.QUIT:
            RUNNING = False

    key = user32.GetAsyncKeyState

    if key(0x1B) & 0x8000: RUNNING = False        # ESC (удержание)

    if key(0x77) & 0x8000:                         # F8 — удержание, надёжнее «нажатия»
        heights = [BASE_H * 0.5] * NCOLS   # сброс, но не до голого пола
        balls = []
        snowmen = []
        window_drift.clear()
        FALLING.clear()
        wind = 0.0
        roll_r = 0.0

    tick += 1
    # Базовая полоска снега: поддерживаем минимум по всей ширине (и после
    # проезда трактора/F8 она восстанавливается сама, сглаженно)
    clamp_to_base()

    if key(0x25) & 0x8000: wind = max(-6.0, wind - 0.4)   # ←
    if key(0x27) & 0x8000: wind = min(6.0, wind + 0.4)    # →

    mx, my = win32gui.GetCursorPos()
    lmb_down = bool(key(0x01) & 0x8000)
    moved = mouse_prev is not None and math.hypot(mx - mouse_prev[0], my - mouse_prev[1]) > 1
    mouse_prev = (mx, my)

    screen.fill(MAGENTA)

    # Сметание
    if moved and not lmb_down and tick % 3 == 0:
        sweep(mx, my, 40, 2)

    # Снежок: КЛАВИШИ ↑ ↓ (основной способ — надёжнее ролика),
    # колесико мышки — запасной, Backspace — исчезнуть
    with _wheel_lock:
        clicks = wheel_clicks[0]
        wheel_clicks[0] = 0

    # стрелка вверх: зажать/тапнуть — снежок растёт
    grow = clicks + (10 if key(0x26) & 0x8000 else 0)   # VK_UP
    shrink = (10 if key(0x28) & 0x8000 else 0)          # VK_DOWN
    wheel_flash = 0
    if grow:
        wheel_flash = 15
        if roll_r <= 0 and grow > 0:
            roll_r = 4.0          # первый раз — снежок появился у курсора
        else:
            roll_r = min(34.0, roll_r + grow * 0.6)
    if shrink:
        roll_r = max(0.0, roll_r - shrink * 0.6)
    if key(0x0E) & 1:  # Backspace — убрать снежок с курсора совсем
        roll_r = 0.0

    if roll_r > 0:
        # Рисуем снежок через спрайт для скорости
        s_surf = get_snowball_surf(roll_r)
        rect = s_surf.get_rect(center=(mx, my))
        screen.blit(s_surf, rect)
        
        # Рост снежка при качении по снегу
        c0 = max(0, int((mx - roll_r) // COL_W))
        c1 = min(NCOLS - 1, int((mx + roll_r) // COL_W))
        for c in range(c0, c1 + 1):
            x = c * COL_W + COL_W / 2
            h = heights[c]
            if h > 0 and my > H - h - roll_r:
                eat = min(h, 0.3)
                heights[c] -= eat
                roll_r = min(34.0, roll_r + eat * 0.6)

    # Клик ЛКМ
    if not lmb_down and prev_lmb and roll_r > 0:
        if roll_r > 3:
            make_snowball(mx, min(my, H - 4), roll_r)
        roll_r = 0.0
    prev_lmb = lmb_down

    # Окна и гирлянды: список окон собирает ОТДЕЛЬНЫЙ поток раз в 2 секунды —
    # «тормознутые» окна (GetWindowText виснет на проводнике с сетевой папкой)
    # больше не замораживают снег; задержка появления окна — максимум 2 секунды
    try:
        while not WIN_QUEUE.empty():
            win_states = WIN_QUEUE.get()
            live = {h for h, _ in win_states}
            for (h, r) in win_states:
                CLOSED_RECT[h] = (r[0], r[2])   # запоминаем ширину на случай закрытия
            for k in dict(window_drift):
                if k not in live:
                    # Закрытое окно: сугроб сыпется вниз. Если рядом (по x) уже
                    # падает каскад с окна, закрытого < 5 сек назад, — сливаемся
                    # с ним в ОДИН общий сход снега, а не отдельный.
                    _drift = window_drift.pop(k)
                    _r = CLOSED_RECT.get(k)  # (x0, x1) ширина окна на момент закрытия
                    nx0 = _r[0] if _r else 0
                    nx1 = _r[1] if _r else W
                    merged = None
                    for fb in FALLING:
                        fy0, fh, fborn, fx0, fx1 = fb
                        if fb[1] < 0 or tick - fborn > 100:   # старый/мёртвый — пропускаем
                            continue
                        if nx0 < fx1 + 60 and nx1 > fx0 - 60:  # окна перекрываются по ширине
                            fb[0] = min(fy0, _drift[1])        # общая кромка — самая высокая
                            fb[1] = fh + _drift[0] * 0.7        # снег суммируется (с запасом)
                            fb[3] = min(fx0, nx0)               # общий x-диапазон
                            fb[4] = max(fx1, nx1)
                            merged = fb
                            break
                    if merged is None:
                        FALLING.append([_drift[1], _drift[0], tick, nx0, nx1])
    except Exception:
        pass

    # Сугроб с закрытого окна стекает ПЛОТНОЙ снежной массой — как на ролике
    # tryflake: снег уходит вниз целиком, осыпью, не исчезая. Объём тот же:
    # сколько лежало на окне, столько сыплется и остаётся горкой у земли.
    # Трактор потом честно сгребает эту горку вместе с остальным снегом.
    for fb in FALLING:
        y0, hpx, born, fx0, fx1 = fb
        age = tick - born
        span = max(20, fx1 - fx0)
        G = 0.22                                   # g в px/кадр² (общая для блока)

        # 1) нижний «фронт» — движущаяся снежная масса: волнистый край, катится
        #    вниз по всей ширине окна; чем толще был сугроб, тем массивнее фронт
        prog = min(1.0, age / (30 + (H - y0) / 18))   # до низа, зависит от высоты окна
        # земля под фронтом: самая высокая точка сугроба в полосе окна
        cols_span = [int(c // COL_W) for c in range(int(fx0), int(fx1), max(1, COL_W))
                     if 0 <= int(c // COL_W) < NCOLS]
        g_front = H - max((heights[c] for c in cols_span), default=14)
        # фронт до сугроба; easing «быстрый старт» (1−(1−p)²) — движение видно
        # с первого кадра, а не разгоняется невидимо как при prog²
        front_y = y0 + 2 + (g_front - y0 - 2) * (1.0 - (1.0 - prog) ** 2)
        thick = max(2.0, hpx * (1.0 - prog * 0.7))    # толщина фронта тает к земле
        nseg = max(12, span // 10)
        a = [(fx0 + span * i / nseg,
              front_y + math.sin(i * 0.7 + age * 0.3) * 3)
             for i in range(nseg + 1)]
        b = [(fx0 + span * i / nseg,
              front_y + thick + math.sin(i * 0.9 - age * 0.25) * 3)
             for i in range(nseg, -1, -1)]
        if prog < 1.0:
            pygame.draw.polygon(screen, SNOW_COLOR, a + b)
            pygame.draw.lines(screen, SNOW_EDGE, False, a, 1)

        # 2) остаток сугроба на кромке: тает В ТЕМПЕ осыпи — не раньше, чем
        # снег реально ушёл вниз (первый ком срывается через t_slide_min,
        # кромка держится пока комья отрываются, потом быстро истончается)
        t_slide_min = math.sqrt(2 * (1.0 + hpx * 0.1) / (G * 0.55))
        if age < t_slide_min:
            remain = hpx
        else:
            # тает быстрее — срыв почти мгновенный, кромка не висит
            remain = max(0.0, hpx - (age - t_slide_min) * 2.2)
        if remain > 0.15:
            top = [(fx0 + span * i / nseg,
                    y0 + 2 - remain + math.sin(i * 0.9 + tick * 0.1) * 1.2)
                   for i in range(nseg + 1)]
            pygame.draw.polygon(screen, SNOW_COLOR,
                                top + [(fx1, y0 + 2), (fx0, y0 + 2)])

        # 3) осыпь за фронтом — по формулам кинематики тела, брошенного
        #    под углом к горизонту. Каждый ком соскальзывает по кромке
        #    (скату) со своим L, углом α и трением μ:
        #      a  = g·(sinα − μ·cosα)        — ускорение скольжения по скату
        #      v0 = √(2·a·L)                 — скорость у края (срыва)
        #      v0x = v0·cosα, v0y = v0·sinα  — составляющие срыва
        #      t_slide = √(2L/a)             — до срыва ком «едет» по кромке
        #      y(t) = y_edge + v0y·t + g·t²/2 — честное свободное падение
        #      x(t) = x_start + v0x·t         — горизонтальный разлёт
        # сколько кадров проживёт вся осыпь: максимальное (скольжение+падение)
        # по всем комьям — пока последний не приземлится (t = (−v0y+√(v0y²+2g·h))/g)
        last_air = 0
        for i in range(48):
            seed = i * 2.39996
            _alpha = 0.6 + abs(math.sin(seed)) * 0.6
            _mu = 0.12 + abs(math.cos(seed * 1.3)) * 0.18
            _a = max(0.02, G * (math.sin(_alpha) - _mu * math.cos(_alpha)))
            _L = 3.0 + hpx * (0.2 + abs(math.sin(seed * 5.7)) * 0.35)
            _t_slide = math.sqrt(2 * _L / _a)
            _v0y = math.sqrt(2 * _a * _L) * math.sin(_alpha) * 0.6
            _t_fall = (-_v0y + math.sqrt(_v0y * _v0y + 2 * G * (H - y0 - 2))) / G
            last_air = max(last_air, _t_slide + _t_fall)
        last_air = int(last_air) + 2
        for i in range(48):
            seed = i * 2.39996
            alpha = 0.6 + abs(math.sin(seed)) * 0.6          # 34°..69° — крутизна кромки
            mu = 0.12 + abs(math.cos(seed * 1.3)) * 0.18     # трение снега о кромку
            a_slide = G * (math.sin(alpha) - mu * math.cos(alpha))
            if a_slide < 0.02:                               # снег прилип — ком не сорвался
                a_slide = 0.02
            # путь по скату короткий: срыв почти сразу — читается как
            # «масса сорвалась с кромки», без дрожания и волны-задержки
            L = 1.0 + hpx * (0.1 + abs(math.sin(seed * 5.7)) * 0.15)
            t_slide = math.sqrt(2 * L / a_slide)            # короткое скольжение
            if age <= t_slide:
                continue
            v0 = math.sqrt(2 * a_slide * L)                  # скорость срыва
            v0x = v0 * math.cos(alpha) * math.sin(seed * 7.3)  # наружу или внутрь — куда съехал
            v0y = v0 * math.sin(alpha) * 0.6                 # вертикальная составляющая срыва
            t = age - t_slide
            lx = fx0 + span * (i + 0.5) / 48 + v0x * t
            col = max(0, min(NCOLS - 1, int(lx // COL_W)))
            ground = H - heights[col]
            # время падения до земли — корень квадратного уравнения
            # (как в справочнике): t = (−v0y + √(v0y² + 2·g·h)) / g,
            # где h — расстояние от кромки до сугроба внизу
            dist = max(1.0, ground - (y0 + 2))
            t_fall = (-v0y + math.sqrt(v0y * v0y + 2 * G * dist)) / G
            if t >= t_fall:                                  # ком долетел — ВСПЛЕСК:
                # «выплеснутая волна»: у точки удара бугор-всплеск, дальше
                # по направлению полёта ком «брызгает» вытянутым языком,
                # края рваные — застывший всплеск, а не ровная горка
                deposit(col, 3.0)
                splash_dir = 1 if v0x >= 0 else -1
                deposit(max(0, min(NCOLS - 1, col + splash_dir)), 1.6)
                deposit(max(0, min(NCOLS - 1, col + splash_dir * 2)), 1.0)
                deposit(max(0, min(NCOLS - 1, col - splash_dir)), 0.5)
                continue
            ly = y0 + 2 + v0y * t + 0.5 * G * t * t
            air_until = t_slide + t_fall                     # сколько кадров ком ещё в воздухе
            rr = max(3, int(4 + hpx * 0.25 * abs(math.sin(seed * 1.7))))
            pts = []
            for k in range(7):
                ang = k / 7 * 6.283 + seed
                rad = rr * (0.8 + 0.35 * math.sin(seed * 9 + k * 2.1))
                pts.append((lx + rad * math.cos(ang), ly + rad * math.sin(ang)))
            pygame.draw.polygon(screen, SNOW_COLOR, pts)
            pygame.draw.polygon(screen, SNOW_EDGE, pts, 1)

        # конец: фронт дошёл до низа, кромка истаяла, ВСЕ комья долетели
        # (max из t_slide+t_fall по всем комьям — последний приземлился)
        if prog >= 1.0 and remain <= 0.15 and age > last_air:
            fb[1] = -1.0
    FALLING[:] = [fb for fb in FALLING if fb[1] >= 0]

    # Комки, скатившиеся с краёв живых окон: свободное падение + долепление
    live_drops = []
    for dr in EDGE_DROPS:
        dx, dy, vy, dborn, dsize = dr
        vy += 0.30                       # ускорение
        dy += vy
        col = max(0, min(NCOLS - 1, int(dx // COL_W)))
        ground = H - heights[col]
        if dy < ground:
            rr = max(2, int(dsize))
            pts = []
            for k in range(6):
                ang = k / 6 * 6.283 + dx * 0.13
                rad = rr * (0.75 + 0.4 * math.sin(dx + k * 2.3))
                pts.append((dx + rad * math.cos(ang), dy + rad * math.sin(ang)))
            pygame.draw.polygon(screen, SNOW_COLOR, pts)
            pygame.draw.polygon(screen, SNOW_EDGE, pts, 1)
            live_drops.append(dr)
        else:
            deposit(col, 1.2)            # долепился к сугробу внизу
    EDGE_DROPS[:] = live_drops

    for (h, r) in win_states:
        x0, y0, x1, _ = r
        span = x1 - x0
        wd = window_drift.setdefault(h, [0.0, y0])
        wd[0] = min(30.0, wd[0] + 0.22)   # копится заметно быстрее
        hpx = wd[0]
        
        if hpx > 1:
            # Реалистичный слой снега на кромке: два уровня неровности —
            # крупные бугры + мелкая «пушистость», и капли-свесы по краям.
            nseg = max(10, span // 10)
            rough = []
            for i in range(nseg + 1):
                sx = x0 + span * i / nseg
                # крупные бугры (медленно колышутся) + мелкий пух
                big = math.sin(i * 0.8 + h % 7) * hpx * 0.22
                puf = math.sin(i * 2.6 + tick * 0.15) * hpx * 0.06
                # провис к краям: у левого и правого края окна слой
                # мягко опускается вниз (до +50% толщины), в середине — бугор
                edge_d = min(i + 1, nseg - i + 1)          # 1 у краёв, больше к центру
                dip = hpx * 0.5 / max(1, edge_d)           # провис только у самих краёв
                rough.append((sx, y0 + 2 - hpx + big + puf + dip))
            layer = [(x0, y0 + 2 + hpx * 0.5)] + rough + [(x1, y0 + 2 + hpx * 0.5)]
            pygame.draw.polygon(screen, SNOW_COLOR, layer)
            # верхняя кромка — линия с рельефом, чтобы слой был «объёмным»
            pygame.draw.lines(screen, SNOW_EDGE, False,
                              [(sx, sy - 1) for sx, sy in rough], 1)

            # СКАТЫВАНИЕ с краёв живого окна: накопившийся снег иногда
            # срывается комком с угла кромки и летит вниз (тоже долепляется)
            if hpx > 4 and random.random() < 0.02:
                ex = random.choice([x0 + 4, x1 - 4])
                EDGE_DROPS.append([ex, y0 + 4, 0.0, tick, hpx * 0.3])
            
        # Гирлянда
        step = max(46, span//18)
        n = max(2, span//step)
        RAINBOW = [(255,80,80),(255,170,60),(255,240,90),(110,230,120),(90,190,255)]
        for i in range(n+1):
            gx = x0+8+i*step
            gy = y0+6+int(math.sin(gx*0.05)*3)
            phase = (tick*0.06+i*0.7)
            if int(phase)%3:
                base = RAINBOW[i%len(RAINBOW)]
                # Ярче: минимум 80% + крупнее огонёк + свечение-ореол
                on = 0.8+0.2*math.sin(phase*2)
                col = (int(base[0]*on), int(base[1]*on), int(base[2]*on))
                halo = (min(255, col[0]//3+40), min(255, col[1]//3+40), min(255, col[2]//3+40))
                pygame.draw.circle(screen, halo, (gx, gy), 6)
                pygame.draw.circle(screen, col, (gx, gy), 4)

    # Падение снега
    for f in flakes:
        f[4] += 0.05
        f[0] += wind + math.sin(f[4])*0.5
        f[1] += f[2]
        c = int(f[0] // COL_W)
        if 0 <= c < NCOLS and f[1] >= H - 2 - heights[c]:
            if heights[c] > BASE_H:
                deposit(c, ACC)   # рост только сверх базовой полоски
            f[0], f[1] = random.uniform(-80, W+80), random.uniform(-H*0.35, -5)
            continue
        if f[1] > H+5 or f[0] < -100 or f[0] > W+100:
            f[0], f[1] = random.uniform(-80, W+80), random.uniform(-H*0.35, -5)
            
        # каждый «комок» рисуется КАЖДЫЙ кадр — никаких пунктирных полосок;
        # радиус 2–3 px с мягкой обводкой даёт плотный снежный ком
        r = max(2, int(f[3]))
        pygame.draw.circle(screen, SNOW_COLOR, (int(f[0]), int(f[1])), r)
        pygame.draw.circle(screen, SNOW_EDGE, (int(f[0]), int(f[1])), r, 1)

    # Сугробы
    if any(h > 0.5 for h in heights):
        pts = [(0, H+2)]
        for c in range(NCOLS):
            h = heights[c]
            wob = math.sin((c*0.35)+tick*0.02)*2
            pts.append((c*COL_W, H-h+wob))
        pts.append((W, H+2))
        pygame.draw.polygon(screen, SNOW_COLOR, pts)

    # Снегоуборщик: выезжает через 2 минуты снегопада (таймер с поправкой на
    # сметание мышкой — просто тикает, пока программа жива), проверка раз в секунду.
    # Едет с той половины экрана, где снега больше. После проезда таймер заново.
    if plow is None:
        plow_timer[0] += 1
        # Выезд и по таймеру (2 мин), и ВНЕ ТАЙМЕРА — как только снег сильно
        # вырос: средняя высота сугроба по нижней кромке > порога (30 px).
        # Причина выезда видна в snow_err.txt: timer / snowgrown
        mean_h = sum(heights[c] for c in range(0, NCOLS, 4)) / max(1, len(range(0, NCOLS, 4)))
        reason = None
        if plow_timer[0] >= PLOW_EVERY:
            reason = "timer"
        elif mean_h > 30 and plow_timer[0] > 16 * 20:
            reason = "snowgrown"
        elif FRESH_DROP[0] > 25 and plow_timer[0] > 16 * 10:
            reason = "freshdrops"   # надолёпилось с закрываемых вкладок — едем сразу
        FRESH_DROP[0] *= 0.995      # счётчик медленно остывает
        if reason:
            plow_timer[0] = 0   # после чистки таймер начнётся заново с нуля
            plow = spawn_plow()
            print("PLOW spawned at tick", tick, "reason", reason,
                  "side", "left" if plow[1] == 1 else "right",
                  "mean_h", round(mean_h, 1), file=sys.stderr)

    if plow is not None:
        px, dir_ = plow
        px += dir_ * 2.0       # медленнее: весь экран ~45 секунд, не прозевать
        plow[0] = px           # ВАЖНО: пишем позицию обратно, иначе она не движется
        # Чистим не только перед отвалом, но и ПОД всей машиной (корпус 60 px):
        # тогда draw_plow читает уже прочищенную высоту — гусеницы на ней и лежат
        cbody0 = int((px - 30) // COL_W)
        cbody1 = int((px + 30) // COL_W)
        front = int((px + 40 * dir_) // COL_W)
        for c in range(min(max(0, cbody0), max(0, front-6)), max(min(NCOLS, cbody1+1), min(NCOLS, front+7))):
            if heights[c] > 0:
                heights[c] = max(0.0, heights[c] - 8)
                
        draw_plow(screen, px, dir_, tick)
        if px < -180 or px > W + 180:
            plow = None

    # Отрисовка снежков и снеговиков через спрайты
    for (bx, by, br) in balls:
        s = get_snowball_surf(br)
        screen.blit(s, s.get_rect(center=(int(bx), int(by))))

    for (sx, sy, sc) in snowmen:
        s = get_snowman_surf(sc)
        # Позиционируем по центру нижней части снеговика
        rect = s.get_rect(midbottom=(int(sx), int(sy)))
        screen.blit(s, rect)

    pygame.display.flip()
    # 16 FPS вместо 20: глазом не отличить, нагрев ниже; +80 снежинок вместо 110,
    # комки рисуются каждый кадр — плотность visually сохраняется
    CLOCK.tick(16)

pygame.quit()
sys.exit()