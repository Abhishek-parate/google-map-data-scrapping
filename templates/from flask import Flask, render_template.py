from flask import Flask, render_template, request, send_file
import pandas as pd
import asyncio
from playwright.async_api import async_playwright
import os
import logging
from dataclasses import dataclass, asdict, field
import datetime
import pytz

app = Flask(__name__)

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

async def scrape_business(search_term, total):
    """Scrapes Google Maps for business details"""
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
                        business.reviews_count = int("".join(filter(str.isdigit, review_count_text)))  # Corrected line

                    reviews_average_text = await page.locator('//div[@jsaction="pane.reviewChart.moreReviews"]//div[@role="img"]').get_attribute("aria-label")
                    business.reviews_average = float(reviews_average_text.split()[0].replace(",", ".")) if reviews_average_text else None

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

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        search_term = request.form.get("search_term")
        total_results = int(request.form.get("total_results"))

        india_timezone = pytz.timezone("Asia/Kolkata")
        current_datetime = datetime.datetime.now(india_timezone).strftime("%Y%m%d_%H%M%S")
        search_for_filename = search_term.replace(" ", "_")
        file_name = f"{current_datetime}_rows_{search_for_filename}"

        business_list = asyncio.run(scrape_business(search_term, total_results))
        file_path = business_list.save_to_excel(file_name)

        return render_template("index.html", businesses=business_list.business_list, file_path=file_path)
    
    return render_template("index.html", businesses=None, file_path=None)

@app.route("/download/<path:filename>")
def download(filename):
    return send_file(filename, as_attachment=True)

if __name__ == "__main__":
    app.run(debug=True)
