from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from dotenv import load_dotenv
import requests
from functools import wraps
import uuid
import os
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import json
from datetime import datetime, timedelta
from io import BytesIO
import random 

# Load environment variables from .env file
load_dotenv()

# --- Flask App Initialization ---
app = Flask(__name__)
app.secret_key = 'your_super_secret_and_long_key'

# --- Backendless Configuration ---
BACKENDLESS_APP_ID = '8D20D88A-D8C3-4B54-846A-BD5E983CAA64'
BACKENDLESS_REST_API_KEY = '323A1426-DFF1-4F5C-AE8B-79B35F891C3D'
# Base URLs for Data, Users, and Files
BACKENDLESS_DATA_URL = f'https://api.backendless.com/{BACKENDLESS_APP_ID}/{BACKENDLESS_REST_API_KEY}/data'
BACKENDLESS_USERS_URL = f'https://api.backendless.com/{BACKENDLESS_APP_ID}/{BACKENDLESS_REST_API_KEY}/users'

# Table Names
BACKENDLESS_JOB_TABLE = 'JobPostings'
BACKENDLESS_EXAM_TABLE = 'Exams'
BACKENDLESS_QUESTION_TABLE = 'Questions'
BACKENDLESS_APPLICATIONS_TABLE = 'StudentApplications'
BACKENDLESS_LOGIN_TABLE = 'exam_login'
BACKENDLESS_RESULTS_TABLE = 'ExamResults'
BACKENDLESS_TERMINATIONS_TABLE = 'ExamTerminations'

# --- Configuration Constants ---
MAX_FILE_SIZE_MB = 5
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

# --- Email Configuration (Defaults changed for SMTPS reliability) ---
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 465))
EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_PASS = os.environ.get("EMAIL_PASS")

# Hardcoded credentials (Admin)
FIXED_USERNAME = 'admin'
FIXED_PASSWORD = 'password123'

# Available Exam Subjects
EXAM_SUBJECTS = ['Aptitude', 'SQL', 'DSA', 'Grammar', 'Python', 'Networking']

# --- Mock Credential Store (Used only for legacy/exam assignment tracking) ---
MOCK_STUDENT_CREDENTIALS = {}

# --- Backendless User API Functions ---

def register_student_user(name, email, password):
    """Registers a new user in the Backendless Users table."""
    url = f"{BACKENDLESS_USERS_URL}/register"
    headers = {'Content-Type': 'application/json'}
    data = {
        'name': name,
        'email': email,
        'password': password,
        'username': email 
    }
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Registration Error: {e.response.text if e.response else e}")
        if e.response and e.response.json().get('message'):
            return {'error': e.response.json()['message']}
        return {'error': "Registration failed due to server or network error."}

def login_student_user(email, password):
    """Logs a user into the Backendless Users system."""
    url = f"{BACKENDLESS_USERS_URL}/login"
    headers = {'Content-Type': 'application/json'}
    data = {
        'login': email,
        'password': password
    }
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Login Error: {e.response.text if e.response else e}")
        return None

def logout_student_user(token):
    """Logs out a user from the Backendless Users system."""
    url = f"{BACKENDLESS_USERS_URL}/logout"
    headers = {'Content-Type': 'application/json', 'user-token': token}
    try:
        requests.get(url, headers=headers)
        return True
    except:
        return False

# --- Database Utility Functions (Initialization functions removed as requested) ---

def delete_backendless_object(table_name, object_id):
    """Utility to delete a single object (used for cleanup)."""
    url = f"{BACKENDLESS_DATA_URL}/{table_name}/{object_id}"
    headers = {'Content-Type': 'application/json'}
    try:
        requests.delete(url, headers=headers).raise_for_status()
        return True
    except requests.exceptions.RequestException:
        return False

# --- Application Data Functions ---

