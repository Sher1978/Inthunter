import sqlite3

try:
    conn = sqlite3.connect("intent_hunter.db")
    cursor = conn.cursor()
    
    # Check if columns exist first to avoid errors if run multiple times
    cursor.execute("PRAGMA table_info(partners);")
    columns = [col[1] for col in cursor.fetchall()]
    
    if "username" not in columns:
        print("Adding username column...")
        cursor.execute("ALTER TABLE partners ADD COLUMN username VARCHAR(255);")
        
    if "first_name" not in columns:
        print("Adding first_name column...")
        cursor.execute("ALTER TABLE partners ADD COLUMN first_name VARCHAR(255);")
        
    conn.commit()
    print("Database altered successfully.")
    
except Exception as e:
    print(f"Error: {e}")
finally:
    if 'conn' in locals():
        conn.close()
