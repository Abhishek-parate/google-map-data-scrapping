from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import asyncio
from playwright.async_api import async_playwright  # Note the async import
import pandas as pd
import os
import logging
from dataclasses import dataclass, field, asdict
import datetime
import pytz
from config import Config
from models import db, User, ScrapedData, SearchQuery

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)  # Initialize db with the Flask app context

bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

@dataclass
class Business:
    """Holds business data"""
    name: str = None
    address: str = None
    website: str = None
    phone_number: str = None
    reviews_count: int = None
    reviews_average: float = None

@dataclass
class BusinessList:
    """Holds list of Business objects and saves to Excel"""
    business_list: list[Business] = field(default_factory=list)
    save_at = "output"

    def dataframe(self):
        """Transform business_list to pandas dataframe"""
        return pd.DataFrame([asdict(business) for business in self.business_list])

    def save_to_excel(self, filename):
        """Save pandas dataframe to Excel file and return file path"""
        if not os.path.exists(self.save_at):
            os.makedirs(self.save_at)
        file_path = os.path.join(self.save_at, f"{filename}.xlsx")
        try:
            self.dataframe().to_excel(file_path, index=False)
            logging.info(f"Saved data to {file_path}")
            return file_path
        except Exception as e:
            logging.error(f"Failed to save data to Excel: {e}")
            return None

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
    """Render the dashboard with user-specific or admin-specific data."""
    users = []
    data = []

    if current_user.role == "admin":
        # Admin sees all users and all scraped data
        users = User.query.all()
        data = ScrapedData.query.all()
    else:
        # Regular users see only their own scraped data
        data = ScrapedData.query.filter_by(user_id=current_user.id).all()

    return render_template("dashboard.html", users=users, data=data)


@app.route("/scrape", methods=["POST", "GET"])
@login_required
async def scrape():
    if request.method == "POST":
        # Handle scraping logic
        search_term = request.form.get("search_term")
        total_results = int(request.form.get("total_results"))

        # Create a new SearchQuery entry to store the search term and the user who made the search
        search_query = SearchQuery(user_id=current_user.id, query=search_term, date=datetime.datetime.utcnow())
        db.session.add(search_query)
        db.session.commit()  # Commit the search query to get the ID

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            await page.goto("https://www.google.com/maps")
            await page.fill('//input[@id="searchboxinput"]', search_term)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(5000)

            listings = await page.locator('//a[contains(@href, "https://www.google.com/maps/place")]').all()
            listings = listings[:total_results]

            # Loop through the listings and store the data in the database
            for listing in listings:
                await listing.click()
                await page.wait_for_timeout(3000)

                name = await listing.inner_text()
                address = await page.locator('//button[@data-item-id="address"]').text_content()
                phone_number = await page.locator('//button[contains(@data-item-id, "phone")]').text_content()

                # Create a ScrapedData object and associate it with the current search query
                scraped_data = ScrapedData(
                    user_id=current_user.id,
                    name=name,
                    address=address,
                    phone_number=phone_number,
                    search_query_id=search_query.id  # Link the scraped data to the search query
                )
                db.session.add(scraped_data)

            db.session.commit()  # Commit all the scraped data to the database
            await browser.close()

        flash("Scraping completed and data saved!", "success")
        return redirect(url_for("dashboard"))

    return render_template("scrape.html")

@app.route("/scrape_business", methods=["GET", "POST"])
def scrape_business_route():
    if request.method == "POST":
        search_term = request.form.get("search_term")
        total_results = int(request.form.get("total_results"))

        india_timezone = pytz.timezone("Asia/Kolkata")
        current_datetime = datetime.datetime.now(india_timezone).strftime("%Y%m%d_%H%M%S")
        search_for_filename = search_term.replace(" ", "_")
        file_name = f"{current_datetime}_rows_{search_for_filename}"

        business_list = asyncio.run(scrape_business(search_term, total_results))
        file_path = business_list.save_to_excel(file_name)

        return render_template("dashboard.html", businesses=business_list.business_list, file_path=file_path)

    return render_template("dashboard.html", businesses=None, file_path=None)

@app.route("/download/<path:filename>")
def download(filename):
    return send_file(filename, as_attachment=True)

@app.route('/scraped-data-details/<int:id>', methods=['GET'])
@login_required
def get_scraped_data_details(id):
    """Fetch and display details for a specific scraped data entry."""
    data = ScrapedData.query.get(id)  # Retrieve the scraped data by ID
    if data:
        query_results = ScrapedData.query.filter_by(search_query_id=data.search_query_id).all()
        result_list = [
            {
                "name": entry.name,
                "address": entry.address,
                "phone": entry.phone_number,
                "additional_info": entry.additional_info or 'N/A'
            }
            for entry in query_results
        ]
        return jsonify(result_list)  # Return JSON for frontend display
    else:
        return jsonify({"error": "Data not found"}), 404

@app.route("/search_queries")
@login_required
def search_queries():
    """Render search queries based on user role."""
    if current_user.role == "admin":
        queries = SearchQuery.query.all()  # Admin sees all search queries
    else:
        queries = SearchQuery.query.filter_by(user_id=current_user.id).all()  # User sees their own queries

    return render_template("search_queries.html", queries=queries)

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("home"))

if __name__ == "__main__":
    with app.app_context():
        db.create_all()  # Create all tables in the database if they don't exist
    app.run(debug=True)
