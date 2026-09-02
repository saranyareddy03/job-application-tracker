# JobTrack - Job Application Tracker

## Features
- User registration and login
- Forgot password with a 6-digit email OTP
- Password reset after OTP verification
- Profile page shows account details without displaying the password
- Job application tracking
- Optional resume attachment per application (PDF/DOC/DOCX, max 5 MB)
- Resume download from application details
- Interview scheduler linked to saved applications
- Dashboard calendar with month navigation and highlighted interview dates
- Automatic Gmail interview reminders approximately 1 day and 1 hour before interviews

## Setup

1. Create the MySQL database/tables:
   - Open MySQL.
   - Run `database.sql`.

2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and enter your MySQL details.

4. For Gmail:
   - Use a Gmail account that has 2-Step Verification enabled.
   - Create a Gmail App Password.
   - Put the App Password in `EMAIL_APP_PASSWORD`.
   - Put the Gmail address in `FROM_EMAIL`.
   - Do not use the normal Gmail account password.

5. Start the application:
   ```bash
   python app.py
   ```

The application runs on port 5001.

## Important
The ZIP intentionally does not contain `.env` or `venv/`. Add your own `.env` locally so passwords and Gmail credentials are not exposed.

The application automatically checks for the newer OTP/reminder database columns when `app.py` starts and adds them if they are missing.
