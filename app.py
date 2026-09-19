from flask import Flask, render_template, request, jsonify, session
from flask_sock import Sock
import sqlite3, random, time, os, hashlib, json
from datetime import datetime, timezone, date

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'data', 'game.db')
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'crack-the-code-local-v7-key')

sock = Sock(app)

# V7 multiplayer rooms live in memory. Secrets never leave the server.
MP_ROOMS = {}
MP_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = db()
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        last_seen TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS games (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        game_token TEXT NOT NULL UNIQUE,
        profile_id INTEGER NOT NULL,
        length INTEGER NOT NULL,
        mode TEXT NOT NULL,
        max_repeat INTEGER,
        secret TEXT NOT NULL,
        started_at REAL NOT NULL,
        finished_at REAL,
        guesses_used INTEGER DEFAULT 0,
        won INTEGER DEFAULT 0,
        time_taken REAL,
        difficulty TEXT DEFAULT 'normal',
        pressure INTEGER DEFAULT 0,
        time_limit INTEGER,
        attempt_limit INTEGER,
        daily INTEGER DEFAULT 0,
        counted INTEGER DEFAULT 1,
        created_at TEXT NOT NULL,
        FOREIGN KEY(profile_id) REFERENCES profiles(id)
    );
    CREATE TABLE IF NOT EXISTS guesses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        game_id INTEGER NOT NULL,
        guess TEXT NOT NULL,
        same_count INTEGER NOT NULL,
        correct_count INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(game_id) REFERENCES games(id)
    );
    ''')
    # Safe upgrades for older V4/V5 databases.
    columns = {r['name'] for r in conn.execute('PRAGMA table_info(games)').fetchall()}
    upgrades = {
        'profile_id': 'ALTER TABLE games ADD COLUMN profile_id INTEGER DEFAULT 1',
        'pressure': 'ALTER TABLE games ADD COLUMN pressure INTEGER DEFAULT 0',
        'time_limit': 'ALTER TABLE games ADD COLUMN time_limit INTEGER',
        'attempt_limit': 'ALTER TABLE games ADD COLUMN attempt_limit INTEGER',
        'daily': 'ALTER TABLE games ADD COLUMN daily INTEGER DEFAULT 0',
        'counted': 'ALTER TABLE games ADD COLUMN counted INTEGER DEFAULT 1',
        'difficulty': "ALTER TABLE games ADD COLUMN difficulty TEXT DEFAULT 'normal'",
    }
    for col, stmt in upgrades.items():
        if col not in columns:
            try: conn.execute(stmt)
            except sqlite3.OperationalError: pass
    row = conn.execute('SELECT id FROM profiles ORDER BY id LIMIT 1').fetchone()
    if not row:
        conn.execute('INSERT INTO profiles(name,created_at,last_seen) VALUES(?,?,?)', ('Player 1', now_iso(), now_iso()))
    conn.commit(); conn.close()


def current_profile_id():
    pid = session.get('profile_id')
    conn = db()
    row = conn.execute('SELECT id FROM profiles WHERE id=?', (pid,)).fetchone() if pid else None
    if not row:
        row = conn.execute('SELECT id FROM profiles ORDER BY id LIMIT 1').fetchone()
        session['profile_id'] = row['id']
    conn.execute('UPDATE profiles SET last_seen=? WHERE id=?', (now_iso(), session['profile_id']))
    conn.commit(); conn.close()
    return session['profile_id']


def generate_secret(length, mode, max_repeat=None, seed=None):
    rng = random.Random(seed) if seed is not None else random
    digits = '0123456789'
    if mode == 'unique':
        return ''.join(rng.sample(digits, length))
    limit = min(max_repeat if max_repeat else length, length)
    while True:
        secret = ''.join(rng.choice(digits) for _ in range(length))
        if max(secret.count(d) for d in set(secret)) <= limit:
            return secret


def valid_guess(guess, length, mode, max_repeat=None):
    if not isinstance(guess, str) or len(guess) != length or not guess.isdigit():
        return False, f'Enter exactly {length} digits.'
    if mode == 'unique' and len(set(guess)) != length:
        return False, 'No duplicate digits are allowed in this mode.'
    if mode == 'repeat' and max_repeat and max(guess.count(d) for d in set(guess)) > max_repeat:
        return False, f'No digit can appear more than {max_repeat} times.'
    return True, ''


def evaluate(secret, guess):
    correct = sum(a == b for a, b in zip(secret, guess))
    sc = {d: secret.count(d) for d in set(secret)}
    gc = {d: guess.count(d) for d in set(guess)}
    same = sum(min(sc.get(d, 0), c) for d, c in gc.items())
    return same, correct


def stats_for(conn, profile_id, length=None, mode=None, max_repeat=None, daily=None):
    clauses = ['profile_id=?', 'counted=1', 'finished_at IS NOT NULL']; params = [profile_id]
    if length is not None: clauses += ['length=?']; params += [length]
    if mode is not None: clauses += ['mode=?']; params += [mode]
    if mode == 'repeat' and max_repeat is not None: clauses += ['max_repeat=?']; params += [max_repeat]
    if daily is not None: clauses += ['daily=?']; params += [int(daily)]
    where = ' WHERE ' + ' AND '.join(clauses)
    total = conn.execute(f'SELECT COUNT(*) c FROM games{where}', params).fetchone()['c']
    wins = conn.execute(f'SELECT COUNT(*) c FROM games{where} AND won=1', params).fetchone()['c']
    best = conn.execute(f'SELECT MIN(guesses_used) v FROM games{where} AND won=1', params).fetchone()['v']
    worst = conn.execute(f'SELECT MAX(guesses_used) v FROM games{where} AND won=1', params).fetchone()['v']
    avg = conn.execute(f'SELECT AVG(guesses_used) v FROM games{where} AND won=1', params).fetchone()['v']
    fastest = conn.execute(f'SELECT MIN(time_taken) v FROM games{where} AND won=1', params).fetchone()['v']
    slowest = conn.execute(f'SELECT MAX(time_taken) v FROM games{where} AND won=1', params).fetchone()['v']
    return {'total': total, 'wins': wins, 'losses': total-wins, 'win_rate': round(wins/total*100,1) if total else 0,
            'best': best, 'worst': worst, 'average': round(avg,1) if avg is not None else None,
            'fastest': fastest, 'slowest': slowest}


def streaks(conn, profile_id):
    rows = conn.execute('SELECT won FROM games WHERE profile_id=? AND counted=1 AND finished_at IS NOT NULL ORDER BY id DESC', (profile_id,)).fetchall()
    current = 0
    for r in rows:
        if r['won']: current += 1
        else: break
    best = run = 0
    for r in reversed(rows):
        if r['won']:
            run += 1; best = max(best, run)
        else: run = 0
    return current, best


def get_game(token, profile_id):
    if not token: return None
    conn = db(); row = conn.execute('SELECT * FROM games WHERE game_token=? AND profile_id=?', (token, profile_id)).fetchone(); conn.close(); return row


def daily_secret(length=4, mode='unique', max_repeat=None):
    seed_text = f'crack-the-code-daily:{date.today().isoformat()}:{length}:{mode}:{max_repeat}'
    seed = int(hashlib.sha256(seed_text.encode()).hexdigest()[:16], 16)
    return generate_secret(length, mode, max_repeat, seed=seed)


@app.route('/')
def index():
    current_profile_id(); return render_template('index.html')


@app.get('/api/profile')
def profiles():
    pid = current_profile_id(); conn = db(); rows = conn.execute('SELECT id,name FROM profiles ORDER BY id').fetchall(); conn.close()
    return jsonify(profiles=[dict(r) for r in rows], active_id=pid)


@app.post('/api/profile/select')
def select_profile():
    data = request.get_json(silent=True) or {}
    try: pid = int(data.get('id'))
    except (TypeError, ValueError): return jsonify(error='Invalid profile.'), 400
    conn = db(); row = conn.execute('SELECT id FROM profiles WHERE id=?', (pid,)).fetchone()
    if not row: conn.close(); return jsonify(error='Profile not found.'), 404
    conn.execute('UPDATE profiles SET last_seen=? WHERE id=?', (now_iso(), pid)); conn.commit(); conn.close(); session['profile_id']=pid
    return jsonify(ok=True)


@app.post('/api/profile/create')
def create_profile():
    name = str((request.get_json(silent=True) or {}).get('name','')).strip()[:24]
    if not name: return jsonify(error='Enter a player name.'), 400
    conn = db()
    try:
        cur = conn.execute('INSERT INTO profiles(name,created_at,last_seen) VALUES(?,?,?)', (name,now_iso(),now_iso())); conn.commit(); pid=cur.lastrowid
    except sqlite3.IntegrityError:
        conn.close(); return jsonify(error='That player already exists.'), 409
    conn.close(); session['profile_id']=pid; return jsonify(ok=True,id=pid,name=name)


@app.post('/api/reset')
def reset_data():
    kind = str((request.get_json(silent=True) or {}).get('kind','history'))
    pid = current_profile_id(); conn=db()
    if kind == 'history':
        conn.execute('DELETE FROM guesses WHERE game_id IN (SELECT id FROM games WHERE profile_id=?)',(pid,)); conn.execute('DELETE FROM games WHERE profile_id=?',(pid,))
    elif kind == 'records':
        conn.execute('UPDATE games SET counted=0 WHERE profile_id=?',(pid,))
    elif kind == 'all':
        conn.execute('DELETE FROM guesses'); conn.execute('DELETE FROM games'); conn.execute('DELETE FROM profiles')
        conn.execute('INSERT INTO profiles(name,created_at,last_seen) VALUES(?,?,?)',('Player 1',now_iso(),now_iso())); session['profile_id']=conn.execute('SELECT id FROM profiles').fetchone()['id']
    else:
        conn.close(); return jsonify(error='Unknown reset action.'), 400
    conn.commit(); conn.close(); return jsonify(ok=True)


@app.post('/api/game/start')
def start_game():
    data=request.get_json(silent=True) or {}; pid=current_profile_id()
    try: length=int(data.get('length',4))
    except (TypeError,ValueError): length=4
    if length not in range(3,8): return jsonify(error='Code length must be between 3 and 7.'),400
    mode=data.get('mode','unique')
    if mode not in ('unique','repeat'): return jsonify(error='Invalid game mode.'),400
    max_repeat=None
    if mode=='repeat':
        try: max_repeat=int(data.get('max_repeat',length))
        except (TypeError,ValueError): max_repeat=length
        if max_repeat<2 or max_repeat>length: return jsonify(error='Invalid repetition limit.'),400
    difficulty=data.get('difficulty','normal')
    if difficulty not in ('relaxed','easy','normal','hard','extreme'): difficulty='normal'
    pressure=bool(data.get('pressure'))
    time_limit=None; attempt_limit=None
    if pressure:
        try: time_limit=int(data.get('time_limit')) if data.get('time_limit') not in (None,'') else None
        except (TypeError,ValueError): time_limit=None
        try: attempt_limit=int(data.get('attempt_limit')) if data.get('attempt_limit') not in (None,'') else None
        except (TypeError,ValueError): attempt_limit=None
        if time_limit is not None and not 15 <= time_limit <= 7200: return jsonify(error='Time limit must be between 15 seconds and 2 hours.'),400
        if attempt_limit is not None and not 1 <= attempt_limit <= 999: return jsonify(error='Attempt limit must be between 1 and 999.'),400
        if time_limit is None and attempt_limit is None: return jsonify(error='Set a time limit, an attempt limit, or both.'),400
    daily=bool(data.get('daily'))
    secret=daily_secret(length,mode,max_repeat) if daily else generate_secret(length,mode,max_repeat)
    token=os.urandom(16).hex(); started=time.time(); conn=db()
    cur=conn.execute('''INSERT INTO games(game_token,profile_id,length,mode,max_repeat,secret,started_at,difficulty,pressure,time_limit,attempt_limit,daily,counted,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(token,pid,length,mode,max_repeat,secret,started,difficulty,int(pressure),time_limit,attempt_limit,int(daily),1,now_iso()))
    conn.commit(); gid=cur.lastrowid; st=stats_for(conn,pid,length,mode,max_repeat); streak,beststreak=streaks(conn,pid); conn.close(); session['game_token']=token
    return jsonify(game_id=gid,token=token,length=length,mode=mode,max_repeat=max_repeat,difficulty=difficulty,pressure=pressure,time_limit=time_limit,attempt_limit=attempt_limit,daily=daily,started_at=started,config_stats=st,streak=streak,best_streak=beststreak)


