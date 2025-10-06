from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from dotenv import load_dotenv
import requests
import os
import json
from datetime import datetime, timezone
from dateutil import parser
from functools import wraps

# --- DeepFace Dependencies ---
# Assuming deepface and its dependencies (numpy, opencv-python) are installed:
try:
    from deepface import DeepFace 
    import base64
    import numpy as np
    import cv2
    DEEPFACE_AVAILABLE = True
except ImportError:
    # print("Warning: DeepFace or its dependencies (numpy, opencv-python) not found. Face detection API will be mocked.")
    DEEPFACE_AVAILABLE = False
# ------------------------------


# Load environment variables from .env file
load_dotenv()

# --- Flask App Initialization ---
app = Flask(__name__)
# IMPORTANT: Use a different secret key for the exam app than the admin app
app.secret_key = 'exam_super_secret_key_for_students'

# --- Backendless Configuration ---

BACKENDLESS_APP_ID = '8D20D88A-D8C3-4B54-846A-BD5E983CAA64'
BACKENDLESS_REST_API_KEY = '323A1426-DFF1-4F5C-AE8B-79B35F891C3D'
BACKENDLESS_BASE_URL = f'https://api.backendless.com/{BACKENDLESS_APP_ID}/{BACKENDLESS_REST_API_KEY}/data'
BACKENDLESS_EXAM_TABLE = 'Exams'
BACKENDLESS_QUESTION_TABLE = 'Questions'
BACKENDLESS_LOGIN_TABLE = 'exam_login'
BACKENDLESS_RESULT_TABLE = 'ExamResults'
BACKENDLESS_TERMINATION_TABLE = 'ExamTerminations' # Table to store termination events

# --- Database Utility Functions ---

def get_login_credential(email, exam_id):
    """Fetches a specific login credential for a student/exam combination."""
    url = f"{BACKENDLESS_BASE_URL}/{BACKENDLESS_LOGIN_TABLE}"
    where_clause = f"applicantEmail = '{email}' AND examId = '{exam_id}'"
    params = {'where': where_clause, 'sortBy': 'sentAt DESC', 'pageSize': 1}
    headers = {'Content-Type': 'application/json'}
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()[0] if response.json() else None
    except requests.exceptions.RequestException:
        print(f"DB Error: Could not retrieve login credential for {email} on exam {exam_id}")
        return None

def get_exam_paper_by_exam_id(exam_id):
    """Fetch exam details + its questions from Backendless using examId."""
    headers = {'Content-Type': 'application/json'}

    try:
        # 1. Fetch exam metadata
        exam_url = f"{BACKENDLESS_BASE_URL}/{BACKENDLESS_EXAM_TABLE}/{exam_id}"
        exam_res = requests.get(exam_url, headers=headers)

        if exam_res.status_code != 200:
            return None
        exam_data = exam_res.json()

        # 2. Fetch linked questions using the examId as a foreign key filter
        q_url = f"{BACKENDLESS_BASE_URL}/{BACKENDLESS_QUESTION_TABLE}?where=examId%3D'{exam_id}'"
        q_res = requests.get(q_url, headers=headers)

        questions = q_res.json() if q_res.status_code == 200 else []

        # 3. Combine into exam_paper object
        return {
            "examId": exam_id,
            "examTitle": exam_data.get("examTitle"),
            "startDateTime": exam_data.get("startDateTime"),
            "endDateTime": exam_data.get("endDateTime"),
            "testDuration": exam_data.get("testDuration"),
            "questions": questions,
            "applicationId": exam_data.get("applicationId")
        }

    except Exception as e:
        print(f"[CRITICAL ERROR] get_exam_paper_by_exam_id failed: {e}")
        return None

def save_exam_result(result_data):
    """Saves the student's exam results to the new ExamResults table."""
    url = f"{BACKENDLESS_BASE_URL}/{BACKENDLESS_RESULT_TABLE}"
    headers = {'Content-Type': 'application/json'}
    data = {
        'applicantEmail': result_data['email'],
        'examId': result_data['exam_id'],
        'applicationId': result_data['application_id'],
        # The 'score' field now expects a percentage (float/double)
        'score': result_data['score'], 
        'totalQuestions': result_data['total_questions'],
        'submissionTime': datetime.now(timezone.utc).isoformat()
    }
    try:
        requests.post(url, headers=headers, json=data).raise_for_status()
        return True
    except requests.exceptions.RequestException:
        return False

