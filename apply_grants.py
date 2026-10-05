import psycopg

# Test DB
conn = psycopg.connect(
    host="127.0.0.1", port=54321, dbname="judicial_test",
    user="postgres", password="yD6rau@unWiNQ081FBtG",
)
cur = conn.cursor()
cur.execute("GRANT SELECT, INSERT, UPDATE ON smtp_settings, agents, skills, ai_models, backups TO judicial_app")
conn.commit()
conn.close()
print("Grants applied to judicial_test")

# Production DB
conn = psycopg.connect(
    host="127.0.0.1", port=54322, dbname="judicial",
    user="postgres", password="yD6rau@unWiNQ081FBtG",
)
cur = conn.cursor()
cur.execute("GRANT SELECT, INSERT, UPDATE ON smtp_settings, agents, skills, ai_models, backups TO judicial_app")
conn.commit()
conn.close()
print("Grants applied to judicial")