@app.post('/api/game/guess')
def make_guess():
    data=request.get_json(silent=True) or {}; pid=current_profile_id(); game=get_game(session.get('game_token') or data.get('token'),pid)
    if not game: return jsonify(error='No active game.'),400
    if game['finished_at'] is not None: return jsonify(error='This game is already complete.'),400
    now=time.time(); elapsed=now-game['started_at']; used=game['guesses_used']
    if game['pressure'] and game['time_limit'] and elapsed >= game['time_limit']:
        conn=db(); conn.execute('UPDATE games SET finished_at=?,won=0,time_taken=? WHERE id=?',(now,elapsed,game['id'])); conn.commit(); conn.close()
        return jsonify(error='Time limit reached.',pressure_lost=True,elapsed=elapsed), 409
    if game['pressure'] and game['attempt_limit'] and used >= game['attempt_limit']:
        conn=db(); conn.execute('UPDATE games SET finished_at=?,won=0,time_taken=? WHERE id=?',(now,elapsed,game['id'])); conn.commit(); conn.close()
        return jsonify(error='Attempt limit reached.',pressure_lost=True,elapsed=elapsed), 409
    guess=str(data.get('guess','')).strip(); ok,error=valid_guess(guess,game['length'],game['mode'],game['max_repeat'])
    if not ok:return jsonify(error=error),400
    same,correct=evaluate(game['secret'],guess); used+=1; won=correct==game['length']
    if game['pressure'] and game['attempt_limit'] and used>=game['attempt_limit'] and not won:
        finished=now; taken=elapsed; pressure_lost=True
    else:
        finished=now if won else None; taken=elapsed if won else None; pressure_lost=False
    conn=db(); conn.execute('UPDATE games SET guesses_used=?,won=?,finished_at=?,time_taken=? WHERE id=?',(used,int(won),finished,taken,game['id']))
    conn.execute('INSERT INTO guesses(game_id,guess,same_count,correct_count,created_at) VALUES(?,?,?,?,?)',(game['id'],guess,same,correct,now_iso())); conn.commit()
    st=stats_for(conn,pid,game['length'],game['mode'],game['max_repeat']); streak,beststreak=streaks(conn,pid)
    rows=conn.execute('SELECT guess,same_count,correct_count FROM guesses WHERE game_id=? ORDER BY id',(game['id'],)).fetchall()
    unique_guesses=len({r['guess'] for r in rows}); repeated=len(rows)-unique_guesses; conn.close()
    out={'same':same,'correct':correct,'won':won,'guesses_used':used,'streak':streak,'best_streak':beststreak,'pressure_lost':pressure_lost}
    if won:
        out.update(secret=game['secret'],time_taken=taken,config_stats=st,new_best=(st['best']==used),analysis={'unique_guesses':unique_guesses,'repeated_guesses':repeated,'efficiency':round(min(100, 55+max(0,(game['length']*4-used)*4)),1)})
    elif pressure_lost:
        out.update(secret=game['secret'],time_taken=taken)
    return jsonify(out)