def check_if_result_exists(email, exam_id):
    """Checks if a student has already submitted results for this exam."""
    url = f"{BACKENDLESS_BASE_URL}/{BACKENDLESS_RESULT_TABLE}"
    where_clause = f"applicantEmail = '{email}' AND examId = '{exam_id}'"
    params = {'where': where_clause, 'pageSize': 1}
    headers = {'Content-Type': 'application/json'}
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return len(response.json()) > 0
    except requests.exceptions.RequestException:
        return False

# ----------------------------------------------------
# *** MODIFIED: Function to check for active termination with isBlocked=True filter ***
# ----------------------------------------------------
def check_active_termination(email, exam_id):
    """Checks if a student has an ACTIVE termination record (isBlocked=True) for this exam."""
    url = f"{BACKENDLESS_BASE_URL}/{BACKENDLESS_TERMINATION_TABLE}"
    
    # MODIFIED: Filter explicitly by isBlocked=True
    where_clause = f"applicantEmail = '{email}' AND examId = '{exam_id}' AND isBlocked = true" 
    params = {'where': where_clause, 'pageSize': 1}
    headers = {'Content-Type': 'application/json'}
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        # If the query returns any result where isBlocked is True, the user is blocked
        return len(response.json()) > 0
    except requests.exceptions.RequestException as e:
        print(f"DB Error: Failed to check for active termination: {e}")
        return False

# ---------------------------------------------------
# *** UPDATED: Function to record termination ***
# ---------------------------------------------------
def terminate_exam(email, exam_id, violation_type, current_score):
    """Saves the exam termination event to Backendless, setting isBlocked=True."""
    url = f"https://api.backendless.com/{BACKENDLESS_APP_ID}/{BACKENDLESS_REST_API_KEY}/data/{BACKENDLESS_TERMINATION_TABLE}"
    headers = {'Content-Type': 'application/json'}
    data = {
        'applicantEmail': email,
        'examId': exam_id,
        'terminationReason': violation_type,
        'currentScore': float(current_score),
        'terminationTime': datetime.now(timezone.utc).isoformat(),
        # Add a flag indicating this is a final, blocking termination
        'isBlocked': True 
    }
    
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"DB Error: Failed to save exam termination to Backendless: {e}")
        return False


# --- Custom Decorator for Student Authentication ---

