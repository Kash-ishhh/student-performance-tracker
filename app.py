import os
from flask import Flask, render_template, request, redirect, url_for, flash, Response, g, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import date
from sqlalchemy import func
import io
import csv

# --- App aur Database Setup ---
# --- App aur Database Setup (Production Ready) ---
basedir = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_super_secret_key'
# Check karo ki hum Render par live hain ya local machine par
database_url = os.environ.get('DATABASE_URL')

if database_url:
    # Hum live (Render) par hain aur PostgreSQL use kar rahe hain
    # Render compatibility ke liye 'postgres://' ko 'postgresql://' se replace karein
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url.replace("postgres://", "postgresql://")
else:
    # Hum local hain. Project folder mein hi SQLite rakho.
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'students.db')

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- Database Models ---

class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    roll_number = db.Column(db.Integer, unique=True, nullable=False)
    grades = db.relationship('Grade', backref='student', lazy=True, cascade="all, delete-orphan")
    attendance_records = db.relationship('Attendance', backref='student', lazy=True, cascade="all, delete-orphan")

    def calculate_average(self):
        if not self.grades: return 0
        total = sum(grade.score for grade in self.grades)
        return round(total / len(self.grades), 2)

    def calculate_attendance_percentage(self):
        total_days = db.session.query(func.count(Attendance.id)).filter_by(student_id=self.id).scalar()
        if not total_days or total_days == 0:
            return 100.0
        
        present_days = db.session.query(func.count(Attendance.id)).filter_by(student_id=self.id, status='Present').scalar()
        return round((present_days / total_days) * 100, 2)

    def __repr__(self):
        return f'<Student {self.name} (Roll: {self.roll_number})>'

class Grade(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    subject = db.Column(db.String(100), nullable=False)
    score = db.Column(db.Integer, nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)

class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=date.today)
    status = db.Column(db.String(10), nullable=False) # 'Present' ya 'Absent'
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    __table_args__ = (db.UniqueConstraint('date', 'student_id', name='_date_student_uc'),)

# --- DB Init Command ---
@app.cli.command('init-db')
def init_db_command():
    with app.app_context():
        db.create_all()
    print('Initialized the database.')

# --- Web Routes ---

@app.route('/')
def index():
    students = Student.query.all()
    today = date.today()
    attendance_today = Attendance.query.filter_by(date=today).first()
    is_attendance_marked = (attendance_today is not None)
    
    g.today_date = today.strftime("%d-%b-%Y")
    
    return render_template('index.html', students=students, is_attendance_marked=is_attendance_marked)

@app.route('/add_student', methods=['POST'])
def add_student():
    try:
        name = request.form.get('name')
        roll_number = int(request.form.get('roll_number'))
        existing_student = Student.query.filter_by(roll_number=roll_number).first()
        if existing_student:
            flash(f'Roll number {roll_number} already exists!', 'danger')
            return redirect(url_for('index'))
        if not name or not roll_number:
            flash('Name and Roll Number are required!', 'warning')
            return redirect(url_for('index'))
        new_student = Student(name=name, roll_number=roll_number)
        db.session.add(new_student)
        db.session.commit()
        flash(f'Student {name} added successfully!', 'success')
    except ValueError:
        flash('Invalid Roll Number! Please enter a number.', 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f'Error adding student: {e}', 'danger')
    return redirect(url_for('index'))

@app.route('/student/<int:student_id>')
def view_student_details(student_id):
    student = Student.query.get_or_404(student_id)
    average = student.calculate_average()
    attendance_percentage = student.calculate_attendance_percentage()
    
    return render_template(
        'student_detail.html', 
        student=student, 
        average=average, 
        attendance_percentage=attendance_percentage
    )

# --- Smart Logic ---
def check_performance_insight(subject, new_score, student_object):
    LOW_SCORE_THRESHOLD = 50
    GOOD_SCORE_THRESHOLD = 80 
    LOW_ATTENDANCE_THRESHOLD = 75
    LOW_CLASS_AVG_THRESHOLD = 55

    attendance_perc = student_object.calculate_attendance_percentage()
    
    all_grades = Grade.query.filter_by(subject=subject).all()
    if not all_grades or len(all_grades) < 2:
        return 

    total_score = sum(g.score for g in all_grades)
    class_average = round(total_score / len(all_grades), 2)

    # Rules (Class-wide, Low Marks + Low Attd, Low Marks + Good Attd, Good Marks + Low Attd)
    if new_score < LOW_SCORE_THRESHOLD and class_average < LOW_CLASS_AVG_THRESHOLD:
        insight = (
            f"💡 INSIGHT (Class-wide): Poori class ne '{subject}' mein low perform kiya hai "
            f"(Class Avg: {class_average}%). Is topic ko review karne ki zaroorat ho sakti hai."
        )
        flash(insight, 'info')
        return 
    if new_score < LOW_SCORE_THRESHOLD and attendance_perc < LOW_ATTENDANCE_THRESHOLD:
        insight = (
            f"💡 INSIGHT (Attendance): {student_object.name} ka score ({new_score}%) low hai, aur unki attendance "
            f"bhi sirf {attendance_perc}% hai. Inhe regular rehne ke liye encourage karein."
        )
        flash(insight, 'danger')
        return 
    if new_score < LOW_SCORE_THRESHOLD and attendance_perc >= LOW_ATTENDANCE_THRESHOLD:
        insight = (
            f"💡 INSIGHT (Attention Needed): {student_object.name} regular hain (Att: {attendance_perc}%), "
            f"fir bhi '{subject}' mein score low ({new_score}%) hai. Inhe personal attention ki zaroorat hai."
        )
        flash(insight, 'warning')
        return 
    if new_score >= GOOD_SCORE_THRESHOLD and attendance_perc < LOW_ATTENDANCE_THRESHOLD:
        insight = (
            f"💡 INFO (Monitor): {student_object.name} ke marks '{subject}' mein acche ({new_score}%) hain, "
            f"lekin attendance {attendance_perc}% hai. Bas minimum criteria par nazar rakhein."
        )
        flash(insight, 'info')
        return 

