#!/usr/bin/env python3
"""
Πthon Arena — Server
EECE 350 Computing Networks Project
Run:  python server.py [PORT]   (default port 5555)

Architecture
------------
• One thread per connected client  (handles login + lobby + game I/O)
• One thread per active game       (tick loop at 10 Hz)
• All shared state guarded by a single server lock
• Wire protocol: newline-delimited JSON  (each message ends with '\\n')

Client → Server message types
-------------------------------
  join              {"type":"join","username":"..."}
  challenge         {"type":"challenge","target":"..."}
  accept_challenge  {"type":"accept_challenge","from":"..."}
  decline_challenge {"type":"decline_challenge","from":"..."}
  input             {"type":"input","direction":"UP"|"DOWN"|"LEFT"|"RIGHT"}
  chat              {"type":"chat","message":"..."}
  spectate          {"type":"spectate"}
  leave_game        {"type":"leave_game"}

Server → Client message types
-------------------------------
  join_ok           {"type":"join_ok","username":"..."}
  join_error        {"type":"join_error","reason":"..."}
  lobby             {"type":"lobby","players":[...],"games":[...]}
  challenge_received{"type":"challenge_received","from":"..."}
  challenge_declined{"type":"challenge_declined","by":"..."}
  info              {"type":"info","message":"..."}
  game_start        {"type":"game_start","snake_id":0|1,"your_name":"...","opponent":"...","grid_w":40,"grid_h":25}
  spectate_ok       {"type":"spectate_ok","grid_w":40,"grid_h":25,"player_names":["p1","p2"]}
  game_state        {"type":"game_state","snakes":[...],"pies":[...],"obstacles":[...],"powerups":[...],"time_left":int,"player_names":["p1","p2"]}
  game_end          {"type":"game_end","winner":"...","scores":[hp0,hp1],"player_names":["p1","p2"],"reason":"..."}
  chat              {"type":"chat","from":"...","message":"..."}
"""

import socket
import threading
import json
import time
import random
import sys

# ── Game configuration ───────────────────────────────────────────────────────
GRID_W          = 40          # grid columns
GRID_H          = 25          # grid rows
TICK            = 0.10        # seconds per game tick  (10 ticks / sec)
GAME_SECONDS    = 120         # match duration
INIT_HP         = 100         # starting health

WALL_DMG        = 20
OBSTACLE_DMG    = 15
BODY_DMG        = 15
HEAD_HEAD_DMG   = 20

PIE_TABLE   = {'apple': 10, 'golden': 25, 'rotten': -15}
PIE_PROBS   = [0.60, 0.20, 0.20]
MAX_PIES    = 8

PW_TYPES        = ['shield', 'speed']   # creative feature: power-ups
PW_INTERVAL     = 20   # seconds between spawns
PW_LIFETIME     = 15   # seconds before auto-despawn
PW_SPEED_TICKS  = 5    # ticks of double-move on speed boost


def rand_pie_type():
    return random.choices(list(PIE_TABLE.keys()), PIE_PROBS)[0]


# ── Client connection wrapper ─────────────────────────────────────────────────
class ClientConn:
    def __init__(self, sock, addr):
        self.sock       = sock
        self.addr       = addr
        self.username   = None
        self.game       = None    # Game | None
        self.snake_id   = None    # 0 | 1
        self.spectator  = False
        self.pdir       = None    # pending direction [dx, dy]
        self._buf       = ""
        self._wlock     = threading.Lock()

    # ── I/O ──

    def send(self, msg: dict):
        """Thread-safe JSON send."""
        try:
            data = (json.dumps(msg) + '\n').encode()
            with self._wlock:
                self.sock.sendall(data)
        except Exception:
            pass

    def readline(self):
        """Blocking read until next '\\n'. Returns None on disconnect/error."""
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


