"""Student Management System - Flask + SQLite.

Modules: Academics, Attendance, Results, LMS, Skills, Placements, Feedback.
Roles:   admin, faculty, student.
"""
import os
import sqlite3
from datetime import date
from functools import wraps

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("SMS_DB", os.path.join(BASE_DIR, "sms.db"))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-in-production")

JOB_STATUSES = ["Applied", "Shortlisted", "Interview", "Selected", "Rejected"]
SKILL_LEVELS = ["Beginner", "Intermediate", "Advanced", "Expert"]


# --------------------------------------------------------------------------
# Database helpers
# --------------------------------------------------------------------------
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def q(sql, args=(), one=False):
    cur = db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    if one:
        return rows[0] if rows else None
    return rows


def ex(sql, args=()):
    cur = db().execute(sql, args)
    db().commit()
    return cur.lastrowid


def init_db():
    """Create tables and a default admin account if the database is new."""
    conn = sqlite3.connect(DB_PATH)
    with open(os.path.join(BASE_DIR, "schema.sql")) as f:
        conn.executescript(f.read())
    if not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        conn.execute(
            "INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)",
            ("Administrator", "admin@college.edu",
             generate_password_hash("admin123"), "admin"))
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
def current_user():
    if "user" not in g:
        uid = session.get("uid")
        g.user = q("SELECT * FROM users WHERE id=?", (uid,), one=True) if uid else None
    return g.user


@app.context_processor
def inject_user():
    return {"me": current_user(), "today": date.today().isoformat()}


