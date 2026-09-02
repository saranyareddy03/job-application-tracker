from dotenv import load_dotenv
import os

load_dotenv()


class Config:

    db_host = os.getenv("DB_HOST") or os.getenv("MYSQLHOST", "localhost")
    db_port = int(os.getenv("DB_PORT") or os.getenv("MYSQLPORT", 3306))
    db_user = os.getenv("DB_USER") or os.getenv("MYSQLUSER", "root")
    db_password = os.getenv("DB_PASSWORD") or os.getenv("MYSQLPASSWORD", "")
    db_name = os.getenv("DB_NAME") or os.getenv("MYSQLDATABASE", "job_tracker")