def save_login_credential(email, password, exam_id, status, application_id):
    """Saves generated login credential and email status to the exam_login table."""
    url = f"{BACKENDLESS_DATA_URL}/{BACKENDLESS_LOGIN_TABLE}"
    headers = {'Content-Type': 'application/json'}
    data = {
        'applicantEmail': email,
        'generatedPassword': password,
        'examId': exam_id,
        'applicationId': application_id, # Link to the job posting as well
        'sentStatus': status,
        'sentAt': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error saving login credential for {email}. Error: {e}")
        return False

def get_login_credential(email):
    """Fetches the latest login credential for a student."""
    url = f"{BACKENDLESS_DATA_URL}/{BACKENDLESS_LOGIN_TABLE}"
    where_clause = f"applicantEmail = '{email}'"
    # Get the latest entry only
    params = {'where': where_clause, 'sortBy': 'sentAt DESC', 'pageSize': 1}
    headers = {'Content-Type': 'application/json'}
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()[0] if response.json() else None
    except requests.exceptions.RequestException:
        return None

def get_all_login_credentials_for_job(application_id):
    """Fetches all login credentials associated with a job application."""
    url = f"{BACKENDLESS_DATA_URL}/{BACKENDLESS_LOGIN_TABLE}"
    where_clause = f"applicationId = '{application_id}'"
    params = {'where': where_clause}
    headers = {'Content-Type': 'application/json'}
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        # Return a dictionary mapped by email for easy lookup: {email: latest_credential}
        credentials = {}
        for cred in response.json():
            email = cred['applicantEmail']
            # Only keep the latest one if multiple exist (although usually only one per job/student)
            if email not in credentials or (credentials[email].get('sentAt', '') < cred['sentAt']):
                credentials[email] = cred
        return credentials
    except requests.exceptions.RequestException:
        print(f"Error fetching login credentials for application {application_id}")
        return {}

def get_job_application_status(email, job_id):
    """Checks if a student has already applied for a specific job and returns the application record."""
    url = f"{BACKENDLESS_DATA_URL}/{BACKENDLESS_APPLICATIONS_TABLE}"
    where_clause = f"applicantEmail = '{email}' AND applicationId = '{job_id}'"
    params = {'where': where_clause, 'pageSize': 1}
    headers = {'Content-Type': 'application/json'}
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()[0] if response.json() else None
    except requests.exceptions.RequestException:
        return None

def get_applied_applications_by_student_email(email):
    """Fetches all applications submitted by a specific student email."""
    url = f"{BACKENDLESS_DATA_URL}/{BACKENDLESS_APPLICATIONS_TABLE}"
    where_clause = f"applicantEmail = '{email}'"
    params = {'where': where_clause, 'sortBy': 'created DESC'}
    headers = {'Content-Type': 'application/json'}
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException:
        print(f"Error fetching applications for {email}")
        return []

def get_applied_students_for_job(application_id):
    """Fetches all student application records for a specific Job Posting ID."""
    url = f"{BACKENDLESS_DATA_URL}/{BACKENDLESS_APPLICATIONS_TABLE}"
    where_clause = f"applicationId = '{application_id}'"
    params = {'where': where_clause, 'sortBy': 'applied_at DESC'}
    headers = {'Content-Type': 'application/json'}
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException:
        print(f"Error fetching applied students for job {application_id}")
        return []

def get_real_exam_results(application_id):
    """
    FETCING REAL DATA: Fetches exam results from the ExamResults table based on the job application ID.
    """
    exam_id = get_exam_id_by_application_id(application_id)
    if not exam_id:
        return {}
    
    return get_real_exam_results_by_exam_id(exam_id) 

def get_real_exam_results_by_exam_id(exam_id):
    """
    FETCING REAL DATA: Fetches exam results from the ExamResults table based on the exam ID.
    """
    if not exam_id:
        return {}
    
    url = f"{BACKENDLESS_DATA_URL}/{BACKENDLESS_RESULTS_TABLE}"
    where_clause = f"examId = '{exam_id}'"
    params = {'where': where_clause}
    headers = {'Content-Type': 'application/json'}

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        results_list = response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching real exam results for exam {exam_id}. Error: {e}")
        return {}

    results = {}
    for res in results_list:
        email = res.get('applicantEmail')
        
        raw_score = float(res.get('score', 0))
        total = float(res.get('totalQuestions', 1))
        percentage = round(raw_score, 2)
        
        if email:
            results[email] = {
                'exam_id': exam_id,
                'score': raw_score,          
                'total_questions': total,      
                'percentage': percentage, 
                'submission_time': res.get('submissionTime', 'N/A')
            }
            
    return results


# --- Exam Termination Functions ---

def get_terminated_students_by_exam_id(exam_id):
    """Fetches all terminated student records for a specific Exam ID."""
    url = f"{BACKENDLESS_DATA_URL}/{BACKENDLESS_TERMINATIONS_TABLE}"
    # Only fetch active terminations
    where_clause = f"examId = '{exam_id}' AND is_active = true" 
    params = {'where': where_clause, 'sortBy': 'terminated_at DESC'}
    headers = {'Content-Type': 'application/json'}
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status() 
        return response.json()
    except requests.exceptions.RequestException as e:
        # Diagnostic print to help user debug table connection/existence
        print(f"Error fetching terminated students for exam {exam_id}")
        if e.response is not None:
             print(f"Backendless API Status: {e.response.status_code}. Response Text: {e.response.text}")
        return []

def remove_termination_status(termination_object_id):
    """
    Updates an ExamTermination record to inactive, effectively removing the block.
    """
    url = f"{BACKENDLESS_DATA_URL}/{BACKENDLESS_TERMINATIONS_TABLE}/{termination_object_id}"
    headers = {'Content-Type': 'application/json'}
    update_data = {
        'is_active': False,
        'removed_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    try:
        requests.put(url, headers=headers, json=update_data).raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error removing termination status for ID {termination_object_id}: {e}")
        return False
        
# --- Core API Functions (Exam/Job Management) ---

def save_multiple_questions(questions_list):
    """Saves a batch of questions one by one."""
    url = f"{BACKENDLESS_DATA_URL}/{BACKENDLESS_QUESTION_TABLE}"
    headers = {'Content-Type': 'application/json'}
    question_ids = []

    for i, question_data in enumerate(questions_list):
        try:
            response = requests.post(url, headers=headers, json=question_data)
            response.raise_for_status()
            question_ids.append(response.json().get('objectId'))
        except requests.exceptions.RequestException as e:
            error_details = 'N/A'
            status_code = e.response.status_code if e.response is not None else 'N/A'
            if e.response is not None:
                try:
                    error_details = e.response.json().get('message', e.response.text)
                except json.JSONDecodeError:
                    error_details = e.response.text
            print(f"Error saving Question {i+1}. Status: {status_code}. Details: {error_details}")
            flash(f"Critical DB Error: Failed to save Question {i+1}. Check console logs.", 'error')
            return None
    return question_ids


def save_exam_paper(exam_data, questions_list_full):
    """Orchestrates the three-step save process: Metadata -> Questions -> Link IDs."""

    # 1. Save Exam Metadata first
    exam_metadata_only = {k: v for k, v in exam_data.items() if k not in ['question_counts', 'total_questions']}
    url = f"{BACKENDLESS_DATA_URL}/{BACKENDLESS_EXAM_TABLE}"
    headers = {'Content-Type': 'application/json'}

    try:
        response = requests.post(url, headers=headers, json=exam_metadata_only)
        response.raise_for_status()
        exam_record = response.json()
        exam_id = exam_record.get('objectId')
    except requests.exceptions.RequestException as e:
        print(f"Error saving initial exam metadata: {e}")
        flash('Failed to create exam record metadata.', 'error')
        return None

    if not exam_id:
        flash('Failed to retrieve new exam ID from database.', 'error')
        return None

    # 2. Update all questions with the new examId foreign key
    for question in questions_list_full:
        question['examId'] = exam_id

    # 3. Save questions in a batch
    question_ids = save_multiple_questions(questions_list_full)

    if question_ids is None or not question_ids:
        # Note: If questions fail to save, a rollback (deleting the exam record) is recommended in production
        return None

    # 4. Update the Exams record with the list of saved Question IDs
    update_data = {'questionIds': question_ids}
    update_url = f"{BACKENDLESS_DATA_URL}/{BACKENDLESS_EXAM_TABLE}/{exam_id}"

    try:
        requests.put(update_url, headers=headers, json=update_data).raise_for_status()
        return exam_id
    except requests.exceptions.RequestException as e:
        print(f"Error updating exam record with question IDs: {e}")
        flash('Failed to link questions to the exam record.', 'error')
        return None

def get_exam_paper_by_exam_id(exam_id):
    """Fetch exam details + its questions from Backendless using examId."""
    headers = {'Content-Type': 'application/json'}

    try:
        # 1. Fetch exam metadata
        exam_url = f"{BACKENDLESS_DATA_URL}/{BACKENDLESS_EXAM_TABLE}/{exam_id}"
        exam_res = requests.get(exam_url, headers=headers)

        if exam_res.status_code != 200:
            print(f"[ERROR] Could not fetch exam {exam_id}: {exam_res.text}")
            return None
        exam_data = exam_res.json()

        # 2. Fetch linked questions using the examId as a foreign key filter
        q_url = f"{BACKENDLESS_DATA_URL}/{BACKENDLESS_QUESTION_TABLE}?where=examId%3D'{exam_id}'"
        q_res = requests.get(q_url, headers=headers)

        if q_res.status_code != 200:
            print(f"[ERROR] Could not fetch questions for {exam_id}: {q_res.text}")
            questions = []
        else:
            questions = q_res.json()

        # 3. Combine into exam_paper object
        exam_paper = {
            "examId": exam_id,
            "applicationId": exam_data.get("applicationId"),
            "examTitle": exam_data.get("examTitle"),
            "startDateTime": exam_data.get("startDateTime"),
            "endDateTime": exam_data.get("endDateTime"),
            "testDuration": exam_data.get("testDuration"),
            "questions": questions
        }
        return exam_paper

    except Exception as e:
        print(f"[CRITICAL ERROR] get_exam_paper_by_exam_id failed: {e}")
        return None

def get_job_postings():
    """Fetches all job postings from Backendless."""
    url = f"{BACKENDLESS_DATA_URL}/{BACKENDLESS_JOB_TABLE}"
    headers = {'Content-Type': 'application/json'}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException:
        flash('Could not fetch applications from the server. Check Backendless.', 'error')
        return []

def get_job_postings_by_status():
    """Fetches all job postings and separates them into Open and Past based on lastDate."""
    all_jobs = get_job_postings()
    open_jobs = []
    past_jobs = []
    today = datetime.now().date()

    for job in all_jobs:
        last_date_str = job.get('lastDate')
        job_last_date = None
        try:
            if last_date_str:
                # Handle YYYY-MM-DD format from HTML date input
                job_last_date = datetime.strptime(last_date_str, "%Y-%m-%d").date()
        except ValueError:
            # If date format is bad, treat as open
            pass

        # If lastDate is missing, or is in the future (or today), it's open.
        # Note: We consider 'today' as the last day to apply.
        if job_last_date is None or job_last_date >= today:
            open_jobs.append(job)
        else:
            past_jobs.append(job)

    return open_jobs, past_jobs

def get_job_posting_by_id(object_id):
    """Fetches a single job posting by objectId."""
    url = f"{BACKENDLESS_DATA_URL}/{BACKENDLESS_JOB_TABLE}/{object_id}"
    headers = {'Content-Type': 'application/json'}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException:
        return None

def create_job_posting(data):
    """Saves a new job posting to Backendless."""
    url = f"{BACKENDLESS_DATA_URL}/{BACKENDLESS_JOB_TABLE}"
    headers = {'Content-Type': 'application/json'}
    try:
        requests.post(url, headers=headers, json=data).raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        flash('Failed to save the application to the server.', 'error')
        return False

def update_job_posting(object_id, data):
    """Updates an existing job posting in Backendless."""
    url = f"{BACKENDLESS_DATA_URL}/{BACKENDLESS_JOB_TABLE}/{object_id}"
    headers = {'Content-Type': 'application/json'}
    try:
        requests.put(url, headers=headers, json=data).raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        flash(f'Failed to update application ID: {object_id}.', 'error')
        return False

def get_exam_paper_by_application_id(application_id):
    """Fetches the latest exam paper + questions by Application ID."""
    exam_url = f"{BACKENDLESS_DATA_URL}/{BACKENDLESS_EXAM_TABLE}"
    where_clause = f"applicationId = '{application_id}'"
    params = {'where': where_clause, 'sortBy': 'created DESC', 'pageSize': 1}
    headers = {'Content-Type': 'application/json'}

    try:
        exam_response = requests.get(exam_url, headers=headers, params=params)
        exam_response.raise_for_status()
        exam_record = exam_response.json()[0] if exam_response.json() else None
    except requests.exceptions.RequestException:
        return None

    if exam_record is None:
        return None

    return get_exam_paper_by_exam_id(exam_record['objectId'])

def get_exam_id_by_application_id(application_id):
    """Helper to get the latest examId without fetching the full questions."""
    exam_url = f"{BACKENDLESS_DATA_URL}/{BACKENDLESS_EXAM_TABLE}"
    where_clause = f"applicationId = '{application_id}'"
    params = {'where': where_clause, 'sortBy': 'created DESC', 'pageSize': 1}
    headers = {'Content-Type': 'application/json'}

    try:
        exam_response = requests.get(exam_url, headers=headers, params=params)
        exam_response.raise_for_status()
        exam_record = exam_response.json()[0] if exam_response.json() else None
        return exam_record['objectId'] if exam_record else None
    except requests.exceptions.RequestException:
        return None

# --- NEW HELPER FUNCTION ---
def get_application_id_by_exam_id(exam_id):
    """Fetches the applicationId associated with a specific examId."""
    exam_url = f"{BACKENDLESS_DATA_URL}/{BACKENDLESS_EXAM_TABLE}/{exam_id}"
    headers = {'Content-Type': 'application/json'}
    try:
        exam_response = requests.get(exam_url, headers=headers)
        exam_response.raise_for_status()
        exam_record = exam_response.json()
        return exam_record.get('applicationId')
    except requests.exceptions.RequestException:
        return None
# ---------------------------

def get_all_exams():
    """Fetches all exam metadata records."""
    url = f"{BACKENDLESS_DATA_URL}/{BACKENDLESS_EXAM_TABLE}"
    headers = {'Content-Type': 'application/json'}
    params = {'sortBy': 'created DESC'}
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException:
        return []

def get_all_exams_by_application_id(application_id):
    """Fetches all exam metadata records associated with an Application ID."""
    exam_url = f"{BACKENDLESS_DATA_URL}/{BACKENDLESS_EXAM_TABLE}"
    where_clause = f"applicationId = '{application_id}'"
    params = {'where': where_clause, 'sortBy': 'created DESC'}
    headers = {'Content-Type': 'application/json'}

    try:
        exam_response = requests.get(exam_url, headers=headers, params=params)
        exam_response.raise_for_status()
        return exam_response.json()
    except requests.exceptions.RequestException:
        return []

def send_exam_invitation_email(recipient_email, password, test_link, exam_title, custom_html_content=None):
    """Attempts to send an email using the configured Gmail account with custom HTML body."""
    if not EMAIL_USER or not EMAIL_PASS:
        print("ERROR: Email credentials not set in .env file. Skipping actual send.")
        return False

    message = MIMEMultipart("alternative")
    message["Subject"] = f"Your Online Assessment for {exam_title}"
    message["From"] = EMAIL_USER
    message["To"] = recipient_email

    # Plain Text version (Fallback)
    text = f"""
    Dear Applicant,
    You have been invited to take an online assessment.
    Exam Link: {test_link}
    Username: {recipient_email}
    Password: {password}
    """
    message.attach(MIMEText(text, "plain"))

    # HTML content
    login_snippet = f"""
    <p><strong>Your unique login details:</strong></p>
    <ul>
        <li><strong>Username:</strong> <code>{recipient_email}</code></li>
        <li><strong>Password:</strong> <code>{password}</code></li>
        <li><strong>Exam Link:</strong> <a href="{test_link}">{test_link}</a></li>
    </ul>
    <hr style="border: 1px solid #eee;">
    """

    if custom_html_content:
        # Inject the login snippet at the beginning of the custom content
        # Wrap custom content in basic HTML structure
        final_html = f"<html><body>{login_snippet}{custom_html_content}</body></html>"
    else:
        # Default HTML structure
        final_html = f"""
        <html>
        <body>
            {login_snippet}
            <p>Dear Applicant,</p>
            <p>You have been invited to take an online assessment for the position.</p>
            <p>Regards,<br>The Hiring Team</p>
        </body>
        </html>
        """

    message.attach(MIMEText(final_html, "html"))

    context = ssl.create_default_context()

    try:
        # FIX: Switched to SMTP_SSL on port 465 for better reliability
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context) as server:
            server.login(EMAIL_USER, EMAIL_PASS)
            server.sendmail(EMAIL_USER, recipient_email, message.as_string())
        return True
    except smtplib.SMTPAuthenticationError:
        print(f"Error sending email to {recipient_email}: SMTP Authentication failed. Check EMAIL_USER and EMAIL_PASS (use App Password for Gmail).")
        return False
    except smtplib.SMTPConnectError as e:
        print(f"Error sending email to {recipient_email}: SMTP Connection failed. Check network settings and port {SMTP_PORT}. Details: {e}")
        return False
    except Exception as e:
        # Catches generic errors and the previous "Connection unexpectedly closed"
        print(f"Error sending email to {recipient_email}: {type(e).__name__} - {e}")
        return False

def send_selection_email(recipient_email, exam_title, percentage, status_message, action_message, custom_html_content):
    """
    Attempts to send a final decision email or generic notification email.
    """
    if not EMAIL_USER or not EMAIL_PASS:
        print("ERROR: Email credentials not set. Skipping results/notification email send.")
        return False

    message = MIMEMultipart("alternative")
    message["Subject"] = f"Update on Your Application for {exam_title}"
    message["From"] = EMAIL_USER
    message["To"] = recipient_email
    
    # Fallback/Plain text
    text = f"""
    Dear Applicant,
    Your Technical Assessment score is {percentage}%.
    Status: {status_message}
    Action: {action_message}
    Message: {custom_html_content}
    """
    message.attach(MIMEText(text, "plain"))

    # HTML content
    # The custom_html_content will contain the dynamic personalized message (including result if applicable)
    final_html = f"""
    <html>
        <body>
            <p>Dear Applicant,</p>
            <p>Regarding the {exam_title} role assessment:</p>
            {"<p><strong>Your Test Result:</strong> " + str(percentage) + "%</p>" if percentage != 'N/A' else ""}
            <hr style="border: 1px solid #eee;">
            {custom_html_content}
            <hr style="border: 1px solid #eee;">
            <p>Regards,<br>The Hiring Team</p>
        </body>
    </html>
    """
    message.attach(MIMEText(final_html, "html"))

    context = ssl.create_default_context()

    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context) as server:
            server.login(EMAIL_USER, EMAIL_PASS)
            server.sendmail(EMAIL_USER, recipient_email, message.as_string())
        return True
    except Exception as e:
        print(f"Error sending results/notification email to {recipient_email}: {type(e).__name__} - {e}")
        return False

