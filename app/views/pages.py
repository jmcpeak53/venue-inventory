from __future__ import annotations

from flask import Blueprint, render_template

bp = Blueprint("pages", __name__)


@bp.get("/")
def home():
    return render_template("home.html")
