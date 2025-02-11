from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import logging
from config import Config
from models import db, User, SearchQuery,ScrapedData
from flask_migrate import Migrate


app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)  # Initialize db with the Flask app context

bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
migrate = Migrate(app, db)

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        email = request.form["email"]
        password = bcrypt.generate_password_hash(request.form["password"]).decode("utf-8")
        new_user = User(username=username, email=email, password=password)
        db.session.add(new_user)
        db.session.commit()
        flash("Registration successful! Please log in.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        user = User.query.filter_by(email=email).first()
        if user and bcrypt.check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "danger")
    return render_template("login.html")

@app.route("/dashboard")
@login_required
def dashboard():
    search_queries = db.session.query(SearchQuery).filter(SearchQuery.user_id == current_user.id).all()
    return render_template("dashboard.html", search_queries=search_queries)

@app.route("/scrape")
@login_required
def scrape():
    flash("Scraping process started!", "info")
    return redirect(url_for("dashboard"))


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("home"))



@app.route("/scraped_data/<int:query_id>")
@login_required
def scraped_data(query_id):
    try:
        # Log incoming request and query_id
        logging.info(f"Received request for /scraped_data/{query_id}")
        
        # Query the SearchQuery and associated ScrapedData
        search_query = db.session.query(SearchQuery).filter_by(id=query_id).first_or_404()
        logging.info(f"Found SearchQuery: {search_query.query} with ID: {query_id}")

        # Fetch the scraped data associated with this search query
        scraped_data = ScrapedData.query.filter_by(search_query_id=query_id).all()
        logging.info(f"Found {len(scraped_data)} entries for search query ID: {query_id}")

        if not scraped_data:
            logging.warning(f"No scraped data found for search query ID: {query_id}")
        
        # Prepare the data to be sent as JSON
        data = {
            "query": search_query.query,
            "date": search_query.date.strftime('%Y-%m-%d %H:%M:%S'),
            "results": [{
                "name": entry.name,
                "address": entry.address,
                "phone_number": entry.phone_number,
                "reviews_count": entry.reviews_count,
                "reviews_average": entry.reviews_average,
                "additional_info": entry.additional_info
            } for entry in scraped_data]
        }

        logging.info(f"Returning data for query ID: {query_id}")
        return jsonify(data)
    
    except Exception as e:
        # Log the error
        logging.error(f"Error in /scraped_data/{query_id}: {e}")
        return jsonify({"error": "An error occurred while fetching data"}), 500



if __name__ == "__main__":
    # Ensure tables are created when the app starts
    with app.app_context():
        db.create_all()  # Create the tables in the database
    app.run(debug=True)
