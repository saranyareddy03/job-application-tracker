from flask import Flask, render_template, url_for, redirect, request, session, send_from_directory
import mysql.connector as sql
from datetime import datetime, timedelta, date, time as dt_time
import calendar
from email.message import EmailMessage
import os
import smtplib
import threading
import time
import secrets
from uuid import uuid4
from werkzeug.utils import secure_filename
from flask_bcrypt import Bcrypt
from config import Config


# =========================================================
# FLASK CONFIGURATION
# =========================================================

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret-key")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

bcrypt = Bcrypt(app)
DBConfig = Config()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
RESUME_UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads", "resumes")
os.makedirs(RESUME_UPLOAD_FOLDER, exist_ok=True)

ALLOWED_RESUME_EXTENSIONS = {"pdf", "doc", "docx"}


# =========================================================
# DATABASE
# =========================================================

def getConnectionWithDB():
    try:
        return sql.connect(
            host=DBConfig.db_host,
            port=int(DBConfig.db_port or 3306),
            user=DBConfig.db_user,
            password=DBConfig.db_password,
            database=DBConfig.db_name
        )
    except sql.Error as e:
        print("Database Connection Error:", e)
        return None


def ensureDatabaseColumns():
    """Safely add columns needed by newer versions of JobTrack."""
    connection = getConnectionWithDB()
    if connection is None:
        return

    try:
        cursor = connection.cursor()
        migrations = {
            "interviews": {
                "round_number": "VARCHAR(30) NULL",
                "location": "VARCHAR(255) NULL",
                "meeting_link": "VARCHAR(500) NULL",
                "reminder_day_sent": "BOOLEAN DEFAULT FALSE",
                "reminder_hour_sent": "BOOLEAN DEFAULT FALSE",
            },
            "users": {
                "reset_otp_hash": "VARCHAR(255) NULL",
                "reset_otp_expires_at": "DATETIME NULL",
            },
            "applications": {
                "resume_file": "VARCHAR(255) NULL",
            }
        }

        for table, columns in migrations.items():
            for column, definition in columns.items():
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = %s
                      AND TABLE_NAME = %s
                      AND COLUMN_NAME = %s
                    """,
                    (DBConfig.db_name, table, column)
                )
                if cursor.fetchone()[0] == 0:
                    cursor.execute(
                        f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}"
                    )

        connection.commit()
        cursor.close()
        connection.close()
    except sql.Error as e:
        print("Database Migration Error:", e)
        try:
            connection.rollback()
            connection.close()
        except Exception:
            pass


# =========================================================
# COMMON HELPERS
# =========================================================

def get_user(user_id):
    return readUserRecordById({"id": user_id})


def allowed_resume(filename):
    return (
        bool(filename)
        and "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_RESUME_EXTENSIONS
    )


def save_resume(file):
    if not file or not file.filename:
        return None, None

    if not allowed_resume(file.filename):
        return None, "Only PDF, DOC, and DOCX resume files are allowed."

    extension = secure_filename(file.filename).rsplit(".", 1)[1].lower()
    stored_name = f"{uuid4().hex}.{extension}"

    try:
        file.save(os.path.join(RESUME_UPLOAD_FOLDER, stored_name))
        return stored_name, None
    except OSError as e:
        print("Resume Save Error:", e)
        return None, "The resume could not be saved."


def delete_resume_file(filename):
    if not filename:
        return
    safe_name = os.path.basename(str(filename))
    path = os.path.join(RESUME_UPLOAD_FOLDER, safe_name)
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError as e:
        print("Resume Delete Error:", e)


def normalize_interview_time(value):
    """
    mysql-connector may return TIME as datetime.timedelta.
    datetime.combine requires datetime.time.
    """
    if value is None:
        return None

    if isinstance(value, dt_time):
        return value

    if isinstance(value, timedelta):
        total_seconds = int(value.total_seconds()) % (24 * 60 * 60)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return dt_time(hours, minutes, seconds)

    if isinstance(value, datetime):
        return value.time()

    if isinstance(value, str):
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                return datetime.strptime(value, fmt).time()
            except ValueError:
                continue

    return None


def combine_interview_datetime(interview_date, interview_time):
    if isinstance(interview_date, datetime):
        interview_date = interview_date.date()

    normalized_time = normalize_interview_time(interview_time)
    if interview_date is None or normalized_time is None:
        return None

    return datetime.combine(interview_date, normalized_time)


# =========================================================
# USER FUNCTIONS
# =========================================================

def insertUserRecord(user_data):
    connection = getConnectionWithDB()
    if connection is None:
        return False

    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO users (name, email, password_hash, is_verified)
            VALUES (%s, %s, %s, %s)
            """,
            (
                user_data["name"],
                user_data["email"],
                user_data["password_hash"],
                False
            )
        )
        connection.commit()
        cursor.close()
        connection.close()
        return True
    except sql.Error as e:
        print("Insert User Error:", e)
        connection.rollback()
        cursor.close()
        connection.close()
        return False


