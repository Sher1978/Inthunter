import sqlite3
conn = sqlite3.connect("intent_hunter.db")
c = conn.cursor()
c.execute("SELECT name FROM sqlite_master WHERE type='table';")
tables = [r[0] for r in c.fetchall()]
print("Tables:", tables)

if "discovered_chats" in tables:
    c.execute("SELECT source, COUNT(*) FROM discovered_chats GROUP BY source;")
    print("discovered_chats sources:", c.fetchall())

# Let's count monitored channels with no leads
c.execute("SELECT COUNT(*) FROM monitored_channels;")
print("Total Monitored channels:", c.fetchone()[0])
