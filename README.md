HROne Attendance Management API

A backend API built using FastAPI and MongoDB to manage employee attendance, punch-in/punch-out records, HR corrections, and attendance reports.

Tech Stack-Python,FastAPI,MongoDB,PyMongo,Setup

Install the required packages:

pip install fastapi uvicorn pymongo python-dotenv

Configure MONGO_URI and MONGO_DB in your .env file.

Start the server:
uvicorn app.main:app --reload --port 8000

Open http://127.0.0.1:8000/docs to test the APIs.

APIs

GET /health – Checks the database connection.
POST /employees – Adds a new employee.
GET /employees – Lists employees.
POST /attendance/punch-in – Records punch-in.
POST /attendance/punch-out – Records punch-out and calculates working hours.
GET /attendance – Retrieves attendance records.
PATCH /attendance/{emp_code}/{date} – Updates attendance records for HR corrections and maintains change history.
GET /analytics/employees/{emp_code}/monthly?month=YYYY-MM – Returns monthly attendance statistics.

Department summary API – Provides department-wise attendance statistics.
Late leaderboard API – Ranks employees based on late minutes.
Department trend API – Shows attendance trends over time.


Features:

Employee and attendance record management.
Late arrival, overtime, working hours, and half-day calculations.
HR attendance regularization with history tracking.
Monthly and department-wise analytics.
MongoDB indexes to prevent duplicate records.

Improvements:

Add more automated tests for edge cases and concurrent requests.
Improve analytics performance for larger datasets.
Make error handling more consistent across endpoints.