def readUserRecordByEmail(user_data):
    connection = getConnectionWithDB()
    if connection is None:
        return False

    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, name, email, password_hash, is_verified, created_at,
                   reset_otp_hash, reset_otp_expires_at
            FROM users
            WHERE email = %s
            """,
            (user_data["email"],)
        )
        data = cursor.fetchone()
        cursor.close()
        connection.close()
        return data
    except sql.Error as e:
        print("Read User Error:", e)
        cursor.close()
        connection.close()
        return False


def readUserRecordById(user_data):
    connection = getConnectionWithDB()
    if connection is None:
        return False

    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, name, email, password_hash, is_verified, created_at,
                   reset_otp_hash, reset_otp_expires_at
            FROM users
            WHERE id = %s
            """,
            (user_data["id"],)
        )
        data = cursor.fetchone()
        cursor.close()
        connection.close()
        return data
    except sql.Error as e:
        print("Read User Error:", e)
        cursor.close()
        connection.close()
        return False


def updateNameByIdorEmail(user_data):
    connection = getConnectionWithDB()
    if connection is None:
        return False

    try:
        cursor = connection.cursor()
        if "id" in user_data:
            cursor.execute(
                "UPDATE users SET name = %s WHERE id = %s",
                (user_data["new_name"], user_data["id"])
            )
        else:
            cursor.execute(
                "UPDATE users SET name = %s WHERE email = %s",
                (user_data["new_name"], user_data["email"])
            )
        connection.commit()
        cursor.close()
        connection.close()
        return True
    except sql.Error as e:
        print("Update Name Error:", e)
        connection.rollback()
        cursor.close()
        connection.close()
        return False


def updatePasswordByIdorEmail(user_data):
    connection = getConnectionWithDB()
    if connection is None:
        return False

    try:
        cursor = connection.cursor()
        if "id" in user_data:
            cursor.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s",
                (user_data["new_password"], user_data["id"])
            )
        else:
            cursor.execute(
                "UPDATE users SET password_hash = %s WHERE email = %s",
                (user_data["new_password"], user_data["email"])
            )
        connection.commit()
        cursor.close()
        connection.close()
        return True
    except sql.Error as e:
        print("Update Password Error:", e)
        connection.rollback()
        cursor.close()
        connection.close()
        return False


def store_reset_otp(email, otp_hash, expires_at):
    connection = getConnectionWithDB()
    if connection is None:
        return False
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE users
            SET reset_otp_hash = %s, reset_otp_expires_at = %s
            WHERE email = %s
            """,
            (otp_hash, expires_at, email)
        )
        connection.commit()
        updated = cursor.rowcount > 0
        cursor.close()
        connection.close()
        return updated
    except sql.Error as e:
        print("Store OTP Error:", e)
        connection.rollback()
        cursor.close()
        connection.close()
        return False


def clear_reset_otp(email):
    connection = getConnectionWithDB()
    if connection is None:
        return False
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE users
            SET reset_otp_hash = NULL, reset_otp_expires_at = NULL
            WHERE email = %s
            """,
            (email,)
        )
        connection.commit()
        cursor.close()
        connection.close()
        return True
    except sql.Error as e:
        print("Clear OTP Error:", e)
        connection.rollback()
        cursor.close()
        connection.close()
        return False


# =========================================================
# EMAIL
# =========================================================

def send_email(to_email, subject, body):
    from_email = os.getenv("FROM_EMAIL", "").strip()
    app_password = os.getenv("EMAIL_APP_PASSWORD", "").strip()

    if not from_email or not app_password or not to_email:
        print("Email Error: FROM_EMAIL, EMAIL_APP_PASSWORD, and recipient are required.")
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_email
    message["To"] = to_email
    message.set_content(body)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as server:
            server.login(from_email, app_password)
            server.send_message(message)
        return True
    except Exception as e:
        print("Gmail Error:", e)
        return False