def student_exam_required(f):
    """Decorator to ensure student is logged in and has the correct exam ID in session."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        exam_id = kwargs.get('exam_id')
        if not session.get('student_logged_in'):
            flash('Please log in to start your test.', 'warning')
            return redirect(url_for('test_login', exam_id=exam_id))

        if session.get('exam_id') != exam_id:
            flash('Access Denied: You are not authorized for this specific exam.', 'error')
            session.pop('student_logged_in', None)
            session.pop('student_email', None)
            session.pop('exam_id', None)
            return redirect(url_for('test_login', exam_id=exam_id))
        
        # *** Secondary check: Block access if isBlocked is True ***
        email = session.get('student_email')
        if check_active_termination(email, exam_id):
            flash('Access Denied: Your exam access has been terminated.', 'error')
            # Clear session to ensure they cannot proceed
            session.clear() 
            return redirect(url_for('exam_finished'))

        return f(*args, **kwargs)
    return decorated_function

# --------------------------
# --- Core Student Routing ---
# --------------------------

@app.route('/')
def index():
    """Generic landing page redirecting to the initial login prompt."""
    return redirect(url_for('test_login', exam_id='ENTER_YOUR_ID'))


@app.route('/test_login/<exam_id>', methods=['GET', 'POST'])
def test_login(exam_id):
    """
    Dedicated login page that requires Exam ID, Email, and Password.
    (STEP 1: LOGIN)
    """
    is_placeholder = exam_id == 'ENTER_YOUR_ID'

    if session.get('student_logged_in') and session.get('exam_id') == exam_id and not is_placeholder:
        # If already logged in, skip login and go to the pre-check
        return redirect(url_for('pre_exam_check', exam_id=exam_id))

    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        login_exam_id = request.form.get('exam_id')

        if not login_exam_id or not email or not password:
            flash('All fields are required.', 'error')
            return render_template('exam.html', test_login_view=True, exam_id=login_exam_id if login_exam_id else exam_id, pre_fill_email=email)

        # ----------------------------------------------------
        # *** CORE BLOCKING LOGIC (Using the modified function) ***
        # ----------------------------------------------------
        if check_active_termination(email, login_exam_id):
            flash('Access Denied: Your exam access has been permanently terminated due to a policy violation.', 'error')
            # The user cannot log in, redirect them to a final page
            return redirect(url_for('exam_finished'))
        # ----------------------------------------------------
        
        # 1. Fetch credential
        credential = get_login_credential(email, login_exam_id)

        # 2. Validate
        if credential and credential.get('generatedPassword') == password:
            session['student_logged_in'] = True
            session['student_email'] = email
            session['exam_id'] = login_exam_id # Store the specific exam ID they logged in for
            
            # Initialize proctoring counters upon successful login
            session['no_face_count'] = 0
            session['multiple_face_count'] = 0
            session['no_face_warning_count'] = 0 

            flash(f"Login successful. Please complete the pre-test system check.", 'success')
            # Success: Redirect to pre-exam check
            return redirect(url_for('pre_exam_check', exam_id=login_exam_id))
        else:
            flash('Invalid credentials. Check your Email, Password, and Exam ID (case-sensitive).', 'error')
            return render_template('exam.html',
                                   test_login_view=True,
                                   exam_id=login_exam_id,
                                   pre_fill_email=email)

    # GET request: Display the login form
    return render_template('exam.html',
                           test_login_view=True,
                           exam_id=exam_id,
                           is_placeholder=is_placeholder)


@app.route('/pre_exam_check/<exam_id>')
@student_exam_required
def pre_exam_check(exam_id):
    """
    Intermediate page to ensure all system checks (camera/mic/face) pass.
    (STEP 2: PRE-EXAM CHECK)
    """
    email = session.get('student_email')

    # 1. Check if the student has already submitted results
    if check_if_result_exists(email, exam_id):
        flash("You have already submitted this exam. Access is denied.", 'warning')
        return redirect(url_for('exam_finished'))

    exam_paper = get_exam_paper_by_exam_id(exam_id)
    if not exam_paper:
        flash('Exam paper not found. Contact the administrator.', 'error')
        return redirect(url_for('test_login', exam_id=exam_id))

    # 2. Time validation
    try:
        start_time = parser.isoparse(exam_paper['startDateTime'])
        end_time = parser.isoparse(exam_paper['endDateTime'])
        now_time = datetime.now(timezone.utc)
        is_exam_open = start_time <= now_time <= end_time
    except Exception:
        is_exam_open = True
        
    if not is_exam_open:
        flash("The exam is currently closed. Check the start and end dates/times.", 'error')
        return redirect(url_for('exam_finished'))

    # 3. Render the pre-check page
    return render_template('exam.html',
                           pre_check_view=True,  # New template flag
                           exam_id=exam_id,
                           exam_title=exam_paper.get('examTitle'),
                           email=email)


@app.route('/exam_instructions/<exam_id>')
@student_exam_required
def exam_instructions(exam_id):
    """
    Intermediate page to show instructions and explicitly prompt for fullscreen.
    (STEP 3: INSTRUCTIONS & FULLSCREEN PROMPT)
    """
    email = session.get('student_email')
    exam_paper = get_exam_paper_by_exam_id(exam_id)
    
    if not exam_paper:
        flash('Exam paper not found.', 'error')
        return redirect(url_for('test_login', exam_id=exam_id))
    
    # Check if the student has already submitted results or if exam is closed
    if check_if_result_exists(email, exam_id):
        flash("You have already submitted this exam.", 'warning')
        return redirect(url_for('exam_finished'))
    
    # Time validation
    try:
        start_time = parser.isoparse(exam_paper['startDateTime'])
        end_time = parser.isoparse(exam_paper['endDateTime'])
        now_time = datetime.now(timezone.utc)
        is_exam_open = start_time <= now_time <= end_time
    except Exception:
        is_exam_open = True
        
    if not is_exam_open:
        flash("The exam is currently closed. Check the start and end dates/times.", 'error')
        return redirect(url_for('exam_finished'))

    # Render the new instructions page
    return render_template('exam.html',
                           instructions_view=True, # The NEW template flag
                           exam_id=exam_id,
                           exam_paper=exam_paper)


# --- API ROUTE TO REPORT CLIENT-SIDE VIOLATIONS (NEW) ---
@app.route('/api/report_violation', methods=['POST'])
def api_report_violation():
    """
    Receives notification of a critical client-side violation (e.g., refresh,
    fullscreen exit, tab switch) and terminates the exam immediately.
    """
    if not session.get('student_logged_in'):
        return jsonify({'success': False, 'message': 'Authentication required.'}), 401

    data = request.json
    # Default to a generic violation if not specified
    violation_type = data.get('violation_type', 'CLIENT_SIDE_VIOLATION') 
    current_score = data.get('current_score', 0)

    exam_id = session.get('exam_id')
    email = session.get('student_email')

    if not exam_id or not email:
        return jsonify({'success': False, 'message': 'Session data missing.'}), 400

    # Log the termination
    terminate_exam(email, exam_id, violation_type, current_score)

    # Clear session to prevent further access
    session.pop('student_logged_in', None)
    session.pop('student_email', None)
    session.pop('exam_id', None)
    session.pop('no_face_count', None)
    session.pop('multiple_face_count', None)
    session.pop('no_face_warning_count', None)

    return jsonify({'success': True, 'message': f'Exam terminated due to {violation_type}.'}), 200

# --- UPDATED API ROUTE FOR DEEPFACE PROCESSING (With Improved Messaging) ---
@app.route('/api/check_face', methods=['POST'])
def api_check_face():
    """
    Receives base64 image data and checks face presence.
    Implements failure countdown and termination logic, with concise messaging.
    """
    if not session.get('student_logged_in'):
        return jsonify({'success': False, 'message': 'Authentication required.'}), 401

    data = request.json
    base64_img = data.get('image_data', '').split('base64,')[-1]
    
    exam_id = session.get('exam_id')
    email = session.get('student_email')
    
    # Initialize/retrieve counters from session
    no_face_count = session.get('no_face_count', 0)
    multiple_face_count = session.get('multiple_face_count', 0)
    no_face_warning_count = session.get('no_face_warning_count', 0) 
    
    # Default response values
    face_detected = True
    multiple_faces = False
    message = "Face Detected" # Simple success message
    should_terminate = False
    violation_type = None

    is_pre_check = data.get('is_pre_check', False) # Used to distinguish between pre-check and in-exam

    if not DEEPFACE_AVAILABLE:
        # Mock success for pre-check; for proctoring phase, only project/display warnings
        face_detected = True
        multiple_faces = False
        message = 'Proctoring ON (DeepFace Mock)' # Simple ON message for mock mode
        # In mock mode, we skip all critical proctoring logic for in-exam phase
        session['no_face_count'] = 0
        session['multiple_face_count'] = 0
        session['no_face_warning_count'] = 0
        return jsonify({'success': True, 
                        'face_detected': face_detected, 
                        'message': message, 
                        'terminate': False,
                        'no_face_count': 0,
                        'multiple_face_count': 0,
                        'no_face_warning_count': 0}), 200

    if not base64_img:
        return jsonify({'success': False, 'message': 'No image data received.'}), 400
        
    # 1. Run DeepFace analysis 
    try:
        image_data = base64.b64decode(base64_img)
        np_arr = np.frombuffer(image_data, np.uint8)
        img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

        detections = DeepFace.extract_faces(
            img_path=img, 
            detector_backend='opencv', 
            enforce_detection=False 
        )
        # Using a default high confidence threshold for a valid face detection
        detected_faces_count = len([d for d in detections if d.get('confidence', 0) > 0.8])

        face_detected = detected_faces_count == 1
        multiple_faces = detected_faces_count > 1

    except Exception as e:
        # Server-side failure is treated as a face-not-detected issue 
        face_detected = False
        multiple_faces = False
        
    
    
    # 2. Apply Termination Logic (Only during the actual exam, not the pre-check)
    if not is_pre_check:
        
        if face_detected:
            # Success: Reset consecutive counters
            session['no_face_count'] = 0
            session['multiple_face_count'] = 0
            # Message is already set to "Face Detected"
            
        elif multiple_faces:
            # Multiple faces detected: Severe Violation (Existing Logic)
            multiple_face_count += 1
            
            if multiple_face_count >= 2:
                should_terminate = True
                violation_type = "MULTIPLE_FACES_DETECTED"
                # Simple message indicating termination
                message = "EXAM TERMINATED: Multiple faces detected twice." 
            else:
                # Concise message for warning
                message = f"WARNING: Multiple faces detected (1/2 checks). Next failure terminates exam."
                
            session['multiple_face_count'] = multiple_face_count
            session['no_face_count'] = 0 # Reset other counter
            
        else: # No face detected
            # No face detected: Time-based/Accumulated Warning Violation (MODIFIED LOGIC)
            
            # 1. Increment the consecutive no-face counter
            no_face_count += 1
            
            # --- 5-Check (approx 5-second) Immediate Termination Logic ---
            if no_face_count >= 5:
                should_terminate = True
                violation_type = "NO_FACE_DETECTED_FOR_5_CHECKS"
                # Simple message indicating termination
                message = "EXAM TERMINATED: Face lost for 5 seconds."
            
            # --- Accumulated Warning Logic ---
            elif no_face_count == 1:
                # Increment the cumulative warning count only on the FIRST consecutive failure
                no_face_warning_count += 1
                
                if no_face_warning_count >= 5:
                    should_terminate = True
                    violation_type = "NO_FACE_DETECTED_5_WARNINGS"
                    # Simple message indicating termination
                    message = "EXAM TERMINATED: 5 Total Warnings Reached."
                else:
                    # Concise warning message showing remaining time/counts
                    remaining_consecutive = 5 - no_face_count
                    remaining_cumulative = 5 - no_face_warning_count
                    message = f"WARNING: Face lost! Total Warnings: {no_face_warning_count}/5. Re-appear in {remaining_consecutive}s!"
            
            else: # no_face_count is 2, 3, or 4 (consecutive checks)
                # Concise warning message showing remaining time
                remaining_consecutive = 5 - no_face_count
                remaining_cumulative = 5 - no_face_warning_count
                message = f"WARNING: Face lost! Total Warnings: {no_face_warning_count}/5. Re-appear in {remaining_consecutive}s!"


        # Update session data
        session['no_face_count'] = no_face_count
        session['no_face_warning_count'] = no_face_warning_count # Save the cumulative warning count
        session['multiple_face_count'] = multiple_face_count
        
        # 3. Handle Termination Action
        if should_terminate:
            current_score = data.get('current_score', 0)
            terminate_exam(email, exam_id, violation_type, current_score)
            
            # Clear session to prevent further access
            session.pop('student_logged_in', None)
            session.pop('student_email', None)
            session.pop('exam_id', None)


    return jsonify({
        'success': True, 
        'face_detected': face_detected, 
        'message': message, # The final, simplified message for the student
        'terminate': should_terminate,
        'no_face_count': session.get('no_face_count', 0),
        'multiple_face_count': session.get('multiple_face_count', 0),
        'no_face_warning_count': session.get('no_face_warning_count', 0) # Return the cumulative count
    }), 200

@app.route('/start_exam/<exam_id>')
@student_exam_required
def start_exam(exam_id):
    """
    Page displaying the actual test paper.
    (STEP 4: EXAM START)
    """
    email = session.get('student_email')

    # Check if the student has already submitted results
    if check_if_result_exists(email, exam_id):
        flash("You have already submitted this exam. Access is denied.", 'warning')
        return redirect(url_for('exam_finished'))

    exam_paper = get_exam_paper_by_exam_id(exam_id)

    if not exam_paper or not exam_paper['questions']:
        flash('Exam paper not found or is empty. Contact the administrator.', 'error')
        return redirect(url_for('test_login', exam_id=exam_id))

    # Time validation
    try:
        start_time = parser.isoparse(exam_paper['startDateTime'])
        end_time = parser.isoparse(exam_paper['endDateTime'])
        now_time = datetime.now(timezone.utc)

        is_exam_open = start_time <= now_time <= end_time
    except Exception:
        is_exam_open = True

    if not is_exam_open:
        flash("The exam is currently closed. Check the start and end dates/times.", 'error')
        return redirect(url_for('exam_finished'))
    
    # Pass the DEEPFACE_AVAILABLE status to the template to control JS execution
    return render_template('exam.html',
                           exam_view=True,
                           exam_paper=exam_paper,
                           email=email,
                           deepface_proctoring=DEEPFACE_AVAILABLE)  


@app.route('/submit_exam/<exam_id>', methods=['POST'])
@student_exam_required
def submit_exam(exam_id):
    """
    Handles the final exam submission, calculation of score as a percentage, 
    and saving the result.
    """

    email = session.get('student_email')

    if check_if_result_exists(email, exam_id):
        flash("You have already submitted this exam. Access denied.", 'warning')
        return redirect(url_for('exam_finished'))

    exam_paper = get_exam_paper_by_exam_id(exam_id)

    if not exam_paper or not exam_paper['questions']:
        flash('Critical Error: Exam data is missing. Submission failed.', 'error')
        return redirect(url_for('exam_finished'))

    # 1. Calculate raw score
    correct_answers = {q['objectId']: q['correctAnswer'] for q in exam_paper['questions']}
    total_questions = len(exam_paper['questions'])
    raw_score = 0
    for q_id, correct_ans in correct_answers.items():
        student_answer = request.form.get(q_id)
        if student_answer and student_answer == correct_ans:
            raw_score += 1

    # 2. Calculate Percentage Score
    if total_questions > 0:
        # Calculate percentage, rounding to 2 decimal places
        percentage_score = round((raw_score / total_questions) * 100, 2)
    else:
        percentage_score = 0.0

    # 3. Save result to Backendless
    application_id = exam_paper.get('applicationId', 'N/A')

    result_data = {
        'email': email,
        'exam_id': exam_id,
        'application_id': application_id,
        # Store the calculated percentage score
        'score': percentage_score, 
        'total_questions': total_questions,
    }

    if save_exam_result(result_data):
        # 4. Clear session and show results
        session.pop('student_logged_in', None)
        session.pop('student_email', None)
        session.pop('exam_id', None)
        session.pop('no_face_count', None) # Clear counters on successful submission
        session.pop('multiple_face_count', None)
        session.pop('no_face_warning_count', None) # Clear cumulative counter

        flash('Your test has been successfully submitted!', 'success')
        return render_template('exam.html',
                               exam_finished_view=True)  
    else:
        flash('Database Error: Failed to save your result. Contact the administrator.', 'error')
        return redirect(url_for('start_exam', exam_id=exam_id))


@app.route('/exam_finished')
def exam_finished():
    """Generic route for post-exam messages."""
    return render_template('exam.html', exam_finished_view=True)

@app.route('/exam_logout')
def exam_logout():
    """Logs the student out and clears session data."""
    session.pop('student_logged_in', None)
    session.pop('student_email', None)
    session.pop('exam_id', None)
    session.pop('no_face_count', None) # Clear counters on logout
    session.pop('multiple_face_count', None)
    session.pop('no_face_warning_count', None) # Clear cumulative counter
    flash('You have been successfully logged out.', 'info')
    return redirect(url_for('index'))

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))  # Render sets PORT dynamically
    app.run(host="0.0.0.0", port=port, debug=False)

