import pandas as pd
from datetime import datetime
import os
import re
import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup

RETAILERS = {
    "Lululemon": "https://shop.lululemon.com/c/sale",
    "Dollar Tree": "https://www.dollartree.com/sale",
    "Wayfair": "https://www.wayfair.com/daily-sales",
    "Capri - Michael Kors": "https://www.michaelkors.com/sale",
    "European Wax Center": "https://www.waxcenter.com/promotions"
}

BANNER_SELECTORS = {
    "Lululemon": [
        "welcome-offer-banner_bannerOuter",
        "welcome-offer-banner_bannerInfoList",
        "primary_promoL1"
    ],
    "Capri - Michael Kors": [
        "full-width-banner-container",
        "sales",
        "pphMessageLink"
    ],
    "European Wax Center": [
        "banner", "promo", "offer", "deal", "alert"
    ],
    "Dollar Tree": [
        "banner", "promo", "offer", "deal", "alert"
    ],
    "Wayfair": [
        "banner", "promo", "offer", "deal", "alert"
    ]
}

def get_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    return driver

def extract_discount_percentages(text):
    pattern = r'(\d{1,3})\s*%\s*off'
    matches = re.findall(pattern, text.lower())
    if matches:
        percentages = [int(m) for m in matches]
        return {
            "discounts_found": ", ".join([f"{p}% off" for p in sorted(set(percentages), reverse=True)]),
            "max_discount": max(percentages),
            "avg_discount": round(sum(percentages) / len(percentages), 1)
        }
    return {"discounts_found": "", "max_discount": 0, "avg_discount": 0}

def extract_promo_banners(soup, retailer_name):
    junk_phrases = [
        'sign in', 'my account', 'my bag', 'track my order',
        'find a store', 'cookie', 'javascript', 'privacy policy',
        'privacy preference', 'privacy pre', 'terms and conditions',
        'email address', 'back button', 'filter', 'checkbox',
        'consent', 'favorites', 'press & hold', 'confirm you are'
    ]

    selectors = BANNER_SELECTORS.get(retailer_name, ['banner', 'promo', 'sale', 'offer'])

    banner_texts = []
    seen = set()

    for selector in selectors:
        tags = soup.find_all(attrs={"class": re.compile(selector, re.I)})
        for tag in tags[:5]:
            text = tag.get_text(strip=True)
            text_lower = text.lower()

            if not text or len(text) < 8 or len(text) > 300:
                continue
            if any(junk in text_lower for junk in junk_phrases):
                continue
            if text in seen:
                continue

            seen.add(text)
            banner_texts.append(text)

    return " | ".join(banner_texts[:3]) if banner_texts else ""

def extract_sale_categories(soup):
    junk_phrases = [
        'skip', 'navigation', 'my account', 'my bag', 'hamburger',
        'sign in', 'search', 'menu', 'back', 'cart', 'items',
        'accessibility', 'cookie', 'all departments'
    ]

    category_tags = soup.find_all(
        attrs={"class": re.compile(r'categor|department|section-title|category-name', re.I)}
    )

    categories = []
    for tag in category_tags[:15]:
        text = tag.get_text(strip=True)
        text_lower = text.lower()

        if not text or len(text) < 3 or len(text) > 40:
            continue
        if any(junk in text_lower for junk in junk_phrases):
            continue

        categories.append(text)

    return ", ".join(list(dict.fromkeys(categories))[:5]) if categories else ""

def get_rolling_signal(retailer, current_discount, filename="promo_log.csv", window=7):
    if not os.path.exists(filename):
        return "insufficient data", 0.0

    df = pd.read_csv(filename)
    retailer_df = df[df["retailer"] == retailer].tail(window)

    if len(retailer_df) < 2:
        return "insufficient data", 0.0

    rolling_avg = round(retailer_df["max_discount_pct"].mean(), 1)

    if current_discount == 0 and rolling_avg == 0:
        return "no promotion", rolling_avg
    elif current_discount > rolling_avg * 1.1:
        return "ABOVE average - elevated promo", rolling_avg
    elif current_discount < rolling_avg * 0.9:
        return "BELOW average - reduced promo", rolling_avg
    else:
        return "in line with average", rolling_avg

def scrape_retailer(driver, name, url):
    try:
        driver.get(url)
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        time.sleep(7)

        soup = BeautifulSoup(driver.page_source, "html.parser")
        page_text = soup.get_text()

        discount_data = extract_discount_percentages(page_text)
        banner_text = extract_promo_banners(soup, name)
        categories = extract_sale_categories(soup)

        return {
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "retailer": name,
            "url": url,
            "max_discount_pct": discount_data["max_discount"],
            "avg_discount_pct": discount_data["avg_discount"],
            "discounts_found": discount_data["discounts_found"],
            "promo_banners": banner_text,
            "sale_categories": categories,
            "status": "success"
        }

    except Exception as e:
        return {
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "retailer": name,
            "url": url,
            "max_discount_pct": 0,
            "avg_discount_pct": 0,
            "discounts_found": "",
            "promo_banners": "",
            "sale_categories": "",
            "status": f"error: {str(e)}"
        }

def save_to_csv(data, filename="promo_log.csv"):
    df_new = pd.DataFrame([data])

    if os.path.exists(filename):
        df_existing = pd.read_csv(filename)
        df_combined = pd.concat([df_existing, df_new], ignore_index=True)
    else:
        df_combined = df_new

    df_combined.to_csv(filename, index=False)

def main():
    print(f"Running promo scan at {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("Starting browser...")

    driver = get_driver()

    try:
        for name, url in RETAILERS.items():
            print(f"Scanning {name}...")
            data = scrape_retailer(driver, name, url)

            signal, rolling_avg = get_rolling_signal(name, data["max_discount_pct"])
            data["rolling_avg_discount"] = rolling_avg
            data["promo_signal"] = signal

            save_to_csv(data)
            print(f"  Max: {data['max_discount_pct']}% | Banners: {data['promo_banners'][:80] if data['promo_banners'] else 'none'}")

    finally:
        driver.quit()

    print("\nScan complete. Check promo_log.csv for results.")

if __name__ == "__main__":
    main()