# --- Termination Removal Email Function ---
def send_termination_removal_email(recipient_email, exam_title):
    """
    Sends an email to the user indicating their exam access has been restored.
    """
    # Simplified email body for restoration
    subject = f"IMPORTANT: Your Exam Access Restored for {exam_title}"
    custom_body = f"""
    <p>We are writing to inform you that the temporary block on your access for the <strong>{exam_title}</strong> exam has been **removed** by the administrator.</p>
    <p>You may now re-attempt the examination if the testing window is still open. Please ensure compliance with all test rules to avoid further interruptions.</p>
    <p>Contact the support team if you encounter any further issues.</p>
    """
    # Use the existing send_selection_email function (passing 'N/A' for result details)
    return send_selection_email(recipient_email, exam_title, 'N/A', 'Restored', 'Access Granted', custom_body)

# Helper to prepare default email body
def generate_default_email_body(exam_title, test_duration):
    return f"""
<p>Dear Applicant,</p>

<p>You have been selected to proceed to the online technical assessment for the <strong>{exam_title}</strong> role.</p>

<p>Please note the key details for your test:</p>
<ul>
    <li><strong>Duration:</strong> {test_duration} minutes</li>
    <li><strong>Date Range:</strong> Please complete the test within the window provided in your calendar invite.</li>
</ul>

<p>Use the unique credentials provided above to access the exam platform via the link. We recommend starting the test shortly before your allotted time to ensure connection stability.</p>

<p>Good luck!</p>

<p>Regards,<br>The Hiring Team</p>
"""
# --- Routing ---

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            flash('You need to log in to access this page.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- New Route to Suppress Tracker Spam (Fix for console output) ---
@app.route('/hybridaction/zybTrackerStatisticsAction', methods=['GET'])
def suppress_tracker_requests():
    # This prevents the client-side tracker script from spamming the console with 404s
    callback = request.args.get('__callback__', 'callback')
    success_json = json.dumps({"code": 0, "message": "Suppressed"})
    response_text = f"{callback}({success_json})"
    
    return app.response_class(
        response=response_text,
        status=200,
        mimetype='application/javascript'
    )
# -----------------------------------------------------------------


@app.route('/')
def index():
    if 'logged_in' in session and session['logged_in']:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if username == FIXED_USERNAME and password == FIXED_PASSWORD:
            session['logged_in'] = True
            session['username'] = username
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            return render_template('admin.html', login_attempt=True)

    return render_template('admin.html')

@app.route('/logout')
@login_required
def logout():
    session.pop('logged_in', None)
    session.pop('username', None)
    flash('You have been successfully logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    open_applications, past_applications = get_job_postings_by_status()
    return render_template('admin.html',
                            dashboard_view=True,
                            open_applications=open_applications,
                            past_applications=past_applications)

@app.route('/add_application', methods=['GET', 'POST'])
@login_required
def add_application():
    if request.method == 'POST':
        data = {
            'jobTitle': request.form.get('jobTitle'),
            'department': request.form.get('department'),
            'location': request.form.get('location'),
            'description': request.form.get('description'),
            'lastDate': request.form.get('lastDate')
        }

        if create_job_posting(data):
            flash(f"Job Posting '{data['jobTitle']}' successfully created!", 'success')
            return redirect(url_for('dashboard'))
        else:
            return render_template('admin.html', add_application_view=True, job_data=data)

    return render_template('admin.html', add_application_view=True, job_data={})

@app.route('/edit_application/<object_id>', methods=['GET', 'POST'])
@login_required
def edit_application(object_id):
    application = get_job_posting_by_id(object_id)

    if application is None:
        flash(f"Application ID {object_id} not found.", 'error')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        updated_data = {
            'jobTitle': request.form.get('jobTitle'),
            'department': request.form.get('department'),
            'location': request.form.get('location'),
            'description': request.form.get('description'),
            'lastDate': request.form.get('lastDate')
        }

        if update_job_posting(object_id, updated_data):
            flash(f"Application '{updated_data['jobTitle']}' successfully updated!", 'success')
            return redirect(url_for('manage_application', object_id=object_id))
        else:
            return render_template('admin.html', edit_application_view=True, application=updated_data)

    return render_template('admin.html', edit_application_view=True, application=application)

@app.route('/manage_application/<object_id>')
@login_required
def manage_application(object_id):
    # Fetch job posting details to display job title in the header
    application = get_job_posting_by_id(object_id)

    # Fetch applied students for this job
    applied_students = get_applied_students_for_job(object_id)

    # Fetch login credentials and status for this job/exam
    login_credentials_map = get_all_login_credentials_for_job(object_id)

    # Fetch all exams linked to this job posting
    all_exams_for_job = get_all_exams_by_application_id(object_id)
    
    # Get the latest exam ID to link the "View Results" button
    latest_exam_id = get_exam_id_by_application_id(object_id)

    # Merge status into student list
    for student in applied_students:
        email = student['applicantEmail']
        cred = login_credentials_map.get(email)
        student['exam_sent_status'] = cred['sentStatus'] if cred else 'NOT_SENT'
        student['exam_password'] = cred['generatedPassword'] if cred else 'N/A'
        student['exam_id'] = cred['examId'] if cred else 'N/A'


    return render_template('admin.html',
                            manage_application_view=True,
                            application_id=object_id,
                            application_title=application.get('jobTitle') if application else 'Unknown Job',
                            applied_students=applied_students,
                            all_exams_for_job=all_exams_for_job,
                            latest_exam_id=latest_exam_id)

# --- Exam Logic - Step 1: Get Metadata and Counts ---

@app.route('/create_exam/<object_id>', methods=['GET', 'POST'])
@login_required
def create_exam(object_id):
    if request.method == 'POST':
        exam_metadata = {
            'applicationId': object_id,
            'examTitle': request.form.get('examTitle'),
            'startDateTime': request.form.get('startDateTime'),
            'endDateTime': request.form.get('endDateTime'),
            'testDuration': request.form.get('testDuration'),
        }

        selected_subjects = request.form.getlist('subjects')
        question_counts = {}
        total_questions = 0

        for subject in selected_subjects:
            count = request.form.get(f'count_{subject}')
            try:
                count = int(count)
                if count > 0:
                    question_counts[subject] = count
                    total_questions += count
            except (ValueError, TypeError):
                pass

        if total_questions == 0:
            flash("Please specify at least one question for a selected subject.", 'warning')
            return redirect(url_for('create_exam', object_id=object_id))

        exam_metadata['question_counts'] = question_counts
        exam_metadata['total_questions'] = total_questions

        session['exam_session_data'] = json.dumps(exam_metadata)

        return redirect(url_for('enter_questions', application_id=object_id))

    return render_template('admin.html',
                            create_exam_view=True,
                            application_id=object_id,
                            exam_subjects=EXAM_SUBJECTS)


# --- Exam Logic - Step 2: Dynamic Question Entry and Save ---

@app.route('/enter_questions/<application_id>', methods=['GET', 'POST'])
@login_required
def enter_questions(application_id):

    exam_session_data_json = session.get('exam_session_data')
    if not exam_session_data_json:
        flash("Exam configuration expired or missing. Please start over.", 'error')
        return redirect(url_for('create_exam', object_id=application_id))

    exam_data = json.loads(exam_session_data_json)

    if request.method == 'POST':
        questions_list_full = []
        q_count = 1

        # Collect all questions from the dynamic form
        for subject, count in exam_data['question_counts'].items():
            for i in range(1, count + 1):
                q_text = request.form.get(f'q{q_count}_text')

                if not q_text:
                    flash(f"Question {q_count} text is missing. Please ensure all question fields are filled.", 'error')
                    return redirect(url_for('enter_questions', application_id=application_id))

                question = {
                    'text': q_text,
                    'subject': subject,
                    'optionA': request.form.get(f'q{q_count}_a'),
                    'optionB': request.form.get(f'q{q_count}_b'),
                    'optionC': request.form.get(f'q{q_count}_c'),
                    'optionD': request.form.get(f'q{q_count}_d'),
                    'correctAnswer': request.form.get(f'q{q_count}_answer'),
                }
                questions_list_full.append(question)
                q_count += 1

        # 1. Save questions and exam metadata using the three-step API process
        exam_id = save_exam_paper(exam_data, questions_list_full)

        # 2. Cleanup session
        session.pop('exam_session_data', None)

        if exam_id:
            flash(f"Exam paper '{exam_data['examTitle']}' successfully saved! ID: {exam_id}", 'success')
            return redirect(url_for('show_exam_paper', exam_id=exam_id))
        else:
            return redirect(url_for('create_exam', object_id=application_id))

    # GET request: Show the dynamic question entry form
    return render_template('admin.html',
                            enter_questions_view=True,
                            application_id=application_id,
                            exam_data=exam_data)


@app.route('/exam_paper/<exam_id>')
@login_required
def show_exam_paper(exam_id):
    """Fetches and displays the saved exam paper details and questions by exam_id."""
    exam_paper = get_exam_paper_by_exam_id(exam_id)

    if not exam_paper:
        flash("No exam paper found for this exam.", 'warning')
        return redirect(url_for('dashboard'))

    return render_template('admin.html',
                            show_exam_paper_view=True,
                            exam_paper=exam_paper,
                            application_id=exam_paper.get('applicationId'))

# --- View All Exam Papers Route ---

@app.route('/view_all_exams')
@login_required
def view_all_exams():
    """Fetches and displays a list of all created exam papers."""
    all_exams = get_all_exams()
    return render_template('admin.html', view_all_exams_view=True, all_exams=all_exams)


# --- Email Preparation Route (Step 1 of 2) ---
# MODIFIED: Redirects to the new specific route using the latest exam ID
@app.route('/prepare_email/<object_id>')
@login_required
def prepare_email(object_id):
    """
    Existing route. Now, it only redirects to the more specific route
    using the LATEST exam associated with the application ID.
    """
    latest_exam_id = get_exam_id_by_application_id(object_id)
    if not latest_exam_id:
        flash("Cannot send emails: No exam paper is linked to this application. Create an exam first.", 'error')
        return redirect(url_for('manage_application', object_id=object_id))

    return redirect(url_for('prepare_email_by_exam_id', exam_id=latest_exam_id))

# --- NEW: Email Preparation Route for a specific Exam ID (Step 1 of 2) ---
@app.route('/prepare_email_by_exam_id/<exam_id>', methods=['GET'])
@login_required
def prepare_email_by_exam_id(exam_id):
    """
    NEW ROUTE: Prepares the email for a SPECIFIC exam ID.
    """
    exam_paper = get_exam_paper_by_exam_id(exam_id)

    if not exam_paper or not exam_paper.get('questions'):
        flash("Cannot send emails: Exam paper is missing or has no questions.", 'error')
        # Try to fall back to the application management page if possible
        application_id_fallback = get_application_id_by_exam_id(exam_id)
        return redirect(url_for('manage_application', object_id=application_id_fallback) if application_id_fallback else url_for('dashboard'))

    application_id = exam_paper['applicationId']
    applied_students = get_applied_students_for_job(application_id)

    if not applied_students:
        flash("No students have applied for this job yet. Cannot send emails.", 'warning')
        return redirect(url_for('manage_application', object_id=application_id))

    exam_title = exam_paper.get('examTitle', 'Online Assessment')
    test_duration = exam_paper.get('testDuration', 'N/A')

    # 1. Generate credentials and default body
    credentials_list = []
    # IMPORTANT: The test link points to the separate exam app, running on port 5001
    test_link_base = f"http://127.0.0.1:5001/test_login/{exam_id}"

    for student in applied_students:
        recipient_email = student['applicantEmail']
        unique_password = str(uuid.uuid4())[:8]
        
        credentials_list.append({
            'email': recipient_email,
            'name': student.get('applicantName', 'Applicant'),
            'password': unique_password,
            'test_link': test_link_base,
            'exam_id': exam_id,  # Crucial: Associate with this specific exam
            'application_id': application_id # Also store application_id
        })

    default_email_body = generate_default_email_body(exam_title, test_duration)

    # 2. Store generated credentials temporarily in session
    session['prepared_credentials'] = json.dumps(credentials_list)
    session['email_send_target_exam_id'] = exam_id # Store the target exam ID for the POST route

    return render_template('admin.html',
                            prepare_email_view=True,
                            application_id=application_id,
                            exam_title=exam_title,
                            credentials_list=credentials_list,
                            default_email_body=default_email_body)


# --- Final Email Sender Route (Step 2 of 2) ---
# MODIFIED: Uses target_exam_id from session for the credentials
@app.route('/send_final_email/<object_id>', methods=['POST'])
@login_required
def send_final_email(object_id):
    """
    The object_id here is the application_id for the redirect, but the
    email logic uses the stored data from the session.
    """
    prepared_credentials_json = session.pop('prepared_credentials', None)
    target_exam_id = session.pop('email_send_target_exam_id', None)

    # Get the editable HTML content
    email_body_html_source = request.form.get('email_body_text', '')

    if not prepared_credentials_json or not email_body_html_source or not target_exam_id:
        flash("Email session expired, body content missing, or target exam is unknown. Please retry.", 'error')
        return redirect(url_for('manage_application', object_id=object_id))

    credentials_list = json.loads(prepared_credentials_json)
    
    # Use target_exam_id to fetch the exam title/details for the email subject
    exam_paper = get_exam_paper_by_exam_id(target_exam_id)

    if not exam_paper:
        flash("Exam details missing for final send.", 'error')
        return redirect(url_for('manage_application', object_id=object_id))

    exam_title = exam_paper.get('examTitle', 'Online Assessment')
    application_id = exam_paper['applicationId'] # Use the real application ID from the exam record
    emails_sent = 0
    email_errors = []

    for student_creds in credentials_list:
        recipient_email = student_creds['email']
        password = student_creds['password']
        test_link = student_creds['test_link']
        exam_id_from_session = student_creds['exam_id']
        application_id_from_session = student_creds['application_id']
        
        # Sanity Check: Ensure the exam ID in the creds matches the target_exam_id
        if exam_id_from_session != target_exam_id:
             email_errors.append(f"Mismatch for {recipient_email}. Skipping.")
             continue

        # Convert textarea input (which uses \n for newlines) to final HTML structure
        final_custom_html = email_body_html_source.replace('\n', '<br>')

        status = 'FAIL'
        if send_exam_invitation_email(recipient_email, password, test_link, exam_title, final_custom_html):
            emails_sent += 1
            status = 'SUCCESS'
        else:
            email_errors.append(recipient_email)

        # 1. Save credential and status to the exam_login table
        # Use the application_id/exam_id stored in the session for maximum accuracy
        save_login_credential(recipient_email, password, exam_id_from_session, status, application_id_from_session)

        # 2. LEGACY: Preserve student registration status in the old mock store (if needed for student login)
        is_registered = MOCK_STUDENT_CREDENTIALS.get(recipient_email, {}).get('is_registered', False)
        MOCK_STUDENT_CREDENTIALS[recipient_email] = {
            'password': password,
            'exam_id': exam_id_from_session,
            'sent_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'is_registered': is_registered
        }

    if emails_sent > 0:
        flash(f"Successfully sent personalized exam invites to {emails_sent} applied students. Status saved in Application Management.", 'success')

    if email_errors:
        flash(f"ERROR: Failed to send email to {len(email_errors)} recipients. Check console for details. Failed emails: {', '.join(email_errors[:3])}...", 'error')

    if emails_sent == 0 and not email_errors:
        flash("Email sending attempted, but 0 emails were sent (possibly due to configuration errors). Check console.", 'error')

    # Redirect to the application management page using the correct application ID
    return redirect(url_for('manage_application', object_id=application_id))


# --- RESULTS AND SHORTLISTING ROUTES (UPDATED TO USE REAL DB RESULTS) ---

@app.route('/view_results/<exam_id>', methods=['GET'])
@login_required
def view_results_by_exam_id(exam_id):
    # 1. Get the parent application ID for context/redirection
    application_id = get_application_id_by_exam_id(exam_id)
    if not application_id:
        flash("Exam not found or not linked to an application.", 'error')
        return redirect(url_for('dashboard'))

    # Fetch job posting details for the header
    application = get_job_posting_by_id(application_id)
    exam_paper = get_exam_paper_by_exam_id(exam_id)
    
    if not application or not exam_paper:
        flash("Application or Exam details not found.", 'error')
        return redirect(url_for('dashboard'))

    # Fetch students who applied for this job (to link application data)
    applied_students = get_applied_students_for_job(application_id)
    
    # Map student application data by email for quick lookup
    applied_student_map = {s['applicantEmail']: s for s in applied_students}

    # --- Fetch real results only for the SPECIFIC EXAM ID ---
    real_results = get_real_exam_results_by_exam_id(exam_id)
    
    # --- Fetch terminated students for this exam ---
    terminated_students = get_terminated_students_by_exam_id(exam_id)
    terminated_map = {t['applicantEmail']: t for t in terminated_students}
    
    # 2. Merge student data with results
    results_with_students = []
    for email, result in real_results.items():
        student_application = applied_student_map.get(email, {})
        
        # Combine application details and exam results
        student_data = {
            **student_application,
            'applicantName': student_application.get('applicantName', 'N/A'),
            'applicantEmail': email,
            'collegeName': student_application.get('collegeName', 'N/A'),
            'cgpa': student_application.get('cgpa', 'N/A'),
            'applied_at': student_application.get('applied_at', 'N/A'),
            **result
        }
        results_with_students.append(student_data)
    
    # Sort by percentage (highest first)
    results_with_students.sort(key=lambda x: x.get('percentage', 0) if x.get('percentage') != 'N/A' else -1, reverse=True)

    # Assign rank based on overall score (before filtering)
    for i, student in enumerate(results_with_students):
        student['rank'] = i + 1
        
    # Filter/Shortlisting Logic (applied to results only)
    min_percent = int(request.args.get('min_percent', 0))
    top_n = int(request.args.get('top_n', 0))
    
    filtered_list = results_with_students
    
    # 1. Filter by minimum percentage
    if min_percent > 0:
        filtered_list = [r for r in filtered_list if r.get('percentage', 0) != 'N/A' and r.get('percentage', 0) >= min_percent]
        
    # 2. Filter by top N (applied *after* percentage filter)
    if top_n > 0:
        filtered_list = filtered_list[:top_n]

    # Determine the set of selected emails
    selected_emails = {s['applicantEmail'] for s in filtered_list}
    
    # Final combined list for display (Results + Terminated)
    emails_with_results = set(real_results.keys())
    all_display_emails = emails_with_results | set(terminated_map.keys())
    rank_map = {r['applicantEmail']: r['rank'] for r in results_with_students}
    
    final_display_list = []
    
    for email in all_display_emails:
        student_application = applied_student_map.get(email, {})
        exam_result = real_results.get(email, {})
        termination_record = terminated_map.get(email)
        
        display_data = {
            'applicantEmail': email,
            'applicantName': student_application.get('applicantName', 'N/A'),
            'applied_at': student_application.get('applied_at', 'N/A'),
            'is_terminated': bool(termination_record),
            'termination_id': termination_record.get('objectId') if termination_record else None,
            'termination_reason': termination_record.get('reason') if termination_record else 'N/A',
            'percentage': exam_result.get('percentage', 'N/A'),
            'rank': rank_map.get(email, 'N/A'),
            'shortlisted': email in selected_emails and email in emails_with_results,
        }
        final_display_list.append(display_data)

    # Sort the final list
    final_display_list.sort(key=lambda x: (
        x['is_terminated'], 
        x['percentage'] if x['percentage'] != 'N/A' else -1, 
        ), reverse=True)

    # Apply the 'show=terminated' filter if present in URL
    show_mode = request.args.get('show')
    is_terminated_view = False
    
    if show_mode == 'terminated':
        # ONLY show terminated students (this addresses the user's specific request)
        results_list_for_template = [s for s in final_display_list if s.get('is_terminated')]
        is_terminated_view = True
        
        if not results_list_for_template:
            flash("No students are currently marked as terminated for this exam.", 'warning')
    else:
        results_list_for_template = final_display_list
        is_terminated_view = False


    # Store list of *all submitted results* (with shortlisting status) for email preparation
    for student in results_with_students:
        student['shortlisted'] = student['applicantEmail'] in selected_emails 
        
    session['results_session_data'] = json.dumps(results_with_students)
    session['results_email_target_exam_id'] = exam_id # Store target exam ID

    return render_template('admin.html',
                            view_results_view=True,
                            application_id=application_id,
                            exam_id=exam_id, 
                            application_title=application.get('jobTitle'),
                            exam_title=exam_paper.get('examTitle', 'Assessment Results'), 
                            results_list=results_list_for_template, 
                            min_percent_filter=min_percent,
                            top_n_filter=top_n,
                            is_terminated_view=is_terminated_view
                            )

# --- NEW ROUTE: Remove Termination and Send Email ---
@app.route('/remove_termination/<exam_id>/<termination_object_id>/<email>')
@login_required
def remove_termination(exam_id, termination_object_id, email):
    """
    Handles the removal of a student's termination status and sends an email.
    """
    exam_paper = get_exam_paper_by_exam_id(exam_id)
    if not exam_paper:
        flash("Exam details not found.", 'error')
        return redirect(url_for('view_all_exams'))

    application_id = exam_paper['applicationId']
    exam_title = exam_paper.get('examTitle', 'Online Assessment')

    if remove_termination_status(termination_object_id):
        
        if send_termination_removal_email(email, exam_title):
            flash(f"Termination removed for {email}. **A restoration email has been automatically sent.**", 'success')
        else:
            flash(f"Termination removed for {email}, but the restoration email FAILED to send. Check SMTP settings.", 'warning')
            
    else:
        flash("Failed to update termination status in the database.", 'error')
        
    # Redirect back to the terminated student view
    return redirect(url_for('view_results_by_exam_id', exam_id=exam_id, show='terminated'))


@app.route('/prepare_results_email_by_exam/<exam_id>', methods=['GET'])
@login_required
def prepare_results_email(exam_id):
    # Check if the exam ID in the URL matches the one stored in the session
    if str(exam_id) != session.get('results_email_target_exam_id'):
        flash("Results session data expired or the wrong exam ID was provided. Please re-run the shortlisting filter.", 'error')
        # Try to find the application ID for redirection
        return redirect(url_for('view_results_by_exam_id', exam_id=exam_id))

    results_data_json = session.get('results_session_data')
    
    if not results_data_json:
        flash("Results session data expired or missing. Please re-run the shortlisting filter.", 'error')
        return redirect(url_for('view_results_by_exam_id', exam_id=exam_id))

    combined_results = json.loads(results_data_json)
    exam_paper = get_exam_paper_by_exam_id(exam_id)
    application_id = exam_paper['applicationId']
    exam_title = exam_paper.get('examTitle', 'Assessment')

    # Prepare default email bodies
    selected_body = """
<p>We are pleased to inform you that **you have been selected** for the next round of interviews based on your strong performance in the assessment.</p>
<p>Your next steps and interview schedule will be shared in a separate email shortly.</p>
    """
    rejected_body = """
<p>Thank you for your interest. While your score was noted, **we are unable to proceed** with your application at this time.</p>
<p>We wish you the best in your future endeavors.</p>
    """

    return render_template('admin.html',
                            prepare_results_email_view=True,
                            application_id=application_id,
                            exam_id=exam_id, # Pass exam_id to template
                            exam_title=exam_title,
                            selected_count=sum(1 for r in combined_results if r.get('shortlisted')),
                            rejected_count=sum(1 for r in combined_results if not r.get('shortlisted')),
                            default_selected_body=selected_body,
                            default_rejected_body=rejected_body)

@app.route('/send_final_results_email_by_exam/<exam_id>', methods=['POST'])
@login_required
def send_final_results_email(exam_id):
    # Check if the exam ID in the URL matches the one stored in the session
    target_exam_id = session.pop('results_email_target_exam_id', None)
    results_data_json = session.pop('results_session_data', None)
    
    if not results_data_json or str(exam_id) != str(target_exam_id):
        flash("Email session expired or target exam is unknown. Please retry by re-running the shortlisting filter.", 'error')
        # Try to find the application ID for redirection
        application_id_fallback = get_application_id_by_exam_id(exam_id)
        return redirect(url_for('manage_application', object_id=application_id_fallback) if application_id_fallback else url_for('dashboard'))

    combined_results = json.loads(results_data_json)
    
    exam_paper = get_exam_paper_by_exam_id(exam_id)
    application_id = exam_paper['applicationId']
    exam_title = exam_paper.get('examTitle', 'Assessment')
    
    # Retrieve customized email bodies
    selected_html = request.form.get('selected_email_body', '').replace('\n', '<br>')
    rejected_html = request.form.get('rejected_email_body', '').replace('\n', '<br>')
    
    emails_sent = 0
    email_errors = []

    for student in combined_results:
        # Only send emails to students who have results
        if student.get('percentage') == 'N/A':
            continue 

        recipient_email = student['applicantEmail']
        percentage = student.get('percentage', 'N/A')
        
        if student.get('shortlisted'):
            status_message = "Selected"
            action_message = "Proceeding to Interview Round"
            custom_body = selected_html
        else:
            status_message = "Not Selected"
            action_message = "Application Not Proceeding"
            custom_body = rejected_html

        if send_selection_email(recipient_email, exam_title, percentage, status_message, action_message, custom_body):
            emails_sent += 1
        else:
            email_errors.append(recipient_email)

    if emails_sent > 0:
        flash(f"Successfully sent results emails to {emails_sent} students.", 'success')

    if email_errors:
        flash(f"ERROR: Failed to send email to {len(email_errors)} recipients. Failed emails: {', '.join(email_errors[:3])}...", 'error')

    return redirect(url_for('manage_application', object_id=application_id))


@app.route('/application_action/<action_type>/<object_id>')
@login_required
def application_action(action_type, object_id):
    """Placeholder route to handle DELETE, etc."""

    if action_type == 'delete':
        flash(f"Application {object_id} **DELETED** (Placeholder action).", 'error')
        return redirect(url_for('dashboard'))

    return redirect(url_for('manage_application', object_id=object_id))


@app.route('/students_and_accounts')
@login_required
def students_and_accounts():
    """Consolidated route for student/account management."""
    # Display mock sent email credentials here
    # In a fully DB-driven app, this would fetch from exam_login table
    return render_template(
        'admin.html',
        students_and_accounts_view=True,
        mock_credentials=MOCK_STUDENT_CREDENTIALS
    )

# --- Student/Applicant Routing ---

@app.route('/student')
def student_portal():
    """Route for the student login/registration page."""
    if session.get('student_logged_in'):
        return redirect(url_for('student_test_dashboard'))
    return render_template('student.html')


@app.route('/student_register', methods=['POST'])
def student_register():
    """Handles new student account creation against the Backendless Users table."""
    name = request.form.get('name')
    email = request.form.get('email')
    password = request.form.get('password')
    confirm_password = request.form.get('confirm_password')

    if password != confirm_password:
        flash('Passwords do not match.', 'error')
        return redirect(url_for('student_portal'))
    
    if not name or not email or not password:
        flash('All fields are required for registration.', 'error')
        return redirect(url_for('student_portal'))

    result = register_student_user(name, email, password)

    if 'error' in result:
        flash(f"Registration failed: {result['error']}", 'error')
        return redirect(url_for('student_portal'))
    
    # Successful registration via Backendless Users table
    flash('Account successfully registered! You can now log in.', 'success')
    return redirect(url_for('student_portal'))


@app.route('/student_login', methods=['POST'])
def student_login():
    """Handles student login against the Backendless Users table."""
    email = request.form.get('email')
    password = request.form.get('password')

    if not email or not password:
        flash('Email and password are required.', 'error')
        return redirect(url_for('student_portal'))

    # 1. Authenticate against Backendless Users Table
    user_data = login_student_user(email, password)

    if user_data:
        # Success via Backendless Users Table
        session['student_logged_in'] = True
        session['student_email'] = email
        session['user_token'] = user_data.get('user-token')
        
        # 2. Fetch the latest assigned exam ID from the custom 'exam_login' table
        credential_data = get_login_credential(email)
        
        # This exam ID is primarily used to check if they are "eligible" to take a test
        session['exam_id_to_take'] = credential_data.get('examId') if credential_data else None

        flash(f"Login successful, {user_data.get('name', 'Applicant')}! You can now access job applications.", 'success')
        return redirect(url_for('student_test_dashboard'))

    else:
        # Authentication failed
        flash('Invalid email or password. Please check your credentials.', 'error')
        return render_template('student.html')


@app.route('/student_dashboard')
def student_test_dashboard():
    """Shows all job applications and the student's applied status."""
    if not session.get('student_logged_in'):
        return redirect(url_for('student_portal'))

    email = session.get('student_email')
    
    # We still need the MOCK_STUDENT_CREDENTIALS for the student_exam_id linkage logic
    student_data = MOCK_STUDENT_CREDENTIALS.get(email, {})

    all_job_postings, _ = get_job_postings_by_status() # Only show open jobs on student dashboard

    # Get all application records for the current student from DB
    applied_applications = get_applied_applications_by_student_email(email)
    applied_app_ids = {app['applicationId']: app for app in applied_applications}

    # Annotate job postings with 'applied' status
    for job in all_job_postings:
        job_id = job.get('objectId')
        job['is_applied'] = job_id in applied_app_ids
        # Inject the stored application details for easy access if needed later
        job['application_details'] = applied_app_ids.get(job_id)

    return render_template(
        'student.html',
        student_dashboard_view=True,
        exam_id_to_take=student_data.get('exam_id'), # Fallback/Legacy exam ID
        all_job_postings=all_job_postings,
    )

@app.route('/submit_job_application', methods=['POST'])
def submit_job_application():
    """
    Handles the submission of detailed student application information, including file validation.
    """
    if not session.get('student_logged_in'):
        flash('You must be logged in to apply for a job.', 'warning')
        return redirect(url_for('student_portal'))

    student_email = session.get('student_email')
    application_id = request.form.get('applicationId')

    # 1. Check if already applied (DB check)
    if get_job_application_status(student_email, application_id):
        flash('You have already applied for this job posting.', 'warning')
        return redirect(url_for('student_test_dashboard'))

    # 2. Handle File Upload (PDF and size validation)
    resume_file = request.files.get('resumeFile')

    if not resume_file or resume_file.filename == '':
        flash('Resume file is required.', 'error')
        return redirect(url_for('student_test_dashboard'))

    # Check file type (MIME type check)
    if resume_file.mimetype != 'application/pdf':
        flash('Invalid file type. Only PDF files are allowed.', 'error')
        return redirect(url_for('student_test_dashboard'))

    # Check file size (max 5MB)
    try:
        resume_file.seek(0, os.SEEK_END)
        file_size = resume_file.tell()
        resume_file.seek(0) # Reset file pointer
    except:
        # Generic error handling if file object is malformed or inaccessible
        flash('Error reading file size.', 'error')
        return redirect(url_for('student_test_dashboard'))

    if file_size > MAX_FILE_SIZE_BYTES:
        flash(f'File size exceeds the limit of {MAX_FILE_SIZE_MB}MB.', 'error')
        return redirect(url_for('student_test_dashboard'))

    # 3. Simulate file upload and get a file ID/reference
    mock_file_id = f"RESUME-{application_id}-{str(uuid.uuid4())}.pdf"

    # 4. Save application details to Backendless StudentApplications table
    application_record = {
        'applicantEmail': student_email,
        'applicationId': application_id,
        'applicantName': request.form.get('fullName'),
        'collegeName': request.form.get('collegeName'),
        'cgpa': request.form.get('cgpa'),
        'file_id': mock_file_id, # Storing the mock ID as a reference
        'applied_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    url = f"{BACKENDLESS_DATA_URL}/{BACKENDLESS_APPLICATIONS_TABLE}"
    headers = {'Content-Type': 'application/json'}

    try:
        requests.post(url, headers=headers, json=application_record).raise_for_status()
        flash('Application submitted successfully! Your details and resume reference have been recorded. Check the Applied Jobs tab for your test status.', 'success')
    except requests.exceptions.RequestException as e:
        print(f"Error saving application to DB: {e}")
        flash('A database error occurred during submission. Please try again.', 'error')

    return redirect(url_for('student_test_dashboard'))


@app.route('/applied_jobs')
def applied_jobs():
    """Shows the list of jobs the student has already applied for."""
    if not session.get('student_logged_in'):
        return redirect(url_for('student_portal'))

    student_email = session.get('student_email')

    # Get all application records for the student from DB
    applied_app_records = get_applied_applications_by_student_email(student_email)

    applied_jobs_details = []

    # Get the latest assigned exam ID from the session (populated during login/registration)
    student_exam_id = session.get('exam_id_to_take')

    for record in applied_app_records:
        app_id = record['applicationId']
        job = get_job_posting_by_id(app_id)
        if job:
            job['application_details'] = {
                'applied_at': record.get('applied_at'),
                'college': record.get('collegeName'),
                'cgpa': record.get('cgpa'),
                'file_id': record.get('file_id') # Used for mock download link
            }

            # Check if this specific application has an exam linked AND the student has credentials for it
            # This is complex in the current structure: we assume if the job has an exam, and the student 
            # has *any* assigned exam ID, they might be ready. For production, the credential check is better.
            job_exam_id = get_exam_id_by_application_id(app_id)
            credential_data = get_login_credential(student_email)
            
            if job_exam_id and credential_data and credential_data.get('examId') == job_exam_id:
                job['exam_assigned'] = 'Yes'
                job['student_exam_id'] = job_exam_id
            else:
                job['exam_assigned'] = 'No'
                job['student_exam_id'] = None
                
            applied_jobs_details.append(job)

    return render_template(
        'student.html',
        student_dashboard_view=True,
        applied_jobs_view=True,
        applied_jobs_details=applied_jobs_details
    )


@app.route('/mock_download_resume/<file_id>')
def mock_download_resume(file_id):
    """
    MOCK ROUTE: Simulates downloading the uploaded PDF.
    """
    print(f"INFO: Simulating download for file ID: {file_id}")
    mock_pdf_content = f"--- MOCK PDF FILE CONTENT ---\n\nThis is a placeholder for the resume PDF ({file_id}) uploaded by the student."

    return send_file(
        BytesIO(mock_pdf_content.encode('utf-8')),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=file_id
    )


@app.route('/test/<exam_id>')
def start_test(exam_id):
    """Redirects to the actual test-taking page (simulated external app)."""
    if not session.get('student_logged_in'):
        flash('You must be logged in to start a test.', 'warning')
        return redirect(url_for('student_portal'))
    
    # Check if the exam is assigned to the current student's email/login
    credential_data = get_login_credential(session.get('student_email'))
    
    if not credential_data or str(exam_id) != str(credential_data.get('examId')):
        flash('Access Denied: This test is not assigned to your account. Check your assigned exam status.', 'error')
        return redirect(url_for('applied_jobs'))

    # In a real system, clicking this button should redirect to the exam application's login URL:
    # return redirect(f"http://127.0.0.1:5001/test_login/{exam_id}")
    
    # For now, simulate the redirect and give instructions
    flash(f"Redirecting to the Exam System. Use the credentials you were emailed or the ones registered in the system. Exam ID: {exam_id}. (Actual test logic is external).", 'success')
    return redirect(url_for('applied_jobs'))


# Note: Added student_logout route for completeness
@app.route('/student_logout')
def student_logout():
    # Attempt to log out of Backendless
    user_token = session.get('user_token')
    if user_token:
        logout_student_user(user_token)

    session.pop('student_logged_in', None)
    session.pop('student_email', None)
    session.pop('user_token', None)
    session.pop('exam_id_to_take', None)
    
    flash('You have been successfully logged out.', 'info')
    return redirect(url_for('student_portal'))


if __name__ == '__main__':
    # Fix for slow reloads: only run logic in the primary process
    if os.environ.get("WERKZEUG_RUN_MAIN") is None or os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        
        if not EMAIL_USER or not EMAIL_PASS:
            print("\n--- WARNING: EMAIL CONFIGURATION ---")
            print("EMAIL_USER or EMAIL_PASS not set. Email sending will likely fail.")
            print("Please set these variables in your .env file.")
            print("-----------------------------------\n")

    app.run(debug=True)
