import sqlite3

def check_latest_inquiry():
    conn = sqlite3.connect("data/inquiries.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    
    if not tables:
        print("Database is empty or no tables found.")
        return

    table_name = [t[0] for t in tables if t[0] != "sqlite_sequence"][0]
    print(f"Querying table: {table_name}\n")

    try:
        cursor.execute(f"SELECT * FROM {table_name} ORDER BY created_at DESC LIMIT 3")
        rows = cursor.fetchall()
        
        if rows:
            for i, row in enumerate(rows):
                print(f"--- Record {i+1} ---")
                for key in row.keys():
                    print(f"{key}: {row[key]}")
                print("\n")
        else:
            print("No records found in the table.")
    except Exception as e:
        print(f"Error querying table: {e}")

if __name__ == "__main__":
    check_latest_inquiry()