@app.route('/add_grade/<int:student_id>', methods=['POST'])
def add_grade(student_id):
    student = Student.query.get_or_404(student_id)
    try:
        subject = request.form.get('subject')
        score = int(request.form.get('score'))

        if not subject:
            flash('Subject is required!', 'warning')
        elif not (0 <= score <= 100):
            flash('Grade must be between 0 and 100!', 'warning')
        else:
            new_grade = Grade(subject=subject, score=score, student=student)
            db.session.add(new_grade)
            db.session.commit()
            flash(f'Grade for {subject} added successfully!', 'success')
            check_performance_insight(subject, score, student)
            
    except ValueError:
        flash('Invalid Score! Please enter a number.', 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f'Error adding grade: {e}', 'danger')
    return redirect(url_for('view_student_details', student_id=student_id))

@app.route('/delete_student/<int:student_id>', methods=['POST'])
def delete_student(student_id):
    student = Student.query.get_or_404(student_id)
    try:
        db.session.delete(student)
        db.session.commit()
        flash(f'Student {student.name} deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting student: {e}', 'danger')
    return redirect(url_for('index'))

@app.route('/mark_attendance', methods=['POST'])
def mark_attendance():
    attendance_date = date.today()
    all_students = Student.query.all()
    
    try:
        for student in all_students:
            status = request.form.get(f'student_{student.id}')
            if not status: continue

            existing_record = Attendance.query.filter_by(
                student_id=student.id, date=attendance_date
            ).first()
            
            if existing_record:
                existing_record.status = status
            else:
                new_record = Attendance(
                    student_id=student.id, date=attendance_date, status=status
                )
                db.session.add(new_record)
        
        db.session.commit()
        flash(f'Attendance for {attendance_date.strftime("%d-%b-%Y")} marked successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error marking attendance: {e}', 'danger')
        
    return redirect(url_for('index'))

# --- Bonus Features ---
@app.route('/class_average/<subject>')
def class_average(subject):
    grades = Grade.query.filter_by(subject=subject).all()
    if not grades: return f"<h1>No grades found for {subject}</h1>"
    total_score = sum(grade.score for grade in grades)
    average = round(total_score / len(grades), 2)
    return f"<h1>Class Average for {subject}: {average}</h1>"

@app.route('/subject_topper/<subject>')
def subject_topper(subject):
    topper_grade = Grade.query.filter_by(subject=subject).order_by(Grade.score.desc()).first()
    if not topper_grade: return f"<h1>No grades found for {subject}</h1>"
    topper_student = topper_grade.student
    return f"<h1>Topper in {subject} is {topper_student.name} (Roll: {topper_student.roll_number}) with {topper_grade.score} marks.</h1>"

@app.route('/export_backup')
def export_backup():
    si = io.StringIO()
    cw = csv.writer(si)
    headers = ['Roll Number', 'Name', 'Overall Average %', 'Attendance %', 'Subject', 'Score']
    cw.writerow(headers)
    students = Student.query.all()
    
    if not students:
        cw.writerow(['No students found in the database.'])
    
    for student in students:
        avg = student.calculate_average()
        att_perc = student.calculate_attendance_percentage()
        if not student.grades:
            cw.writerow([student.roll_number, student.name, avg, att_perc, 'N/A', 'N/A'])
        else:
            for grade in student.grades:
                cw.writerow([student.roll_number, student.name, avg, att_perc, grade.subject, grade.score])

    output = si.getvalue()
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=student_backup.csv"}
    )

# --- NAYA ROUTE: Analysis Dashboard Page ---
@app.route('/analysis')
def analysis_dashboard():
    """
    Naya Analysis Dashboard HTML page show karta hai.
    """
    return render_template('analysis_dashboard.html')


# --- NAYA ROUTE: Chart Data ke liye API ---
@app.route('/api/chart-data')
def get_chart_data():
    """
    Graphs banane ke liye data ko JSON format mein bhejta hai.
    """
    students = Student.query.all()
    
    labels = []  # Student ke naam (X-axis)
    avg_scores_data = [] # Data 1
    attendance_data = [] # Data 2
    scatter_data = []    # Data 3 (Attendance vs Score)

    for student in students:
        labels.append(student.name)
        avg_scores_data.append(student.calculate_average())
        attendance_perc = student.calculate_attendance_percentage()
        attendance_data.append(attendance_perc)
        
        scatter_data.append({
            'x': attendance_perc,
            'y': student.calculate_average(),
            'label': student.name # Point par hover karne se naam dikhega
        })

    return jsonify({
        'labels': labels,
        'avg_scores': avg_scores_data,
        'attendance': attendance_data,
        'scatter_data': scatter_data
    })

# --- Run the App ---
if __name__ == '__main__':
    app.run(debug=True)