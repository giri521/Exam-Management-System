# Exam Management System

### Deployed Links

* **Exam Portal:** [https://exam-management-system-1-4zbn.onrender.com](https://exam-management-system-1-4zbn.onrender.com)
* **Student Login:** [https://exam-management-system-5ck2.onrender.com/student](https://exam-management-system-5ck2.onrender.com/student)
* **Admin Login:** [https://exam-management-system-5ck2.onrender.com/login](https://exam-management-system-5ck2.onrender.com/login)

## Overview

Exam Management System is a web-based application that provides an efficient and automated solution for managing online examinations. It supports **Admin** and **Student** roles with dedicated portals for each. The system streamlines exam creation, management, and performance tracking with real-time evaluation.

## Features

* **Admin Panel:** Create, update, and delete exams.
* **Question Management:** Add, edit, and categorize questions.
* **Student Portal:** Students can log in, take exams, and view results.
* **Result Analytics:** Instant scoring and result tracking.
* **Secure Login System:** Role-based authentication for admin and student.
* **Responsive Design:** Works across devices.

## Technologies Used

* **Backend:** Python, Flask
* **Frontend:** HTML, CSS, JavaScript
* **Database:** SQLite / MySQL
* **Libraries:** Flask-Login, SQLAlchemy, WTForms

## Getting Started

### Prerequisites

* Python 3.x
* pip

### Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/giri521/Exam-Management-System.git
   ```
2. Navigate to the project directory:

   ```bash
   cd Exam-Management-System
   ```
3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```
4. Run the application:

   ```bash
   python app.py
   ```
5. Open the app in your browser at `http://127.0.0.1:5000/` or use the deployed links.

## Usage

### For Admins

1. Login via the Admin Portal.
2. Create exams, add questions, and assign them to students.
3. Monitor ongoing exams and view student performance.

### For Students

1. Login via the Student Portal.
2. Attempt the assigned exams.
3. Submit answers and view scores instantly.

## File Structure

```
Exam-Management-System/
│
├── app.py                  # Main application file
├── templates/              # HTML templates (Admin & Student views)
├── static/                 # CSS, JS, and image files
├── models.py               # Database models
├── requirements.txt        # Dependencies
└── README.md               # Documentation
```

## Future Enhancements

* Add teacher/invigilator role for live monitoring.
* Include timer-based exams with auto-submit.
* Add email notifications for exam results.
* Implement detailed analytics dashboards.
* Enable PDF export of results.

## Contributing

Contributions are welcome! Fork the repository and submit a pull request. Suggested improvements include adding advanced analytics, notifications, or expanding roles and permissions.


