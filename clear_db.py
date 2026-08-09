"""
Clears all stored offers from the local database.

Run with:
    python clear_db.py
"""

import db

if __name__ == "__main__":
    db.init_db()
    db.clear_all()
    print("Database cleared — 'price_history' table is now empty.")
