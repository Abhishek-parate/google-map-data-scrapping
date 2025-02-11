import csv
from io import StringIO, BytesIO
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import logging
from config import Config
from models import db, User, SearchQuery, ScrapedData
from flask_migrate import Migrate
from openpyxl import Workbook  # Add this line
from dataclasses import dataclass, field
import pytz
from datetime import datetime 
import asyncio
from playwright.async_api import async_playwright


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



# Define business data model
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



async def scrape_business(search_term, total, user_id):
    """Scrapes Google Maps for business details and stores them in the database"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        try:
            logging.info(f"Searching for: {search_term}")
            await page.goto("https://www.google.com/maps", timeout=60000)
            await page.wait_for_timeout(5000)
            await page.fill('//input[@id="searchboxinput"]', search_term)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(5000)

            listings = []
            previously_counted = 0

            while True:
                await page.mouse.wheel(0, 10000)
                await page.wait_for_timeout(2000)
                current_count = await page.locator('//a[contains(@href, "https://www.google.com/maps/place")]').count()

                if current_count >= total:
                    listings = await page.locator('//a[contains(@href, "https://www.google.com/maps/place")]').all()
                    listings = listings[:total]
                    break
                elif current_count == previously_counted:
                    listings = await page.locator('//a[contains(@href, "https://www.google.com/maps/place")]').all()
                    break
                else:
                    previously_counted = current_count

            business_list = BusinessList()
            logging.info(f"Found {len(listings)} listings.")

            # Create a SearchQuery and store it
            search_query = SearchQuery(query=search_term, user_id=user_id)
            db.session.add(search_query)
            db.session.commit()  # Commit after creating the SearchQuery to get its ID

            for listing in listings:
                try:
                    await listing.click()
                    await page.wait_for_timeout(3000)

                    business = Business()
                    business.name = await get_text(page, 'h1.DUwDvf.lfPIob')
                    business.address = await get_text(page, '//button[@data-item-id="address"]//div[contains(@class, "fontBodyMedium")]', is_xpath=True)
                    business.website = await get_text(page, '//a[@data-item-id="authority"]//div[contains(@class, "fontBodyMedium")]', is_xpath=True)
                    business.phone_number = await get_text(page, '//button[contains(@data-item-id, "phone")]//div[contains(@class, "fontBodyMedium")]', is_xpath=True)

                    review_count_text = await get_text(page, '//div[contains(@class, "F7nice")]//span[contains(@aria-label, "review")]', is_xpath=True)

                    if review_count_text:
                        business.reviews_count = int("".join(filter(str.isdigit, review_count_text)))  

                    reviews_average_text = await page.locator('//div[@jsaction="pane.reviewChart.moreReviews"]//div[@role="img"]').get_attribute("aria-label")
                    business.reviews_average = float(reviews_average_text.split()[0].replace(",", ".")) if reviews_average_text else None

                    # Create ScrapedData for each business and associate it with the SearchQuery
                    scraped_data = ScrapedData(
                        user_id=user_id,
                        name=business.name,
                        address=business.address,
                        website=business.website,
                        phone_number=business.phone_number,
                        reviews_count=business.reviews_count,
                        reviews_average=business.reviews_average,
                        search_query_id=search_query.id
                    )
                    db.session.add(scraped_data)
                    db.session.commit()  # Commit after each entry to save it

                    business_list.business_list.append(business)
                except Exception as e:
                    logging.error(f"Error scraping listing: {e}")

            await browser.close()
            return business_list
        except Exception as e:
            logging.error(f"Error during scraping: {e}")
            await browser.close()
            return BusinessList()
        

async def get_text(page, selector, is_xpath=False):
    """Helper function to get text from a selector"""
    try:
        if is_xpath:
            locator = page.locator(f"xpath={selector}")
        else:
            locator = page.locator(selector)
        return await locator.inner_text() if await locator.count() > 0 else None
    except Exception:
        return None


@app.route("/scrape", methods=["GET", "POST"])
@login_required
def scrape_view():
    if request.method == "POST":
        search_term = request.form.get("search_term")
        total_results = int(request.form.get("total_results"))

        user_id = current_user.id

        # Run the scraper and save data in DB only
        asyncio.run(scrape_business(search_term, total_results, user_id))

        flash("Scraping completed and data saved in the database!", "success")
        return redirect(url_for("dashboard"))

    return render_template("scrape.html", businesses=None)



@app.route("/api/scrape", methods=["POST"])
def scrape_api():
    data = request.get_json()
    search_term = data.get('search_term')
    total_results = int(data.get('total_results'))

    user_id = current_user.id  # Assuming user authentication

    asyncio.run(scrape_business(search_term, total_results, user_id))

    return jsonify({"message": "Scraping successful. Data saved in the database."})


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
    total_queries = db.session.query(SearchQuery).filter(SearchQuery.user_id == current_user.id).count()
    total_scraped_data = db.session.query(ScrapedData).filter(ScrapedData.user_id == current_user.id).count()
    return render_template("dashboard.html", 
                           search_queries=search_queries, 
                           total_queries=total_queries, 
                           total_scraped_data=total_scraped_data)


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
        logging.info(f"Received request for /scraped_data/{query_id}")
        search_query = db.session.query(SearchQuery).filter_by(id=query_id).first_or_404()
        logging.info(f"Found SearchQuery: {search_query.query} with ID: {query_id}")

        scraped_data = ScrapedData.query.filter_by(search_query_id=query_id).all()
        logging.info(f"Found {len(scraped_data)} entries for search query ID: {query_id}")

        if not scraped_data:
            logging.warning(f"No scraped data found for search query ID: {query_id}")
        
        data = {
            "query": search_query.query,
            "date": search_query.date.strftime('%Y-%m-%d %H:%M:%S'),
            "results": [{
                "name": entry.name,
                "address": entry.address,
                "phone_number": entry.phone_number,
                "reviews_count": entry.reviews_count,
                "reviews_average": entry.reviews_average,
                "website": entry.website
            } for entry in scraped_data]
        }

        logging.info(f"Returning data for query ID: {query_id}")
        return jsonify(data)
    
    except Exception as e:
        logging.error(f"Error in /scraped_data/{query_id}: {e}")
        return jsonify({"error": "An error occurred while fetching data"}), 500


@app.route("/export_csv/<int:query_id>")
@login_required
def export_csv(query_id):
    try:
        # Correct query retrieval
        search_query = db.session.query(SearchQuery).filter_by(id=query_id).first_or_404()
        scraped_data = ScrapedData.query.filter_by(search_query_id=query_id).all()

        if not scraped_data:
            flash("No scraped data available for this query.", "warning")
            return redirect(url_for("dashboard"))

        # Use StringIO for text-based CSV data
        output = StringIO()
        writer = csv.writer(output)

        # Write the header
        writer.writerow(["Name", "Address", "Phone Number", "Reviews Count", "Reviews Average", "Additional Info"])

        # Write data rows
        for data in scraped_data:
            writer.writerow([data.name, 
                             data.address, 
                             data.phone_number, 
                             data.reviews_count, 
                             data.reviews_average, 
                             data.website])

        output.seek(0)  # Rewind the file pointer to the beginning

        # Convert StringIO to BytesIO for sending binary data
        binary_output = BytesIO(output.getvalue().encode())
        binary_output.seek(0)

        return send_file(binary_output, mimetype="text/csv", as_attachment=True, download_name=f"scraped_data_{query_id}.csv")

    except Exception as e:
        logging.error(f"Error exporting CSV for query {query_id}: {e}")
        flash("An error occurred while exporting the data.", "danger")
        return redirect(url_for("dashboard"))




@app.route("/export_excel/<int:query_id>")
@login_required
def export_excel(query_id):
    try:
        # Correctly query for the SearchQuery using filter_by and first_or_404
        search_query = db.session.query(SearchQuery).filter_by(id=query_id).first_or_404()

        # Fetch the scraped data for this query
        scraped_data = ScrapedData.query.filter_by(search_query_id=query_id).all()

        # If no data is found, flash a warning and return to the dashboard
        if not scraped_data:
            flash("No scraped data available for this query.", "warning")
            return redirect(url_for("dashboard"))

        # Prepare the Excel workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Scraped Data"
        ws.append(["Name", "Address", "Phone Number", "Reviews Count", "Reviews Average", "Additional Info"])

        # Write data rows to Excel
        for data in scraped_data:
            ws.append([data.name, data.address, data.phone_number, data.reviews_count, data.reviews_average, data.website])

        # Save the workbook to memory (BytesIO)
        output = BytesIO()
        wb.save(output)
        output.seek(0)

        # Send the Excel file as a response
        return send_file(output, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
                 as_attachment=True, download_name=f"scraped_data_{query_id}.xlsx")

    
    except Exception as e:
        # Log and handle errors
        logging.error(f"Error exporting Excel for query {query_id}: {e}")
        flash("An error occurred while exporting the data.", "danger")
        return redirect(url_for("dashboard"))

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
