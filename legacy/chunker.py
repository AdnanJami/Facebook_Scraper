import json
import time
import os
import random
import pyperclip
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# -----------------------------
# Helper Functions
# -----------------------------

def get_post_link_in_viewport(driver):
    """
    Finds the Share button inside the visible viewport, clicks it, selects Copy Link,
    and returns the post URL.
    """
    try:
        def is_in_viewport(elem):
            return driver.execute_script("""
                const rect = arguments[0].getBoundingClientRect();
                return (
                    rect.top >= 0 &&
                    rect.left >= 0 &&
                    rect.bottom <= (window.innerHeight || document.documentElement.clientHeight) &&
                    rect.right <= (window.innerWidth || document.documentElement.clientWidth) &&
                    rect.height > 20 &&
                    rect.width > 20
                );
            """, elem)

        # Find all Share buttons
        share_buttons = driver.find_elements(
            By.XPATH,
            "//div[@role='button']//span[text()='Share' or text()='Share...']/ancestor::div[@role='button']"
        )
        if not share_buttons:
            print("❌ No Share buttons found.")
            return None

        # Filter buttons in viewport
        visible_buttons = [b for b in share_buttons if is_in_viewport(b)]
        if not visible_buttons:
            print("❌ No Share buttons in viewport.")
            return None

        share_btn = visible_buttons[0]

        # Scroll into view and adjust
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", share_btn)
        driver.execute_script("window.scrollBy(0, -80);")
        time.sleep(0.3)

        # Click Share button via JS
        driver.execute_script("arguments[0].click();", share_btn)
        time.sleep(0.6)

        # Wait for Copy Link button
        copy_btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//span[contains(text(),'Copy link') or contains(text(),'Copy Post Link')]"
            ))
        )
        driver.execute_script("arguments[0].click();", copy_btn)
        time.sleep(0.3)

        # Get link from clipboard
        link = pyperclip.paste()
        if "facebook.com" in link:
            print("✔ Copied link:", link)
            return link
        else:
            print("❌ Clipboard did not contain valid link")
            return None

    except Exception as e:
        print("❌ Error getting link:", e)
        return None


def scroll_page(driver, pixels):
    """
    Scrolls the page by a specific number of pixels (instant scroll)
    """
    old_y = driver.execute_script("return window.pageYOffset;")
    driver.execute_script("window.scrollBy(0, arguments[0]);", pixels)
    time.sleep(0.8)
    new_y = driver.execute_script("return window.pageYOffset;")
    return new_y != old_y  # True if scroll happened


def screenshot_and_scroll(driver, max_posts=5, scroll_pixels=700):
    """
    Scrolls through the page, detects posts in viewport, and gets their links
    """
    post_count = 0
    print(f"📄 Starting post capture. Max posts: {max_posts}")
    time.sleep(2)

    while post_count < max_posts:
        try:
            # Get post link in viewport
            post_link = get_post_link_in_viewport(driver)
            if post_link:
                post_count += 1
                print(f"✅ Post {post_count} link: {post_link}")

            # Scroll down
            scrolled = scroll_page(driver, scroll_pixels)
            if not scrolled:
                print("⚠ Reached bottom or scroll blocked. Exiting loop.")
                break

        except Exception as e:
            print(f"Error in scrolling loop: {e}")
            time.sleep(1)
            continue

    print(f"\n🎉 Done! Total posts captured: {post_count}")


# -----------------------------
# MAIN
# -----------------------------

try:
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.get("https://www.facebook.com/")
    print("🌐 Opened Facebook")

    # Load cookies
    with open("cookies.json", "r") as f:
        cookies = json.load(f)

    for cookie in cookies:
        cookie.pop("sameSite", None)
        driver.add_cookie(cookie)

    print("✅ Cookies loaded")
    driver.refresh()
    time.sleep(5)

    # Navigate to group
    group_url = "https://www.facebook.com/groups/263561763818649"
    driver.get(group_url)
    time.sleep(10)

    # Start scrolling and capturing posts
    screenshot_and_scroll(driver, max_posts=5, scroll_pixels=700)

    # Save updated cookies
    with open("cookies.json", "w") as f:
        json.dump(driver.get_cookies(), f)
    print("✅ Cookies updated")

except Exception as e:
    print("❌ Error:", e)

finally:
    try:
        driver.quit()
    except:
        pass