@app.post('/api/game/expire')
def expire_game():
    data=request.get_json(silent=True) or {}; pid=current_profile_id(); game=get_game(session.get('game_token') or data.get('token'),pid)
    if not game: return jsonify(error='No active game.'),400
    if game['finished_at'] is not None: return jsonify(ok=True,already_finished=True),200
    now=time.time(); elapsed=now-game['started_at']
    if not game['pressure'] or not game['time_limit'] or elapsed < game['time_limit']:
        return jsonify(error='Game is not expired.'),400
    conn=db(); conn.execute('UPDATE games SET finished_at=?,won=0,time_taken=? WHERE id=?',(now,elapsed,game['id'])); conn.commit(); conn.close()
    return jsonify(ok=True,pressure_lost=True,secret=game['secret'],elapsed=elapsed,guesses_used=game['guesses_used'])


@app.get('/api/game/history')
def history():
    pid=current_profile_id(); conn=db(); rows=conn.execute('SELECT id,length,mode,max_repeat,difficulty,pressure,time_limit,attempt_limit,daily,guesses_used,won,time_taken,created_at,counted FROM games WHERE profile_id=? AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 80',(pid,)).fetchall(); conn.close(); return jsonify(games=[dict(r) for r in rows])


@app.get('/api/stats')
def stats():
    pid=current_profile_id(); conn=db(); overall=stats_for(conn,pid); current,best=streaks(conn,pid)
    try:length=int(request.args.get('length')) if request.args.get('length') else None
    except ValueError:length=None
    mode=request.args.get('mode') or None
    try:max_repeat=int(request.args.get('max_repeat')) if request.args.get('max_repeat') else None
    except ValueError:max_repeat=None
    config=stats_for(conn,pid,length,mode,max_repeat) if length and mode else None; conn.close()
    return jsonify(**overall,current_streak=current,best_streak=best,config=config)


