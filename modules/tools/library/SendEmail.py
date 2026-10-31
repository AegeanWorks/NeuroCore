import smtplib
import ssl
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- Configuration ---
# Ensure these environment variables are set, or configure them here.
# For Gmail, use an App Password if 2FA is enabled.
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SENDER_EMAIL = os.getenv("SMTP_EMAIL")
SENDER_PASSWORD = os.getenv("SMTP_PASSWORD")

# Arguments provided by the LLM via the 'args' dictionary
to_email = args.get("to_email")
subject = args.get("subject")
body = args.get("body")

if not to_email or not subject or not body:
    result = "Error: Missing required arguments: to_email, subject, or body."
elif not SENDER_EMAIL or not SENDER_PASSWORD:
    result = "Error: SMTP credentials not configured. Please set SMTP_EMAIL and SMTP_PASSWORD environment variables."
else:
    try:
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        # Create secure SSL context with certificate verification
        context = ssl.create_default_context()
        
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls(context=context)
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.send_message(msg)
        
        result = f"Success: Email sent to {to_email}"
    except Exception as e:
        result = f"Error sending email: {str(e)}"
