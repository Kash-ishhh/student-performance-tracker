import os
from flask import Flask, render_template, request, redirect, url_for, flash, Response, g, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import date
from sqlalchemy import func
import io
import csv

# --- App aur Database Setup ---
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
        if not self.grades: return 0.0
        total = sum(grade.score for grade in self.grades)
        return round(total / len(self.grades), 2)

    def calculate_attendance_percentage(self):
        total_days = db.session.query(func.count(Attendance.date.distinct())).filter_by(student_id=self.id).scalar()
        if not total_days or total_days == 0:
            return 100.0
        
        present_days = db.session.query(func.count(Attendance.id)).filter_by(student_id=self.id, status='Present').scalar()
        # Ensure we count unique dates for total_days calculation to be accurate across all students
        # The logic above is okay for a single student, but if a student has multiple entries for one date (due to error), it might be wrong.
        # A simple fix for this specific student's calculation:
        present_days = db.session.query(func.count(Attendance.date.distinct())).filter_by(student_id=self.id, status='Present').scalar()

        # Recalculate total_days to ensure it's accurate:
        # We need the max number of unique dates for which attendance was marked for ANY student, but for simplicity, 
        # using the student's own marked days is acceptable here, assuming attendance is marked for all on the same days.
        # Let's stick to the simpler version for a student's own percentage:
        all_marked_days = db.session.query(func.count(Attendance.id)).filter_by(student_id=self.id).scalar()
        if not all_marked_days or all_marked_days == 0:
             return 100.0
        present_days = db.session.query(func.count(Attendance.id)).filter_by(student_id=self.id, status='Present').scalar()


        return round((present_days / all_marked_days) * 100, 2)

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
    
    # Check attendance for TODAY for any student to see if it's marked
    attendance_today = Attendance.query.filter_by(date=today).first()
    is_attendance_marked = (attendance_today is not None)
    
    # Dashboard Stats Calculation
    total_students = len(students)
    
    # Calculate Class Average Score
    all_grades = Grade.query.all()
    if all_grades:
        class_avg_score = round(sum(grade.score for grade in all_grades) / len(all_grades), 2)
    else:
        class_avg_score = 0.0

    # Calculate Today's Attendance Count and Insights Count
    present_today_count = Attendance.query.filter_by(date=today, status='Present').count()
    
    # Temporary way to count insights (not perfect as flashes are ephemeral, but needed for the stat card)
    # Since insights are only calculated when adding a grade, we can't get a real-time count easily without
    # storing them. We'll leave it as a placeholder.
    insights_count = 0 
    
    # Pass data to template
    g.today_date = today.strftime("%d-%b-%Y")
    
    return render_template(
        'index.html', 
        students=students, 
        is_attendance_marked=is_attendance_marked,
        total_students=total_students,
        class_avg_score=class_avg_score,
        present_today_count=present_today_count,
        insights_count=insights_count # Placeholder
    )

