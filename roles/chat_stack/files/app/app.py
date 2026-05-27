import os
import uuid
import secrets
import hashlib
import smtplib
from datetime import datetime, timedelta
from functools import wraps
from email.message import EmailMessage

from flask import (
    Flask,
    request,
    redirect,
    url_for,
    session,
    render_template,
    flash,
    send_file,
    abort,
)
import pymysql
from pymysql.cursors import DictCursor
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from minio import Minio
from minio.error import S3Error
from io import BytesIO


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-only")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
app.config["PREFERRED_URL_SCHEME"] = "https"

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


def db_conn():
    return pymysql.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "3306")),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=True,
    )


def minio_client():
    secure = os.environ.get("MINIO_SECURE", "false").lower() == "true"
    return Minio(
        os.environ["MINIO_ENDPOINT"],
        access_key=os.environ["MINIO_ACCESS_KEY"],
        secret_key=os.environ["MINIO_SECRET_KEY"],
        secure=secure,
    )


def ensure_bucket():
    client = minio_client()
    bucket = os.environ["MINIO_BUCKET"]
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)


def init_db():
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(64) NOT NULL UNIQUE,
                    email VARCHAR(255) NOT NULL UNIQUE,
                    password_hash VARCHAR(255) NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS chat_groups (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(128) NOT NULL,
                    description TEXT NULL,
                    owner_user_id INT NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (owner_user_id) REFERENCES users(id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS group_members (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    group_id INT NOT NULL,
                    user_id INT NOT NULL,
                    role VARCHAR(32) NOT NULL DEFAULT 'member',
                    joined_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_group_user (group_id, user_id),
                    FOREIGN KEY (group_id) REFERENCES chat_groups(id) ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS attachments (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    object_key VARCHAR(512) NOT NULL,
                    original_filename VARCHAR(255) NOT NULL,
                    content_type VARCHAR(128) NOT NULL,
                    size_bytes BIGINT NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    group_id INT NOT NULL,
                    user_id INT NOT NULL,
                    body TEXT NULL,
                    attachment_id INT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (group_id) REFERENCES chat_groups(id) ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (attachment_id) REFERENCES attachments(id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS password_reset_tokens (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    token_hash VARCHAR(128) NOT NULL UNIQUE,
                    expires_at DATETIME NOT NULL,
                    used_at DATETIME NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

@app.before_request
def prepare():
    ensure_bucket()
    init_db()


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, username, email FROM users WHERE id=%s", (user_id,))
            return cur.fetchone()


@app.context_processor
def inject_user():
    return {"current_user": current_user()}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def is_group_member(group_id, user_id):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM group_members WHERE group_id=%s AND user_id=%s",
                (group_id, user_id),
            )
            return cur.fetchone() is not None

def is_group_owner(group_id, user_id):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM group_members
                WHERE group_id=%s
                  AND user_id=%s
                  AND role='owner'
                """,
                (group_id, user_id),
            )
            return cur.fetchone() is not None


def allowed_file(filename):
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_EXTENSIONS

def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def send_password_reset_email(to_email, reset_url):
    mail_server = os.environ["MAIL_SERVER"]
    mail_port = int(os.environ.get("MAIL_PORT", "587"))
    mail_use_tls = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
    mail_username = os.environ["MAIL_USERNAME"]
    mail_password = os.environ["MAIL_PASSWORD"]
    mail_default_sender = os.environ["MAIL_DEFAULT_SENDER"]

    subject = "Simple Chat パスワードリセット"
    body = f"""Simple Chat のパスワードリセットを受け付けました。

以下のURLを開いて、新しいパスワードを設定してください。

{reset_url}

このURLの有効期限は1時間です。
心当たりがない場合は、このメールを無視してください。
"""

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = mail_default_sender
    msg["To"] = to_email
    msg.set_content(body)

    with smtplib.SMTP(mail_server, mail_port, timeout=20) as smtp:
        if mail_use_tls:
            smtp.starttls()
        smtp.login(mail_username, mail_password)
        smtp.send_message(msg)

@app.route("/")
def index():
    if session.get("user_id"):
        return redirect(url_for("groups"))
    return redirect(url_for("login"))


@app.route("/health")
def health():
    return {"status": "ok"}


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not username or not email or not password:
            flash("すべての項目を入力してください。")
            return redirect(url_for("register"))

        password_hash = generate_password_hash(password)

        try:
            with db_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO users (username, email, password_hash)
                        VALUES (%s, %s, %s)
                        """,
                        (username, email, password_hash),
                    )
            flash("ユーザー登録が完了しました。ログインしてください。")
            return redirect(url_for("login"))
        except pymysql.err.IntegrityError:
            flash("そのユーザー名またはメールアドレスは既に使われています。")
            return redirect(url_for("register"))

    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE username=%s", (username,))
                user = cur.fetchone()

        if not user or not check_password_hash(user["password_hash"], password):
            flash("ユーザーIDまたはパスワードが違います。")
            return redirect(url_for("login"))

        session["user_id"] = user["id"]
        flash("ログインしました。")
        return redirect(url_for("groups"))

    return render_template("login.html")

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        # セキュリティ上、存在しないメールでも同じメッセージを返す
        generic_message = "入力されたメールアドレス宛に、パスワードリセット手順を送信しました。"

        if not email:
            flash("メールアドレスを入力してください。")
            return redirect(url_for("forgot_password"))

        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, email FROM users WHERE email=%s", (email,))
                user = cur.fetchone()

                if user:
                    token = secrets.token_urlsafe(32)
                    token_hash = hash_token(token)
                    expires_at = datetime.now() + timedelta(hours=1)

                    cur.execute(
                        """
                        INSERT INTO password_reset_tokens
                        (user_id, token_hash, expires_at)
                        VALUES (%s, %s, %s)
                        """,
                        (user["id"], token_hash, expires_at),
                    )

                    reset_url = url_for("reset_password", token=token, _external=True)

                    reset_url = url_for(
                        "reset_password",
                        token=token,
                        _external=True,
                        _scheme="https",
                    )

                    try:
                        send_password_reset_email(user["email"], reset_url)
                    except Exception as e:
                        app.logger.exception("Failed to send password reset email")
                        flash(f"メール送信に失敗しました: {e}")
                        return redirect(url_for("forgot_password"))

        flash(generic_message)
        return redirect(url_for("login"))

    return render_template("forgot_password.html")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    token_hash = hash_token(token)

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT prt.*, u.email
                FROM password_reset_tokens prt
                JOIN users u ON u.id = prt.user_id
                WHERE prt.token_hash=%s
                  AND prt.used_at IS NULL
                """,
                (token_hash,),
            )
            reset_token = cur.fetchone()

    if not reset_token:
        flash("パスワードリセットURLが無効です。")
        return redirect(url_for("forgot_password"))

    if reset_token["expires_at"] < datetime.now():
        flash("パスワードリセットURLの有効期限が切れています。")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")

        if not password or not password_confirm:
            flash("新しいパスワードを入力してください。")
            return redirect(url_for("reset_password", token=token))

        if password != password_confirm:
            flash("パスワードが一致しません。")
            return redirect(url_for("reset_password", token=token))

        password_hash = generate_password_hash(password)

        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET password_hash=%s WHERE id=%s",
                    (password_hash, reset_token["user_id"]),
                )

                cur.execute(
                    """
                    UPDATE password_reset_tokens
                    SET used_at=%s
                    WHERE id=%s
                    """,
                    (datetime.now(), reset_token["id"]),
                )

        flash("パスワードを変更しました。新しいパスワードでログインしてください。")
        return redirect(url_for("login"))

    return render_template("reset_password.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("ログアウトしました。")
    return redirect(url_for("login"))


@app.route("/groups")
@login_required
def groups():
    user_id = session["user_id"]

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT g.*
                FROM chat_groups g
                JOIN group_members gm ON gm.group_id = g.id
                WHERE gm.user_id=%s
                ORDER BY g.created_at DESC
                """,
                (user_id,),
            )
            my_groups = cur.fetchall()

    return render_template("groups.html", groups=my_groups)


@app.route("/groups/new", methods=["GET", "POST"])
@login_required
def new_group():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        user_id = session["user_id"]

        if not name:
            flash("グループ名を入力してください。")
            return redirect(url_for("new_group"))

        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chat_groups (name, description, owner_user_id)
                    VALUES (%s, %s, %s)
                    """,
                    (name, description, user_id),
                )
                group_id = cur.lastrowid

                cur.execute(
                    """
                    INSERT INTO group_members (group_id, user_id, role)
                    VALUES (%s, %s, 'owner')
                    """,
                    (group_id, user_id),
                )

        flash("グループを作成しました。")
        return redirect(url_for("group_chat", group_id=group_id))

    return render_template("new_group.html")


@app.route("/groups/<int:group_id>")
@login_required
def group_chat(group_id):
    user_id = session["user_id"]

    if not is_group_member(group_id, user_id):
        abort(403)

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM chat_groups WHERE id=%s", (group_id,))
            group = cur.fetchone()

            cur.execute(
                """
                SELECT
                    m.id,
                    m.user_id,
                    m.body,
                    m.created_at,
                    u.username,
                    a.id AS attachment_id,
                    a.original_filename,
                    a.content_type
                FROM messages m
                JOIN users u ON u.id = m.user_id
                LEFT JOIN attachments a ON a.id = m.attachment_id
                WHERE m.group_id=%s
                ORDER BY m.created_at ASC
                """,
                (group_id,),
            )
            messages = cur.fetchall()

            cur.execute(
                """
                SELECT u.id, u.username, gm.role
                FROM group_members gm
                JOIN users u ON u.id = gm.user_id
                WHERE gm.group_id=%s
                ORDER BY gm.joined_at ASC
                """,
                (group_id,),
            )
            members = cur.fetchall()

    owner = is_group_owner(group_id, user_id)

    return render_template(
        "chat.html",
        group=group,
        messages=messages,
        members=members,
    )

@app.route("/api/groups/<int:group_id>/messages")
@login_required
def api_group_messages(group_id):
    user_id = session["user_id"]

    if not is_group_member(group_id, user_id):
        abort(403)

    after_id = request.args.get("after_id", "0")
    try:
        after_id = int(after_id)
    except ValueError:
        after_id = 0

    owner = is_group_owner(group_id, user_id)

    with db_conn() as conn:
        with conn.cursor() as cur:
            # 現在DB上に存在するメッセージID一覧
            cur.execute(
                """
                SELECT id
                FROM messages
                WHERE group_id=%s
                ORDER BY id ASC
                """,
                (group_id,),
            )
            active_ids = [row["id"] for row in cur.fetchall()]

            # 新着メッセージ取得
            cur.execute(
                """
                SELECT
                    m.id,
                    m.user_id,
                    m.body,
                    DATE_FORMAT(m.created_at, '%%Y-%%m-%%d %%H:%%i:%%s') AS created_at,
                    u.username,
                    a.id AS attachment_id,
                    a.original_filename,
                    a.content_type
                FROM messages m
                JOIN users u ON u.id = m.user_id
                LEFT JOIN attachments a ON a.id = m.attachment_id
                WHERE m.group_id=%s
                  AND m.id > %s
                ORDER BY m.created_at ASC
                """,
                (group_id, after_id),
            )
            rows = cur.fetchall()

    messages = []
    for row in rows:
        messages.append({
            "id": row["id"],
            "user_id": row["user_id"],
            "username": row["username"],
            "body": row["body"],
            "created_at": row["created_at"],
            "attachment_id": row["attachment_id"],
            "attachment_url": url_for("view_attachment", attachment_id=row["attachment_id"]) if row["attachment_id"] else None,
            "original_filename": row["original_filename"],
            "can_delete": row["user_id"] == user_id or owner,
        })

    return {
        "messages": messages,
        "active_ids": active_ids,
    }


@app.route("/groups/<int:group_id>/members")
@login_required
def group_members(group_id):
    user_id = session["user_id"]

    if not is_group_member(group_id, user_id):
        abort(403)

    owner = is_group_owner(group_id, user_id)

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM chat_groups WHERE id=%s", (group_id,))
            group = cur.fetchone()

            if not group:
                abort(404)

            cur.execute(
                """
                SELECT u.id, u.username, u.email, gm.role, gm.joined_at
                FROM group_members gm
                JOIN users u ON u.id = gm.user_id
                WHERE gm.group_id=%s
                ORDER BY
                  CASE gm.role WHEN 'owner' THEN 0 ELSE 1 END,
                  gm.joined_at ASC
                """,
                (group_id,),
            )
            members = cur.fetchall()

            cur.execute(
                """
                SELECT id, username, email
                FROM users
                WHERE id NOT IN (
                    SELECT user_id
                    FROM group_members
                    WHERE group_id=%s
                )
                ORDER BY username ASC
                """,
                (group_id,),
            )
            candidates = cur.fetchall()

    return render_template(
        "members.html",
        group=group,
        members=members,
        candidates=candidates,
        owner=owner,
    )


@app.route("/groups/<int:group_id>/members/add", methods=["POST"])
@login_required
def add_group_member(group_id):
    user_id = session["user_id"]

    if not is_group_owner(group_id, user_id):
        abort(403)

    new_user_id = request.form.get("user_id")

    if not new_user_id:
        flash("追加するユーザーを選択してください。")
        return redirect(url_for("group_members", group_id=group_id))

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM chat_groups WHERE id=%s", (group_id,))
            group = cur.fetchone()

            if not group:
                abort(404)

            cur.execute("SELECT id FROM users WHERE id=%s", (new_user_id,))
            user = cur.fetchone()

            if not user:
                flash("指定されたユーザーが存在しません。")
                return redirect(url_for("group_members", group_id=group_id))

            try:
                cur.execute(
                    """
                    INSERT INTO group_members (group_id, user_id, role)
                    VALUES (%s, %s, 'member')
                    """,
                    (group_id, new_user_id),
                )
                flash("メンバーを追加しました。")
            except pymysql.err.IntegrityError:
                flash("そのユーザーは既にメンバーです。")

    return redirect(url_for("group_members", group_id=group_id))

@app.route("/groups/<int:group_id>/members/<int:target_user_id>/remove", methods=["POST"])
@login_required
def remove_group_member(group_id, target_user_id):
    user_id = session["user_id"]

    # owner だけが他メンバーを削除可能
    if not is_group_owner(group_id, user_id):
        abort(403)

    # owner 自身は削除不可
    if target_user_id == user_id:
        flash("owner自身は削除できません。")
        return redirect(url_for("group_members", group_id=group_id))

    with db_conn() as conn:
        with conn.cursor() as cur:
            # 対象メンバー確認
            cur.execute(
                """
                SELECT gm.id, gm.role, u.username
                FROM group_members gm
                JOIN users u ON u.id = gm.user_id
                WHERE gm.group_id=%s
                  AND gm.user_id=%s
                """,
                (group_id, target_user_id),
            )
            target = cur.fetchone()

            if not target:
                flash("指定されたメンバーは存在しません。")
                return redirect(url_for("group_members", group_id=group_id))

            # owner は削除不可
            if target["role"] == "owner":
                flash("ownerは削除できません。")
                return redirect(url_for("group_members", group_id=group_id))

            cur.execute(
                """
                DELETE FROM group_members
                WHERE group_id=%s
                  AND user_id=%s
                """,
                (group_id, target_user_id),
            )

    flash("メンバーを削除しました。")
    return redirect(url_for("group_members", group_id=group_id))


@app.route("/groups/<int:group_id>/leave", methods=["POST"])
@login_required
def leave_group(group_id):
    user_id = session["user_id"]

    if not is_group_member(group_id, user_id):
        abort(403)

    # owner は退出不可
    if is_group_owner(group_id, user_id):
        flash("ownerはグループから退出できません。先にowner移譲機能を実装してください。")
        return redirect(url_for("group_chat", group_id=group_id))

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM group_members
                WHERE group_id=%s
                  AND user_id=%s
                """,
                (group_id, user_id),
            )

    flash("グループから退出しました。")
    return redirect(url_for("groups"))

@app.route("/groups/<int:group_id>/send", methods=["POST"])
@login_required
def send_message(group_id):
    user_id = session["user_id"]

    if not is_group_member(group_id, user_id):
        abort(403)

    body = request.form.get("body", "").strip()
    file = request.files.get("image")
    attachment_id = None

    if file and file.filename:
        if not allowed_file(file.filename):
            flash("許可されていない画像形式です。")
            return redirect(url_for("group_chat", group_id=group_id))

        original_filename = secure_filename(file.filename)
        content_type = file.content_type or "application/octet-stream"
        data = file.read()
        size_bytes = len(data)

        ext = original_filename.rsplit(".", 1)[1].lower()
        object_key = f"groups/{group_id}/{uuid.uuid4().hex}.{ext}"

        client = minio_client()
        bucket = os.environ["MINIO_BUCKET"]
        client.put_object(
            bucket,
            object_key,
            BytesIO(data),
            length=size_bytes,
            content_type=content_type,
        )

        with db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO attachments
                    (user_id, object_key, original_filename, content_type, size_bytes)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (user_id, object_key, original_filename, content_type, size_bytes),
                )
                attachment_id = cur.lastrowid

    if not body and not attachment_id:
        flash("メッセージまたは画像を入力してください。")
        return redirect(url_for("group_chat", group_id=group_id))

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO messages (group_id, user_id, body, attachment_id)
                VALUES (%s, %s, %s, %s)
                """,
                (group_id, user_id, body, attachment_id),
            )

    return redirect(url_for("group_chat", group_id=group_id))

@app.route("/messages/<int:message_id>/delete", methods=["POST"])
@login_required
def delete_message(message_id):
    user_id = session["user_id"]

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    m.id,
                    m.group_id,
                    m.user_id,
                    m.attachment_id,
                    a.object_key
                FROM messages m
                LEFT JOIN attachments a ON a.id = m.attachment_id
                WHERE m.id=%s
                """,
                (message_id,),
            )
            message = cur.fetchone()

            if not message:
                abort(404)

            group_id = message["group_id"]

            if not is_group_member(group_id, user_id):
                abort(403)

            can_delete = (
                message["user_id"] == user_id
                or is_group_owner(group_id, user_id)
            )

            if not can_delete:
                abort(403)

            attachment_id = message["attachment_id"]
            object_key = message["object_key"]

            # 先に messages を削除
            cur.execute("DELETE FROM messages WHERE id=%s", (message_id,))

            # 添付があれば MinIO object と attachments レコードも削除
            if attachment_id:
                if object_key:
                    try:
                        client = minio_client()
                        bucket = os.environ["MINIO_BUCKET"]
                        client.remove_object(bucket, object_key)
                    except S3Error:
                        # DB削除を優先し、MinIO側の削除失敗は致命扱いしない
                        pass

                cur.execute("DELETE FROM attachments WHERE id=%s", (attachment_id,))

    flash("メッセージを削除しました。")
    return redirect(url_for("group_chat", group_id=group_id))

@app.route("/attachments/<int:attachment_id>")
@login_required
def view_attachment(attachment_id):
    user_id = session["user_id"]

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.*, m.group_id
                FROM attachments a
                JOIN messages m ON m.attachment_id = a.id
                WHERE a.id=%s
                """,
                (attachment_id,),
            )
            attachment = cur.fetchone()

    if not attachment:
        abort(404)

    if not is_group_member(attachment["group_id"], user_id):
        abort(403)

    client = minio_client()
    bucket = os.environ["MINIO_BUCKET"]

    try:
        response = client.get_object(bucket, attachment["object_key"])
        data = response.read()
        response.close()
        response.release_conn()
    except S3Error:
        abort(404)

    return send_file(
        BytesIO(data),
        mimetype=attachment["content_type"],
        download_name=attachment["original_filename"],
    )
