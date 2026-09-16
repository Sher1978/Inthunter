import sqlite3

def add_columns():
    conn = sqlite3.connect("intent_hunter.db")
    c = conn.cursor()
    try:
        c.execute("ALTER TABLE user_activity_logs ADD COLUMN channel_username VARCHAR(255)")
        print("Added channel_username to user_activity_logs")
    except Exception as e:
        print("user_activity_logs:", e)
        
    try:
        c.execute("ALTER TABLE ai_evaluation_logs ADD COLUMN channel_username VARCHAR(255)")
        print("Added channel_username to ai_evaluation_logs")
    except Exception as e:
        print("ai_evaluation_logs:", e)

    conn.commit()
    conn.close()

if __name__ == "__main__":
    add_columns()
