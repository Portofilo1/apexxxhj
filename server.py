#!/usr/bin/env python3
"""
APEXX CASINO - Backend API
Запуск: python3 server.py
"""

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import sqlite3
import time
import os
import hashlib
import hmac
import json
from contextlib import contextmanager

app = Flask(__name__)
CORS(app)

DB_PATH = "apexx.db"
BOT_TOKEN = "8703016461:AAEhYXg6d_F4MWsXotnEJ2HSil3GRCcG33c"  # вставь токен

# ──────────────────── DB ────────────────────

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT DEFAULT '',
                first_name  TEXT DEFAULT '',
                balance     REAL DEFAULT 0.0,
                total_won   REAL DEFAULT 0.0,
                total_lost  REAL DEFAULT 0.0,
                ref_id      INTEGER DEFAULT NULL,
                ref_earned  REAL DEFAULT 0.0,
                language    TEXT DEFAULT 'ru',
                games_played INTEGER DEFAULT 0,
                created_at  INTEGER DEFAULT (strftime('%s','now'))
            );
            CREATE TABLE IF NOT EXISTS transactions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER,
                amount      REAL,
                tx_type     TEXT,
                description TEXT,
                created_at  INTEGER DEFAULT (strftime('%s','now'))
            );
            CREATE TABLE IF NOT EXISTS bets (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER,
                game        TEXT,
                bet_amount  REAL,
                multiplier  REAL,
                win_amount  REAL,
                result      TEXT,
                created_at  INTEGER DEFAULT (strftime('%s','now'))
            );
            CREATE TABLE IF NOT EXISTS withdrawals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER,
                amount      REAL,
                check_text  TEXT,
                status      TEXT DEFAULT 'pending',
                created_at  INTEGER DEFAULT (strftime('%s','now'))
            );
        """)

# ──────────────────── TELEGRAM AUTH ────────────────────

def verify_telegram_data(init_data: str) -> dict | None:
    """Проверяет подпись Telegram WebApp initData"""
    try:
        parsed = {}
        for part in init_data.split("&"):
            k, v = part.split("=", 1)
            parsed[k] = v
        
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None

        from urllib.parse import unquote
        data_check = "\n".join(
            f"{k}={unquote(v)}" for k, v in sorted(parsed.items())
        )

        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(expected, received_hash):
            return None

        user_str = parsed.get("user", "{}")
        return json.loads(unquote(user_str))
    except Exception:
        return None

def get_user_id_from_request() -> int | None:
    """Достаёт user_id из заголовка или init_data"""
    # Из заголовка X-User-Id (для простоты в Termux)
    uid = request.headers.get("X-User-Id")
    if uid:
        try:
            return int(uid)
        except:
            pass

    # Из Telegram initData
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if init_data:
        user = verify_telegram_data(init_data)
        if user:
            return user.get("id")
    
    # Из тела запроса
    data = request.get_json(silent=True) or {}
    uid = data.get("user_id")
    if uid:
        return int(uid)

    return None

# ──────────────────── HELPERS ────────────────────

def ensure_user(user_id: int, username: str = "", first_name: str = ""):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?,?,?)",
            (user_id, username, first_name)
        )

def get_user(user_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None

def get_refs_count(user_id: int) -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM users WHERE ref_id=?", (user_id,)).fetchone()[0]

# ──────────────────── API ROUTES ────────────────────

@app.route("/api/user", methods=["GET"])
def api_get_user():
    uid = get_user_id_from_request()
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    user = get_user(uid)
    if not user:
        return jsonify({"error": "user not found"}), 404

    refs = get_refs_count(uid)
    return jsonify({
        "user_id":      user["user_id"],
        "username":     user["username"],
        "first_name":   user["first_name"],
        "balance":      round(user["balance"], 2),
        "total_won":    round(user["total_won"], 2),
        "total_lost":   round(user["total_lost"], 2),
        "ref_earned":   round(user["ref_earned"], 2),
        "games_played": user["games_played"],
        "language":     user["language"],
        "refs_count":   refs,
    })

@app.route("/api/user/init", methods=["POST"])
def api_init_user():
    data = request.get_json(silent=True) or {}
    uid = data.get("user_id")
    if not uid:
        uid = get_user_id_from_request()
    if not uid:
        return jsonify({"error": "no user_id"}), 400

    uid = int(uid)
    username = data.get("username", "")
    first_name = data.get("first_name", "")
    ref_id = data.get("ref_id")

    ensure_user(uid, username, first_name)

    if ref_id and int(ref_id) != uid:
        with get_conn() as conn:
            conn.execute(
                "UPDATE users SET ref_id=? WHERE user_id=? AND ref_id IS NULL",
                (int(ref_id), uid)
            )

    user = get_user(uid)
    return jsonify({
        "ok": True,
        "balance": round(user["balance"], 2),
        "first_name": user["first_name"],
        "username": user["username"],
    })

@app.route("/api/balance", methods=["GET"])
def api_balance():
    uid = get_user_id_from_request()
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    user = get_user(uid)
    if not user:
        return jsonify({"error": "not found"}), 404
    return jsonify({"balance": round(user["balance"], 2)})

@app.route("/api/bet", methods=["POST"])
def api_bet():
    """Мини апп отправляет результат игры сюда"""
    uid = get_user_id_from_request()
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    game        = data.get("game", "unknown")
    bet_amount  = float(data.get("bet_amount", 0))
    multiplier  = float(data.get("multiplier", 0))
    win_amount  = float(data.get("win_amount", 0))
    result      = data.get("result", "lose")  # "win" | "lose"

    if bet_amount <= 0:
        return jsonify({"error": "invalid bet"}), 400

    user = get_user(uid)
    if not user:
        return jsonify({"error": "user not found"}), 404

    with get_conn() as conn:
        # Проверяем баланс
        if user["balance"] < bet_amount:
            return jsonify({"error": "insufficient funds", "balance": round(user["balance"], 2)}), 400

        # Списываем ставку
        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (bet_amount, uid))

        if result == "win" and win_amount > 0:
            conn.execute(
                "UPDATE users SET balance = balance + ?, total_won = total_won + ?, games_played = games_played + 1 WHERE user_id=?",
                (win_amount, win_amount - bet_amount, uid)
            )
        else:
            conn.execute(
                "UPDATE users SET total_lost = total_lost + ?, games_played = games_played + 1 WHERE user_id=?",
                (bet_amount, uid)
            )
            # Реферальная комиссия 18%
            if user["ref_id"]:
                commission = round(bet_amount * 0.18, 4)
                conn.execute(
                    "UPDATE users SET balance = balance + ?, ref_earned = ref_earned + ? WHERE user_id=?",
                    (commission, commission, user["ref_id"])
                )

        conn.execute(
            "INSERT INTO bets (user_id, game, bet_amount, multiplier, win_amount, result) VALUES (?,?,?,?,?,?)",
            (uid, game, bet_amount, multiplier, win_amount if result == "win" else 0, result)
        )
        conn.execute(
            "INSERT INTO transactions (user_id, amount, tx_type, description) VALUES (?,?,?,?)",
            (uid, bet_amount, "debit", f"bet_{game}")
        )

    updated = get_user(uid)
    return jsonify({
        "ok": True,
        "balance": round(updated["balance"], 2),
        "result": result,
    })

@app.route("/api/withdraw", methods=["POST"])
def api_withdraw():
    uid = get_user_id_from_request()
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    amount = float(data.get("amount", 0))

    if amount < 5:
        return jsonify({"error": "minimum withdrawal is $5"}), 400

    user = get_user(uid)
    if not user:
        return jsonify({"error": "user not found"}), 404
    if user["balance"] < amount:
        return jsonify({"error": "insufficient funds", "balance": round(user["balance"], 2)}), 400

    check_text = (
        f"🎰 APEXX CASINO\n\n"
        f"💰 Сумма: ${amount:.2f}\n"
        f"👤 ID: {uid}\n"
        f"📅 {time.strftime('%d.%m.%Y %H:%M')}\n\n"
        f"Забирай профит. Удачи в следующий раз! 🔥"
    )

    with get_conn() as conn:
        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (amount, uid))
        conn.execute(
            "INSERT INTO withdrawals (user_id, amount, check_text) VALUES (?,?,?)",
            (uid, amount, check_text)
        )
        conn.execute(
            "INSERT INTO transactions (user_id, amount, tx_type, description) VALUES (?,?,?,?)",
            (uid, amount, "debit", "withdrawal")
        )

    updated = get_user(uid)
    return jsonify({
        "ok": True,
        "balance": round(updated["balance"], 2),
        "check_text": check_text,
    })

@app.route("/api/deposit", methods=["POST"])
def api_deposit():
    """Только для тестов / админа"""
    uid = get_user_id_from_request()
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    amount = float(data.get("amount", 0))
    if amount <= 0:
        return jsonify({"error": "invalid amount"}), 400
    with get_conn() as conn:
        conn.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, uid))
        conn.execute(
            "INSERT INTO transactions (user_id, amount, tx_type, description) VALUES (?,?,?,?)",
            (uid, amount, "credit", "deposit")
        )
    updated = get_user(uid)
    return jsonify({"ok": True, "balance": round(updated["balance"], 2)})

@app.route("/api/language", methods=["POST"])
def api_language():
    uid = get_user_id_from_request()
    if not uid:
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    lang = data.get("language", "ru")
    if lang not in ("ru", "en"):
        return jsonify({"error": "invalid language"}), 400
    with get_conn() as conn:
        conn.execute("UPDATE users SET language=? WHERE user_id=?", (lang, uid))
    return jsonify({"ok": True})

# Отдаём мини апп
@app.route("/")
@app.route("/miniapp")
def serve_miniapp():
    path = os.path.join(os.path.dirname(__file__), "miniapp.html")
    if os.path.exists(path):
        return send_file(path)
    return "miniapp.html not found — положи его рядом с server.py", 404

@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": int(time.time())})

# ──────────────────── MAIN ────────────────────

if __name__ == "__main__":
    init_db()
    print("=" * 40)
    print("  APEXX CASINO — Backend API")
    print("=" * 40)
    print(f"  БД:      {os.path.abspath(DB_PATH)}")
    print(f"  Сервер:  http://0.0.0.0:8000")
    print(f"  Мини апп: http://localhost:8000/")
    print("=" * 40)
    import os as _os
    app.run(host="0.0.0.0", port=int(_os.environ.get("PORT", 8000)), debug=False)
