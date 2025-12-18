# -*- coding: utf-8 -*-
from functools import wraps
from datetime import datetime, timedelta

from flask import (
    Flask, render_template, redirect, url_for,
    request, session, flash, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, timedelta
import random

app = Flask(__name__)
app.config["SECRET_KEY"] = "super-secret-change-me"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///botpanel.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


# ====== МОДЕЛИ ======
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

    ranges = db.relationship("NumberRange", backref="user", lazy=True)


class NumberRange(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    start = db.Column(db.Integer, nullable=False)
    end = db.Column(db.Integer, nullable=False)

    # какая пятёрка (левая/правая) играет на победу в текущей игре
    win_group = db.Column(db.String(8), default="right")  # "left" / "right"
    # сколько игр уже полностью сыграно (для рандомизации позиций)
    games_completed = db.Column(db.Integer, default=0)

    # последний сохранённый текст для автовходов (то, что ты вставляешь в textarea)
    autologin_text = db.Column(db.Text)  # <-- ДОБАВЛЕНО

    jobs = db.relationship(
        "AutoLoginJob",
        backref="range",
        lazy=True,
        cascade="all, delete-orphan",
    )
    logs = db.relationship(
        "AutoLoginLog",
        backref="range",
        lazy=True,
        cascade="all, delete-orphan",
    )
    commands = db.relationship(
        "RangeCommand",
        backref="range",
        lazy=True,
        cascade="all, delete-orphan",
    )
    account_states = db.relationship(
        "AccountState",
        backref="range",
        lazy=True,
        cascade="all, delete-orphan",
    )
    client_updates = db.relationship(
        "ClientUpdate",
        backref="range",
        lazy=True,
        cascade="all, delete-orphan",
    )


class AutoLoginJob(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    range_id = db.Column(db.Integer, db.ForeignKey("number_range.id"), nullable=False)
    number = db.Column(db.Integer, nullable=False)

    login = db.Column(db.String(255), nullable=False)
    password = db.Column(db.String(255), nullable=False)
    mail = db.Column(db.String(255))
    mail_password = db.Column(db.String(255))

    status = db.Column(db.String(32), default="pending")  # pending / taken / done / error
    error_message = db.Column(db.String(500))


class AutoLoginLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    range_id = db.Column(db.Integer, db.ForeignKey("number_range.id"), nullable=False)
    number = db.Column(db.Integer, nullable=False)
    message = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class RangeCommand(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    range_id = db.Column(db.Integer, db.ForeignKey("number_range.id"), nullable=False)
    number = db.Column(db.Integer, nullable=False)
    action = db.Column(db.String(50), nullable=False)  # novokek / played / autoconfig / startbot / stopbot
    status = db.Column(db.String(32), default="pending")  # pending / taken / done / error
    error_message = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class AccountState(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    range_id = db.Column(db.Integer, db.ForeignKey("number_range.id"), nullable=False)
    number = db.Column(db.Integer, nullable=False)
    steam_id = db.Column(db.String(64), nullable=False)
    last_update = db.Column(db.DateTime, default=datetime.utcnow)
    # Lobby ID для этого номера (может быть NULL)
    lobby_id = db.Column(db.String(64))

    # сколько игр отыграл этот номер
    games_played = db.Column(db.Integer, default=0)
    # последняя сторона, за которую играл бот: "svet" / "tma"
    side = db.Column(db.String(8))
    # последняя позиция 1..5
    last_position = db.Column(db.Integer)
    # последний режим: True = WIN, False = LOOSE
    last_play_for_win = db.Column(db.Boolean)



class LobbyState(db.Model):
    """
    Текущее состояние lobby_id по номеру (одна строка на номер).
    Это сделано отдельно от AccountState, чтобы:
      - не зависеть от last_update (его трогают разные эндпоинты),
      - не "ломать" окно ожидания 6 сек при повторных отправках одного и того же lobby_id,
      - не ловить баг с несколькими AccountState-строками на один номер.
    """
    __table_args__ = (
        db.UniqueConstraint("range_id", "number", name="uq_lobby_state_range_number"),
    )

    id = db.Column(db.Integer, primary_key=True)
    range_id = db.Column(db.Integer, db.ForeignKey("number_range.id"), nullable=False)
    number = db.Column(db.Integer, nullable=False)

    lobby_id = db.Column(db.String(64))
    # Время, когда ЭТОТ номер впервые увидел текущий lobby_id
    lobby_seen_at = db.Column(db.DateTime)
    # Heartbeat: когда в последний раз этот номер прислал lobby_id
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)


class ClientUpdate(db.Model):
    """
    Событие «обновить клиент Dota» для конкретной пятёрки диапазона.
    Лидер (1 или 6) создаёт запись, остальные боты своей пятёрки читают.
    """
    id = db.Column(db.Integer, primary_key=True)
    range_id = db.Column(db.Integer, db.ForeignKey("number_range.id"), nullable=False)
    group_index = db.Column(db.Integer, nullable=False)  # 0, 1, 2... (каждая пятёрка)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ClientUpdateAck(db.Model):
    """
    Подтверждение (ack) от конкретного номера, что он выполнил обновление клиента
    по конкретному событию ClientUpdate.

    Нужно, чтобы после перезапуска бот НЕ выполнял одно и то же обновление снова.
    """
    __tablename__ = "client_update_ack_v2"
    __table_args__ = (
        db.UniqueConstraint("update_id", "number", name="uq_client_update_ack_v2"),
    )

    id = db.Column(db.Integer, primary_key=True)
    update_id = db.Column(db.Integer, db.ForeignKey("client_update.id"), nullable=False)
    number = db.Column(db.Integer, nullable=False)
    ack_at = db.Column(db.DateTime, default=datetime.utcnow)


# ====== ХЕЛПЕРЫ ======
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        user = User.query.get(session["user_id"])
        if not user or not user.is_admin:
            flash("Недостаточно прав.", "danger")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return wrapper


def current_user():
    if "user_id" not in session:
        return None
    return User.query.get(session["user_id"])


@app.context_processor
def inject_current_user():
    # теперь в шаблонах есть переменная current_user
    return {"current_user": current_user()}


# ====== РОУТЫ ОСНОВНЫЕ ======
@app.route("/")
def index():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    if user.is_admin:
        return redirect(url_for("admin_dashboard"))
    return redirect(url_for("user_dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash("Неверный логин или пароль.", "danger")
        else:
            session["user_id"] = user.id
            flash("Успешный вход.", "success")
            return redirect(url_for("index"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Вы вышли из аккаунта.", "info")
    return redirect(url_for("login"))


# ====== АДМИНКА ======
@app.route("/admin")
@admin_required
def admin_dashboard():
    users = User.query.order_by(User.id).all()
    return render_template("admin_dashboard.html", users=users)


@app.route("/admin/users.new", methods=["GET", "POST"])
@admin_required
def admin_create_user():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        is_admin = bool(request.form.get("is_admin"))

        if not username or not password:
            flash("Логин и пароль обязательны.", "danger")
        elif User.query.filter_by(username=username).first():
            flash("Пользователь с таким логином уже существует.", "danger")
        else:
            user = User(
                username=username,
                password_hash=generate_password_hash(password),
                is_admin=is_admin,
            )
            db.session.add(user)
            db.session.commit()
            flash("Пользователь создан.", "success")
            return redirect(url_for("admin_dashboard"))

    return render_template("admin_user_edit.html", user=None)


@app.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_user(user_id):
    user = User.query.get_or_404(user_id)

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        is_admin = bool(request.form.get("is_admin"))

        if not username:
            flash("Логин обязателен.", "danger")
        else:
            user.username = username
            user.is_admin = is_admin
            if password:
                user.password_hash = generate_password_hash(password)
            db.session.commit()
            flash("Пользователь обновлён.", "success")
            return redirect(url_for("admin_dashboard"))

    return render_template("admin_user_edit.html", user=user)


@app.route("/admin/users/<int:user_id>/ranges", methods=["GET", "POST"])
@admin_required
def admin_user_ranges(user_id):
    user = User.query.get_or_404(user_id)

    if request.method == "POST":
        rng_text = request.form.get("range", "").replace(" ", "")
        try:
            start_str, end_str = rng_text.split("-")
            start = int(start_str)
            end = int(end_str)
            if start > end:
                raise ValueError
        except Exception:
            flash("Неверный формат диапазона. Пример: 61-70", "danger")
        else:
            db.session.add(NumberRange(user=user, start=start, end=end))
            db.session.commit()
            flash("Диапазон добавлен.", "success")

    ranges = NumberRange.query.filter_by(user_id=user.id).order_by(
        NumberRange.start
    ).all()
    return render_template("admin_user_ranges.html", user=user, ranges=ranges)


@app.route("/admin/ranges/<int:range_id>/delete")
@admin_required
def admin_delete_range(range_id):
    rng = NumberRange.query.get_or_404(range_id)
    user_id = rng.user_id
    db.session.delete(rng)
    db.session.commit()
    flash("Диапазон удалён.", "info")
    return redirect(url_for("admin_user_ranges", user_id=user_id))


# ====== КАБИНЕТ ПОЛЬЗОВАТЕЛЯ ======
@app.route("/cabinet")
@login_required
def user_dashboard():
    user = current_user()
    ranges = NumberRange.query.filter_by(user_id=user.id).order_by(
        NumberRange.start
    ).all()
    return render_template(
        "user_dashboard.html",
        user=user,
        ranges=ranges,
    )


@app.route("/range/<int:range_id>")
@login_required
def user_range(range_id: int):
    user = current_user()
    rng = NumberRange.query.get_or_404(range_id)

    # админ может управлять любым диапазоном
    if not user.is_admin and rng.user_id != user.id:
        flash("У вас нет прав на этот диапазон.", "danger")
        return redirect(url_for("user_dashboard"))

    tab = request.args.get("tab", "settings")
    if tab not in ("settings", "autologin", "logs", "state"):
        tab = "settings"

    # ВСЕ задачи по диапазону (их теперь максимум = размер диапазона)
    jobs = AutoLoginJob.query.filter_by(range_id=rng.id).order_by(
        AutoLoginJob.id.desc()
    ).all()

    logs = AutoLoginLog.query.filter_by(range_id=rng.id).order_by(
        AutoLoginLog.created_at.desc()
    ).limit(200).all()

    states = AccountState.query.filter_by(range_id=rng.id).order_by(
        AccountState.number,
        AccountState.steam_id,
    ).all()

    return render_template(
        "user_range.html",
        user=user,
        rng=rng,
        tab=tab,
        jobs=jobs,
        logs=logs,
        states=states,
    )


@app.route("/range/<int:range_id>/logs-json")
@login_required
def range_logs_json(range_id: int):
    user = current_user()
    rng = NumberRange.query.get_or_404(range_id)

    if not user.is_admin and rng.user_id != user.id:
        return jsonify({"ok": False, "error": "forbidden"}), 403

    logs = AutoLoginLog.query.filter_by(range_id=rng.id).order_by(
        AutoLoginLog.created_at.desc()
    ).limit(200).all()

    data = [
        {
            "time": l.created_at.strftime("%H:%M:%S"),
            "number": l.number,
            "message": l.message,
        }
        for l in reversed(logs)
    ]
    return jsonify({"ok": True, "logs": data})


@app.route("/range/<int:range_id>/autologin/start", methods=["POST"])
@login_required
def user_range_autologin_start(range_id):
    """
    Старт автовходов:
      - старые задачи ПОЛНОСТЬЮ очищаем для диапазона;
      - создаём новые задачи только под текущий список аккаунтов;
      - сырой текст аккаунтов сохраняем в rng.autologin_text (чтобы остался в textarea).
    """
    user = current_user()
    rng = NumberRange.query.get_or_404(range_id)

    if not user.is_admin and rng.user_id != user.id:
        flash("У вас нет прав на этот диапазон.", "danger")
        return redirect(url_for("user_dashboard"))

    raw = request.form.get("accounts", "")
    lines = [line.strip() for line in raw.splitlines() if line.strip()]

    if not lines:
        flash("Нужно указать хотя бы одну строку с логином.", "danger")
        return redirect(url_for("user_range", range_id=range_id, tab="autologin"))

    # сохраняем текст, чтобы он оставался в форме
    rng.autologin_text = raw

    # УДАЛЯЕМ ВСЕ старые задачи этого диапазона (а не только pending),
    # чтобы всегда было максимум «по одному на номер».
    AutoLoginJob.query.filter_by(range_id=rng.id).delete()

    numbers = list(range(rng.start, rng.end + 1))
    count = min(len(lines), len(numbers))
    created = 0

    for i in range(count):
        line = lines[i]
        parts = line.split(":")
        if len(parts) < 2:
            continue

        login = parts[0]
        password = parts[1]
        mail = parts[2] if len(parts) > 2 else None
        mail_password = parts[3] if len(parts) > 3 else None

        num = numbers[i]

        job = AutoLoginJob(
            range_id=rng.id,
            number=num,
            login=login,
            password=password,
            mail=mail,
            mail_password=mail_password,
            status="pending",
        )
        db.session.add(job)
        created += 1

    db.session.commit()
    flash(f"Создано задач автологина: {created}.", "success")
    return redirect(url_for("user_range", range_id=range_id, tab="autologin"))


@app.route("/range/<int:range_id>/command", methods=["POST"])
@login_required
def user_range_command(range_id):
    user = current_user()
    rng = NumberRange.query.get_or_404(range_id)

    if not user.is_admin and rng.user_id != user.id:
        flash("У вас нет прав на этот диапазон.", "danger")
        return redirect(url_for("user_dashboard"))

    action = request.form.get("action")

    labels = {
        "startbot": "Запуск бота",
        "stopbot": "Остановить бота",
        "novokek": "Новичок (750 MMR)",
        "played": "Я уже играл (1600 MMR)",
        "autoconfig": "Автонастройка",
    }

    if action not in labels:
        flash("Неизвестное действие.", "danger")
        return redirect(url_for("user_range", range_id=range_id, tab="settings"))

    for num in range(rng.start, rng.end + 1):
        cmd = RangeCommand(
            range_id=rng.id,
            number=num,
            action=action,
            status="pending",
        )
        db.session.add(cmd)

    db.session.commit()

    flash(f"Команда «{labels[action]}» отправлена на ботов диапазона.", "success")
    return redirect(url_for("user_range", range_id=range_id, tab="settings"))


@app.route("/range/<int:range_id>/game-settings", methods=["POST"])
@login_required
def user_range_game_settings(range_id):
    user = current_user()
    rng = NumberRange.query.get_or_404(range_id)

    if not user.is_admin and rng.user_id != user.id:
        flash("У вас нет прав на этот диапазон.", "danger")
        return redirect(url_for("user_dashboard"))

    action = request.form.get("action")

    if action == "set_win_group":
        win_group = request.form.get("win_group")
        if win_group not in ("left", "right"):
            flash("Неверное значение стороны.", "danger")
            return redirect(url_for("user_range", range_id=rng.id, tab="settings"))
        rng.win_group = win_group
        db.session.commit()
        flash("Режим победа/поражение обновлён.", "success")
        return redirect(url_for("user_range", range_id=rng.id, tab="settings"))

    elif action == "reset_games":
        rng.games_completed = 0
        rows = AccountState.query.filter_by(range_id=rng.id).all()
        for r in rows:
            r.games_played = 0
        db.session.commit()
        flash("Счётчики игр для диапазона сброшены.", "success")
        return redirect(url_for("user_range", range_id=rng.id, tab="state"))

    else:
        flash("Неизвестное действие.", "danger")
        return redirect(url_for("user_range", range_id=rng.id, tab="settings"))


# ====== API ДЛЯ АВТОВХОДА ======
@app.route("/api/autologin/next", methods=["POST"])
def api_autologin_next():
    data = request.get_json(force=True, silent=True) or {}
    number = data.get("number")

    if number is None:
        return jsonify({"ok": False, "error": "number required"}), 400

    job = (
        AutoLoginJob.query
        .filter_by(number=number, status="pending")
        .order_by(AutoLoginJob.id)
        .first()
    )

    if not job:
        return jsonify({"ok": True, "job": None})

    job.status = "taken"
    db.session.commit()

    return jsonify({
        "ok": True,
        "job": {
            "id": job.id,
            "number": job.number,
            "login": job.login,
            "password": job.password,
            "mail": job.mail,
            "mail_password": job.mail_password,
        }
    })


@app.route("/api/autologin/result", methods=["POST"])
def api_autologin_result():
    data = request.get_json(force=True, silent=True) or {}
    job_id = data.get("id")
    status = data.get("status")
    message = data.get("message", "")

    if not job_id or status not in ("done", "error"):
        return jsonify({"ok": False, "error": "bad payload"}), 400

    job = AutoLoginJob.query.get(job_id)
    if not job:
        return jsonify({"ok": False, "error": "job not found"}), 404

    job.status = status
    job.error_message = message[:500]
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/autologin/log", methods=["POST"])
def api_autologin_log():
    data = request.get_json(force=True, silent=True) or {}
    number = data.get("number")
    message = (data.get("message") or "").strip()

    if number is None or not message:
        return jsonify({"ok": False, "error": "number and message required"}), 400

    rng = (
        NumberRange.query
        .filter(NumberRange.start <= int(number), NumberRange.end >= int(number))
        .first()
    )
    if not rng:
        return jsonify({"ok": False, "error": "range not found"}), 404

    log_row = AutoLoginLog(
        range_id=rng.id,
        number=int(number),
        message=message[:500],
    )
    db.session.add(log_row)
    db.session.commit()

    return jsonify({"ok": True})


# ====== API ДЛЯ КОМАНД ======
@app.route("/api/command/next", methods=["POST"])
def api_command_next():
    data = request.get_json(force=True, silent=True) or {}
    number = data.get("number")

    if number is None:
        return jsonify({"ok": False, "error": "number required"}), 400

    cmd = (
        RangeCommand.query
        .filter_by(number=number, status="pending")
        .order_by(RangeCommand.id)
        .first()
    )
    if not cmd:
        return jsonify({"ok": True, "command": None})

    cmd.status = "taken"
    db.session.commit()

    return jsonify({
        "ok": True,
        "command": {
            "id": cmd.id,
            "action": cmd.action,
        }
    })


@app.route("/api/command/result", methods=["POST"])
def api_command_result():
    data = request.get_json(force=True, silent=True) or {}
    cmd_id = data.get("id")
    status = data.get("status")
    message = data.get("message", "")

    if not cmd_id or status not in ("done", "error"):
        return jsonify({"ok": False, "error": "bad payload"}), 400

    cmd = RangeCommand.query.get(cmd_id)
    if not cmd:
        return jsonify({"ok": False, "error": "command not found"}), 404

    cmd.status = status
    cmd.error_message = message[:500]
    db.session.commit()
    return jsonify({"ok": True})




@app.route("/api/command/state")
def api_command_state():
    """
    GET /api/command/state?number=51

    Возвращает "липкое" состояние startbot/stopbot для указанного number.

    Зачем:
      - Команда startbot/stopbot в текущей схеме — одноразовая и быстро уходит в done.
      - Если клиент (exe) перезапустили, он может "не увидеть" startbot и будет спать.
      - По этому эндпоинту клиент может синхронизироваться и понять, нужно ли ему работать.
    """
    number = request.args.get("number", type=int)
    if number is None:
        return jsonify({"ok": False, "error": "number required"}), 400

    cmd = (
        RangeCommand.query
        .filter(
            RangeCommand.number == int(number),
            RangeCommand.action.in_(("startbot", "stopbot")),
        )
        .order_by(RangeCommand.id.desc())
        .first()
    )

    if not cmd:
        return jsonify({
            "ok": True,
            "active": False,
            "last_action": None,
            "id": None,
            "created_at": None,
        })

    return jsonify({
        "ok": True,
        "active": True if cmd.action == "startbot" else False,
        "last_action": cmd.action,
        "id": cmd.id,
        "created_at": cmd.created_at.isoformat() + "Z" if cmd.created_at else None,
    })

# ====== API ДЛЯ СОСТОЯНИЯ АККАУНТОВ (STEAM ID) ======
@app.route("/api/accounts/update", methods=["POST"])
def api_accounts_update():
    data = request.get_json(force=True, silent=True) or {}
    number = data.get("number")
    steam_ids = data.get("steam_ids") or []

    if number is None:
        return jsonify({"ok": False, "error": "number required"}), 400

    try:
        number_int = int(number)
    except Exception:
        return jsonify({"ok": False, "error": "bad number"}), 400

    if not steam_ids:
        return jsonify({"ok": False, "error": "no steam_ids"}), 400

    rng = (
        NumberRange.query
        .filter(NumberRange.start <= number_int, NumberRange.end >= number_int)
        .first()
    )
    if not rng:
        return jsonify({"ok": False, "error": "range not found"}), 404

    # сохраняем старые счётчики игр по steam_id
    old_rows = AccountState.query.filter_by(range_id=rng.id, number=number_int).all()
    old_games = {r.steam_id: (r.games_played or 0) for r in old_rows}

    # чтобы lobby_id не пропадал при перезапуске EXE и повторной отправке /api/accounts/update
    old_lobby_id = None
    for r in sorted(old_rows, key=lambda x: x.last_update or datetime.min, reverse=True):
        if r.lobby_id:
            old_lobby_id = r.lobby_id
            break

    for r in old_rows:
        db.session.delete(r)

    for sid in steam_ids:
        sid_str = str(sid).strip()
        if not sid_str:
            continue
        row = AccountState(
            range_id=rng.id,
            number=number_int,
            steam_id=sid_str,
            last_update=datetime.utcnow(),
            lobby_id=old_lobby_id,
            games_played=old_games.get(sid_str, 0),
        )
        db.session.add(row)

    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/accounts/party")
def api_accounts_party():
    number = request.args.get("number", type=int)
    if number is None:
        return jsonify({"ok": False, "error": "number required"}), 400

    rng = (
        NumberRange.query
        .filter(NumberRange.start <= number, NumberRange.end >= number)
        .first()
    )
    if not rng:
        return jsonify({"ok": False, "error": "range not found"}), 404

    last_digit = abs(number) % 10
    if last_digit not in (1, 6):
        return jsonify({"ok": True, "party": []})

    party_numbers = []
    for i in range(1, 5):
        n = number + i
        if n <= rng.end:
            party_numbers.append(n)

    if not party_numbers:
        return jsonify({"ok": True, "party": []})

    rows = (
        AccountState.query
        .filter(
            AccountState.range_id == rng.id,
            AccountState.number.in_(party_numbers),
        )
        .order_by(AccountState.number, AccountState.last_update.desc())
        .all()
    )

    id_map = {}
    for row in rows:
        if row.number not in id_map:
            id_map[row.number] = row.steam_id

    party = [{"number": n, "steam_id": id_map[n]} for n in party_numbers if n in id_map]
    return jsonify({"ok": True, "party": party})


# ====== API ДЛЯ LOBBY ID ======
@app.route("/api/accounts/lobby_update", methods=["POST"])
def api_accounts_lobby_update():
    data = request.get_json(force=True, silent=True) or {}
    number = data.get("number")
    lobby_id = (data.get("lobby_id") or "").strip()

    if number is None:
        return jsonify({"ok": False, "error": "number required"}), 400
    if not lobby_id:
        return jsonify({"ok": False, "error": "no lobby_id"}), 400

    try:
        number_int = int(number)
    except Exception:
        return jsonify({"ok": False, "error": "bad number"}), 400

    rng = (
        NumberRange.query
        .filter(NumberRange.start <= number_int, NumberRange.end >= number_int)
        .first()
    )
    if not rng:
        return jsonify({"ok": False, "error": "range not found"}), 404

    now = datetime.utcnow()

    # 1) Надёжное хранение lobby_id (одна строка на номер)
    st = LobbyState.query.filter_by(range_id=rng.id, number=number_int).first()
    if not st:
        st = LobbyState(
            range_id=rng.id,
            number=number_int,
            lobby_id=lobby_id,
            lobby_seen_at=now,
            updated_at=now,
        )
        db.session.add(st)
    else:
        if st.lobby_id != lobby_id:
            st.lobby_id = lobby_id
            st.lobby_seen_at = now
        st.updated_at = now

    # 2) Для отображения в таблице «Состояние аккаунтов» — проставляем lobby_id во ВСЕ строки номера
    rows = AccountState.query.filter_by(range_id=rng.id, number=number_int).all()
    if not rows:
        row = AccountState(
            range_id=rng.id,
            number=number_int,
            steam_id="unknown",
            last_update=now,
        )
        db.session.add(row)
        rows = [row]

    for r in rows:
        r.lobby_id = lobby_id
        # last_update можно использовать как heartbeat (чтобы видно было что бот жив)
        r.last_update = now

    db.session.commit()
    return jsonify({"ok": True})
@app.route("/api/accounts/lobby_state")
def api_accounts_lobby_state():
    """
    GET /api/accounts/lobby_state?number=51

    Ответ:
      { "ok": true, "mode": "same"|"different"|"waiting", "lobby_id": "..."|null }

    Что было не так раньше:
      - ожидание WAIT_SECONDS считалось ОТ МОЕГО lobby_id;
      - у тех, кто ещё не успел отправить новый lobby_id, "мой lobby_id" был старый,
        окно ожидания уже давно прошло -> сервер почти сразу отдавал "different".

    Что делаем теперь (без всяких CHANGE_WINDOW_SECONDS):
      - смотрим ПО ВСЕМУ диапазону, какой lobby_id был изменён ПОСЛЕДНИМ (max(lobby_seen_at));
      - берём его как "текущую волну" (wave_lobby_id);
      - после ПЕРВОГО появления этого wave_lobby_id в диапазоне даём WAIT_SECONDS,
        чтобы остальные боты успели отправить свой lobby_id;
      - только после этого начинаем возвращать same/different.

    Это ровно то, что тебе нужно: после первого lobby_id — пауза, и только потом решение.
    """
    number = request.args.get("number", type=int)
    if number is None:
        return jsonify({"ok": False, "error": "number required"}), 400

    rng = (
        NumberRange.query
        .filter(NumberRange.start <= number, NumberRange.end >= number)
        .first()
    )
    if not rng:
        return jsonify({"ok": False, "error": "range not found"}), 404

    now = datetime.utcnow()
    WAIT_SECONDS = 8  # можешь менять (6..10). 8 похоже на то, как было в самом первом варианте

    # Через какое время считать данные "протухшими" (бот упал/не шлёт lobby_id)
    TTL_SECONDS = 15 * 60  # 15 минут
    cutoff = now - timedelta(seconds=TTL_SECONDS)

    my_state = LobbyState.query.filter_by(range_id=rng.id, number=number).first()
    if (
        (not my_state)
        or (not my_state.lobby_id)
        or (not my_state.updated_at)
        or (my_state.updated_at < cutoff)
    ):
        return jsonify({"ok": True, "mode": "waiting", "lobby_id": None})

    my_lobby = my_state.lobby_id

    numbers = list(range(rng.start, rng.end + 1))

    # --- 1) Определяем последнюю "волну" lobby_id в диапазоне ---
    change_rows = (
        LobbyState.query
        .filter(
            LobbyState.range_id == rng.id,
            LobbyState.number.in_(numbers),
            LobbyState.updated_at >= cutoff,
            LobbyState.lobby_id.isnot(None),
            LobbyState.lobby_seen_at.isnot(None),
        )
        .all()
    )

    if change_rows:
        newest = None
        for r in change_rows:
            if not r.lobby_seen_at:
                continue
            if newest is None or r.lobby_seen_at > newest.lobby_seen_at:
                newest = r

        if newest and newest.lobby_id:
            wave_lobby = newest.lobby_id

            # время первого появления wave_lobby в диапазоне
            wave_first = None
            for r in change_rows:
                if r.lobby_id == wave_lobby and r.lobby_seen_at:
                    if wave_first is None or r.lobby_seen_at < wave_first:
                        wave_first = r.lobby_seen_at

            if wave_first:
                age = (now - wave_first).total_seconds()
                if age < WAIT_SECONDS:
                    return jsonify({"ok": True, "mode": "waiting", "lobby_id": wave_lobby})

    # --- 2) Окно прошло — сверяем по всему диапазону ---
    rows = (
        LobbyState.query
        .filter(
            LobbyState.range_id == rng.id,
            LobbyState.number.in_(numbers),
            LobbyState.updated_at >= cutoff,
            LobbyState.lobby_id.isnot(None),
        )
        .all()
    )

    latest_per_number = {int(r.number): r.lobby_id for r in rows}

    # 1) Если есть хоть один lobby_id, отличающийся от моего → different
    if any(lobby != my_lobby for lobby in latest_per_number.values()):
        return jsonify({"ok": True, "mode": "different", "lobby_id": my_lobby})

    # 2) Если НЕТ конфликтов, но не все номера диапазона прислали lobby_id → тоже different
    if len(latest_per_number) < len(numbers):
        return jsonify({"ok": True, "mode": "different", "lobby_id": my_lobby})

    # 3) Иначе: каждый номер имеет lobby_id == мой → same
    return jsonify({"ok": True, "mode": "same", "lobby_id": my_lobby})

@app.route("/api/accounts/lobby_reset", methods=["POST"])
def api_accounts_lobby_reset():
    """
    Клиент может вызывать этот метод после нажатия accept,
    но мы больше НЕ трогаем lobby_id в базе, чтобы не ломать
    вычисление same/different для остальных ботов.
    """
    data = request.get_json(force=True, silent=True) or {}
    number = data.get("number")

    if number is None:
        return jsonify({"ok": False, "error": "number required"}), 400

    # никаких изменений в БД не делаем
    return jsonify({"ok": True})



# ====== API ДЛЯ ОБНОВЛЕНИЯ КЛИЕНТА (client_igri) ======

def _client_update_group_numbers(rng: NumberRange, group_index: int) -> list[int]:
    """Возвращает номера (в пределах диапазона) для конкретной пятёрки group_index."""
    start_num = int(rng.start) + int(group_index) * 5
    if start_num > int(rng.end):
        return []
    end_num = min(start_num + 4, int(rng.end))
    return list(range(start_num, end_num + 1))


def _client_update_is_done(update_row: ClientUpdate, group_numbers: list[int]) -> tuple[bool, set[int]]:
    """(done, acked_numbers_set) для события update_row."""
    acks = ClientUpdateAck.query.filter_by(update_id=update_row.id).all()
    acked_numbers = {int(a.number) for a in acks if a.number is not None}
    done = bool(group_numbers) and all(n in acked_numbers for n in group_numbers)
    return done, acked_numbers


@app.route("/api/client_update/leader", methods=["POST"])
def api_client_update_leader():
    """
    Лидер пятёрки сообщает, что увидел окно обновления клиента.
    Сервер создаёт событие для всей пятёрки (или возвращает уже существующее),
    чтобы боты обновились РОВНО ОДИН раз на событие.
    """
    data = request.get_json(force=True, silent=True) or {}
    number = data.get("number")

    if number is None:
        return jsonify({"ok": False, "error": "number required"}), 400

    try:
        number_int = int(number)
    except Exception:
        return jsonify({"ok": False, "error": "bad number"}), 400

    rng = (
        NumberRange.query
        .filter(NumberRange.start <= number_int, NumberRange.end >= number_int)
        .first()
    )
    if not rng:
        return jsonify({"ok": False, "error": "range not found"}), 404

    total = int(rng.end) - int(rng.start) + 1
    if total <= 0:
        return jsonify({"ok": False, "error": "empty range"}), 400

    rel = number_int - int(rng.start)
    if rel < 0 or rel >= total:
        return jsonify({"ok": False, "error": "number not in range"}), 400

    group_index = rel // 5
    group_numbers = _client_update_group_numbers(rng, group_index)

    # --- Анти-спам / дедупликация ---
    # Если уже есть "активное" событие (не все отписались ack), возвращаем его, чтобы не плодить 100 событий.
    # Если событие зависло очень давно (STALE_SECONDS) — разрешаем создать новое.
    STALE_SECONDS = 6 * 3600  # 6 часов
    DONE_DEBOUNCE_SECONDS = 120  # 2 минуты (защита от двойного клика/фолс-позитива)

    now = datetime.utcnow()

    last_row = (
        ClientUpdate.query
        .filter_by(range_id=rng.id, group_index=group_index)
        .order_by(ClientUpdate.id.desc())
        .first()
    )
    if last_row:
        done, _acked = _client_update_is_done(last_row, group_numbers)
        age = None
        try:
            if last_row.created_at:
                age = (now - last_row.created_at).total_seconds()
        except Exception:
            age = None

        if not done:
            # Если ещё не все ack — считаем событие активным и возвращаем его (пока не устарело)
            if age is None or age < STALE_SECONDS:
                return jsonify({
                    "ok": True,
                    "id": last_row.id,
                    "group_index": group_index,
                    "reused": True,
                    "done": False,
                })

        # Если done, но лидер "дёрнул" ручку повторно сразу — тоже вернём прошлое, чтобы не пересоздавать
        if done and age is not None and age < DONE_DEBOUNCE_SECONDS:
            return jsonify({
                "ok": True,
                "id": last_row.id,
                "group_index": group_index,
                "reused": True,
                "done": True,
            })

    # Создаём новое событие
    row = ClientUpdate(
        range_id=rng.id,
        group_index=group_index,
        created_at=now,
    )
    db.session.add(row)
    db.session.commit()

    return jsonify({
        "ok": True,
        "id": row.id,
        "group_index": group_index,
        "reused": False,
        "done": False,
        "created_at": row.created_at.isoformat() + "Z",
    })


@app.route("/api/client_update/check")
def api_client_update_check():
    """
    GET /api/client_update/check?number=51

    Возвращает ПОСЛЕДНЕЕ событие обновления клиента для пятёрки,
    НО только если данный number ещё НЕ подтвердил (ack) это событие.

    Ответ:
      { "ok": true, "id": <int> | null, "group_index": <int> | null, "created_at": <str> | null }
    """
    number = request.args.get("number", type=int)
    if number is None:
        return jsonify({"ok": False, "error": "number required"}), 400

    rng = (
        NumberRange.query
        .filter(NumberRange.start <= number, NumberRange.end >= number)
        .first()
    )
    if not rng:
        return jsonify({"ok": False, "error": "range not found"}), 404

    total = int(rng.end) - int(rng.start) + 1
    if total <= 0:
        return jsonify({"ok": True, "id": None, "group_index": None})

    rel = int(number) - int(rng.start)
    if rel < 0 or rel >= total:
        return jsonify({"ok": False, "error": "number not in range"}), 400

    group_index = rel // 5
    group_numbers = _client_update_group_numbers(rng, group_index)

    row = (
        ClientUpdate.query
        .filter_by(range_id=rng.id, group_index=group_index)
        .order_by(ClientUpdate.id.desc())
        .first()
    )
    if not row:
        return jsonify({"ok": True, "id": None, "group_index": group_index})

    done, acked_numbers = _client_update_is_done(row, group_numbers)

    # если событие уже закрыто (все ack), или этот бот уже ack — ничего не возвращаем
    if done or int(number) in acked_numbers:
        return jsonify({
            "ok": True,
            "id": None,
            "group_index": group_index,
            "created_at": None,
            "done": bool(done),
            "acked": len(acked_numbers),
            "total": len(group_numbers),
        })

    return jsonify({
        "ok": True,
        "id": row.id,
        "group_index": row.group_index,
        "created_at": row.created_at.isoformat() + "Z",
        "done": bool(done),
        "acked": len(acked_numbers),
        "total": len(group_numbers),
    })


@app.route("/api/client_update/ack", methods=["POST"])
def api_client_update_ack():
    """
    POST /api/client_update/ack
    payload: { "number": 52, "id": 123 }

    Записывает подтверждение, что бот number уже отработал обновление по событию id.
    """
    data = request.get_json(force=True, silent=True) or {}
    number = data.get("number")
    update_id = data.get("id") or data.get("update_id")

    if number is None or update_id is None:
        return jsonify({"ok": False, "error": "number and id required"}), 400

    try:
        number_int = int(number)
        update_id_int = int(update_id)
    except Exception:
        return jsonify({"ok": False, "error": "bad number/id"}), 400

    update_row = ClientUpdate.query.get(update_id_int)
    if not update_row:
        return jsonify({"ok": False, "error": "update event not found"}), 404

    rng = NumberRange.query.get(update_row.range_id)
    if not rng:
        return jsonify({"ok": False, "error": "range not found"}), 404

    total = int(rng.end) - int(rng.start) + 1
    if total <= 0:
        return jsonify({"ok": False, "error": "empty range"}), 400

    rel = number_int - int(rng.start)
    if rel < 0 or rel >= total:
        return jsonify({"ok": False, "error": "number not in range"}), 400

    group_index = rel // 5
    if int(group_index) != int(update_row.group_index):
        return jsonify({"ok": False, "error": "wrong group"}), 400

    group_numbers = _client_update_group_numbers(rng, group_index)

    # пишем ack один раз
    exists = ClientUpdateAck.query.filter_by(update_id=update_row.id, number=number_int).first()
    if not exists:
        ack = ClientUpdateAck(
            update_id=update_row.id,
            number=number_int,
            ack_at=datetime.utcnow(),
        )
        db.session.add(ack)
        db.session.commit()

    done, acked_numbers = _client_update_is_done(update_row, group_numbers)

    return jsonify({
        "ok": True,
        "id": update_row.id,
        "group_index": update_row.group_index,
        "done": bool(done),
        "acked": len(acked_numbers),
        "total": len(group_numbers),
    })

# ====== API ДЛЯ ИГРОВОЙ ЛОГИКИ (СТОРОНА, ПОЗИЦИИ, WIN/LOOSE) ======
@app.route("/api/game/config", methods=["POST"])
def api_game_config():
    data = request.get_json(force=True, silent=True) or {}
    number = data.get("number")
    side = (data.get("side") or "").strip()

    if number is None or side not in ("svet", "tma"):
        return jsonify({"ok": False, "error": "number and side required"}), 400

    try:
        number_int = int(number)
    except Exception:
        return jsonify({"ok": False, "error": "bad number"}), 400

    rng = (
        NumberRange.query
        .filter(NumberRange.start <= number_int, NumberRange.end >= number_int)
        .first()
    )
    if not rng:
        return jsonify({"ok": False, "error": "range not found"}), 404

    numbers = list(range(rng.start, rng.end + 1))
    total = len(numbers)
    if total <= 0:
        return jsonify({"ok": False, "error": "empty range"}), 400

    rel = number_int - rng.start
    if rel < 0 or rel >= total:
        return jsonify({"ok": False, "error": "number not in range"}), 400

    # разбиваем диапазон по пятёркам: [start..start+4], [start+5..start+9], ...
    group_index = rel // 5  # 0,1,...
    index_in_group = rel % 5

    group_start = group_index * 5
    group_numbers = numbers[group_start:group_start + 5]
    if not group_numbers:
        group_numbers = [number_int]

    # название группы для UI и win/lose (для 51-60 будет "left"/"right")
    group = "left" if group_index == 0 else "right"

    # какая группа сейчас играет на победу
    win_group = rng.win_group or "right"
    play_for_win = (group == win_group)

    games_completed = rng.games_completed or 0

    # рандомная перестановка позиций 1..N внутри пятёрки,
    # но детерминированная от range_id, group_index и games_completed
    rnd = random.Random(rng.id * 1000 + group_index * 100 + games_completed)
    base_positions = list(range(1, len(group_numbers) + 1))
    rnd.shuffle(base_positions)
    pos_map = {group_numbers[i]: base_positions[i] for i in range(len(group_numbers))}
    position = int(pos_map.get(number_int, 1))

    # сохраняем состояние в AccountState (сторона, позиция, режим)
    now = datetime.utcnow()
    rows = (
        AccountState.query
        .filter_by(range_id=rng.id, number=number_int)
        .all()
    )
    if not rows:
        row = AccountState(
            range_id=rng.id,
            number=number_int,
            steam_id="unknown",
            last_update=now,
        )
        db.session.add(row)
        rows = [row]

    for r in rows:
        r.last_update = now
        r.side = side
        r.last_position = position
        r.last_play_for_win = play_for_win

    games_played = max((r.games_played or 0) for r in rows)
    db.session.commit()

    return jsonify({
        "ok": True,
        "config": {
            "number": number_int,
            "side": side,
            "group": group,
            "play_for_win": bool(play_for_win),
            "position": position,
            "games_played": int(games_played),
            "games_completed": int(games_completed),
            "win_group": win_group,
        },
    })


@app.route("/api/game/finished", methods=["POST"])
def api_game_finished():
    data = request.get_json(force=True, silent=True) or {}
    number = data.get("number")

    if number is None:
        return jsonify({"ok": False, "error": "number required"}), 400

    try:
        number_int = int(number)
    except Exception:
        return jsonify({"ok": False, "error": "bad number"}), 400

    rng = (
        NumberRange.query
        .filter(NumberRange.start <= number_int, NumberRange.end >= number_int)
        .first()
    )
    if not rng:
        return jsonify({"ok": False, "error": "range not found"}), 404

    now = datetime.utcnow()
    rows = (
        AccountState.query
        .filter_by(range_id=rng.id, number=number_int)
        .all()
    )
    if not rows:
        row = AccountState(
            range_id=rng.id,
            number=number_int,
            steam_id="unknown",
            last_update=now,
        )
        db.session.add(row)
        rows = [row]

    for r in rows:
        r.games_played = (r.games_played or 0) + 1
        r.last_update = now

    # мастер — первый номер диапазона; только он переключает WIN/LOOSE
    is_master = (number_int == rng.start)
    if is_master:
        rng.games_completed = (rng.games_completed or 0) + 1
        win_group = rng.win_group or "right"
        rng.win_group = "left" if win_group == "right" else "right"

    db.session.commit()

    return jsonify({
        "ok": True,
        "is_master": is_master,
        "games_completed": rng.games_completed,
        "win_group": rng.win_group,
    })


# ====== CLI ======
@app.cli.command("init-db")
def init_db():
    db.create_all()
    print("База создана.")


@app.cli.command("create-admin")
def create_admin():
    username = "tek1"
    password = "Kolya777"
    if User.query.filter_by(username=username).first():
        print("Админ уже существует.")
        return
    user = User(
        username=username,
        password_hash=generate_password_hash(password),
        is_admin=True,
    )
    db.session.add(user)
    db.session.commit()
    print(f"Создан админ {username}/{password}")


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5000, debug=False)