@app.get('/api/game/<int:game_id>/guesses')
def game_guesses(game_id):
    pid=current_profile_id(); conn=db(); row=conn.execute('SELECT id FROM games WHERE id=? AND profile_id=?',(game_id,pid)).fetchone()
    if not row: conn.close(); return jsonify(error='Game not found.'),404
    rows=conn.execute('SELECT guess,same_count,correct_count,created_at FROM guesses WHERE game_id=? ORDER BY id',(game_id,)).fetchall(); conn.close(); return jsonify(guesses=[dict(r) for r in rows])


init_db()
if __name__ == '__main__':
    app.run(host=os.environ.get('HOST','127.0.0.1'),port=int(os.environ.get('PORT','5000')),debug=False)


def mp_room_code():
    while True:
        code = ''.join(random.choice(MP_ALPHABET) for _ in range(6))
        if code not in MP_ROOMS:
            return code


def mp_valid_settings(s):
    s = s or {}
    try: length = int(s.get('length', 4))
    except (TypeError, ValueError): length = 4
    mode = s.get('mode', 'unique')
    try: max_repeat = int(s.get('max_repeat')) if s.get('max_repeat') not in (None, '', 'null') else None
    except (TypeError, ValueError): max_repeat = None
    difficulty = s.get('difficulty', 'normal')
    feedback_mode = s.get('feedback_mode', 'automatic')
    pressure = bool(s.get('pressure', False))
    try: time_limit = int(s.get('time_limit')) if s.get('time_limit') else None
    except (TypeError, ValueError): time_limit = None
    try: attempt_limit = int(s.get('attempt_limit')) if s.get('attempt_limit') else None
    except (TypeError, ValueError): attempt_limit = None
    if length not in range(3, 8): raise ValueError('Code length must be 3–7.')
    if mode not in ('unique', 'repeat'): raise ValueError('Invalid number style.')
    if mode == 'repeat' and (max_repeat is None or max_repeat < 2 or max_repeat > length):
        raise ValueError('Choose a valid repetition limit.')
    if mode == 'unique': max_repeat = None
    if difficulty not in ('relaxed', 'easy', 'normal', 'hard', 'extreme'):
        raise ValueError('Invalid difficulty.')
    if feedback_mode not in ('automatic', 'manual'):
        raise ValueError('Invalid feedback mode.')
    if pressure:
        if time_limit is not None and not 1 <= time_limit <= 120: raise ValueError('Time limit must be 1–120 minutes.')
        if attempt_limit is not None and not 1 <= attempt_limit <= 999: raise ValueError('Attempt limit must be 1–999.')
        if time_limit is None and attempt_limit is None: raise ValueError('Pressure mode needs a time or attempt limit.')
    else:
        time_limit = attempt_limit = None
    return {'length': length, 'mode': mode, 'max_repeat': max_repeat,
            'difficulty': difficulty, 'feedback_mode': feedback_mode,
            'pressure': pressure, 'time_limit': time_limit, 'attempt_limit': attempt_limit}


