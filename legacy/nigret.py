import json
import time
import random
import os
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from PIL import ImageGrab
from PIL import Image
def stitch_vertical(images):
    """Stitch images vertically."""
    widths = [img.width for img in images]
    heights = [img.height for img in images]

    max_width = max(widths)
    total_height = sum(heights)

    result = Image.new("RGB", (max_width, total_height), (255, 255, 255))

    y = 0
    for img in images:
        result.paste(img, (0, y))
        y += img.height

    return result
def expand_post(driver, post_element):
    """Expand 'See more' inside a single post."""
    selectors = [
        ".//div[contains(text(),'See more')]",
        ".//span[contains(text(),'See more')]",
    ]

    for sel in selectors:
        try:
            buttons = post_element.find_elements(By.XPATH, sel)
            for btn in buttons:
                if "see more" in btn.text.lower():
                    try:
                        btn.click()
                    except:
                        driver.execute_script("arguments[0].click();", btn)
                    time.sleep(random.uniform(0.2, 0.5))
        except:
            pass
def capture_post(driver, post_element, save_dir, index):
    """
    Capture a full Facebook post by scrolling through it and stitching.
    """

    if not os.path.exists(save_dir):
        os.makedirs(save_dir)

    expand_post(driver, post_element)      # expand post content first
    time.sleep(0.6)

    parts = []
    viewport_height = driver.execute_script("return window.innerHeight;")

    # Scroll post top into view
    driver.execute_script("arguments[0].scrollIntoView(true);", post_element)
    time.sleep(0.7)

    while True:
        # Capture screenshot region (your coordinates)
        img = ImageGrab.grab(bbox=(510, 294, 1290, 1017))
        parts.append(img)

        # Check if post bottom is on screen
        rect = driver.execute_script("""
            const r = arguments[0].getBoundingClientRect();
            return {top: r.top, bottom: r.bottom, height: r.height};
        """, post_element)

        if rect["bottom"] <= viewport_height:
            break

        # Scroll slightly to capture next part
        driver.execute_script("window.scrollBy(0, arguments[0]);", viewport_height - 180)
        time.sleep(0.8)

    stitched = stitch_vertical(parts)

    outfile = os.path.join(save_dir, f"post_{index:03d}.png")
    stitched.save(outfile)
    print("📌 Saved stitched post:", outfile)
def capture_all_posts(driver, max_posts=10, save_dir="facebook_posts"):
    post_selector = "//div[@role='article']"

    time.sleep(3)
    posts = driver.find_elements(By.XPATH, post_selector)

    print(f"🔍 Found {len(posts)} posts on screen")

    count = min(max_posts, len(posts))

    for i in range(count):
        print(f"\n===== 📸 Capturing Post {i+1}/{count} =====")
        try:
            capture_post(driver, posts[i], save_dir, i)
        except Exception as e:
            print("Error capturing post:", e)
try:
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )
    driver.get("https://www.facebook.com/")
    print("Opened Facebook")

    # Load cookies
    with open("cookies.json", "r") as f:
        cookies = json.load(f)

    for cookie in cookies:
        cookie.pop("sameSite", None)
        driver.add_cookie(cookie)

    print("Cookies loaded!")
    driver.refresh()
    time.sleep(6)

    print("Logged in successfully!")

    # Navigate to group
    group_url = "https://www.facebook.com/groups/263561763818649"
    driver.get(group_url)
    time.sleep(10)

    # Capture posts
    capture_all_posts(driver, max_posts=10, save_dir="stitched_posts")

    # Update cookies
    with open("cookies.json", "w") as f:
        json.dump(driver.get_cookies(), f)
    print("Cookies updated.")

except Exception as e:
    print("Error:", e)

finally:
    try:
        driver.quit()
    except:
        pass
