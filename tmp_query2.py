import sqlite3
import pandas as pd

conn = sqlite3.connect("intent_hunter.db")
c = conn.cursor()

c.execute("SELECT source, status, COUNT(*) FROM channel_candidates GROUP BY source, status;")
print("Candidates by source/status:")
for row in c.fetchall():
    print(row)

print("---")
c.execute("SELECT COUNT(*) FROM channel_candidates WHERE source LIKE '%Sherlock%';")
print("Candidates with Sherlock in source:", c.fetchone()[0])
