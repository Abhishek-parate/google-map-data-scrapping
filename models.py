from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime 

db = SQLAlchemy()

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(10), nullable=False, default="user") 



class ScrapedData(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    name = db.Column(db.String(255))
    address = db.Column(db.String(255))
    website = db.Column(db.String(255))
    phone_number = db.Column(db.String(50))
    reviews_count = db.Column(db.Integer)
    reviews_average = db.Column(db.Float)
    search_query_id = db.Column(db.Integer, db.ForeignKey("search_query.id"), nullable=False)  # ForeignKey should be correct



class SearchQuery(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    query = db.Column(db.String(255), nullable=False)
    date = db.Column(db.DateTime, default=datetime.utcnow)  
    user = db.relationship("User", backref=db.backref("search_queries", lazy=True))
    results = db.relationship("ScrapedData", backref="search_query", lazy=True)
