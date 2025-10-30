from app import app, db  # Import your app and db instance

with app.app_context():
    db.create_all()
    print("Database tables created successfully!")