# ── Game state ────────────────────────────────────────────────────────────────
class Game:
    _gid = 0

    def __init__(self, p1: ClientConn, p2: ClientConn):
        Game._gid += 1
        self.gid        = Game._gid
        self.players    = [p1, p2]
        self.spectators = []
        self.running    = False
        self.winner     = None
        self.reason     = ""
        self.time_left  = float(GAME_SECONDS)
        self._last_pw   = 0.0

        # Static obstacle layout (symmetric)
        self.obstacles = [
            [10, 5],  [10, 19],
            [20, 5],  [20, 12], [20, 19],
            [30, 5],  [30, 19],
            [15, 9],  [15, 15],
            [25, 9],  [25, 15],
        ]

        mid = GRID_H // 2
        self.snakes = [
            {
                'body'  : [[3 + j, mid] for j in range(3, 0, -1)],  # head at [3, mid]
                'dir'   : [1, 0],
                'health': INIT_HP,
                'shield': False,    # creative: absorbs next collision
                'sptick': 0,        # creative: speed boost ticks remaining
                'alive' : True,
            },
            {
                'body'  : [[GRID_W - 4 - j, mid] for j in range(3, 0, -1)],
                'dir'   : [-1, 0],
                'health': INIT_HP,
                'shield': False,
                'sptick': 0,
                'alive' : True,
            },
        ]

        self.pies     = []   # [{'pos':[x,y],'type':str,'value':int}]
        self.powerups = []   # [{'pos':[x,y],'type':str,'exp':float}]

        for _ in range(MAX_PIES):
            self._spawn_pie()

    # ── helpers ──

    def _taken(self) -> set:
        s = set()
        for sn in self.snakes:
            for c in sn['body']:
                s.add(tuple(c))
        for o in self.obstacles:
            s.add(tuple(o))
        for p in self.pies:
            s.add(tuple(p['pos']))
        for pw in self.powerups:
            s.add(tuple(pw['pos']))
        return s

    def _rand_empty(self):
        taken = self._taken()
        for _ in range(400):
            x = random.randint(1, GRID_W - 2)
            y = random.randint(1, GRID_H - 2)
            if (x, y) not in taken:
                return [x, y]
        return None

    def _spawn_pie(self):
        pos = self._rand_empty()
        if pos:
            t = rand_pie_type()
            self.pies.append({'pos': pos, 'type': t, 'value': PIE_TABLE[t]})

    def _spawn_powerup(self):
        pos = self._rand_empty()
        if pos:
            t = random.choice(PW_TYPES)
            self.powerups.append({'pos': pos, 'type': t, 'exp': time.time() + PW_LIFETIME})

    # ── tick ──

    def tick(self) -> bool:
        """Advance game one tick. Returns True to continue, False when over."""
        self.time_left -= TICK

        # Expire powerups
        now = time.time()
        self.powerups = [pw for pw in self.powerups if pw['exp'] > now]

        # Periodic powerup spawn
        if now - self._last_pw >= PW_INTERVAL:
            self._spawn_powerup()
            self._last_pw = now

        # Move each alive snake
        for i in range(2):
            sn = self.snakes[i]
            if not sn['alive']:
                continue

            cl = self.players[i]
            if cl.pdir is not None:
                nd  = cl.pdir
                cl.pdir = None
                cur = sn['dir']
                # Disallow 180° reversal
                if not (nd[0] == -cur[0] and nd[1] == -cur[1]):
                    sn['dir'] = nd

            # Speed boost: 2 steps this tick
            steps = 2 if sn['sptick'] > 0 else 1
            if sn['sptick'] > 0:
                sn['sptick'] -= 1

            for _ in range(steps):
                if sn['alive']:
                    self._step(i)

        # Check deaths
        for sn in self.snakes:
            if sn['health'] <= 0:
                sn['health'] = 0
                sn['alive']  = False

        alive = [i for i, sn in enumerate(self.snakes) if sn['alive']]

        if len(alive) == 0:
            h = [sn['health'] for sn in self.snakes]
            self.winner = self.players[0 if h[0] >= h[1] else 1].username
            if h[0] == h[1]:
                self.winner = 'Draw'
            self.reason = "Both snakes lost all health"
            return False

        if len(alive) == 1:
            dead = 1 - alive[0]
            self.winner = self.players[alive[0]].username
            self.reason = f"{self.players[dead].username} ran out of health"
            return False

        if self.time_left <= 0:
            h = [sn['health'] for sn in self.snakes]
            if h[0] > h[1]:
                self.winner = self.players[0].username
            elif h[1] > h[0]:
                self.winner = self.players[1].username
            else:
                self.winner = 'Draw'
            self.reason = "Time limit reached"
            return False

        # Replenish pies
        while len(self.pies) < MAX_PIES:
            self._spawn_pie()

        return True

    def _step(self, idx: int):
        """Move snake idx one cell. Handles all collision types."""
        sn    = self.snakes[idx]
        other = self.snakes[1 - idx]
        head  = sn['body'][0]
        d     = sn['dir']
        nh    = [head[0] + d[0], head[1] + d[1]]

        def take_dmg(amt: int):
            if sn['shield']:
                sn['shield'] = False   # absorb
            else:
                sn['health'] -= amt

        # ── Wall collision ──
        if not (0 <= nh[0] < GRID_W and 0 <= nh[1] < GRID_H):
            take_dmg(WALL_DMG)
            return   # snake stays, does not move

        # ── Obstacle collision ──
        if nh in self.obstacles:
            take_dmg(OBSTACLE_DMG)
            return

        # ── Head-to-head collision ──
        if other['alive'] and nh == other['body'][0]:
            take_dmg(HEAD_HEAD_DMG)
            if other['shield']:
                other['shield'] = False
            else:
                other['health'] -= HEAD_HEAD_DMG
            return

        # ── Other snake body collision ──
        if other['alive'] and nh in other['body']:
            take_dmg(BODY_DMG)
            return

        # ── Self collision (exclude tail – it will vacate) ──
        if nh in sn['body'][:-1]:
            take_dmg(BODY_DMG)
            return

        # ── Valid move: advance snake ──
        sn['body'].insert(0, nh)

        # Check pie
        grew = False
        for pie in self.pies[:]:
            if pie['pos'] == nh:
                val         = pie['value']
                sn['health'] = max(0, min(INIT_HP * 2, sn['health'] + val))
                self.pies.remove(pie)
                grew = True
                break

        # Check powerup
        for pw in self.powerups[:]:
            if pw['pos'] == nh:
                if pw['type'] == 'shield':
                    sn['shield'] = True
                elif pw['type'] == 'speed':
                    sn['sptick'] = PW_SPEED_TICKS
                self.powerups.remove(pw)
                break

        if not grew:
            sn['body'].pop()   # remove tail (no growth)

    def state_dict(self) -> dict:
        return {
            'type'        : 'game_state',
            'snakes'      : self.snakes,
            'pies'        : self.pies,
            'obstacles'   : self.obstacles,
            'powerups'    : [{'pos': pw['pos'], 'type': pw['type']} for pw in self.powerups],
            'time_left'   : round(self.time_left),
            'player_names': [p.username for p in self.players],
        }


