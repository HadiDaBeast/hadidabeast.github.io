"""
Prispulsen — basic local web app.

Serves a page listing current offers stored in the local SQLite DB, with
a manual "refresh" action that re-runs the fetch against all 7 stores.

Run with:
    pip install flask requests
    python app.py
Then open http://localhost:5000
"""

from flask import Flask, render_template, redirect, url_for
import db
import fetch_prices

app = Flask(__name__)


@app.route("/")
def index():
    db.init_db()
    offers = db.get_current_offers()
    last_fetch = db.get_last_fetch_time()
    return render_template("index.html", offers=offers, last_fetch=last_fetch)


@app.route("/refresh", methods=["POST"])
def refresh():
    # Runs synchronously. 7 stores x 10 queries = ~70 requests, so this
    # can take a minute or so — fine for local dev. A real deployment
    # would run this as a scheduled background job instead (see the
    # "once a week" pipeline design in project-plan.md).
    fetch_prices.main()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True)
