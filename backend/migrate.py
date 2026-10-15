"""Apply schema.sql to DATABASE_URL:  DATABASE_URL=... python migrate.py"""
import pathlib
from app import db

sql = (pathlib.Path(__file__).parent / "schema.sql").read_text()
for stmt in filter(None, (s.strip() for s in sql.split(";"))):
    db.run(stmt)
print("schema applied")
