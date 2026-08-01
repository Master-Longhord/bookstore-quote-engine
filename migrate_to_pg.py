import sqlite3
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.adapters.postgres_store import InquiryModel, SeenMessageModel, Base

SQLITE_PATH = "data/inquiries.db"
POSTGRES_URL = "postgresql+psycopg://bookstore_admin:secure_password_here@localhost:5432/bookstore"

def migrate():
    print("Connecting to SQLite...")
    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_cursor = sqlite_conn.cursor()

    print("Connecting to PostgreSQL...")
    pg_engine = create_engine(POSTGRES_URL)
    Base.metadata.create_all(pg_engine)
    Session = sessionmaker(bind=pg_engine)

    # 1. Migrate Inquiries
    print("Migrating inquiries...")
    sqlite_cursor.execute("SELECT id, sender, status, created_at, payload FROM inquiries")
    rows = sqlite_cursor.fetchall()
    
    migrated_inquiries = 0
    with Session() as session:
        with session.begin():
            for row in rows:
                # Ensure payload is parsed correctly from JSON string
                payload_data = json.loads(row["payload"])
                
                inquiry_obj = InquiryModel(
                    id=row["id"],
                    sender=row["sender"],
                    status=row["status"],
                    created_at=row["created_at"],
                    payload=payload_data
                )
                session.merge(inquiry_obj) # merge handles upserts safely
                migrated_inquiries += 1

    print(f"Successfully migrated {migrated_inquiries} inquiries.")

    # 2. Migrate Seen Messages (Dedup)
    print("Migrating seen messages...")
    try:
        sqlite_cursor.execute("SELECT channel_message_id, seen_at FROM seen_messages")
        seen_rows = sqlite_cursor.fetchall()
        
        migrated_seen = 0
        with Session() as session:
            with session.begin():
                for row in seen_rows:
                    seen_obj = SeenMessageModel(
                        channel_message_id=row["channel_message_id"],
                        seen_at=row["seen_at"]
                    )
                    session.merge(seen_obj)
                    migrated_seen += 1
        print(f"Successfully migrated {migrated_seen} seen messages.")
    except sqlite3.OperationalError:
        print("No seen_messages table found in SQLite, skipping.")

    sqlite_conn.close()
    print("Migration completed successfully!")

if __name__ == "__main__":
    migrate()