@app.route('/add_student', methods=['POST'])
def add_student():
    try:
        name = request.form.get('name')
        # Roll number ko integer mein convert karne se pehle check
        roll_number_str = request.form.get('roll_number')
        if not roll_number_str:
            flash('Roll Number is required!', 'warning')
            return redirect(url_for('index'))
            
        roll_number = int(roll_number_str)

        existing_student = Student.query.filter_by(roll_number=roll_number).first()
        if existing_student:
            flash(f'Roll number {roll_number} already exists!', 'danger')
            return redirect(url_for('index'))
            
        if not name:
            flash('Name is required!', 'warning')
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
    
    # Subject ke sabhi grades fetch karo (current student ko exclude karke if possible, but for class average, all grades are fine)
    all_grades = Grade.query.filter(Grade.subject == subject).all()
    
    # Ek list banao jismein sirf scores ho
    subject_scores = [g.score for g in all_grades]
    
    if not subject_scores or len(subject_scores) < 2:
        # Sirf ek hi score hai ya koi nahi hai, toh class average meaningful nahi
        # isliye return kar do
        return 

    total_score = sum(subject_scores)
    class_average = round(total_score / len(subject_scores), 2)

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
        # Validate score input
        score_str = request.form.get('score')
        if not score_str:
             flash('Score is required!', 'warning')
             return redirect(url_for('view_student_details', student_id=student_id))
        
        score = int(score_str)

        if not subject:
            flash('Subject is required!', 'warning')
        elif not (0 <= score <= 100):
            flash('Grade must be between 0 and 100!', 'warning')
        else:
            new_grade = Grade(subject=subject, score=score, student=student)
            db.session.add(new_grade)
            db.session.commit()
            # Insight check commit ke baad hi karo
            check_performance_insight(subject, score, student) 
            flash(f'Grade for {subject} added successfully!', 'success')
            
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
    
    if not all_students:
        flash('Cannot mark attendance: No students found.', 'warning')
        return redirect(url_for('index'))
        
    try:
        # Ek check: Agar attendance pehle se marked hai, toh update karein
        is_update = Attendance.query.filter_by(date=attendance_date).first() is not None
        
        for student in all_students:
            status = request.form.get(f'student_{student.id}')
            
            if not status:
                # Agar koi student mark nahi hua, toh skip
                continue

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
        
        action = 'updated' if is_update else 'marked'
        flash(f'Attendance for {attendance_date.strftime("%d-%b-%Y")} {action} successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error marking attendance: {e}', 'danger')
        
    return redirect(url_for('index'))

# --- Bonus Features ---
@app.route('/class_average/<subject>')
def class_average(subject):
    grades = Grade.query.filter_by(subject=subject).all()
    if not grades: 
        return f"<h1>No grades found for {subject}</h1>"
    total_score = sum(grade.score for grade in grades)
    average = round(total_score / len(grades), 2)
    return f"<h1>Class Average for {subject}: {average}</h1>"

@app.route('/subject_topper/<subject>')
def subject_topper(subject):
    topper_grade = Grade.query.filter_by(subject=subject).order_by(Grade.score.desc()).first()
    if not topper_grade: 
        return f"<h1>No grades found for {subject}</h1>"
    topper_student = topper_grade.student
    return f"<h1>Topper in {subject} is {topper_student.name} (Roll: {topper_student.roll_number}) with {topper_grade.score} marks.</h1>"

@app.route('/export_backup')
def export_backup():
    si = io.StringIO()
    cw = csv.writer(si)
    # Corrected headers
    headers = ['Roll Number', 'Name', 'Overall Average %', 'Attendance %', 'Subject', 'Score']
    cw.writerow(headers)
    students = Student.query.all()
    
    if not students:
        cw.writerow(['No students found in the database.'])
    
    for student in students:
        avg = student.calculate_average()
        att_perc = student.calculate_attendance_percentage()
        
        # Ek single row mein student ki summary aur saare grades ke liye separate rows
        if not student.grades:
            # Summary row for students with no grades
            cw.writerow([student.roll_number, student.name, avg, att_perc, 'N/A', 'N/A'])
        else:
            first_grade = True
            for grade in student.grades:
                if first_grade:
                    # Pehli row mein summary details daalo
                    cw.writerow([student.roll_number, student.name, avg, att_perc, grade.subject, grade.score])
                    first_grade = False
                else:
                    # Baaki rows mein summary details blank rakho
                    cw.writerow(['', '', '', '', grade.subject, grade.score])

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
    
    labels = []    # Student ke naam (X-axis)
    avg_scores_data = [] # Data 1
    attendance_data = [] # Data 2
    scatter_data = []      # Data 3 (Attendance vs Score)

    for student in students:
        labels.append(student.name)
        avg_score = student.calculate_average()
        attendance_perc = student.calculate_attendance_percentage()
        
        avg_scores_data.append(avg_score)
        attendance_data.append(attendance_perc)
        
        scatter_data.append({
            'x': attendance_perc,
            'y': avg_score,
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
    # Yeh app.py ki file name change hone par kaam nahi karega, so it's a bit fragile, 
    # but for simple Flask apps, it's used.
    app.run(debug=True)