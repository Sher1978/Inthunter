import sqlite3
conn = sqlite3.connect("intent_hunter.db")
c = conn.cursor()
c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='scraper_accounts';")
print(c.fetchone()[0])
c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='monitored_channels';")
print(c.fetchone()[0])
c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='channel_candidates';")
print(c.fetchone()[0])