def sendInterviewReminder(to_email, company, role, interview, reminder_label):
    when = str(interview.get("interview_date"))
    normalized_time = normalize_interview_time(interview.get("interview_time"))
    if normalized_time:
        when += f" at {normalized_time.strftime('%I:%M %p')}"

    round_text = interview.get("round_number") or interview.get("interview_round") or "Interview"
    if interview.get("round_number") and interview.get("interview_round"):
        round_text += f" · {interview['interview_round']}"

    location = interview.get("location") or interview.get("interview_type") or "Not specified"

    body = f"""Hi,

This is a JobTrack reminder for your upcoming interview.

Company: {company}
Role: {role}
Round: {round_text}
Date & time: {when}
Location / type: {location}
"""

    if interview.get("meeting_link"):
        body += f"Meeting link: {interview['meeting_link']}\n"

    if interview.get("notes"):
        body += f"Notes: {interview['notes']}\n"

    body += "\nGood luck with your interview!\n\n— JobTrack"

    return send_email(
        to_email,
        f"JobTrack reminder: {company} interview in {reminder_label}",
        body
    )


# =========================================================
# APPLICATION FUNCTIONS
# =========================================================

def insertApplicationRecord(application_data):
    connection = getConnectionWithDB()
    if connection is None:
        return False

    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO applications
            (user_id, company_name, job_role, location, job_type,
             applied_date, status, salary, job_url, notes, resume_file)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                application_data["user_id"],
                application_data["company_name"],
                application_data["job_role"],
                application_data["location"],
                application_data["job_type"],
                application_data["applied_date"] or None,
                application_data["status"],
                application_data["salary"],
                application_data["job_url"],
                application_data["notes"],
                application_data.get("resume_file") or None
            )
        )
        connection.commit()
        cursor.close()
        connection.close()
        return True
    except sql.Error as e:
        print("Insert Application Error:", e)
        connection.rollback()
        cursor.close()
        connection.close()
        return False


def readApplicationRecords(user_id, search="", status="", job_type=""):
    connection = getConnectionWithDB()
    if connection is None:
        return False

    try:
        cursor = connection.cursor(dictionary=True)
        query = """
            SELECT *
            FROM applications
            WHERE user_id = %s
        """
        params = [user_id]

        if search:
            query += " AND (company_name LIKE %s OR job_role LIKE %s)"
            like = f"%{search}%"
            params.extend([like, like])

        if status:
            query += " AND status = %s"
            params.append(status)

        if job_type:
            query += " AND job_type = %s"
            params.append(job_type)

        query += " ORDER BY created_at DESC"

        cursor.execute(query, tuple(params))
        records = cursor.fetchall()
        cursor.close()
        connection.close()
        return records
    except sql.Error as e:
        print("Read Applications Error:", e)
        cursor.close()
        connection.close()
        return False


def readApplicationRecordById(application_id, user_id):
    connection = getConnectionWithDB()
    if connection is None:
        return False

    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT *
            FROM applications
            WHERE id = %s AND user_id = %s
            """,
            (application_id, user_id)
        )
        record = cursor.fetchone()
        cursor.close()
        connection.close()
        return record
    except sql.Error as e:
        print("Read Application Error:", e)
        cursor.close()
        connection.close()
        return False


def updateApplicationRecord(application_data):
    connection = getConnectionWithDB()
    if connection is None:
        return False

    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE applications
            SET company_name = %s,
                job_role = %s,
                location = %s,
                job_type = %s,
                applied_date = %s,
                status = %s,
                salary = %s,
                job_url = %s,
                notes = %s,
                resume_file = %s
            WHERE id = %s AND user_id = %s
            """,
            (
                application_data["company_name"],
                application_data["job_role"],
                application_data["location"],
                application_data["job_type"],
                application_data["applied_date"] or None,
                application_data["status"],
                application_data["salary"],
                application_data["job_url"],
                application_data["notes"],
                application_data.get("resume_file") or None,
                application_data["id"],
                application_data["user_id"]
            )
        )
        connection.commit()
        cursor.close()
        connection.close()
        return True
    except sql.Error as e:
        print("Update Application Error:", e)
        connection.rollback()
        cursor.close()
        connection.close()
        return False


