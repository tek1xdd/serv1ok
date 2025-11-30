# server.py
# -*- coding: utf-8 -*-
from functools import wraps
from datetime import datetime

from flask import (
    Flask, render_template, redirect, url_for,
    request, session, flash, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

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
    action = db.Column(db.String(50), nullable=False)  # novokek / played / autoconfig / startbot
    status = db.Column(db.String(32), default="pending")  # pending / taken / done / error
    error_message = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class AccountState(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    range_id = db.Column(db.Integer, db.ForeignKey("number_range.id"), nullable=False)
    number = db.Column(db.Integer, nullable=False)
    steam_id = db.Column(db.String(64), nullable=False)
    last_update = db.Column(db.DateTime, default=datetime.utcnow)


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


@app.route("/admin/users/new", methods=["GET", "POST"])
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
    """Страница конкретного диапазона: вкладки Настройка/Автовходы/Логи/Состояние."""
    user = current_user()
    rng = NumberRange.query.get_or_404(range_id)

    if rng.user_id != user.id:
        flash("У вас нет прав на этот диапазон.", "danger")
        return redirect(url_for("user_dashboard"))

    tab = request.args.get("tab", "settings")
    if tab not in ("settings", "autologin", "logs", "state"):
        tab = "settings"

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
    """JSON для автообновления логов во вкладке 'Логи'."""
    user = current_user()
    rng = NumberRange.query.get_or_404(range_id)
    if rng.user_id != user.id:
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
    """Создание задач автологина по диапазону."""
    user = current_user()
    rng = NumberRange.query.get_or_404(range_id)

    if rng.user_id != user.id:
        flash("У вас нет прав на этот диапазон.", "danger")
        return redirect(url_for("user_dashboard"))

    raw = request.form.get("accounts", "")
    lines = [line.strip() for line in raw.splitlines() if line.strip()]

    if not lines:
        flash("Нужно указать хотя бы одну строку с логином.", "danger")
        return redirect(url_for("user_range", range_id=range_id, tab="autologin"))

    AutoLoginJob.query.filter_by(range_id=rng.id, status="pending").delete()

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
    """Кнопки 'Запуск бота' / 'Новичок' / 'Я уже играл' / 'Автонастройка'."""
    user = current_user()
    rng = NumberRange.query.get_or_404(range_id)

    if rng.user_id != user.id:
        flash("У вас нет прав на этот диапазон.", "danger")
        return redirect(url_for("user_dashboard"))

    action = request.form.get("action")

    labels = {
        "startbot": "Запуск бота",
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


# ====== API ДЛЯ СОСТОЯНИЯ АККАУНТОВ ======
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

    AccountState.query.filter_by(range_id=rng.id, number=number_int).delete()

    for sid in steam_ids:
        sid_str = str(sid).strip()
        if not sid_str:
            continue
        row = AccountState(
            range_id=rng.id,
            number=number_int,
            steam_id=sid_str,
            last_update=datetime.utcnow(),
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
