"""Reset the database and load demo data:  python seed.py"""
import os
import random
import sqlite3
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

import app as sms

if os.path.exists(sms.DB_PATH):
    os.remove(sms.DB_PATH)
sms.init_db()  # schema + default admin

conn = sqlite3.connect(sms.DB_PATH)
conn.execute("PRAGMA foreign_keys = ON")
pw = generate_password_hash("password123")
random.seed(7)


def add_user(name, email, role, roll=None, dept=None):
    return conn.execute(
        "INSERT INTO users(name,email,password_hash,role,roll_no,department) VALUES(?,?,?,?,?,?)",
        (name, email, pw, role, roll, dept)).lastrowid


faculty = [add_user("Dr. Meera Rao", "meera@college.edu", "faculty", dept="Computer Science"),
           add_user("Prof. Arjun Nair", "arjun@college.edu", "faculty", dept="Computer Science")]
names = ["Aarav Sharma", "Diya Reddy", "Kabir Singh", "Ananya Iyer", "Rohan Gupta", "Sneha Patel"]
students = [add_user(n, f"{n.split()[0].lower()}@college.edu", "student",
                     f"CS21{i + 1:03d}", "Computer Science") for i, n in enumerate(names)]

courses = []
for code, title, cr, f in [("CS101", "Programming in Python", 4, 0), ("CS102", "Data Structures", 4, 0),
                           ("CS103", "Database Systems", 3, 1), ("MA101", "Discrete Mathematics", 3, 1)]:
    courses.append(conn.execute(
        "INSERT INTO courses(code,title,credits,semester,faculty_id) VALUES(?,?,?,?,?)",
        (code, title, cr, 3, faculty[f])).lastrowid)
for s in students:
    for c in courses:
        conn.execute("INSERT INTO enrollments(student_id,course_id) VALUES(?,?)", (s, c))
        for d in range(10):
            day = (date.today() - timedelta(days=d + 1)).isoformat()
            conn.execute("INSERT INTO attendance(student_id,course_id,date,present) VALUES(?,?,?,?)",
                         (s, c, day, 1 if random.random() < 0.85 else 0))
        for exam, mx in [("Midterm", 50), ("Final", 100)]:
            conn.execute("INSERT INTO results(student_id,course_id,exam,marks,max_marks) VALUES(?,?,?,?,?)",
                         (s, c, exam, round(mx * random.uniform(0.45, 0.97), 1), mx))

conn.execute("INSERT INTO materials(course_id,title,description,url) VALUES(?,?,?,?)",
             (courses[0], "Python official tutorial", "Read chapters 1-5", "https://docs.python.org/3/tutorial/"))
conn.execute("INSERT INTO assignments(course_id,title,description,due_date,max_marks) VALUES(?,?,?,?,?)",
             (courses[0], "Lab 1: Functions", "Write 5 functions and explain each.",
              (date.today() + timedelta(days=7)).isoformat(), 10))
for s, (sk, lvl) in zip(students, [("Python", "Advanced"), ("SQL", "Intermediate"), ("Java", "Intermediate"),
                                   ("React", "Beginner"), ("Python", "Intermediate"), ("C++", "Advanced")]):
    conn.execute("INSERT INTO skills(student_id,name,level,certification) VALUES(?,?,?,?)", (s, sk, lvl, None))
for company, role, pkg in [("Infosys", "Systems Engineer", "4.5 LPA"), ("Zoho", "Software Developer", "8 LPA")]:
    conn.execute("INSERT INTO jobs(company,role,package,eligibility,description,deadline) VALUES(?,?,?,?,?,?)",
                 (company, role, pkg, "CGPA 6.0+", "On-campus drive",
                  (date.today() + timedelta(days=21)).isoformat()))
conn.execute("INSERT INTO applications(job_id,student_id,status) VALUES(1,?,?)", (students[0], "Shortlisted"))
conn.execute("INSERT INTO feedback(student_id,course_id,rating,comments,anonymous) VALUES(?,?,?,?,?)",
             (students[1], courses[0], 5, "Very clear explanations.", 1))
conn.commit()
conn.close()
print("Demo data loaded.\n  admin@college.edu / admin123\n  meera@college.edu / password123 (faculty)"
      "\n  aarav@college.edu / password123 (student)")