def deleteApplicationRecord(application_id, user_id):
    application = readApplicationRecordById(application_id, user_id)
    if not application:
        return False

    connection = getConnectionWithDB()
    if connection is None:
        return False

    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            DELETE FROM applications
            WHERE id = %s AND user_id = %s
            """,
            (application_id, user_id)
        )
        connection.commit()
        cursor.close()
        connection.close()

        delete_resume_file(application.get("resume_file"))
        return True
    except sql.Error as e:
        print("Delete Application Error:", e)
        connection.rollback()
        cursor.close()
        connection.close()
        return False


# =========================================================
# DASHBOARD
# =========================================================

def getDashboardStatistics(user_id):
    connection = getConnectionWithDB()
    if connection is None:
        return False

    try:
        cursor = connection.cursor()
        statistics = {}

        for key, condition in [
            ("total", ""),
            ("applied", " AND status = 'Applied'"),
            ("interviews", " AND status = 'Interview'"),
            ("selected", " AND status = 'Selected'"),
            ("rejected", " AND status = 'Rejected'"),
            ("withdrawn", " AND status = 'Withdrawn'")
        ]:
            cursor.execute(
                f"SELECT COUNT(*) FROM applications WHERE user_id = %s{condition}",
                (user_id,)
            )
            statistics[key] = cursor.fetchone()[0]

        cursor.close()
        connection.close()
        return statistics
    except sql.Error as e:
        print("Dashboard Statistics Error:", e)
        cursor.close()
        connection.close()
        return False


# =========================================================
# INTERVIEW FUNCTIONS
# =========================================================

def insertInterviewRecord(interview_data):
    connection = getConnectionWithDB()
    if connection is None:
        return False

    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO interviews
            (application_id, interview_date, interview_time, round_number,
             interview_round, interview_type, location, meeting_link, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                interview_data["application_id"],
                interview_data["interview_date"],
                interview_data.get("interview_time") or None,
                interview_data.get("round_number") or None,
                interview_data.get("interview_round") or None,
                interview_data.get("interview_type") or None,
                interview_data.get("location") or None,
                interview_data.get("meeting_link") or None,
                interview_data.get("notes") or None
            )
        )
        connection.commit()
        cursor.close()
        connection.close()
        return True
    except sql.Error as e:
        print("Insert Interview Error:", e)
        connection.rollback()
        cursor.close()
        connection.close()
        return False


def readInterviewRecords(user_id, upcoming_only=False):
    connection = getConnectionWithDB()
    if connection is None:
        return False

    try:
        cursor = connection.cursor(dictionary=True)
        query = """
            SELECT interviews.*, applications.company_name, applications.job_role
            FROM interviews
            INNER JOIN applications ON interviews.application_id = applications.id
            WHERE applications.user_id = %s
        """
        params = [user_id]

        if upcoming_only:
            query += """
                AND (
                    interviews.interview_date > CURDATE()
                    OR (
                        interviews.interview_date = CURDATE()
                        AND (
                            interviews.interview_time IS NULL
                            OR interviews.interview_time >= CURTIME()
                        )
                    )
                )
            """

        query += """
            ORDER BY interviews.interview_date ASC,
                     interviews.interview_time ASC,
                     interviews.id ASC
        """

        cursor.execute(query, tuple(params))
        records = cursor.fetchall()
        cursor.close()
        connection.close()
        return records
    except sql.Error as e:
        print("Read Interviews Error:", e)
        cursor.close()
        connection.close()
        return False


def getInterviewRecordsForMonth(user_id, year, month):
    connection = getConnectionWithDB()
    if connection is None:
        return []

    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT interviews.id, interviews.interview_date, interviews.interview_time,
                   interviews.round_number, interviews.interview_round,
                   applications.company_name, applications.job_role
            FROM interviews
            INNER JOIN applications ON interviews.application_id = applications.id
            WHERE applications.user_id = %s
              AND YEAR(interviews.interview_date) = %s
              AND MONTH(interviews.interview_date) = %s
            ORDER BY interviews.interview_date, interviews.interview_time
            """,
            (user_id, year, month)
        )
        rows = cursor.fetchall()
        cursor.close()
        connection.close()
        return rows
    except sql.Error as e:
        print("Calendar Error:", e)
        cursor.close()
        connection.close()
        return []