# ── Server ────────────────────────────────────────────────────────────────────
class Server:
    def __init__(self, port: int):
        self.port       = port
        self.clients    = {}     # username -> ClientConn
        self.challenges = {}     # challenger_name -> challenged_name
        self.active     = None   # current Game (one at a time for basic spec)
        self._lock      = threading.Lock()

    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(('', self.port))
        srv.listen(30)
        print(f'[SERVER] Pithon Arena listening on :{self.port}')
        try:
            while True:
                sock, addr = srv.accept()
                t = threading.Thread(
                    target=self._handle,
                    args=(ClientConn(sock, addr),),
                    daemon=True,
                )
                t.start()
        except KeyboardInterrupt:
            print('[SERVER] Shutting down.')

    # ── Lobby broadcast ──

    def _lobby_msg(self) -> dict:
        with self._lock:
            names = list(self.clients.keys())
            games = []
            if self.active and self.active.running:
                games.append({
                    'players'   : [p.username for p in self.active.players],
                    'spectators': len(self.active.spectators),
                })
        return {'type': 'lobby', 'players': names, 'games': games}

    def _broadcast_lobby(self):
        msg = self._lobby_msg()
        with self._lock:
            lobby_clients = [c for c in self.clients.values() if c.game is None]
        for c in lobby_clients:
            c.send(msg)

    # ── Per-client thread ──

    def _handle(self, cl: ClientConn):
        # ── Phase 1: join (username) ──
        while True:
            line = cl.readline()
            if line is None:
                cl.sock.close()
                return
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get('type') != 'join':
                continue
            uname = (msg.get('username') or '').strip()[:20]
            if not uname:
                cl.send({'type': 'join_error', 'reason': 'Username cannot be empty'})
                continue
            with self._lock:
                if uname in self.clients:
                    cl.send({'type': 'join_error', 'reason': 'Username already taken'})
                    continue
                cl.username = uname
                self.clients[uname] = cl
            cl.send({'type': 'join_ok', 'username': uname})
            print(f'  + {uname}  joined from {cl.addr[0]}')
            break

        self._broadcast_lobby()

        # ── Phase 2: lobby / game ──
        try:
            while True:
                line = cl.readline()
                if line is None:
                    break
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                self._dispatch(cl, msg)
        finally:
            self._remove(cl)

    def _dispatch(self, cl: ClientConn, msg: dict):
        t = msg.get('type')

        # ── challenge ──
        if t == 'challenge':
            target_name = msg.get('target', '')
            with self._lock:
                target     = self.clients.get(target_name)
                busy       = bool(self.active and self.active.running)
                own_chal   = cl.username in self.challenges
            err = None
            if not target:
                err = 'Player not found'
            elif target_name == cl.username:
                err = "You can't challenge yourself"
            elif target.game is not None:
                err = 'That player is already in a game'
            elif busy:
                err = 'A match is already in progress; wait for it to end'
            elif own_chal:
                err = 'You already have a pending challenge'
            if err:
                cl.send({'type': 'info', 'message': err})
            else:
                with self._lock:
                    self.challenges[cl.username] = target_name
                target.send({'type': 'challenge_received', 'from': cl.username})

        # ── accept challenge ──
        elif t == 'accept_challenge':
            from_name = msg.get('from', '')
            valid = False
            with self._lock:
                challenger = self.clients.get(from_name)
                if self.challenges.get(from_name) == cl.username:
                    del self.challenges[from_name]
                    valid = True
            if challenger and valid:
                self._start_game(challenger, cl)
            else:
                cl.send({'type': 'info', 'message': 'Challenge is no longer valid'})

        # ── decline challenge ──
        elif t == 'decline_challenge':
            from_name = msg.get('from', '')
            with self._lock:
                challenger = self.clients.get(from_name)
                self.challenges.pop(from_name, None)
            if challenger:
                challenger.send({'type': 'challenge_declined', 'by': cl.username})

        # ── spectate ──
        elif t == 'spectate':
            with self._lock:
                game = self.active
            if game and game.running:
                cl.game      = game
                cl.spectator = True
                game.spectators.append(cl)
                cl.send({
                    'type'        : 'spectate_ok',
                    'grid_w'      : GRID_W,
                    'grid_h'      : GRID_H,
                    'player_names': [p.username for p in game.players],
                })
                cl.send(game.state_dict())
            else:
                cl.send({'type': 'info', 'message': 'No active game to spectate right now'})

        # ── game input ──
        elif t == 'input':
            dir_map = {
                'UP': [0, -1], 'DOWN': [0, 1],
                'LEFT': [-1, 0], 'RIGHT': [1, 0],
            }
            d = dir_map.get(msg.get('direction'))
            if d and cl.game and not cl.spectator:
                cl.pdir = d

        # ── chat ──
        elif t == 'chat':
            text = (msg.get('message') or '').strip()[:200]
            if not text:
                return
            chat_msg = {'type': 'chat', 'from': cl.username, 'message': text}
            if cl.game:
                g = cl.game
                for p in g.players:
                    p.send(chat_msg)
                for s in g.spectators:
                    s.send(chat_msg)
            else:
                # Lobby broadcast
                with self._lock:
                    targets = [c for c in self.clients.values() if c.game is None]
                for c in targets:
                    c.send(chat_msg)

        # ── forfeit ──
        elif t == 'leave_game':
            if cl.game and not cl.spectator:
                g = cl.game
                if g.running:
                    other = 1 - cl.snake_id
                    g.winner  = g.players[other].username
                    g.reason  = f"{cl.username} forfeited"
                    g.running = False

    def _remove(self, cl: ClientConn):
        """Clean up on disconnect."""
        print(f'  - {cl.username} disconnected')
        with self._lock:
            if cl.username:
                self.clients.pop(cl.username, None)
            # Clean up any challenge involving this client
            self.challenges.pop(cl.username, None)
            for k, v in list(self.challenges.items()):
                if v == cl.username:
                    del self.challenges[k]
        if cl.game:
            if cl.spectator:
                try:
                    cl.game.spectators.remove(cl)
                except ValueError:
                    pass
            elif cl.game.running:
                g = cl.game
                other = 1 - cl.snake_id
                g.winner  = g.players[other].username
                g.reason  = f"{cl.username} disconnected"
                g.running = False
        try:
            cl.sock.close()
        except Exception:
            pass
        self._broadcast_lobby()

    # ── Game start & loop ──

    def _start_game(self, p1: ClientConn, p2: ClientConn):
        g = Game(p1, p2)
        p1.game = g;  p1.snake_id = 0
        p2.game = g;  p2.snake_id = 1
        with self._lock:
            self.active = g

        p1.send({
            'type': 'game_start', 'snake_id': 0,
            'your_name': p1.username, 'opponent': p2.username,
            'grid_w': GRID_W, 'grid_h': GRID_H,
        })
        p2.send({
            'type': 'game_start', 'snake_id': 1,
            'your_name': p2.username, 'opponent': p1.username,
            'grid_w': GRID_W, 'grid_h': GRID_H,
        })
        self._broadcast_lobby()

        g.running   = True
        g._last_pw  = time.time()
        threading.Thread(target=self._gameloop, args=(g,), daemon=True).start()

    def _gameloop(self, g: Game):
        print(f'  [GAME {g.gid}] {g.players[0].username} vs {g.players[1].username}')
        while g.running:
            t0 = time.time()
            if not g.tick():
                g.running = False
                break
            state = g.state_dict()
            for p in g.players:
                p.send(state)
            for s in g.spectators:
                s.send(state)
            elapsed = time.time() - t0
            sleep   = TICK - elapsed
            if sleep > 0:
                time.sleep(sleep)

        # Announce result
        end_msg = {
            'type'        : 'game_end',
            'winner'      : g.winner,
            'scores'      : [sn['health'] for sn in g.snakes],
            'player_names': [p.username for p in g.players],
            'reason'      : g.reason,
        }
        for p in g.players:
            p.send(end_msg)
            p.game = None;  p.snake_id = None
        for s in g.spectators:
            s.send(end_msg)
            s.game = None;  s.spectator = False
        g.spectators.clear()
        print(f'  [GAME {g.gid}] Over — winner: {g.winner}  ({g.reason})')
        self._broadcast_lobby()


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5555
    Server(port).run()