def mp_public(room):
    return {
        'code': room['code'],
        'host': room['host'],
        'players': [
            {'slot': 1, 'name': room['players'][1]['name'], 'ready': bool(room['players'][1]['secret'])},
            {'slot': 2, 'name': room['players'][2]['name'], 'ready': bool(room['players'][2]['secret'])},
        ],
        'settings': room['settings'],
        'status': room['status'],
        'turn': room['turn'],
        'winner': room.get('winner'),
        'history': room['history'],
        'pending': room.get('pending_feedback'),
        'created_at': room['created_at'],
    }


def mp_send(room, payload, slot=None):
    msg = json.dumps(payload)
    targets = [room['players'][slot]['ws']] if slot in (1, 2) else [room['players'][1]['ws'], room['players'][2]['ws']]
    for ws in targets:
        if ws is not None:
            try: ws.send(msg)
            except Exception: pass


def mp_broadcast_state(room):
    public = mp_public(room)
    for slot in (1, 2):
        ws = room['players'][slot]['ws']
        if ws is not None:
            payload = {'type': 'state', 'room': public, 'you': slot}
            try: ws.send(json.dumps(payload))
            except Exception: pass


def mp_error(ws, message):
    try: ws.send(json.dumps({'type': 'error', 'message': message}))
    except Exception: pass