def deleteInterviewRecord(interview_id, user_id):
    connection = getConnectionWithDB()
    if connection is None:
        return False

    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            DELETE interviews
            FROM interviews
            INNER JOIN applications
              ON interviews.application_id = applications.id
            WHERE interviews.id = %s
              AND applications.user_id = %s
            """,
            (interview_id, user_id)
        )
        connection.commit()
        cursor.close()
        connection.close()
        return True
    except sql.Error as e:
        print("Delete Interview Error:", e)
        connection.rollback()
        cursor.close()
        connection.close()
        return False


# =========================================================
# REGISTER
# =========================================================

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not name or not email or not password:
            return render_template("register.html", error="All fields are required.")

        if len(password) < 6:
            return render_template("register.html", error="Password must be at least 6 characters.")

        if password != confirm_password:
            return render_template("register.html", error="Passwords do not match.")

        existing_user = readUserRecordByEmail({"email": email})

        if existing_user is False:
            return render_template("register.html", error="Database connection failed.")

        if existing_user is not None:
            return render_template("register.html", error="Email is already registered.")

        password_hash = bcrypt.generate_password_hash(password).decode("utf-8")

        if insertUserRecord({
            "name": name,
            "email": email,
            "password_hash": password_hash
        }):
            return redirect(url_for("login"))

        return render_template("register.html", error="Unable to create account.")

    return render_template("register.html")


# =========================================================
# LOGIN
# =========================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            return render_template("login.html", error="Email and password are required.")

        user = readUserRecordByEmail({"email": email})

        if user is False:
            return render_template("login.html", error="Database connection failed.")

        if user is None:
            return render_template("login.html", error="Invalid email or password.")

        try:
            password_correct = bcrypt.check_password_hash(
                user["password_hash"], password
            )
        except ValueError:
            return render_template(
                "login.html",
                error="Invalid password record. Please reset your password."
            )

        if not password_correct:
            return render_template("login.html", error="Invalid email or password.")

        session["user_id"] = user["id"]
        session["user_name"] = user["name"]
        session["user_email"] = user["email"]

        return redirect(url_for("dashboard"))

    return render_template("login.html")


# =========================================================
# FORGOT PASSWORD / OTP
# =========================================================

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        if not email:
            return render_template("forgot_password.html", error="Please enter your email.")

        user = readUserRecordByEmail({"email": email})

        # Do not reveal whether an account exists.
        if user is None or user is False:
            return render_template(
                "forgot_password.html",
                message="If an account exists for that email, an OTP has been sent."
            )

        otp = f"{secrets.randbelow(900000) + 100000:06d}"
        otp_hash = bcrypt.generate_password_hash(otp).decode("utf-8")
        expires_at = datetime.now() + timedelta(minutes=10)

        if not store_reset_otp(email, otp_hash, expires_at):
            return render_template(
                "forgot_password.html",
                error="Unable to generate the reset OTP. Please try again."
            )

        body = f"""Hi {user['name']},

Your JobTrack password reset OTP is:

{otp}

This OTP is valid for 10 minutes.

If you did not request a password reset, you can ignore this email.

