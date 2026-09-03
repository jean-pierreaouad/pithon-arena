#!/usr/bin/env python3
"""
Πthon Arena — Client
EECE 350 Computing Networks Project
Run:  python client.py [SERVER_IP] [PORT]

Controls (during game)
  WASD / Arrow keys  move your snake
  T                  open chat input
  Enter              send chat message
  Escape             cancel chat input
"""

import pygame
import socket
import threading
import json
import sys
import time

# ── Window / grid layout ─────────────────────────────────────────────────────
WIN_W   = 1080
WIN_H   = 700
GAME_W  = 800        # game grid area width  (40 cells × 20 px)
GAME_H  = 500        # game grid area height (25 cells × 20 px)
SIDE_W  = WIN_W - GAME_W   # 280 — right panel
CHAT_H  = WIN_H - GAME_H   # 200 — bottom chat strip
CELL    = 20         # pixels per cell
GRID_W  = GAME_W // CELL   # 40
GRID_H  = GAME_H // CELL   # 25
FPS     = 60

# ── Colour palette (dark space theme) ────────────────────────────────────────
C_BG        = ( 12,  12,  22)
C_GAME_BG   = (  8,   8,  16)
C_GRID      = ( 18,  18,  32)
C_SIDE_BG   = ( 18,  18,  30)
C_CHAT_BG   = ( 14,  14,  24)
C_BORDER    = ( 55,  55,  90)
C_WHITE     = (230, 230, 240)
C_GREY      = (130, 130, 160)
C_DK_GREY   = ( 60,  60,  90)

# Snake colours   [player-0,  player-1]
C_BODY      = [( 30, 190,  90), (210,  90,  20)]
C_HEAD      = [( 60, 255, 130), (255, 130,  40)]

# Collectible colours
C_PIE   = {'apple': (200, 55, 55), 'golden': (240, 195, 25), 'rotten': ( 75, 140, 50)}
C_PW    = {'shield': ( 55, 140, 245), 'speed': (255, 215, 45)}
C_OBS   = ( 70,  70,  90)

C_HP_HI = ( 50, 200, 100)
C_HP_MID= (220, 180,  50)
C_HP_LO = (220,  55,  55)

C_ACCENT  = ( 90, 115, 255)
C_BTN     = ( 35,  45, 100)
C_BTN_HOV = ( 55,  70, 155)
C_BTN_TXT = (200, 210, 255)
C_GREEN_BTN = (35, 110, 55)
C_RED_BTN   = (110, 35, 35)


# ── Network helper ────────────────────────────────────────────────────────────
class Net:
    def __init__(self):
        self.sock  = None
        self._buf  = ""
        self._lock = threading.Lock()

    def connect(self, host: str, port: int):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        self.sock.connect((host, port))
        self.sock.settimeout(None)

    def send(self, msg: dict):
        try:
            data = (json.dumps(msg) + '\n').encode()
            with self._lock:
                self.sock.sendall(data)
        except Exception as e:
            print(f'[NET] send error: {e}')

    def readline(self):
        try:
            while '\n' not in self._buf:
                chunk = self.sock.recv(4096)
                if not chunk:
                    return None
                self._buf += chunk.decode('utf-8', errors='replace')
            line, self._buf = self._buf.split('\n', 1)
            return line.strip()
        except Exception:
            return None

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


# ── Drawing utilities ─────────────────────────────────────────────────────────
def draw_rect_aa(surface, color, rect, radius=5):
    pygame.draw.rect(surface, color, rect, border_radius=radius)

def draw_rect_border(surface, color, rect, width=1, radius=5):
    pygame.draw.rect(surface, color, rect, width, border_radius=radius)