def mp_start_if_ready(room):
    if room['players'][1]['secret'] and room['players'][2]['secret'] and room['status'] == 'lobby':
        room['status'] = 'playing'
        room['turn'] = 1
        room['started_at'] = time.time()
        room['history'] = []
        room['pending_feedback'] = None
        mp_broadcast_state(room)
        mp_send(room, {'type': 'turn', 'slot': 1, 'message': f"{room['players'][1]['name']}'s turn."})


def mp_submit_secret(room, slot, secret):
    settings = room['settings']
    ok, msg = valid_guess(secret, settings['length'], settings['mode'], settings['max_repeat'])
    if not ok: raise ValueError(msg)
    if room['status'] != 'lobby': raise ValueError('The game has already started.')
    room['players'][slot]['secret'] = secret


def mp_submit_guess(room, slot, guess):
    settings = room['settings']
    if room['status'] != 'playing': raise ValueError('The game is not accepting guesses.')
    if room['pending_feedback']: raise ValueError('Waiting for feedback on the current guess.')
    if room['turn'] != slot: raise ValueError('It is not your turn.')
    ok, msg = valid_guess(guess, settings['length'], settings['mode'], settings['max_repeat'])
    if not ok: raise ValueError(msg)
    target = 2 if slot == 1 else 1
    if settings['attempt_limit'] is not None:
        attempts = sum(1 for h in room['history'] if h['slot'] == slot)
        if attempts >= settings['attempt_limit']:
            raise ValueError('Your attempt limit has been reached.')
    if settings['feedback_mode'] == 'automatic':
        same, correct = evaluate(room['players'][target]['secret'], guess)
        entry = {'round': len(room['history']) + 1, 'slot': slot, 'name': room['players'][slot]['name'],
                 'guess': guess, 'same': same, 'correct': correct, 'pending': False}
        room['history'].append(entry)
        if correct == settings['length']:
            room['status'] = 'finished'; room['winner'] = slot; room['turn'] = None
            mp_broadcast_state(room)
        else:
            # Hold the result briefly so the turn changes in a deliberate, one-by-one rhythm.
            room['turn'] = None
            room['pending_feedback'] = {'hold': True, 'slot': slot, 'target': target, 'guess': guess, 'round': len(room['history'])}
            mp_broadcast_state(room)
            time.sleep(0.85)
            room['pending_feedback'] = None
            room['turn'] = target
            mp_broadcast_state(room)
    else:
        room['pending_feedback'] = {'slot': slot, 'target': target, 'guess': guess,
                                     'round': len(room['history']) + 1}
        mp_broadcast_state(room)


def mp_manual_feedback(room, owner_slot, same, correct):
    pending = room.get('pending_feedback')
    if not pending: raise ValueError('No feedback is waiting.')
    if pending['target'] != owner_slot: raise ValueError('Only the secret owner can submit this feedback.')
    length = room['settings']['length']
    try: same, correct = int(same), int(correct)
    except (TypeError, ValueError): raise ValueError('Feedback must be numeric.')
    if not (0 <= correct <= same <= length): raise ValueError('Feedback counts are invalid.')
    # Manual mode intentionally trusts the secret owner to verify the result.
    entry = {'round': pending['round'], 'slot': pending['slot'],
             'name': room['players'][pending['slot']]['name'], 'guess': pending['guess'],
             'same': same, 'correct': correct, 'pending': False}
    room['history'].append(entry)
    room['pending_feedback'] = None
    if correct == length:
        room['status'] = 'finished'; room['winner'] = pending['slot']; room['turn'] = None
    else:
        room['turn'] = owner_slot
    mp_broadcast_state(room)