— JobTrack
"""

        if not send_email(
            email,
            "JobTrack password reset OTP",
            body
        ):
            clear_reset_otp(email)
            return render_template(
                "forgot_password.html",
                error="The OTP email could not be sent. Check the Gmail settings in your .env file."
            )

        session["reset_email"] = email

        return redirect(url_for("reset_password"))

    return render_template("forgot_password.html")


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    email = session.get("reset_email")

    if not email:
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        otp = request.form.get("otp", "").strip()
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not otp or not new_password or not confirm_password:
            return render_template(
                "reset_password.html",
                email=email,
                error="All fields are required."
            )

        if len(new_password) < 6:
            return render_template(
                "reset_password.html",
                email=email,
                error="Password must be at least 6 characters."
            )

        if new_password != confirm_password:
            return render_template(
                "reset_password.html",
                email=email,
                error="Passwords do not match."
            )

        user = readUserRecordByEmail({"email": email})

        if not user or not user.get("reset_otp_hash"):
            return render_template(
                "reset_password.html",
                email=email,
                error="This OTP is invalid or has expired. Please request a new one."
            )

        expires_at = user.get("reset_otp_expires_at")
        if not expires_at or datetime.now() > expires_at:
            clear_reset_otp(email)
            return render_template(
                "reset_password.html",
                email=email,
                error="This OTP has expired. Please request a new one."
            )

        try:
            otp_correct = bcrypt.check_password_hash(
                user["reset_otp_hash"], otp
            )
        except ValueError:
            otp_correct = False

        if not otp_correct:
            return render_template(
                "reset_password.html",
                email=email,
                error="Invalid OTP. Please check the code and try again."
            )

        new_hash = bcrypt.generate_password_hash(new_password).decode("utf-8")

        if not updatePasswordByIdorEmail({
            "email": email,
            "new_password": new_hash
        }):
            return render_template(
                "reset_password.html",
                email=email,
                error="Password could not be updated. Please try again."
            )

        clear_reset_otp(email)
        session.pop("reset_email", None)

        return redirect(url_for("login", reset="success"))

    return render_template("reset_password.html", email=email)


# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# =========================================================
# DASHBOARD
# =========================================================

@app.route("/")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]
    statistics = getDashboardStatistics(user_id) or {
        "total": 0, "applied": 0, "interviews": 0,
        "selected": 0, "rejected": 0, "withdrawn": 0
    }

    applications = readApplicationRecords(user_id) or []
    upcoming_interviews = readInterviewRecords(user_id, upcoming_only=True) or []
    interviews = upcoming_interviews[:3]

    today = date.today()

    try:
        calendar_year = int(request.args.get("year", today.year))
        calendar_month = int(request.args.get("month", today.month))
        if calendar_month < 1 or calendar_month > 12:
            raise ValueError
    except (ValueError, TypeError):
        calendar_year = today.year
        calendar_month = today.month

    calendar_month_name = date(
        calendar_year, calendar_month, 1
    ).strftime("%B")

    if calendar_month == 1:
        previous_month = 12
        previous_year = calendar_year - 1
    else:
        previous_month = calendar_month - 1
        previous_year = calendar_year

    if calendar_month == 12:
        next_month = 1
        next_year = calendar_year + 1
    else:
        next_month = calendar_month + 1
        next_year = calendar_year

    first_weekday, days_in_month = calendar.monthrange(
        calendar_year, calendar_month
    )

    # Sunday-first calendar.
    sunday_offset = (first_weekday + 1) % 7
    calendar_days = (
        [None] * sunday_offset
        + list(range(1, days_in_month + 1))
    )

    calendar_interviews = getInterviewRecordsForMonth(
        user_id, calendar_year, calendar_month
    ) or []

    calendar_interview_days = sorted({
        (i["interview_date"].date() if isinstance(i["interview_date"], datetime)
         else i["interview_date"]).day
        for i in calendar_interviews
        if i.get("interview_date")
    })

    now_day = (
        today.day
        if (calendar_year, calendar_month) == (today.year, today.month)
        else 0
    )

    return render_template(
        "dashboard.html",
        statistics=statistics,
        applications=applications,
        interviews=interviews,
        calendar_year=calendar_year,
        calendar_month=calendar_month,
        calendar_month_name=calendar_month_name,
        calendar_days=calendar_days,
        calendar_interviews=calendar_interviews,
        calendar_interview_days=calendar_interview_days,
        now_day=now_day,
        previous_year=previous_year,
        previous_month=previous_month,
        next_year=next_year,
        next_month=next_month
    )


# =========================================================
# APPLICATIONS
# =========================================================

@app.route("/applications")
def applications():
    if "user_id" not in session:
        return redirect(url_for("login"))

    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()
    job_type = request.args.get("job_type", "").strip()

    records = readApplicationRecords(
        session["user_id"], search, status, job_type
    ) or []

    return render_template(
        "applications.html",
        applications=records,
        search=search,
        selected_status=status,
        selected_job_type=job_type
    )


@app.route("/applications/add", methods=["GET", "POST"])
def add_application():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        application_data = {
            "user_id": session["user_id"],
            "company_name": request.form.get("company_name", "").strip(),
            "job_role": request.form.get("job_role", "").strip(),
            "location": request.form.get("location", "").strip(),
            "job_type": request.form.get("job_type", ""),
            "applied_date": request.form.get("applied_date", ""),
            "status": request.form.get("status", "Applied"),
            "salary": request.form.get("salary", "").strip(),
            "job_url": request.form.get("job_url", "").strip(),
            "notes": request.form.get("notes", "").strip(),
            "resume_file": ""
        }

        if not application_data["company_name"]:
            return render_template(
                "add_application.html",
                title="Add Application",
                subtitle="Save a new job application to your tracker.",
                button_text="Save Application",
                application=application_data,
                error="Company name is required."
            )

        if not application_data["job_role"]:
            return render_template(
                "add_application.html",
                title="Add Application",
                subtitle="Save a new job application to your tracker.",
                button_text="Save Application",
                application=application_data,
                error="Job role is required."
            )

        resume_file, resume_error = save_resume(request.files.get("resume_file"))

        if resume_error:
            return render_template(
                "add_application.html",
                title="Add Application",
                subtitle="Save a new job application to your tracker.",
                button_text="Save Application",
                application=application_data,
                error=resume_error
            )

        application_data["resume_file"] = resume_file or ""

        if insertApplicationRecord(application_data):
            return redirect(url_for("applications"))

        delete_resume_file(resume_file)
        return render_template(
            "add_application.html",
            title="Add Application",
            subtitle="Save a new job application to your tracker.",
            button_text="Save Application",
            application=application_data,
            error="Application could not be saved."
        )

    return render_template(
        "add_application.html",
        title="Add Application",
        subtitle="Save a new job application to your tracker.",
        button_text="Save Application",
        application={}
    )


@app.route("/applications/view/<int:id>")
def view_application(id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    application = readApplicationRecordById(id, session["user_id"])

    if not application:
        return redirect(url_for("applications"))

    return render_template(
        "view_application.html",
        application=application
    )


@app.route("/applications/resume/<int:id>")
def download_resume(id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    application = readApplicationRecordById(id, session["user_id"])

    if not application or not application.get("resume_file"):
        return redirect(url_for("view_application", id=id))

    filename = os.path.basename(application["resume_file"])
    path = os.path.join(RESUME_UPLOAD_FOLDER, filename)

    if not os.path.isfile(path):
        return redirect(url_for("view_application", id=id))

    return send_from_directory(
        RESUME_UPLOAD_FOLDER,
        filename,
        as_attachment=True
    )


@app.route("/applications/edit/<int:id>", methods=["GET", "POST"])
def edit_application(id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    application = readApplicationRecordById(id, session["user_id"])

    if not application:
        return redirect(url_for("applications"))

    if request.method == "POST":
        old_resume = application.get("resume_file")

        application_data = {
            "id": id,
            "user_id": session["user_id"],
            "company_name": request.form.get("company_name", "").strip(),
            "job_role": request.form.get("job_role", "").strip(),
            "location": request.form.get("location", "").strip(),
            "job_type": request.form.get("job_type", ""),
            "applied_date": request.form.get("applied_date", ""),
            "status": request.form.get("status", "Applied"),
            "salary": request.form.get("salary", "").strip(),
            "job_url": request.form.get("job_url", "").strip(),
            "notes": request.form.get("notes", "").strip(),
            "resume_file": old_resume or ""
        }

        if not application_data["company_name"] or not application_data["job_role"]:
            return render_template(
                "edit_application.html",
                application=application,
                error="Company name and job role are required."
            )

        remove_resume = request.form.get("remove_resume") == "1"
        new_resume = request.files.get("resume_file")
        saved_new_resume = None

        if new_resume and new_resume.filename:
            saved_new_resume, resume_error = save_resume(new_resume)
            if resume_error:
                return render_template(
                    "edit_application.html",
                    application=application,
                    error=resume_error
                )
            application_data["resume_file"] = saved_new_resume
        elif remove_resume:
            application_data["resume_file"] = ""

        if updateApplicationRecord(application_data):
            if application_data["resume_file"] != old_resume:
                delete_resume_file(old_resume)
            return redirect(url_for("applications"))

        delete_resume_file(saved_new_resume)
        return render_template(
            "edit_application.html",
            application=application,
            error="Application could not be updated."
        )

    return render_template(
        "edit_application.html",
        application=application
    )


@app.route("/applications/delete/<int:id>", methods=["POST"])
def delete_application(id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    deleteApplicationRecord(id, session["user_id"])
    return redirect(url_for("applications"))


# =========================================================
# INTERVIEWS
# =========================================================

@app.route("/interviews", methods=["GET", "POST"])
def interviews():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]
    applications = readApplicationRecords(user_id) or []

    if request.method == "POST":
        application_id = request.form.get("application_id", type=int)
        application = (
            readApplicationRecordById(application_id, user_id)
            if application_id else None
        )

        if not application:
            return render_template(
                "interviews.html",
                applications=applications,
                interviews=readInterviewRecords(user_id) or [],
                error="Please select a valid application."
            )

        interview_date = request.form.get("interview_date", "").strip()
        interview_time = request.form.get("interview_time", "").strip()

        if not interview_date:
            return render_template(
                "interviews.html",
                applications=applications,
                interviews=readInterviewRecords(user_id) or [],
                error="Interview date is required."
            )

        if not interview_time:
            return render_template(
                "interviews.html",
                applications=applications,
                interviews=readInterviewRecords(user_id) or [],
                error="Interview time is required."
            )

        interview_data = {
            "application_id": application_id,
            "interview_date": interview_date,
            "interview_time": interview_time,
            "round_number": request.form.get("round_number", "").strip(),
            "interview_round": request.form.get("interview_round", "").strip(),
            "interview_type": request.form.get("interview_type", "").strip(),
            "location": request.form.get("location", "").strip(),
            "meeting_link": request.form.get("meeting_link", "").strip(),
            "notes": request.form.get("notes", "").strip()
        }

        if insertInterviewRecord(interview_data):
            return redirect(url_for("interviews"))

        return render_template(
            "interviews.html",
            applications=applications,
            interviews=readInterviewRecords(user_id) or [],
            error="Interview could not be saved. Check your database columns."
        )

    records = readInterviewRecords(user_id) or []
    selected_application = request.args.get("application_id", type=int)

    return render_template(
        "interviews.html",
        applications=applications,
        interviews=records,
        selected_application=selected_application
    )


@app.route("/interviews/add/<int:application_id>", methods=["GET", "POST"])
def add_interview(application_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    return redirect(url_for("interviews", application_id=application_id))


@app.route("/interviews/delete/<int:id>", methods=["POST"])
def delete_interview(id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    deleteInterviewRecord(id, session["user_id"])
    return redirect(url_for("interviews"))


# =========================================================
# PROFILE
# =========================================================

@app.route("/profile", methods=["GET", "POST"])
def profile():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user = get_user(session["user_id"])

    if request.method == "POST":
        new_name = request.form.get("name", "").strip()

        if not new_name:
            return render_template(
                "profile.html",
                user=user,
                error="Name cannot be empty."
            )

        if updateNameByIdorEmail({
            "id": session["user_id"],
            "new_name": new_name
        }):
            session["user_name"] = new_name
            user = get_user(session["user_id"])

    return render_template("profile.html", user=user)


# =========================================================
# LEGACY CHANGE PASSWORD ROUTE
# Redirects users to the secure email OTP flow.
# =========================================================

@app.route("/profile/change-password", methods=["GET", "POST"])
def change_password():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user = get_user(session["user_id"])
    if user:
        session["reset_email"] = user["email"]

    return redirect(url_for("reset_password"))


# =========================================================
# REMINDER WORKER
# =========================================================

def processInterviewReminders():
    connection = getConnectionWithDB()
    if connection is None:
        return

    try:
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT interviews.*, applications.company_name, applications.job_role,
                   users.email
            FROM interviews
            INNER JOIN applications
                ON interviews.application_id = applications.id
            INNER JOIN users
                ON applications.user_id = users.id
            WHERE interviews.interview_date >= CURDATE()
              AND interviews.interview_date <= DATE_ADD(CURDATE(), INTERVAL 2 DAY)
            """
        )

        rows = cursor.fetchall()
        now = datetime.now()

        for row in rows:
            interview_dt = combine_interview_datetime(
                row.get("interview_date"),
                row.get("interview_time")
            )

            if interview_dt is None:
                continue

            seconds_until = (interview_dt - now).total_seconds()

            field = None
            label = None

            if (
                23 * 3600 <= seconds_until <= 25 * 3600
                and not row.get("reminder_day_sent")
            ):
                field = "reminder_day_sent"
                label = "1 day"

            elif (
                45 * 60 <= seconds_until <= 75 * 60
                and not row.get("reminder_hour_sent")
            ):
                field = "reminder_hour_sent"
                label = "1 hour"

            if field:
                sent = sendInterviewReminder(
                    row["email"],
                    row["company_name"],
                    row["job_role"],
                    row,
                    label
                )

                if sent:
                    cursor.execute(
                        f"UPDATE interviews SET {field} = TRUE WHERE id = %s",
                        (row["id"],)
                    )

        connection.commit()
        cursor.close()
        connection.close()

    except Exception as e:
        print("Reminder Worker Error:", e)
        try:
            connection.rollback()
            connection.close()
        except Exception:
            pass


def reminder_worker():
    while True:
        try:
            processInterviewReminders()
        except Exception as e:
            print("Reminder Worker Error:", e)
        time.sleep(60)


# =========================================================
# ERROR HANDLER
# =========================================================

@app.errorhandler(413)
def file_too_large(error):
    if "user_id" in session:
        return render_template(
            "add_application.html",
            title="Add Application",
            subtitle="Save a new job application to your tracker.",
            button_text="Save Application",
            application={},
            error="Resume file is too large. Maximum size is 5 MB."
        ), 413
    return "File too large. Maximum size is 5 MB.", 413


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    ensureDatabaseColumns()

    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        threading.Thread(
            target=reminder_worker,
            daemon=True
        ).start()

    app.run(
        debug=True,
        host="0.0.0.0",
        port=5001
    )