def blit_center(surface, font, text, cx, cy, color):
    surf = font.render(text, True, color)
    surface.blit(surf, (cx - surf.get_width() // 2, cy - surf.get_height() // 2))

def clamp(val, lo, hi):
    return max(lo, min(hi, val))


# ── Main Application ──────────────────────────────────────────────────────────
class App:
    S_CONNECT  = 'connect'
    S_LOGIN    = 'login'
    S_LOBBY    = 'lobby'
    S_GAME     = 'game'
    S_SPECTATE = 'spectate'
    S_OVER     = 'over'

    def __init__(self):
        pygame.init()
        pygame.display.set_caption('Pithon Arena')
        self.screen = pygame.display.set_mode((WIN_W, WIN_H))
        self.clock  = pygame.time.Clock()
        self._load_fonts()

        self.net   = Net()
        self.state = self.S_CONNECT

        # ── connect screen ──
        self.s_ip   = '127.0.0.1'
        self.s_port = '5555'
        self.cf     = 'ip'        # active field
        self.c_err  = ''

        # ── login screen ──
        self.l_name = ''
        self.l_err  = ''

        # ── lobby ──
        self.my_name      = ''
        self.lobby_list   = []    # [usernames]
        self.lobby_games  = []    # [{'players':[..], 'spectators':N}]
        self.sel_player   = None
        self.pending_from = None  # incoming challenge sender
        self.info_msg     = ''
        self.info_timer   = 0.0

        # ── game ──
        self.snake_id = 0
        self.p_names  = ['', '']
        self.gs       = None      # latest game_state dict
        self.is_spec  = False

        # ── game over ──
        self.go_data  = None

        # ── chat ──
        self.chat_log  = []       # [{'from':str,'message':str}]
        self.chat_inp  = ''
        self.chat_mode = False

        # ── button rects (rebuilt each frame) ──
        self._btns: dict[str, pygame.Rect] = {}

        # ── network message queue ──
        self._q  = []
        self._ql = threading.Lock()

    # ── Fonts ──

    def _load_fonts(self):
        candidates = ['segoeui', 'calibri', 'arial', 'helvetica', 'freesansbold']
        mono_cands = ['consolas', 'couriernew', 'courier', 'freemono']
        def best(names, size, bold=False):
            for n in names:
                f = pygame.font.SysFont(n, size, bold=bold)
                if f:
                    return f
            return pygame.font.Font(None, size)
        self.f36 = best(candidates, 36, bold=True)
        self.f24 = best(candidates, 24, bold=True)
        self.f18 = best(candidates, 18, bold=False)
        self.f14 = best(candidates, 14, bold=False)
        self.f12 = best(mono_cands, 13, bold=False)

    # ── Network receive thread ──

    def _recv_loop(self):
        while True:
            line = self.net.readline()
            if line is None:
                self._enq({'type': '_disc'})
                break
            try:
                self._enq(json.loads(line))
            except Exception:
                pass

    def _enq(self, msg):
        with self._ql:
            self._q.append(msg)

    def _deq(self):
        with self._ql:
            out = self._q[:]
            self._q.clear()
        return out

    # ── Main loop ──

    def run(self):
        while True:
            dt = self.clock.tick(FPS) / 1000.0
            for msg in self._deq():
                self._on_server(msg)
            if self.info_timer > 0:
                self.info_timer = max(0.0, self.info_timer - dt)
            self._handle_events()
            self._draw()
            pygame.display.flip()

    # ── Event handling ──

    def _handle_events(self):
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                self.net.close()
                pygame.quit()
                sys.exit()
            s = self.state
            if   s == self.S_CONNECT:  self._ev_connect(ev)
            elif s == self.S_LOGIN:    self._ev_login(ev)
            elif s == self.S_LOBBY:    self._ev_lobby(ev)
            elif s in (self.S_GAME, self.S_SPECTATE):
                                       self._ev_game(ev)
            elif s == self.S_OVER:     self._ev_over(ev)

    # connect screen events
    def _ev_connect(self, ev):
        if ev.type == pygame.MOUSEBUTTONDOWN:
            if 'connect_btn' in self._btns and self._btns['connect_btn'].collidepoint(ev.pos):
                self._do_connect()
            elif 'ip_field' in self._btns and self._btns['ip_field'].collidepoint(ev.pos):
                self.cf = 'ip'
            elif 'port_field' in self._btns and self._btns['port_field'].collidepoint(ev.pos):
                self.cf = 'port'
        if ev.type == pygame.KEYDOWN:
            if ev.key == pygame.K_TAB:
                self.cf = 'port' if self.cf == 'ip' else 'ip'
            elif ev.key == pygame.K_RETURN:
                self._do_connect()
            elif ev.key == pygame.K_BACKSPACE:
                if self.cf == 'ip':
                    self.s_ip   = self.s_ip[:-1]
                else:
                    self.s_port = self.s_port[:-1]
            elif ev.unicode and ev.unicode.isprintable():
                if self.cf == 'ip' and len(self.s_ip) < 20:
                    self.s_ip   += ev.unicode
                elif self.cf == 'port' and len(self.s_port) < 6:
                    self.s_port += ev.unicode

    def _do_connect(self):
        try:
            self.net.connect(self.s_ip, int(self.s_port))
            threading.Thread(target=self._recv_loop, daemon=True).start()
            self.state = self.S_LOGIN
            self.c_err = ''
        except Exception as e:
            self.c_err = str(e)

    # login screen events
    def _ev_login(self, ev):
        if ev.type == pygame.MOUSEBUTTONDOWN:
            if 'join_btn' in self._btns and self._btns['join_btn'].collidepoint(ev.pos):
                self._send_join()
        if ev.type == pygame.KEYDOWN:
            if ev.key == pygame.K_RETURN:
                self._send_join()
            elif ev.key == pygame.K_BACKSPACE:
                self.l_name = self.l_name[:-1]
            elif ev.unicode and ev.unicode.isprintable() and len(self.l_name) < 20:
                self.l_name += ev.unicode

    def _send_join(self):
        n = self.l_name.strip()
        if n:
            self.net.send({'type': 'join', 'username': n})

    # lobby screen events
    def _ev_lobby(self, ev):
        # If in chat mode, capture keys there
        if self.chat_mode:
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_RETURN:
                    t = self.chat_inp.strip()
                    if t:
                        self.net.send({'type': 'chat', 'message': t})
                    self.chat_inp  = ''
                    self.chat_mode = False
                elif ev.key == pygame.K_ESCAPE:
                    self.chat_mode = False
                    self.chat_inp  = ''
                elif ev.key == pygame.K_BACKSPACE:
                    self.chat_inp  = self.chat_inp[:-1]
                elif ev.unicode and ev.unicode.isprintable() and len(self.chat_inp) < 100:
                    self.chat_inp += ev.unicode
            return

        if ev.type == pygame.KEYDOWN and ev.key == pygame.K_t:
            self.chat_mode = True

        if ev.type == pygame.MOUSEBUTTONDOWN:
            for key, r in self._btns.items():
                if not r.collidepoint(ev.pos):
                    continue
                if key.startswith('pl_'):
                    self.sel_player = key[3:]
                elif key == 'challenge_btn':
                    if self.sel_player:
                        self.net.send({'type': 'challenge', 'target': self.sel_player})
                elif key == 'spectate_btn':
                    self.net.send({'type': 'spectate'})
                elif key == 'accept_btn':
                    if self.pending_from:
                        self.net.send({'type': 'accept_challenge', 'from': self.pending_from})
                        self.pending_from = None
                elif key == 'decline_btn':
                    if self.pending_from:
                        self.net.send({'type': 'decline_challenge', 'from': self.pending_from})
                        self.pending_from = None
                elif key == 'chat_input_box':
                    self.chat_mode = True

    # game/spectate events
    def _ev_game(self, ev):
        if self.chat_mode:
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_RETURN:
                    t = self.chat_inp.strip()
                    if t:
                        self.net.send({'type': 'chat', 'message': t})
                    self.chat_inp  = ''
                    self.chat_mode = False
                elif ev.key == pygame.K_ESCAPE:
                    self.chat_mode = False
                    self.chat_inp  = ''
                elif ev.key == pygame.K_BACKSPACE:
                    self.chat_inp  = self.chat_inp[:-1]
                elif ev.unicode and ev.unicode.isprintable() and len(self.chat_inp) < 100:
                    self.chat_inp += ev.unicode
            return

        if ev.type == pygame.MOUSEBUTTONDOWN:
            if 'chat_box' in self._btns and self._btns['chat_box'].collidepoint(ev.pos):
                self.chat_mode = True

        if ev.type == pygame.KEYDOWN:
            if ev.key == pygame.K_t:
                self.chat_mode = True
                return
            if not self.is_spec:
                DMAP = {
                    pygame.K_UP: 'UP',    pygame.K_w: 'UP',
                    pygame.K_DOWN: 'DOWN', pygame.K_s: 'DOWN',
                    pygame.K_LEFT: 'LEFT', pygame.K_a: 'LEFT',
                    pygame.K_RIGHT: 'RIGHT', pygame.K_d: 'RIGHT',
                }
                d = DMAP.get(ev.key)
                if d:
                    self.net.send({'type': 'input', 'direction': d})

    # game-over events
    def _ev_over(self, ev):
        if ev.type == pygame.KEYDOWN and ev.key in (
            pygame.K_RETURN, pygame.K_SPACE, pygame.K_ESCAPE
        ):
            self.state   = self.S_LOBBY
            self.gs      = None
            self.go_data = None
            self.chat_log.clear()

    # ── Server message handler ──

    def _on_server(self, msg: dict):
        t = msg.get('type')

        if t == '_disc':
            self.c_err = 'Disconnected from server.'
            self.state = self.S_CONNECT

        elif t == 'join_ok':
            self.my_name = msg['username']
            self.state   = self.S_LOBBY
            self.l_err   = ''

        elif t == 'join_error':
            self.l_err = msg.get('reason', 'Error')

        elif t == 'lobby':
            self.lobby_list  = msg.get('players', [])
            self.lobby_games = msg.get('games', [])

        elif t == 'challenge_received':
            self.pending_from = msg['from']
            self._info(f"{msg['from']} challenged you!", 6.0)

        elif t == 'challenge_declined':
            self._info(f"{msg.get('by', '?')} declined your challenge", 4.0)

        elif t == 'info':
            self._info(msg.get('message', ''), 4.0)

        elif t == 'game_start':
            self.snake_id  = msg['snake_id']
            self.is_spec   = False
            self.gs        = None
            self.chat_log.clear()
            self.chat_mode = False
            self.chat_inp  = ''
            yn = msg.get('your_name', self.my_name)
            op = msg.get('opponent', '?')
            self.p_names = [yn, op] if self.snake_id == 0 else [op, yn]
            self.state = self.S_GAME

        elif t == 'spectate_ok':
            self.p_names   = msg.get('player_names', ['P1', 'P2'])
            self.is_spec   = True
            self.gs        = None
            self.chat_log.clear()
            self.chat_mode = False
            self.state     = self.S_SPECTATE

        elif t == 'game_state':
            self.gs = msg

        elif t == 'game_end':
            self.go_data = msg
            self.gs      = None
            self.state   = self.S_OVER

        elif t == 'chat':
            entry = {'from': msg['from'], 'message': msg['message']}
            self.chat_log.append(entry)
            if len(self.chat_log) > 60:
                self.chat_log.pop(0)

    def _info(self, text: str, dur: float = 4.0):
        self.info_msg   = text
        self.info_timer = dur

    # ── Master draw dispatcher ──

    def _draw(self):
        self.screen.fill(C_BG)
        self._btns.clear()
        s = self.state
        if   s == self.S_CONNECT:  self._dr_connect()
        elif s == self.S_LOGIN:    self._dr_login()
        elif s == self.S_LOBBY:    self._dr_lobby()
        elif s in (self.S_GAME, self.S_SPECTATE):
                                   self._dr_game()
        elif s == self.S_OVER:     self._dr_over()

    # ─────────────────────────────────────────────────────────
    # Connect screen
    # ─────────────────────────────────────────────────────────
    def _dr_connect(self):
        cx = WIN_W // 2
        blit_center(self.screen, self.f36, 'Pithon Arena', cx, 120, C_WHITE)
        blit_center(self.screen, self.f14, 'Real-Time Multiplayer Snake Battle', cx, 162, C_GREY)

        fy = 230
        for label, val, key in [
            ('Server IP Address', self.s_ip, 'ip'),
            ('Port Number',       self.s_port, 'port'),
        ]:
            lt = self.f14.render(label, True, C_GREY)
            self.screen.blit(lt, (cx - 160, fy))
            fy += 22
            r  = pygame.Rect(cx - 160, fy, 320, 38)
            draw_rect_aa(self.screen, (22, 25, 50), r, 6)
            active = self.cf == key
            draw_rect_border(self.screen, C_ACCENT if active else C_BORDER, r, 1, 6)
            vt = self.f18.render(val + ('|' if active else ''), True, C_WHITE)
            self.screen.blit(vt, (r.x + 10, r.y + 9))
            self._btns[f'{key}_field'] = r
            fy += 60

        # Button
        br = pygame.Rect(cx - 90, fy, 180, 42)
        mpos = pygame.mouse.get_pos()
        draw_rect_aa(self.screen, C_BTN_HOV if br.collidepoint(mpos) else C_BTN, br, 8)
        blit_center(self.screen, self.f18, 'Connect', cx, fy + 21, C_BTN_TXT)
        self._btns['connect_btn'] = br

        if self.c_err:
            et = self.f14.render(self.c_err, True, (220, 75, 75))
            self.screen.blit(et, (cx - et.get_width() // 2, fy + 54))

        hint = self.f12.render('TAB to switch fields    ENTER to connect', True, C_DK_GREY)
        self.screen.blit(hint, (cx - hint.get_width() // 2, WIN_H - 28))

    # ─────────────────────────────────────────────────────────
    # Login screen
    # ─────────────────────────────────────────────────────────
    def _dr_login(self):
        cx = WIN_W // 2
        blit_center(self.screen, self.f36, 'Choose a Username', cx, 210, C_WHITE)

        r = pygame.Rect(cx - 160, 268, 320, 40)
        draw_rect_aa(self.screen, (22, 25, 50), r, 6)
        draw_rect_border(self.screen, C_ACCENT, r, 1, 6)
        nt = self.f18.render(self.l_name + '|', True, C_WHITE)
        self.screen.blit(nt, (r.x + 10, r.y + 10))

        br = pygame.Rect(cx - 65, 325, 130, 38)
        mpos = pygame.mouse.get_pos()
        draw_rect_aa(self.screen, C_BTN_HOV if br.collidepoint(mpos) else C_BTN, br, 7)
        blit_center(self.screen, self.f18, 'Join', cx, 344, C_BTN_TXT)
        self._btns['join_btn'] = br

        if self.l_err:
            et = self.f14.render(self.l_err, True, (220, 75, 75))
            self.screen.blit(et, (cx - et.get_width() // 2, 375))

    # ─────────────────────────────────────────────────────────
    # Lobby screen
    # ─────────────────────────────────────────────────────────
    def _dr_lobby(self):
        # ── header ──
        ht = self.f24.render(f'Pithon Arena    {self.my_name}', True, C_WHITE)
        self.screen.blit(ht, (16, 10))
        pygame.draw.line(self.screen, C_BORDER, (0, 44), (WIN_W, 44))

        # ── info banner ──
        if self.info_timer > 0:
            it = self.f14.render(self.info_msg, True, (255, 215, 70))
            self.screen.blit(it, (WIN_W // 2 - it.get_width() // 2, 50))
        pygame.draw.line(self.screen, C_BORDER, (0, 70), (WIN_W, 70))

        mpos = pygame.mouse.get_pos()

        # ── LEFT column (0..340): player list ──
        lx = 16
        pt = self.f14.render('Online Players', True, C_GREY)
        self.screen.blit(pt, (lx, 78))

        py = 98
        others = [n for n in self.lobby_list if n != self.my_name]
        if not others:
            nt = self.f12.render('Waiting for others to join...', True, C_DK_GREY)
            self.screen.blit(nt, (lx + 4, py + 4))
            py += 22
        for name in others:
            r = pygame.Rect(lx, py, 300, 30)
            is_sel = name == self.sel_player
            bg = (50, 55, 110) if is_sel else ((28, 33, 60) if r.collidepoint(mpos) else (22, 25, 44))
            draw_rect_aa(self.screen, bg, r, 5)
            nt = self.f18.render(name, True, C_WHITE if is_sel else (190, 195, 220))
            self.screen.blit(nt, (r.x + 10, r.y + 6))
            self._btns[f'pl_{name}'] = r
            py += 34

        py += 6
        if self.sel_player and self.sel_player in others:
            br = pygame.Rect(lx, py, 200, 34)
            draw_rect_aa(self.screen, C_BTN_HOV if br.collidepoint(mpos) else C_BTN, br, 7)
            ct = self.f14.render(f'Challenge  {self.sel_player}', True, C_BTN_TXT)
            self.screen.blit(ct, (br.x + 10, br.y + 9))
            self._btns['challenge_btn'] = br
            py += 44

        # ── Incoming challenge ──
        if self.pending_from:
            pygame.draw.line(self.screen, C_BORDER, (lx, py), (lx + 310, py))
            py += 8
            ct = self.f14.render(f'{self.pending_from}  challenged you!', True, (255, 210, 60))
            self.screen.blit(ct, (lx, py)); py += 24
            for label, key, col in [
                ('Accept', 'accept_btn', C_GREEN_BTN),
                ('Decline', 'decline_btn', C_RED_BTN),
            ]:
                r = pygame.Rect(lx, py, 120, 30)
                hover_col = tuple(clamp(c + 20, 0, 255) for c in col)
                draw_rect_aa(self.screen, hover_col if r.collidepoint(mpos) else col, r, 5)
                blit_center(self.screen, self.f14, label, r.centerx, r.centery, C_WHITE)
                self._btns[key] = r
                lx += 130
            lx = 16; py += 38

        # ── Divider ──
        pygame.draw.line(self.screen, C_BORDER, (330, 70), (330, WIN_H - CHAT_H))

        # ── RIGHT column (350+): ongoing games ──
        rx = 348
        gt = self.f14.render('Ongoing Match', True, C_GREY)
        self.screen.blit(gt, (rx, 78))

        if self.lobby_games:
            for gm in self.lobby_games:
                ps  = '  vs  '.join(gm['players'])
                sp  = f"   ({gm['spectators']} watching)"
                glt = self.f18.render(ps + sp, True, (140, 200, 120))
                self.screen.blit(glt, (rx, 100))

            br = pygame.Rect(rx, 132, 160, 32)
            draw_rect_aa(self.screen, C_BTN_HOV if br.collidepoint(mpos) else C_BTN, br, 6)
            blit_center(self.screen, self.f14, 'Watch  (spectate)', br.centerx, br.centery, C_BTN_TXT)
            self._btns['spectate_btn'] = br
        else:
            nt = self.f12.render('No match in progress', True, C_DK_GREY)
            self.screen.blit(nt, (rx + 4, 102))

        # How-to
        how_y = 200
        lines = [
            'How to play:',
            '  1. Select a player from the list',
            '  2. Click "Challenge" to start a match',
            '  3. Use WASD or arrow keys to move',
            '  4. Eat pies to gain HP',
            '     A = Apple (+10)   G = Golden (+25)   R = Rotten (-15)',
            '  5. Avoid walls, obstacles, and the other snake',
            '  6. Powerups:  S = Shield (blocks 1 hit)   Z = Speed (2x speed)',
            '  7. Press T to chat',
            '  8. Highest HP when time runs out wins!',
        ]
        for line in lines:
            lt = self.f12.render(line, True, C_DK_GREY if line.startswith(' ') else C_GREY)
            self.screen.blit(lt, (rx, how_y)); how_y += 17

        # ── Chat strip ──
        chat_top = WIN_H - CHAT_H
        pygame.draw.line(self.screen, C_BORDER, (0, chat_top), (WIN_W, chat_top))
        self._dr_chat(chat_top + 4, WIN_H - 4, btn_key='chat_input_box')

    # ─────────────────────────────────────────────────────────
    # Game / Spectate screen
    # ─────────────────────────────────────────────────────────
    def _dr_game(self):
        # ── game grid ──
        draw_rect_aa(self.screen, C_GAME_BG, pygame.Rect(0, 0, GAME_W, GAME_H), 0)

        # Grid lines
        for x in range(0, GAME_W + 1, CELL):
            pygame.draw.line(self.screen, C_GRID, (x, 0), (x, GAME_H))
        for y in range(0, GAME_H + 1, CELL):
            pygame.draw.line(self.screen, C_GRID, (0, y), (GAME_W, y))

        if self.gs:
            self._dr_objects(self.gs)

        # Grid border
        draw_rect_border(self.screen, C_BORDER, pygame.Rect(0, 0, GAME_W, GAME_H), 2, 0)

        # ── right panel ──
        draw_rect_aa(self.screen, C_SIDE_BG, pygame.Rect(GAME_W, 0, SIDE_W, GAME_H), 0)
        pygame.draw.line(self.screen, C_BORDER, (GAME_W, 0), (GAME_W, GAME_H))
        self._dr_side_panel()

        # ── chat strip ──
        chat_top = GAME_H
        pygame.draw.line(self.screen, C_BORDER, (0, chat_top), (WIN_W, chat_top))
        self._dr_chat(chat_top + 4, WIN_H - 4, btn_key='chat_box')

        # Spectating banner
        if self.is_spec:
            sb = self.f18.render('  SPECTATING  ', True, (70, 180, 255))
            sbr = pygame.Rect(GAME_W // 2 - sb.get_width() // 2 - 4, 5,
                              sb.get_width() + 8, sb.get_height() + 4)
            draw_rect_aa(self.screen, (20, 40, 80), sbr, 6)
            self.screen.blit(sb, (sbr.x + 4, sbr.y + 2))

        # Waiting overlay
        if not self.gs:
            wt = self.f18.render('Waiting for match to begin...', True, C_GREY)
            self.screen.blit(wt, (GAME_W // 2 - wt.get_width() // 2, GAME_H // 2 - 10))

    def _dr_objects(self, gs: dict):
        obstacles = gs.get('obstacles', [])
        pies      = gs.get('pies',      [])
        powerups  = gs.get('powerups',  [])
        snakes    = gs.get('snakes',    [])

        # Obstacles
        for obs in obstacles:
            ox, oy = obs[0] * CELL, obs[1] * CELL
            draw_rect_aa(self.screen, C_OBS,
                         pygame.Rect(ox + 1, oy + 1, CELL - 2, CELL - 2), 3)
            # X marks
            pygame.draw.line(self.screen, (42, 42, 62),
                             (ox + 4, oy + 4), (ox + CELL - 5, oy + CELL - 5), 2)
            pygame.draw.line(self.screen, (42, 42, 62),
                             (ox + CELL - 5, oy + 4), (ox + 4, oy + CELL - 5), 2)

        # Pies
        for pie in pies:
            px = pie['pos'][0] * CELL + CELL // 2
            py = pie['pos'][1] * CELL + CELL // 2
            col = C_PIE.get(pie['type'], (180, 80, 80))
            pygame.draw.circle(self.screen, col, (px, py), CELL // 2 - 1)
            ltr = {'apple': 'A', 'golden': 'G', 'rotten': 'R'}.get(pie['type'], '?')
            lt  = self.f12.render(ltr, True, C_WHITE)
            self.screen.blit(lt, (px - lt.get_width() // 2, py - lt.get_height() // 2))

        # Power-ups (diamond shape)
        for pw in powerups:
            cx2 = pw['pos'][0] * CELL + CELL // 2
            cy2 = pw['pos'][1] * CELL + CELL // 2
            col = C_PW.get(pw['type'], (200, 200, 200))
            r   = CELL // 2 - 1
            pts = [(cx2, cy2 - r), (cx2 + r, cy2), (cx2, cy2 + r), (cx2 - r, cy2)]
            pygame.draw.polygon(self.screen, col, pts)
            ltr = 'S' if pw['type'] == 'shield' else 'Z'
            lt  = self.f12.render(ltr, True, C_BG)
            self.screen.blit(lt, (cx2 - lt.get_width() // 2, cy2 - lt.get_height() // 2))

        # Snakes
        for i, sn in enumerate(snakes):
            body  = sn.get('body', [])
            alive = sn.get('alive', True)
            if not body:
                continue
            if i < 2:
                bc = C_BODY[i] if alive else (50, 55, 65)
                hc = C_HEAD[i] if alive else (70, 75, 85)
            else:
                bc = hc = (140, 140, 140)

            for j, seg in enumerate(body):
                sx, sy = seg[0] * CELL, seg[1] * CELL
                col = hc if j == 0 else bc
                draw_rect_aa(self.screen, col,
                             pygame.Rect(sx + 1, sy + 1, CELL - 2, CELL - 2), 4)

            # Shield glow around head
            if sn.get('shield') and body:
                hx, hy = body[0][0] * CELL, body[0][1] * CELL
                draw_rect_border(self.screen, C_PW['shield'],
                                 pygame.Rect(hx, hy, CELL, CELL), 2, 4)

            # Speed glow
            if sn.get('sptick', 0) > 0 and body:
                hx, hy = body[0][0] * CELL, body[0][1] * CELL
                draw_rect_border(self.screen, C_PW['speed'],
                                 pygame.Rect(hx - 1, hy - 1, CELL + 2, CELL + 2), 2, 5)

    def _dr_side_panel(self):
        px  = GAME_W + 10
        pw  = SIDE_W - 18
        gs  = self.gs
        mpos = pygame.mouse.get_pos()

        # ── Timer ──
        tl  = gs.get('time_left', 0) if gs else 0
        tc  = (220, 65, 65) if tl < 30 else C_WHITE
        tt  = self.f24.render(f'{int(tl):3d}s', True, tc)
        blit_center(self.screen, self.f24, f'{int(tl):3d}s',
                    GAME_W + SIDE_W // 2, 22, tc)

        # ── Player panels ──
        for i in range(2):
            top = 52 + i * 190
            sn  = (gs['snakes'][i]
                   if gs and i < len(gs.get('snakes', []))
                   else None)
            hp    = max(0, sn['health'])  if sn else 0
            alive = sn.get('alive', True) if sn else False
            name  = self.p_names[i] if i < len(self.p_names) else f'P{i+1}'
            is_me = (i == self.snake_id) and not self.is_spec
            col   = C_HEAD[i]

            # ── Name ──
            prefix = '[YOU] ' if is_me else ''
            nt = self.f18.render(prefix + name, True, col)
            self.screen.blit(nt, (px, top))
            if sn and not alive:
                dt = self.f12.render('DEAD', True, (200, 55, 55))
                self.screen.blit(dt, (px + nt.get_width() + 6, top + 3))

            # ── HP bar ──
            bar_y = top + 26
            bar_r = pygame.Rect(px, bar_y, pw, 20)
            draw_rect_aa(self.screen, (28, 28, 48), bar_r, 4)
            fill  = int(pw * hp / 100) if hp > 0 else 0
            if fill > 0:
                hc = C_HP_HI if hp > 50 else (C_HP_MID if hp > 25 else C_HP_LO)
                draw_rect_aa(self.screen, hc, pygame.Rect(px, bar_y, fill, 20), 4)
            draw_rect_border(self.screen, C_BORDER, bar_r, 1, 4)
            ht2 = self.f12.render(f'{hp} HP', True, C_WHITE)
            self.screen.blit(ht2, (px + pw // 2 - ht2.get_width() // 2, bar_y + 3))

            # ── Status badges ──
            bx = px; by = bar_y + 26
            if sn and sn.get('shield'):
                st = self.f12.render('[SHIELD]', True, C_PW['shield'])
                self.screen.blit(st, (bx, by)); bx += st.get_width() + 6
            if sn and sn.get('sptick', 0) > 0:
                sp = self.f12.render('[SPEED]', True, C_PW['speed'])
                self.screen.blit(sp, (bx, by))

            # ── Divider ──
            if i == 0:
                pygame.draw.line(self.screen, C_BORDER,
                                 (px, top + 180), (px + pw, top + 180))

        # ── Legend ──
        leg_y = 52 + 2 * 190
        legend = [
            ('Pies:', ''),
            ('  A = Apple    +10 HP', ''),
            ('  G = Golden  +25 HP', ''),
            ('  R = Rotten  -15 HP', ''),
            ('', ''),
            ('Power-ups (creative feature):', ''),
            ('  S = Shield   blocks 1 hit', ''),
            ('  Z = Speed   2x speed (5 ticks)', ''),
        ]
        for text, _ in legend:
            lt = self.f12.render(text, True, C_DK_GREY)
            self.screen.blit(lt, (px, leg_y)); leg_y += 15

        # ── Controls hint ──
        ctrl_y = GAME_H - 75
        for hint in ['WASD / Arrows: move', 'T: chat']:
            ht = self.f12.render(hint, True, C_DK_GREY)
            self.screen.blit(ht, (px, ctrl_y)); ctrl_y += 16

    # ─────────────────────────────────────────────────────────
    # Shared chat strip
    # ─────────────────────────────────────────────────────────
    def _dr_chat(self, top: int, bottom: int, btn_key: str):
        # Title
        label_col = C_ACCENT if self.chat_mode else C_DK_GREY
        tl = self.f12.render('Chat  (T to type)', True, label_col)
        self.screen.blit(tl, (10, top))

        # Input box
        in_h = 22
        ir   = pygame.Rect(8, bottom - in_h - 1, WIN_W - 16, in_h)
        draw_rect_aa(self.screen, (20, 20, 38), ir, 4)
        draw_rect_border(self.screen, C_ACCENT if self.chat_mode else C_BORDER, ir, 1, 4)
        if self.chat_mode:
            disp = self.chat_inp[-68:] + '|'
            tc   = C_WHITE
        else:
            disp = 'Press T to open chat...'
            tc   = C_DK_GREY
        it = self.f12.render(disp, True, tc)
        self.screen.blit(it, (ir.x + 6, ir.y + 4))
        self._btns[btn_key] = ir

        # Messages
        msg_top = top + 18
        msg_bot = bottom - in_h - 6
        vis     = max(1, (msg_bot - msg_top) // 16)
        msgs    = self.chat_log[-vis:]
        for j, cm in enumerate(msgs):
            raw = f"{cm['from']}: {cm['message']}"
            ct  = self.f12.render(raw[:96], True, (190, 195, 215))
            self.screen.blit(ct, (10, msg_top + j * 16))

    # ─────────────────────────────────────────────────────────
    # Game-over screen
    # ─────────────────────────────────────────────────────────
    def _dr_over(self):
        # Dim background
        dim = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 170))
        self.screen.blit(dim, (0, 0))

        cx = WIN_W // 2

        # Card
        card = pygame.Rect(cx - 260, 130, 520, 380)
        draw_rect_aa(self.screen, (18, 18, 32), card, 14)
        draw_rect_border(self.screen, C_ACCENT, card, 2, 14)

        d      = self.go_data or {}
        winner = d.get('winner', '?')
        reason = d.get('reason', '')
        scores = d.get('scores', [0, 0])
        names  = d.get('player_names', self.p_names)

        # Headline
        if winner == 'Draw':
            msg_col = (220, 185, 50)
            headline = 'DRAW!'
        elif winner == self.my_name and not self.is_spec:
            msg_col  = (60, 220, 110)
            headline = 'YOU WIN!'
        elif self.is_spec:
            msg_col  = (160, 200, 255)
            headline = f'{winner} wins!'
        else:
            msg_col  = (220, 65, 65)
            headline = f'{winner} wins!'

        blit_center(self.screen, self.f36, headline, cx, 165, msg_col)

        # Reason
        rt = self.f14.render(reason, True, C_GREY)
        self.screen.blit(rt, (cx - rt.get_width() // 2, 208))

        # Score rows
        for i in range(min(2, len(names))):
            hp   = scores[i] if i < len(scores) else 0
            yt   = 240 + i * 80
            col  = C_HEAD[i] if i < 2 else C_WHITE
            nt   = self.f18.render(names[i], True, col)
            self.screen.blit(nt, (card.x + 28, yt))

            bw = 260;  bx = card.x + 218
            draw_rect_aa(self.screen, (28, 28, 50), pygame.Rect(bx, yt + 3, bw, 20), 4)
            fw = int(bw * max(0, hp) / 100)
            if fw > 0:
                hc = C_HP_HI if hp > 50 else (C_HP_MID if hp > 25 else C_HP_LO)
                draw_rect_aa(self.screen, hc, pygame.Rect(bx, yt + 3, fw, 20), 4)
            draw_rect_border(self.screen, C_BORDER, pygame.Rect(bx, yt + 3, bw, 20), 1, 4)
            ht2 = self.f12.render(f'{max(0, hp)} HP', True, C_WHITE)
            self.screen.blit(ht2, (bx + bw // 2 - ht2.get_width() // 2, yt + 6))

            # Winner crown
            if names[i] == winner:
                wt = self.f14.render('WINNER', True, (240, 200, 40))
                self.screen.blit(wt, (card.x + 28, yt + 24))

        hint = self.f12.render('Press ENTER or SPACE to return to lobby', True, C_DK_GREY)
        self.screen.blit(hint, (cx - hint.get_width() // 2, card.bottom - 36))


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app = App()
    if len(sys.argv) >= 3:
        app.s_ip   = sys.argv[1]
        app.s_port = sys.argv[2]
    elif len(sys.argv) == 2:
        app.s_ip   = sys.argv[1]
    app.run()