@sock.route('/ws')
def multiplayer_socket(ws):
    room = None
    slot = None
    try:
        while True:
            raw = ws.receive()
            if raw is None:
                break
            try: data = json.loads(raw)
            except Exception:
                mp_error(ws, 'Invalid message.')
                continue
            typ = data.get('type')
            try:
                if typ == 'resume':
                    code = str(data.get('code','')).strip().upper()
                    cid = str(data.get('client_id') or '')
                    r = MP_ROOMS.get(code)
                    found = next((s for s in (1,2) if r and r['players'][s].get('client_id') == cid), None)
                    if not r or not found: raise ValueError('Room session expired. Join the room again.')
                    room = r; slot = found; room['players'][slot]['ws'] = ws
                    ws.send(json.dumps({'type':'joined','code':code,'slot':slot,'room':mp_public(room)}))
                    mp_broadcast_state(room)
                if typ == 'create':
                    if room is not None: raise ValueError('You are already in a room.')
                    settings = mp_valid_settings(data.get('settings'))
                    code = mp_room_code()
                    room = {'code': code, 'host': 1, 'status': 'lobby', 'turn': None, 'winner': None,
                            'settings': settings, 'history': [], 'pending_feedback': None,
                            'created_at': now_iso(), 'started_at': None,
                            'players': {1: {'name': str(data.get('name') or 'Player 1')[:24], 'secret': None, 'ws': ws, 'client_id': str(data.get('client_id') or '')},
                                        2: {'name': None, 'secret': None, 'ws': None, 'client_id': ''}}}
                    MP_ROOMS[code] = room; slot = 1
                    ws.send(json.dumps({'type':'joined','code':code,'slot':1,'room':mp_public(room)}))
                elif typ == 'join':
                    if room is not None: raise ValueError('You are already in a room.')
                    code = str(data.get('code','')).strip().upper()
                    r = MP_ROOMS.get(code)
                    if not r: raise ValueError('Room not found.')
                    if r['players'][2]['ws'] is not None: raise ValueError('Room is full.')
                    room = r; slot = 2; room['players'][2]['ws'] = ws
                    room['players'][2]['name'] = str(data.get('name') or 'Player 2')[:24]
                    room['players'][2]['client_id'] = str(data.get('client_id') or '')
                    ws.send(json.dumps({'type':'joined','code':code,'slot':2,'room':mp_public(room)}))
                    mp_broadcast_state(room)
                elif typ == 'settings':
                    if not room or slot != room['host']: raise ValueError('Only the host can change settings.')
                    if room['status'] != 'lobby': raise ValueError('Settings are locked after the game starts.')
                    room['settings'] = mp_valid_settings(data.get('settings'))
                    room['players'][1]['secret'] = room['players'][2]['secret'] = None
                    mp_broadcast_state(room)
                elif typ == 'secret':
                    if not room: raise ValueError('Join a room first.')
                    mp_submit_secret(room, slot, str(data.get('secret','')).strip())
                    mp_broadcast_state(room); mp_start_if_ready(room)
                elif typ == 'guess':
                    if not room: raise ValueError('Join a room first.')
                    mp_submit_guess(room, slot, str(data.get('guess','')).strip())
                elif typ == 'feedback':
                    if not room: raise ValueError('Join a room first.')
                    mp_manual_feedback(room, slot, data.get('same'), data.get('correct'))
                elif typ == 'leave':
                    break
                elif typ == 'ping':
                    ws.send(json.dumps({'type':'pong'}))
                else:
                    mp_error(ws, 'Unknown multiplayer action.')
            except ValueError as e:
                mp_error(ws, str(e))
            except Exception as e:
                mp_error(ws, 'Server error. Please try again.')
    finally:
        if room and slot:
            if room['players'][slot]['ws'] is ws:
                room['players'][slot]['ws'] = None
            # Keep room state for reconnect; remove only if both slots have disconnected.
            if room['players'][1]['ws'] is None and room['players'][2]['ws'] is None:
                MP_ROOMS.pop(room['code'], None)
            else:
                mp_broadcast_state(room)
