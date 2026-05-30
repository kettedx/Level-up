import os, sqlite3, json, uuid, datetime
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from functools import wraps
from flask import Flask, request, jsonify, g
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_cors import CORS
import bcrypt
import jwt

app = Flask(__name__)
_secret = os.environ.get('SECRET_KEY')
if not _secret:
    raise RuntimeError("SECRET_KEY não definida! Configure a variável de ambiente.")
app.config['SECRET_KEY'] = _secret
_allowed_origins = os.environ.get('ALLOWED_ORIGINS', '*').split(',')
CORS(app, origins=_allowed_origins)

# Serve frontend static files
@app.route('/')
def serve_index():
    from flask import send_file
    return send_file('index.html')

@app.route('/src/<path:path>')
def serve_static(path):
    from flask import send_from_directory
    return send_from_directory('src', path)
socketio = SocketIO(app, cors_allowed_origins=_allowed_origins, async_mode='threading')

DB_PATH = os.environ.get('DB_PATH', 'levelup.db')
# Garante que o diretório do DB existe
_db_dir = os.path.dirname(DB_PATH)
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db

def init_db():
    db = get_db()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        avatar TEXT DEFAULT 'p-masc1',
        title TEXT DEFAULT 'DEV INICIANTE',
        quote TEXT DEFAULT 'Disciplina hoje, liberdade amanhã.',
        level INTEGER DEFAULT 1,
        xp INTEGER DEFAULT 0,
        coins INTEGER DEFAULT 100,
        streak INTEGER DEFAULT 0,
        last_activity TEXT,
        is_online INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        profile_public INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS missions (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT,
        category TEXT DEFAULT 'principal',
        xp_reward INTEGER DEFAULT 100,
        progress INTEGER DEFAULT 0,
        completed INTEGER DEFAULT 0,
        completed_at TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        title TEXT NOT NULL,
        category TEXT DEFAULT 'outros',
        xp_reward INTEGER DEFAULT 10,
        done INTEGER DEFAULT 0,
        done_at TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS attributes (
        user_id TEXT PRIMARY KEY,
        foco INTEGER DEFAULT 10,
        energia INTEGER DEFAULT 10,
        disciplina INTEGER DEFAULT 10,
        criatividade INTEGER DEFAULT 10,
        saude INTEGER DEFAULT 10,
        dinheiro INTEGER DEFAULT 10,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS activity_log (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        action TEXT NOT NULL,
        xp_gained INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS friends (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        friend_id TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(user_id, friend_id),
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY(friend_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        room_id TEXT NOT NULL,
        sender_id TEXT NOT NULL,
        content TEXT NOT NULL,
        msg_type TEXT DEFAULT 'text',
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(sender_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS shop_items (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        price INTEGER NOT NULL,
        img_key TEXT,
        description TEXT
    );

    CREATE TABLE IF NOT EXISTS user_inventory (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        equipped INTEGER DEFAULT 0,
        acquired_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY(item_id) REFERENCES shop_items(id)
    );

    CREATE TABLE IF NOT EXISTS streak_log (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        date TEXT NOT NULL,
        UNIQUE(user_id, date),
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    """)

    
    items = [
        ('item_001','Gato Hacker','avatar',200,'p-masc1','Avatar especial pixel art'),
        ('item_002','Dev Ninja','avatar',150,'p-masc2','Avatar ninja coder'),
        ('item_003','Cyber Girl','avatar',200,'p-fem1','Avatar cyber girl'),
        ('item_004','Shadow Lady','avatar',250,'p-fem2','Avatar shadow lady'),
        ('item_005','Espada Pixel','item',300,'sword','Arma lendária do dev'),
        ('item_006','Aura Rosa','effect',300,'aura','Efeito visual épico'),
        ('item_007','Fundo Neon','theme',200,'fundo','Tema fundo neon city'),
        ('item_008','Tema Matrix','theme',300,'matrix','Tema matrix hacker'),
    ]
    for item in items:
        db.execute(
            "INSERT OR IGNORE INTO shop_items (id,name,category,price,img_key,description) VALUES (?,?,?,?,?,?)",
            item
        )
    db.commit()
    db.close()
    print("✅ Database initialized")


def make_token(user_id):
    payload = {
        'user_id': user_id,
        'exp': datetime.datetime.utcnow() + datetime.timedelta(days=30)
    }
    return jwt.encode(payload, app.config['SECRET_KEY'], algorithm='HS256')

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not token:
            return jsonify({'error': 'Token necessário'}), 401
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            g.user_id = data['user_id']
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token expirado'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Token inválido'}), 401
        return f(*args, **kwargs)
    return decorated

def xp_for_level(level):
    return level * 500

def calc_level(xp):
    level = 1
    total = 0
    while True:
        needed = xp_for_level(level)
        if total + needed > xp:
            return level, xp - total, needed
        total += needed
        level += 1

def add_xp(db, user_id, amount, action):
    db.execute("UPDATE users SET xp = xp + ? WHERE id = ?", (amount, user_id))
    db.execute(
        "INSERT INTO activity_log (id,user_id,action,xp_gained) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, action, amount)
    )
    
    attr_map = {
        'study': 'foco', 'exercise': 'saude', 'project': 'criatividade',
        'discipline': 'disciplina', 'task': 'energia'
    }
    for key, attr in attr_map.items():
        if key in action.lower():
            db.execute(f"UPDATE attributes SET {attr} = MIN(100, {attr} + 2) WHERE user_id = ?", (user_id,))
            break


@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username', '').strip()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    avatar = data.get('avatar', 'p-masc1')

    if not username or not email or not password:
        return jsonify({'error': 'Preencha todos os campos'}), 400
    if len(username) < 3:
        return jsonify({'error': 'Username muito curto'}), 400
    if len(password) < 6:
        return jsonify({'error': 'Senha muito curta'}), 400

    db = get_db()
    try:
        user_id = str(uuid.uuid4())
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        db.execute(
            "INSERT INTO users (id,username,email,password_hash,avatar) VALUES (?,?,?,?,?)",
            (user_id, username, email, pw_hash, avatar)
        )
        db.execute(
            "INSERT INTO attributes (user_id) VALUES (?)", (user_id,)
        )
        
        default_missions = [
            (str(uuid.uuid4()), user_id, 'Criar e lançar meu portfólio', 'Mostre seu trabalho para o mundo.', 'principal', 200),
            (str(uuid.uuid4()), user_id, 'Aprender React', 'Domine os fundamentos do React.', 'principal', 150),
            (str(uuid.uuid4()), user_id, 'Fazer 50 commits', 'Constância é tudo!', 'principal', 100),
        ]
        for m in default_missions:
            db.execute("INSERT INTO missions (id,user_id,title,description,category,xp_reward) VALUES (?,?,?,?,?,?)", m)
        
        default_tasks = [
            (str(uuid.uuid4()), user_id, 'Estudar JavaScript por 30 min', 'estudos', 10),
            (str(uuid.uuid4()), user_id, 'Beber 2L de água', 'saude', 5),
            (str(uuid.uuid4()), user_id, 'Praticar inglês por 20 min', 'estudos', 10),
        ]
        for t in default_tasks:
            db.execute("INSERT INTO tasks (id,user_id,title,category,xp_reward) VALUES (?,?,?,?,?)", t)
        db.commit()
        token = make_token(user_id)
        return jsonify({'token': token, 'user_id': user_id, 'username': username}), 201
    except sqlite3.IntegrityError as e:
        if 'username' in str(e):
            return jsonify({'error': 'Username já em uso'}), 409
        return jsonify({'error': 'Email já cadastrado'}), 409
    finally:
        db.close()

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    identifier = data.get('identifier', '').strip()
    password = data.get('password', '')

    db = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE username=? OR email=?", (identifier, identifier.lower())
    ).fetchone()
    db.close()

    if not user or not bcrypt.checkpw(password.encode(), user['password_hash'].encode()):
        return jsonify({'error': 'Credenciais inválidas'}), 401

    token = make_token(user['id'])
    return jsonify({'token': token, 'user_id': user['id'], 'username': user['username']})


@app.route('/api/me', methods=['GET'])
@require_auth
def get_me():
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (g.user_id,)).fetchone()
    attrs = db.execute("SELECT * FROM attributes WHERE user_id=?", (g.user_id,)).fetchone()
    recent = db.execute(
        "SELECT * FROM activity_log WHERE user_id=? ORDER BY created_at DESC LIMIT 5",
        (g.user_id,)
    ).fetchall()
    db.close()

    if not user:
        return jsonify({'error': 'Usuário não encontrado'}), 404

    level, xp_in_level, xp_needed = calc_level(user['xp'])
    return jsonify({
        'id': user['id'],
        'username': user['username'],
        'email': user['email'],
        'avatar': user['avatar'],
        'title': user['title'],
        'quote': user['quote'],
        'level': level,
        'xp': user['xp'],
        'xp_in_level': xp_in_level,
        'xp_needed': xp_needed,
        'coins': user['coins'],
        'streak': user['streak'],
        'created_at': user['created_at'],
        'profile_public': user['profile_public'],
        'attributes': dict(attrs) if attrs else {},
        'recent_activity': [dict(r) for r in recent],
    })

@app.route('/api/me', methods=['PUT'])
@require_auth
def update_me():
    data = request.json
    allowed = ['avatar', 'title', 'quote', 'profile_public']
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({'error': 'Nada para atualizar'}), 400
    db = get_db()
    sets = ', '.join(f"{k}=?" for k in updates)
    db.execute(f"UPDATE users SET {sets} WHERE id=?", (*updates.values(), g.user_id))
    db.commit()
    db.close()
    return jsonify({'ok': True})

@app.route('/api/users/<username>', methods=['GET'])
@require_auth
def get_profile(username):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not user:
        return jsonify({'error': 'Usuário não encontrado'}), 404
    if not user['profile_public'] and user['id'] != g.user_id:
        return jsonify({'error': 'Perfil privado'}), 403
    attrs = db.execute("SELECT * FROM attributes WHERE user_id=?", (user['id'],)).fetchone()
    missions_done = db.execute("SELECT COUNT(*) as c FROM missions WHERE user_id=? AND completed=1", (user['id'],)).fetchone()['c']
    tasks_done = db.execute("SELECT COUNT(*) as c FROM tasks WHERE user_id=? AND done=1", (user['id'],)).fetchone()['c']

    
    friendship = db.execute(
        "SELECT * FROM friends WHERE (user_id=? AND friend_id=?) OR (user_id=? AND friend_id=?)",
        (g.user_id, user['id'], user['id'], g.user_id)
    ).fetchone()
    db.close()

    level, xp_in_level, xp_needed = calc_level(user['xp'])
    return jsonify({
        'id': user['id'],
        'username': user['username'],
        'avatar': user['avatar'],
        'title': user['title'],
        'quote': user['quote'],
        'level': level,
        'xp': user['xp'],
        'xp_in_level': xp_in_level,
        'xp_needed': xp_needed,
        'streak': user['streak'],
        'created_at': user['created_at'],
        'attributes': dict(attrs) if attrs else {},
        'missions_completed': missions_done,
        'tasks_completed': tasks_done,
        'friendship_status': friendship['status'] if friendship else None,
        'friendship_initiator': friendship['user_id'] if friendship else None,
    })


@app.route('/api/missions', methods=['GET'])
@require_auth
def get_missions():
    category = request.args.get('category', None)
    db = get_db()
    if category:
        rows = db.execute("SELECT * FROM missions WHERE user_id=? AND category=? ORDER BY created_at DESC", (g.user_id, category)).fetchall()
    else:
        rows = db.execute("SELECT * FROM missions WHERE user_id=? ORDER BY created_at DESC", (g.user_id,)).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/missions', methods=['POST'])
@require_auth
def create_mission():
    data = request.json
    if not data.get('title'):
        return jsonify({'error': 'Título obrigatório'}), 400
    mission_id = str(uuid.uuid4())
    db = get_db()
    db.execute(
        "INSERT INTO missions (id,user_id,title,description,category,xp_reward) VALUES (?,?,?,?,?,?)",
        (mission_id, g.user_id, data['title'], data.get('description',''), data.get('category','principal'), data.get('xp_reward',100))
    )
    db.commit()
    mission = db.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone()
    db.close()
    return jsonify(dict(mission)), 201

@app.route('/api/missions/<mission_id>/progress', methods=['PUT'])
@require_auth
def update_mission_progress(mission_id):
    data = request.json
    progress = min(100, max(0, int(data.get('progress', 0))))
    db = get_db()
    mission = db.execute("SELECT * FROM missions WHERE id=? AND user_id=?", (mission_id, g.user_id)).fetchone()
    if not mission:
        db.close()
        return jsonify({'error': 'Missão não encontrada'}), 404

    completed = progress >= 100
    completed_at = datetime.datetime.utcnow().isoformat() if completed else None
    xp_gained = 0

    if completed and not mission['completed']:
        xp_gained = mission['xp_reward']
        add_xp(db, g.user_id, xp_gained, f'study:mission:{mission["title"]}')
        db.execute("UPDATE users SET coins = coins + ? WHERE id=?", (xp_gained // 10, g.user_id))

    db.execute(
        "UPDATE missions SET progress=?, completed=?, completed_at=? WHERE id=?",
        (progress, 1 if completed else 0, completed_at, mission_id)
    )
    db.commit()
    db.close()
    return jsonify({'progress': progress, 'completed': completed, 'xp_gained': xp_gained})

@app.route('/api/missions/<mission_id>', methods=['DELETE'])
@require_auth
def delete_mission(mission_id):
    db = get_db()
    db.execute("DELETE FROM missions WHERE id=? AND user_id=?", (mission_id, g.user_id))
    db.commit()
    db.close()
    return jsonify({'ok': True})


@app.route('/api/tasks', methods=['GET'])
@require_auth
def get_tasks():
    category = request.args.get('category', None)
    db = get_db()
    if category and category != 'todas':
        rows = db.execute("SELECT * FROM tasks WHERE user_id=? AND category=? ORDER BY created_at DESC", (g.user_id, category)).fetchall()
    else:
        rows = db.execute("SELECT * FROM tasks WHERE user_id=? ORDER BY created_at DESC", (g.user_id,)).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/tasks', methods=['POST'])
@require_auth
def create_task():
    data = request.json
    if not data.get('title'):
        return jsonify({'error': 'Título obrigatório'}), 400
    task_id = str(uuid.uuid4())
    db = get_db()
    db.execute(
        "INSERT INTO tasks (id,user_id,title,category,xp_reward) VALUES (?,?,?,?,?)",
        (task_id, g.user_id, data['title'], data.get('category','outros'), data.get('xp_reward',10))
    )
    db.commit()
    task = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    db.close()
    return jsonify(dict(task)), 201

@app.route('/api/tasks/<task_id>/toggle', methods=['PUT'])
@require_auth
def toggle_task(task_id):
    db = get_db()
    task = db.execute("SELECT * FROM tasks WHERE id=? AND user_id=?", (task_id, g.user_id)).fetchone()
    if not task:
        db.close()
        return jsonify({'error': 'Tarefa não encontrada'}), 404

    new_done = 0 if task['done'] else 1
    xp_gained = 0
    done_at = datetime.datetime.utcnow().isoformat() if new_done else None

    if new_done:
        xp_gained = task['xp_reward']
        add_xp(db, g.user_id, xp_gained, f'task:{task["category"]}:{task["title"]}')
        
        today = datetime.date.today().isoformat()
        db.execute("INSERT OR IGNORE INTO streak_log (id,user_id,date) VALUES (?,?,?)",
                   (str(uuid.uuid4()), g.user_id, today))
        
        streak = _calc_streak(db, g.user_id)
        db.execute("UPDATE users SET streak=? WHERE id=?", (streak, g.user_id))

    db.execute("UPDATE tasks SET done=?, done_at=? WHERE id=?", (new_done, done_at, task_id))
    db.commit()
    db.close()
    return jsonify({'done': bool(new_done), 'xp_gained': xp_gained})

def _calc_streak(db, user_id):
    rows = db.execute(
        "SELECT date FROM streak_log WHERE user_id=? ORDER BY date DESC", (user_id,)
    ).fetchall()
    if not rows:
        return 0
    streak = 1
    dates = [datetime.date.fromisoformat(r['date']) for r in rows]
    for i in range(1, len(dates)):
        if (dates[i-1] - dates[i]).days == 1:
            streak += 1
        else:
            break
    return streak

@app.route('/api/tasks/<task_id>', methods=['DELETE'])
@require_auth
def delete_task(task_id):
    db = get_db()
    db.execute("DELETE FROM tasks WHERE id=? AND user_id=?", (task_id, g.user_id))
    db.commit()
    db.close()
    return jsonify({'ok': True})


@app.route('/api/ranking', methods=['GET'])
@require_auth
def get_ranking():
    scope = request.args.get('scope', 'global')
    db = get_db()

    if scope == 'friends':
        rows = db.execute("""
            SELECT u.id, u.username, u.avatar, u.xp, u.level, u.streak
            FROM users u
            JOIN friends f ON (f.friend_id=u.id AND f.user_id=? AND f.status='accepted')
                           OR (f.user_id=u.id AND f.friend_id=? AND f.status='accepted')
            ORDER BY u.xp DESC LIMIT 20
        """, (g.user_id, g.user_id)).fetchall()
    elif scope == 'week':
        week_ago = (datetime.datetime.utcnow() - datetime.timedelta(days=7)).isoformat()
        rows = db.execute("""
            SELECT u.id, u.username, u.avatar, SUM(al.xp_gained) as xp, u.level, u.streak
            FROM users u
            JOIN activity_log al ON al.user_id=u.id AND al.created_at > ?
            GROUP BY u.id ORDER BY xp DESC LIMIT 20
        """, (week_ago,)).fetchall()
    else:
        rows = db.execute("""
            SELECT id, username, avatar, xp, level, streak
            FROM users ORDER BY xp DESC LIMIT 20
        """).fetchall()

    result = []
    for i, r in enumerate(rows):
        d = dict(r)
        level, xp_in_level, xp_needed = calc_level(d.get('xp', 0))
        d['level'] = level
        d['position'] = i + 1
        d['is_me'] = d['id'] == g.user_id
        result.append(d)
    db.close()
    return jsonify(result)


@app.route('/api/friends', methods=['GET'])
@require_auth
def get_friends():
    db = get_db()
    rows = db.execute("""
        SELECT u.id, u.username, u.avatar, u.level, u.xp, u.streak, u.is_online, f.status, f.user_id as initiator
        FROM friends f
        JOIN users u ON (f.friend_id=u.id AND f.user_id=?)
                     OR (f.user_id=u.id AND f.friend_id=?)
        WHERE f.status IN ('accepted','pending')
        ORDER BY f.status DESC, u.username
    """, (g.user_id, g.user_id)).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/friends/request', methods=['POST'])
@require_auth
def send_friend_request():
    data = request.json
    username = data.get('username', '').strip()
    db = get_db()
    target = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if not target:
        db.close()
        return jsonify({'error': 'Usuário não encontrado'}), 404
    if target['id'] == g.user_id:
        db.close()
        return jsonify({'error': 'Você não pode adicionar a si mesmo'}), 400
    try:
        db.execute(
            "INSERT INTO friends (id,user_id,friend_id,status) VALUES (?,?,?,'pending')",
            (str(uuid.uuid4()), g.user_id, target['id'])
        )
        db.commit()
        
        socketio.emit('friend_request', {'from': g.user_id}, room=f'user_{target["id"]}')
    except sqlite3.IntegrityError:
        db.close()
        return jsonify({'error': 'Solicitação já enviada'}), 409
    db.close()
    return jsonify({'ok': True})

@app.route('/api/friends/respond', methods=['POST'])
@require_auth
def respond_friend_request():
    data = request.json
    requester_id = data.get('user_id')
    action = data.get('action')  

    db = get_db()
    if action == 'accept':
        db.execute(
            "UPDATE friends SET status='accepted' WHERE user_id=? AND friend_id=?",
            (requester_id, g.user_id)
        )
        socketio.emit('friend_accepted', {'by': g.user_id}, room=f'user_{requester_id}')
    else:
        db.execute(
            "DELETE FROM friends WHERE user_id=? AND friend_id=?",
            (requester_id, g.user_id)
        )
    db.commit()
    db.close()
    return jsonify({'ok': True})


@app.route('/api/chat/history/<room_id>', methods=['GET'])
@require_auth
def chat_history(room_id):
    
    parts = room_id.split('_')
    if g.user_id not in parts and room_id != 'global':
        return jsonify({'error': 'Acesso negado'}), 403
    db = get_db()
    msgs = db.execute("""
        SELECT m.*, u.username, u.avatar
        FROM messages m JOIN users u ON m.sender_id=u.id
        WHERE m.room_id=? ORDER BY m.created_at ASC LIMIT 100
    """, (room_id,)).fetchall()
    db.close()
    return jsonify([dict(m) for m in msgs])


@app.route('/api/analytics', methods=['GET'])
@require_auth
def get_analytics():
    period = request.args.get('period', 'week')
    db = get_db()

    if period == 'week':
        since = (datetime.datetime.utcnow() - datetime.timedelta(days=7)).isoformat()
    else:
        since = (datetime.datetime.utcnow() - datetime.timedelta(days=30)).isoformat()

    xp_total = db.execute(
        "SELECT COALESCE(SUM(xp_gained),0) as total FROM activity_log WHERE user_id=? AND created_at>?",
        (g.user_id, since)
    ).fetchone()['total']

    tasks_done = db.execute(
        "SELECT COUNT(*) as c FROM tasks WHERE user_id=? AND done=1 AND done_at>?",
        (g.user_id, since)
    ).fetchone()['c']

    
    xp_by_day = db.execute("""
        SELECT DATE(created_at) as day, SUM(xp_gained) as xp
        FROM activity_log WHERE user_id=? AND created_at>?
        GROUP BY day ORDER BY day
    """, (g.user_id, since)).fetchall()

    
    by_category = db.execute("""
        SELECT
            CASE
                WHEN action LIKE '%study%' OR action LIKE '%estudos%' THEN 'Estudos'
                WHEN action LIKE '%project%' OR action LIKE '%projeto%' THEN 'Projetos'
                WHEN action LIKE '%saude%' OR action LIKE '%exercise%' THEN 'Saúde'
                ELSE 'Outros'
            END as cat,
            SUM(xp_gained) as xp
        FROM activity_log WHERE user_id=? AND created_at>?
        GROUP BY cat ORDER BY xp DESC
    """, (g.user_id, since)).fetchall()

    productive_time = tasks_done * 30  

    db.close()
    return jsonify({
        'xp_total': xp_total,
        'tasks_done': tasks_done,
        'xp_by_day': [dict(r) for r in xp_by_day],
        'by_category': [dict(r) for r in by_category],
        'productive_minutes': productive_time,
    })


@app.route('/api/shop', methods=['GET'])
@require_auth
def get_shop():
    db = get_db()
    items = db.execute("SELECT * FROM shop_items ORDER BY category, price").fetchall()
    inventory = db.execute("SELECT item_id FROM user_inventory WHERE user_id=?", (g.user_id,)).fetchall()
    owned = {r['item_id'] for r in inventory}
    db.close()
    result = []
    for item in items:
        d = dict(item)
        d['owned'] = d['id'] in owned
        result.append(d)
    return jsonify(result)

@app.route('/api/shop/buy/<item_id>', methods=['POST'])
@require_auth
def buy_item(item_id):
    db = get_db()
    item = db.execute("SELECT * FROM shop_items WHERE id=?", (item_id,)).fetchone()
    if not item:
        db.close()
        return jsonify({'error': 'Item não encontrado'}), 404
    user = db.execute("SELECT coins FROM users WHERE id=?", (g.user_id,)).fetchone()
    owned = db.execute("SELECT id FROM user_inventory WHERE user_id=? AND item_id=?", (g.user_id, item_id)).fetchone()
    if owned:
        db.close()
        return jsonify({'error': 'Você já possui este item'}), 409
    if user['coins'] < item['price']:
        db.close()
        return jsonify({'error': 'Moedas insuficientes'}), 402
    db.execute("UPDATE users SET coins=coins-? WHERE id=?", (item['price'], g.user_id))
    db.execute("INSERT INTO user_inventory (id,user_id,item_id) VALUES (?,?,?)",
               (str(uuid.uuid4()), g.user_id, item_id))
    db.commit()
    db.close()
    return jsonify({'ok': True, 'coins_spent': item['price']})


@app.route('/api/share/<username>', methods=['GET'])
def public_profile(username):
    """Public share page - no auth needed"""
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username=? AND profile_public=1", (username,)).fetchone()
    if not user:
        db.close()
        return jsonify({'error': 'Perfil não encontrado ou privado'}), 404
    attrs = db.execute("SELECT * FROM attributes WHERE user_id=?", (user['id'],)).fetchone()
    missions = db.execute(
        "SELECT * FROM missions WHERE user_id=? AND completed=1 ORDER BY completed_at DESC LIMIT 5",
        (user['id'],)
    ).fetchall()
    level, xp_in_level, xp_needed = calc_level(user['xp'])
    db.close()
    return jsonify({
        'username': user['username'],
        'avatar': user['avatar'],
        'title': user['title'],
        'quote': user['quote'],
        'level': level,
        'xp': user['xp'],
        'xp_in_level': xp_in_level,
        'xp_needed': xp_needed,
        'streak': user['streak'],
        'attributes': dict(attrs) if attrs else {},
        'recent_missions': [dict(m) for m in missions],
    })


connected_users = {}  

@socketio.on('connect')
def on_connect():
    token = request.args.get('token', '')
    try:
        data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
        user_id = data['user_id']
        connected_users[request.sid] = user_id
        join_room(f'user_{user_id}')
        join_room('global')
        db = get_db()
        db.execute("UPDATE users SET is_online=1 WHERE id=?", (user_id,))
        user = db.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
        db.commit()
        db.close()
        emit('status_update', {'user_id': user_id, 'online': True}, broadcast=True)
        print(f'✅ {user["username"]} connected')
    except Exception as e:
        print(f'❌ Socket connect error: {e}')

@socketio.on('disconnect')
def on_disconnect():
    user_id = connected_users.pop(request.sid, None)
    if user_id:
        db = get_db()
        db.execute("UPDATE users SET is_online=0 WHERE id=?", (user_id,))
        db.commit()
        db.close()
        emit('status_update', {'user_id': user_id, 'online': False}, broadcast=True)

@socketio.on('send_message')
def on_message(data):
    user_id = connected_users.get(request.sid)
    if not user_id:
        return
    room_id = data.get('room_id', 'global')
    content = data.get('content', '').strip()
    if not content or len(content) > 500:
        return

    
    if room_id != 'global':
        parts = room_id.split('_')
        if user_id not in parts:
            return

    db = get_db()
    msg_id = str(uuid.uuid4())
    user = db.execute("SELECT username, avatar FROM users WHERE id=?", (user_id,)).fetchone()
    db.execute(
        "INSERT INTO messages (id,room_id,sender_id,content) VALUES (?,?,?,?)",
        (msg_id, room_id, user_id, content)
    )
    db.commit()
    db.close()

    message = {
        'id': msg_id,
        'room_id': room_id,
        'sender_id': user_id,
        'username': user['username'],
        'avatar': user['avatar'],
        'content': content,
        'created_at': datetime.datetime.utcnow().isoformat(),
    }
    emit('new_message', message, room=room_id)

@socketio.on('join_dm')
def on_join_dm(data):
    user_id = connected_users.get(request.sid)
    if not user_id:
        return
    friend_id = data.get('friend_id')
    room_id = '_'.join(sorted([user_id, friend_id]))
    join_room(room_id)
    emit('joined_room', {'room_id': room_id})

@socketio.on('typing')
def on_typing(data):
    user_id = connected_users.get(request.sid)
    if not user_id:
        return
    room_id = data.get('room_id', 'global')
    emit('user_typing', {'user_id': user_id}, room=room_id, include_self=False)


if __name__ == '__main__':
    init_db()
    print("🎮 LevelUp Dev Mode - Backend iniciado!")
    print("🌐 http://localhost:5000")
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    socketio.run(app, host='0.0.0.0', port=port, debug=debug)