def login_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            user = current_user()
            if not user:
                return redirect(url_for("login"))
            if roles and user["role"] not in roles:
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        user = q("SELECT * FROM users WHERE email=?", (email,), one=True)
        if user and check_password_hash(user["password_hash"], request.form["password"]):
            session.clear()
            session["uid"] = user["id"]
            return redirect(url_for("dashboard"))
        flash("Email or password is incorrect.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# --------------------------------------------------------------------------
# Shared calculations
# --------------------------------------------------------------------------
def grade_for(pct):
    for limit, letter, points in [(90, "O", 10), (80, "A+", 9), (70, "A", 8),
                                  (60, "B", 7), (50, "C", 6), (40, "D", 5)]:
        if pct >= limit:
            return letter, points
    return "F", 0


def attendance_summary(student_id):
    rows = q("""SELECT c.id, c.code, c.title, COUNT(a.id) AS total,
                       COALESCE(SUM(a.present), 0) AS present
                FROM enrollments e
                JOIN courses c ON c.id = e.course_id
                LEFT JOIN attendance a ON a.course_id = c.id AND a.student_id = e.student_id
                WHERE e.student_id = ? GROUP BY c.id ORDER BY c.code""", (student_id,))
    out = []
    for r in rows:
        pct = round(100 * r["present"] / r["total"], 1) if r["total"] else None
        out.append({**dict(r), "pct": pct})
    return out


def result_summary(student_id):
    rows = q("""SELECT c.id, c.code, c.title, c.credits, SUM(r.marks) AS got,
                       SUM(r.max_marks) AS mx
                FROM results r JOIN courses c ON c.id = r.course_id
                WHERE r.student_id = ? GROUP BY c.id ORDER BY c.code""", (student_id,))
    out, credits, points = [], 0, 0
    for r in rows:
        pct = round(100 * r["got"] / r["mx"], 1) if r["mx"] else 0
        letter, pts = grade_for(pct)
        out.append({**dict(r), "pct": pct, "grade": letter, "points": pts})
        credits += r["credits"]
        points += pts * r["credits"]
    return out, (round(points / credits, 2) if credits else 0)


def course_or_404(course_id):
    course = q("SELECT * FROM courses WHERE id=?", (course_id,), one=True)
    if not course:
        abort(404)
    return course


def can_manage(course):
    """Admins manage every course; faculty only their own."""
    u = current_user()
    return u["role"] == "admin" or (u["role"] == "faculty" and course["faculty_id"] == u["id"])


def can_access(course):
    u = current_user()
    if u["role"] == "student":
        return bool(q("SELECT 1 FROM enrollments WHERE student_id=? AND course_id=?",
                      (u["id"], course["id"]), one=True))
    return can_manage(course)


def my_courses():
    u = current_user()
    if u["role"] == "admin":
        return q("SELECT * FROM courses ORDER BY code")
    if u["role"] == "faculty":
        return q("SELECT * FROM courses WHERE faculty_id=? ORDER BY code", (u["id"],))
    return q("""SELECT c.* FROM courses c JOIN enrollments e ON e.course_id=c.id
                WHERE e.student_id=? ORDER BY c.code""", (u["id"],))


def enrolled_students(course_id):
    return q("""SELECT u.* FROM users u JOIN enrollments e ON e.student_id=u.id
                WHERE e.course_id=? ORDER BY u.roll_no, u.name""", (course_id,))


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
@app.route("/")
@login_required()
def dashboard():
    u = current_user()
    stats = {}
    if u["role"] == "admin":
        one = lambda sql: q(sql, one=True)[0]
        stats = {
            "Students": one("SELECT COUNT(*) FROM users WHERE role='student'"),
            "Faculty": one("SELECT COUNT(*) FROM users WHERE role='faculty'"),
            "Courses": one("SELECT COUNT(*) FROM courses"),
            "Open jobs": one("SELECT COUNT(*) FROM jobs"),
            "Students placed": one("SELECT COUNT(DISTINCT student_id) FROM applications WHERE status='Selected'"),
        }
    elif u["role"] == "faculty":
        courses = my_courses()
        stats = {"My courses": len(courses),
                 "Students taught": q("""SELECT COUNT(DISTINCT e.student_id) FROM enrollments e
                        JOIN courses c ON c.id=e.course_id WHERE c.faculty_id=?""",
                                      (u["id"],), one=True)[0]}
    else:
        att = attendance_summary(u["id"])
        total = sum(a["total"] for a in att)
        present = sum(a["present"] for a in att)
        _, cgpa = result_summary(u["id"])
        pending = q("""SELECT COUNT(*) FROM assignments a
                       JOIN enrollments e ON e.course_id=a.course_id AND e.student_id=?
                       WHERE NOT EXISTS (SELECT 1 FROM submissions s
                           WHERE s.assignment_id=a.id AND s.student_id=?)""",
                    (u["id"], u["id"]), one=True)[0]
        stats = {
            "Courses enrolled": len(att),
            "Attendance": f"{round(100 * present / total, 1)}%" if total else "No data",
            "CGPA": cgpa if cgpa else "No data",
            "Pending assignments": pending,
            "Job applications": q("SELECT COUNT(*) FROM applications WHERE student_id=?",
                                  (u["id"],), one=True)[0],
        }
    return render_template("dashboard.html", stats=stats, courses=my_courses())


# --------------------------------------------------------------------------
# Admin: users
# --------------------------------------------------------------------------
@app.route("/users", methods=["GET", "POST"])
@login_required("admin")
def users():
    if request.method == "POST":
        f = request.form
        try:
            ex("""INSERT INTO users(name,email,password_hash,role,roll_no,department)
                  VALUES(?,?,?,?,?,?)""",
               (f["name"].strip(), f["email"].strip().lower(),
                generate_password_hash(f["password"]), f["role"],
                f.get("roll_no") or None, f.get("department") or None))
            flash("User added.", "ok")
        except sqlite3.IntegrityError:
            flash("A user with this email already exists.", "error")
        return redirect(url_for("users"))
    return render_template("users.html",
                           users=q("SELECT * FROM users ORDER BY role, name"))


@app.post("/users/<int:uid>/delete")
@login_required("admin")
def delete_user(uid):
    if uid == current_user()["id"]:
        flash("You cannot delete your own account.", "error")
    else:
        ex("DELETE FROM users WHERE id=?", (uid,))
        flash("User deleted.", "ok")
    return redirect(url_for("users"))


# --------------------------------------------------------------------------
# Academics: courses and enrollment
# --------------------------------------------------------------------------
@app.route("/courses", methods=["GET", "POST"])
@login_required()
def courses():
    u = current_user()
    if request.method == "POST":
        if u["role"] != "admin":
            abort(403)
        f = request.form
        try:
            ex("INSERT INTO courses(code,title,credits,semester,faculty_id) VALUES(?,?,?,?,?)",
               (f["code"].strip().upper(), f["title"].strip(), int(f["credits"]),
                int(f["semester"]), f.get("faculty_id") or None))
            flash("Course created.", "ok")
        except sqlite3.IntegrityError:
            flash("A course with this code already exists.", "error")
        return redirect(url_for("courses"))
    rows = q("""SELECT c.*, u.name AS faculty_name,
                (SELECT COUNT(*) FROM enrollments WHERE course_id=c.id) AS enrolled
                FROM courses c LEFT JOIN users u ON u.id=c.faculty_id
                ORDER BY c.semester, c.code""")
    mine = {r["id"] for r in my_courses()} if u["role"] == "student" else set()
    faculty = q("SELECT id,name FROM users WHERE role='faculty' ORDER BY name")
    return render_template("courses.html", courses=rows, mine=mine, faculty=faculty)


@app.post("/courses/<int:cid>/enroll")
@login_required("student")
def enroll(cid):
    course_or_404(cid)
    ex("INSERT OR IGNORE INTO enrollments(student_id,course_id) VALUES(?,?)",
       (current_user()["id"], cid))
    flash("Enrolled.", "ok")
    return redirect(url_for("courses"))


@app.post("/courses/<int:cid>/drop")
@login_required("student")
def drop(cid):
    ex("DELETE FROM enrollments WHERE student_id=? AND course_id=?",
       (current_user()["id"], cid))
    flash("Course dropped.", "ok")
    return redirect(url_for("courses"))


@app.post("/courses/<int:cid>/delete")
@login_required("admin")
def delete_course(cid):
    ex("DELETE FROM courses WHERE id=?", (cid,))
    flash("Course deleted.", "ok")
    return redirect(url_for("courses"))


@app.route("/courses/<int:cid>/roster", methods=["GET", "POST"])
@login_required("admin", "faculty")
def roster(cid):
    course = course_or_404(cid)
    if not can_manage(course):
        abort(403)
    if request.method == "POST" and current_user()["role"] == "admin":
        ex("INSERT OR IGNORE INTO enrollments(student_id,course_id) VALUES(?,?)",
           (int(request.form["student_id"]), cid))
        flash("Student enrolled.", "ok")
        return redirect(url_for("roster", cid=cid))
    others = q("""SELECT id,name,roll_no FROM users WHERE role='student' AND id NOT IN
                  (SELECT student_id FROM enrollments WHERE course_id=?) ORDER BY name""", (cid,))
    return render_template("roster.html", course=course,
                           students=enrolled_students(cid), others=others)


# --------------------------------------------------------------------------
# Attendance
# --------------------------------------------------------------------------
@app.route("/attendance")
@login_required()
def attendance():
    u = current_user()
    if u["role"] == "student":
        return render_template("attendance_student.html", rows=attendance_summary(u["id"]))
    return render_template("course_pick.html", title="Attendance", courses=my_courses(),
                           endpoint="mark_attendance", action="Mark attendance")


@app.route("/attendance/<int:cid>", methods=["GET", "POST"])
@login_required("admin", "faculty")
def mark_attendance(cid):
    course = course_or_404(cid)
    if not can_manage(course):
        abort(403)
    day = request.values.get("date") or date.today().isoformat()
    students = enrolled_students(cid)
    if request.method == "POST":
        present_ids = set(request.form.getlist("present"))
        for s in students:
            db().execute("""INSERT INTO attendance(student_id,course_id,date,present)
                            VALUES(?,?,?,?)
                            ON CONFLICT(student_id,course_id,date)
                            DO UPDATE SET present=excluded.present""",
                         (s["id"], cid, day, 1 if str(s["id"]) in present_ids else 0))
        db().commit()
        flash(f"Attendance saved for {day}.", "ok")
        return redirect(url_for("mark_attendance", cid=cid, date=day))
    marked = {r["student_id"]: r["present"] for r in
              q("SELECT student_id,present FROM attendance WHERE course_id=? AND date=?", (cid, day))}
    # Default to present when the date has not been recorded yet.
    status = {s["id"]: marked.get(s["id"], 1) for s in students}
    totals = {r["student_id"]: r for r in q(
        """SELECT student_id, COUNT(*) total, SUM(present) present FROM attendance
           WHERE course_id=? GROUP BY student_id""", (cid,))}
    return render_template("attendance_mark.html", course=course, students=students,
                           day=day, status=status, totals=totals)


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------
@app.route("/results")
@login_required()
def results():
    u = current_user()
    if u["role"] == "student":
        summary, cgpa = result_summary(u["id"])
        detail = q("""SELECT c.code, c.title, r.exam, r.marks, r.max_marks FROM results r
                      JOIN courses c ON c.id=r.course_id WHERE r.student_id=?
                      ORDER BY c.code, r.exam""", (u["id"],))
        return render_template("results_student.html", summary=summary, cgpa=cgpa, detail=detail)
    return render_template("course_pick.html", title="Results", courses=my_courses(),
                           endpoint="enter_results", action="Enter marks")


@app.route("/results/<int:cid>", methods=["GET", "POST"])
@login_required("admin", "faculty")
def enter_results(cid):
    course = course_or_404(cid)
    if not can_manage(course):
        abort(403)
    students = enrolled_students(cid)
    if request.method == "POST":
        exam = request.form["exam"].strip()
        max_marks = float(request.form["max_marks"])
        saved = 0
        for s in students:
            raw = request.form.get(f"marks_{s['id']}", "").strip()
            if raw == "":
                continue
            marks = min(float(raw), max_marks)
            db().execute("""INSERT INTO results(student_id,course_id,exam,marks,max_marks)
                            VALUES(?,?,?,?,?)
                            ON CONFLICT(student_id,course_id,exam)
                            DO UPDATE SET marks=excluded.marks, max_marks=excluded.max_marks""",
                         (s["id"], cid, exam, marks, max_marks))
            saved += 1
        db().commit()
        flash(f"Saved marks for {saved} student(s) in {exam}.", "ok")
        return redirect(url_for("enter_results", cid=cid))
    existing = q("""SELECT u.name, u.roll_no, r.exam, r.marks, r.max_marks FROM results r
                    JOIN users u ON u.id=r.student_id WHERE r.course_id=?
                    ORDER BY r.exam, u.roll_no""", (cid,))
    return render_template("results_enter.html", course=course, students=students,
                           existing=existing)


# --------------------------------------------------------------------------
# LMS: materials, assignments, submissions
# --------------------------------------------------------------------------
@app.route("/lms")
@login_required()
def lms():
    return render_template("course_pick.html", title="Learning space", courses=my_courses(),
                           endpoint="lms_course", action="Open")


@app.route("/lms/<int:cid>")
@login_required()
def lms_course(cid):
    course = course_or_404(cid)
    if not can_access(course):
        abort(403)
    u = current_user()
    assignments = q("""SELECT a.*, s.id AS sub_id, s.grade, s.submitted_at,
                       (SELECT COUNT(*) FROM submissions WHERE assignment_id=a.id) AS sub_count
                       FROM assignments a
                       LEFT JOIN submissions s ON s.assignment_id=a.id AND s.student_id=?
                       WHERE a.course_id=? ORDER BY a.due_date""", (u["id"], cid))
    materials = q("SELECT * FROM materials WHERE course_id=? ORDER BY created_at DESC", (cid,))
    return render_template("lms_course.html", course=course, assignments=assignments,
                           materials=materials, manage=can_manage(course))


@app.post("/lms/<int:cid>/material")
@login_required("admin", "faculty")
def add_material(cid):
    if not can_manage(course_or_404(cid)):
        abort(403)
    f = request.form
    ex("INSERT INTO materials(course_id,title,description,url) VALUES(?,?,?,?)",
       (cid, f["title"].strip(), f.get("description"), f.get("url")))
    flash("Material added.", "ok")
    return redirect(url_for("lms_course", cid=cid))


@app.post("/lms/<int:cid>/assignment")
@login_required("admin", "faculty")
def add_assignment(cid):
    if not can_manage(course_or_404(cid)):
        abort(403)
    f = request.form
    ex("INSERT INTO assignments(course_id,title,description,due_date,max_marks) VALUES(?,?,?,?,?)",
       (cid, f["title"].strip(), f.get("description"), f.get("due_date") or None,
        int(f.get("max_marks") or 10)))
    flash("Assignment posted.", "ok")
    return redirect(url_for("lms_course", cid=cid))


@app.route("/lms/assignment/<int:aid>", methods=["GET", "POST"])
@login_required()
def assignment(aid):
    a = q("SELECT * FROM assignments WHERE id=?", (aid,), one=True)
    if not a:
        abort(404)
    course = course_or_404(a["course_id"])
    if not can_access(course):
        abort(403)
    u = current_user()
    if request.method == "POST":
        if u["role"] != "student":
            abort(403)
        ex("""INSERT INTO submissions(assignment_id,student_id,content) VALUES(?,?,?)
              ON CONFLICT(assignment_id,student_id)
              DO UPDATE SET content=excluded.content, submitted_at=CURRENT_TIMESTAMP""",
           (aid, u["id"], request.form["content"].strip()))
        flash("Submission saved.", "ok")
        return redirect(url_for("assignment", aid=aid))
    if u["role"] == "student":
        mine = q("SELECT * FROM submissions WHERE assignment_id=? AND student_id=?",
                 (aid, u["id"]), one=True)
        return render_template("assignment.html", a=a, course=course, mine=mine, subs=None)
    subs = q("""SELECT s.*, u.name, u.roll_no FROM submissions s JOIN users u ON u.id=s.student_id
                WHERE s.assignment_id=? ORDER BY u.roll_no""", (aid,))
    return render_template("assignment.html", a=a, course=course, mine=None, subs=subs)


@app.post("/lms/submission/<int:sid>/grade")
@login_required("admin", "faculty")
def grade_submission(sid):
    s = q("""SELECT s.id, s.assignment_id, a.course_id, a.max_marks FROM submissions s
             JOIN assignments a ON a.id=s.assignment_id WHERE s.id=?""", (sid,), one=True)
    if not s:
        abort(404)
    if not can_manage(course_or_404(s["course_id"])):
        abort(403)
    grade = min(float(request.form["grade"]), s["max_marks"])
    ex("UPDATE submissions SET grade=?, remarks=? WHERE id=?",
       (grade, request.form.get("remarks"), sid))
    flash("Grade saved.", "ok")
    return redirect(url_for("assignment", aid=s["assignment_id"]))


# --------------------------------------------------------------------------
# Skills
# --------------------------------------------------------------------------
@app.route("/skills", methods=["GET", "POST"])
@login_required()
def skills():
    u = current_user()
    if request.method == "POST":
        if u["role"] != "student":
            abort(403)
        f = request.form
        ex("INSERT INTO skills(student_id,name,level,certification) VALUES(?,?,?,?)",
           (u["id"], f["name"].strip(), f["level"], f.get("certification") or None))
        flash("Skill added.", "ok")
        return redirect(url_for("skills"))
    if u["role"] == "student":
        rows = q("SELECT * FROM skills WHERE student_id=? ORDER BY name", (u["id"],))
        return render_template("skills.html", rows=rows, levels=SKILL_LEVELS, all_view=False)
    term = request.args.get("skill", "").strip()
    rows = q("""SELECT s.*, u.name AS student_name, u.roll_no FROM skills s
                JOIN users u ON u.id=s.student_id WHERE s.name LIKE ?
                ORDER BY s.name, u.name""", (f"%{term}%",))
    return render_template("skills.html", rows=rows, levels=SKILL_LEVELS, all_view=True, term=term)


@app.post("/skills/<int:sid>/delete")
@login_required("student")
def delete_skill(sid):
    ex("DELETE FROM skills WHERE id=? AND student_id=?", (sid, current_user()["id"]))
    return redirect(url_for("skills"))


# --------------------------------------------------------------------------
# Placements
# --------------------------------------------------------------------------
@app.route("/placements", methods=["GET", "POST"])
@login_required()
def placements():
    u = current_user()
    if request.method == "POST":
        if u["role"] != "admin":
            abort(403)
        f = request.form
        ex("""INSERT INTO jobs(company,role,package,eligibility,description,deadline)
              VALUES(?,?,?,?,?,?)""",
           (f["company"].strip(), f["role"].strip(), f.get("package"), f.get("eligibility"),
            f.get("description"), f.get("deadline") or None))
        flash("Job posted.", "ok")
        return redirect(url_for("placements"))
    jobs = q("""SELECT j.*, (SELECT COUNT(*) FROM applications WHERE job_id=j.id) AS applicants
                FROM jobs j ORDER BY j.deadline IS NULL, j.deadline""")
    mine = {}
    if u["role"] == "student":
        mine = {r["job_id"]: r["status"] for r in
                q("SELECT job_id,status FROM applications WHERE student_id=?", (u["id"],))}
    return render_template("placements.html", jobs=jobs, mine=mine)


@app.post("/placements/<int:jid>/apply")
@login_required("student")
def apply_job(jid):
    ex("INSERT OR IGNORE INTO applications(job_id,student_id) VALUES(?,?)",
       (jid, current_user()["id"]))
    flash("Application submitted.", "ok")
    return redirect(url_for("placements"))


@app.get("/placements/<int:jid>/applicants")
@login_required("admin", "faculty")
def applicants(jid):
    job = q("SELECT * FROM jobs WHERE id=?", (jid,), one=True)
    if not job:
        abort(404)
    rows = q("""SELECT a.*, u.name, u.roll_no, u.department,
                (SELECT GROUP_CONCAT(name, ', ') FROM skills WHERE student_id=u.id) AS skills
                FROM applications a JOIN users u ON u.id=a.student_id
                WHERE a.job_id=? ORDER BY a.applied_at""", (jid,))
    return render_template("applicants.html", job=job, rows=rows, statuses=JOB_STATUSES)


@app.post("/placements/application/<int:aid>/status")
@login_required("admin")
def update_application(aid):
    status = request.form["status"]
    if status not in JOB_STATUSES:
        abort(400)
    row = q("SELECT job_id FROM applications WHERE id=?", (aid,), one=True)
    if not row:
        abort(404)
    ex("UPDATE applications SET status=? WHERE id=?", (status, aid))
    flash("Status updated.", "ok")
    return redirect(url_for("applicants", jid=row["job_id"]))


@app.post("/placements/<int:jid>/delete")
@login_required("admin")
def delete_job(jid):
    ex("DELETE FROM jobs WHERE id=?", (jid,))
    flash("Job removed.", "ok")
    return redirect(url_for("placements"))


# --------------------------------------------------------------------------
# Feedback
# --------------------------------------------------------------------------
@app.route("/feedback", methods=["GET", "POST"])
@login_required()
def feedback():
    u = current_user()
    if request.method == "POST":
        if u["role"] != "student":
            abort(403)
        f = request.form
        course = course_or_404(int(f["course_id"]))
        if not can_access(course):
            abort(403)
        rating = max(1, min(5, int(f["rating"])))
        ex("INSERT INTO feedback(student_id,course_id,rating,comments,anonymous) VALUES(?,?,?,?,?)",
           (u["id"], course["id"], rating, f.get("comments"), 1 if f.get("anonymous") else 0))
        flash("Thanks, your feedback was sent.", "ok")
        return redirect(url_for("feedback"))
    if u["role"] == "student":
        mine = q("""SELECT f.*, c.code, c.title FROM feedback f JOIN courses c ON c.id=f.course_id
                    WHERE f.student_id=? ORDER BY f.created_at DESC""", (u["id"],))
        return render_template("feedback.html", courses=my_courses(), mine=mine, summary=None)
    ids = [c["id"] for c in my_courses()] or [0]
    marks = ",".join("?" * len(ids))
    summary = q(f"""SELECT c.id, c.code, c.title, COUNT(f.id) AS n, ROUND(AVG(f.rating),2) AS avg
                    FROM courses c LEFT JOIN feedback f ON f.course_id=c.id
                    WHERE c.id IN ({marks}) GROUP BY c.id ORDER BY c.code""", ids)
    comments = q(f"""SELECT f.*, c.code, u.name AS student_name FROM feedback f
                     JOIN courses c ON c.id=f.course_id JOIN users u ON u.id=f.student_id
                     WHERE f.course_id IN ({marks}) AND COALESCE(f.comments,'')<>''
                     ORDER BY f.created_at DESC LIMIT 50""", ids)
    return render_template("feedback.html", courses=[], mine=None, summary=summary,
                           comments=comments)


@app.errorhandler(403)
def forbidden(_e):
    return render_template("error.html", code=403,
                           msg="You don't have access to this page."), 403


@app.errorhandler(404)
def not_found(_e):
    return render_template("error.html", code=404, msg="That page doesn't exist."), 404


init_db()

if __name__ == "__main__":
    app.run(debug=True)
