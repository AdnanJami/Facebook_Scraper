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
from PIL import Image
from selenium.webdriver.common.action_chains import ActionChains


def move_mouse_away(driver):
    """Move mouse to a safe area to prevent hover previews."""
    try:
        # Move mouse to top-left corner or outside content area
        actions = ActionChains(driver)
        actions.move_by_offset(0, 0).perform()
        # Reset action chains
        actions.reset_actions()
    except:
        pass

def stitch_vertical(images, output_path="stitched_vertical.png"):
    """Stitch images vertically in order.
    
    Args:
        images: List of PIL Image objects
        output_path: Path to save the stitched image
    """
    if not images:
        print("❌ No images provided")
        return
    
    try:
        # Calculate max width and total height
        max_width = max(img.width for img in images)
        total_height = sum(img.height for img in images)
        
        # Create blank final image
        stitched = Image.new("RGB", (max_width, total_height), (255, 255, 255))
        
        # Paste images one by one (centered horizontally)
        current_y = 0
        for img in images:
            # Center horizontally if image is narrower than max_width
            x_offset = (max_width - img.width) // 2
            stitched.paste(img, (x_offset, current_y))
            current_y += img.height
        
        # Save
        stitched.save(output_path)
        print(f"✔ Vertical stitched image saved to: {output_path}")
        
    except Exception as e:
        print(f"❌ Error: {e}")

def click_see_more_buttons(driver):
    """
    Clicks ONLY the 'See more' buttons that are currently visible
    inside the viewport (not below the fold).
    """

    selectors = [
        "//div[@data-ad-preview='message']//div[contains(text(),'See more')]",
        "//div[contains(@class,'userContent')]//div[contains(text(),'See more')]",
        "//div[contains(@class,'userContent')]//span[contains(text(),'See more')]",
        "//div[contains(text(),'See more')]",
        "//span[contains(text(),'See more')]",
    ]

    clicked = 0
    viewport_clicked = set()

    def is_in_viewport(elem):
        """Return True if the element is physically in the visible viewport."""
        return driver.execute_script("""
            const rect = arguments[0].getBoundingClientRect();
            return (
                rect.top >= 0 &&
                rect.left >= 0 &&
                rect.bottom <= (window.innerHeight || document.documentElement.clientHeight) &&
                rect.right <= (window.innerWidth || document.documentElement.clientWidth)
            );
        """, elem)

    for selector in selectors:
        try:
            buttons = driver.find_elements(By.XPATH, selector)

            for btn in buttons:
                try:
                    if id(btn) in viewport_clicked:
                        continue

                    # Must contain "See more"
                    if "see more" not in btn.text.lower():
                        continue

                    # Must be in visible viewport
                    if not is_in_viewport(btn):
                        continue

                    # Try clicking
                    try:
                        btn.click()
                    except:
                        driver.execute_script("arguments[0].click();", btn)

                    viewport_clicked.add(id(btn))
                    clicked += 1
                    print(f"✔ Clicked See more in viewport #{clicked}")

                    time.sleep(random.uniform(0.5, 1.2))

                except:
                    continue

        except:
            continue

    print(f"Total clicked in viewport: {clicked}")
    return clicked


def comment_find(driver):
    """
    Detects Facebook comment input that is visible in the viewport.
    """
    # More specific selectors for Facebook comment boxes
    xpaths = [
        "//div[@role='textbox' and @contenteditable='true' and contains(@aria-label, 'comment')]",
        "//div[@role='textbox' and @contenteditable='true' and contains(@aria-label, 'Comment')]",
        "//div[contains(@class, 'commentable')]//div[@role='textbox']",
    ]
    
    comment_found = False

    def is_in_viewport(elem):
        return driver.execute_script("""
            const rect = arguments[0].getBoundingClientRect();
            return (
                rect.top >= 0 &&
                rect.left >= 0 &&
                rect.bottom <= (window.innerHeight || document.documentElement.clientHeight) &&
                rect.right <= (window.innerWidth || document.documentElement.clientWidth) &&
                rect.width > 0 &&
                rect.height > 0
            );
        """, elem)

    try:
        for xpath in xpaths:
            elements = driver.find_elements(By.XPATH, xpath)
            for el in elements:
                try:
                    aria = (el.get_attribute("aria-label") or "").lower()
                    placeholder = (el.get_attribute("aria-placeholder") or "").lower()
                    
                    # Check for comment-related attributes
                    if "comment" in aria or "comment" in placeholder:
                        if is_in_viewport(el):
                            print(f"✔ Comment box detected (aria: '{aria}')")
                            comment_found = True
                            return True
                except:
                    continue
                    
    except Exception as e:
        print("Error detecting comment box:", e)

    return comment_found


def screenshot_and_scroll(driver, screenshot_folder="screenshots",
                          scroll_delay=2.0, max_screenshots=100):

    if not os.path.exists(screenshot_folder):
        os.makedirs(screenshot_folder)
        print(f"Created folder {screenshot_folder}")
    
    images = []
    screenshot_count = 0
    print("📸 Starting screenshot + scroll...")
    
    driver.execute_script(
        "window.scrollBy({top: arguments[0], behavior: 'smooth'});",
        100
    )
    print("⬇ Scrolled to watch for initial content...")
    time.sleep(scroll_delay)
    
    while screenshot_count < max_screenshots:
        move_mouse_away(driver)
        time.sleep(0.3)
        try:
            print("\nExpanding posts...")
            click_see_more_buttons(driver)

            # Check for comment box BEFORE screenshot
            comm = comment_find(driver)
            
             
            
            # Take screenshot
            img = ImageGrab.grab(bbox=(510, 294, 1290, 1017))
            images.append(img)
            
            print(f"📸 Captured image (total in buffer: {len(images)})")

            # If comment found, stitch and save
            # Count consecutive comment detections
            if comm:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                file_path = os.path.join(
                    screenshot_folder,
                    f"screenshot_{screenshot_count:04d}_{timestamp}.png"
                )
                
                stitch_vertical(images, output_path=file_path)
                print(f"💾 Saved stitched screenshot {screenshot_count}: {file_path}")

                # Reset for next post
                images = []
                images.append(img)
                screenshot_count += 1

                

            # Scroll detection
            current_y = driver.execute_script("return window.pageYOffset;")
            page_height = driver.execute_script("return document.body.scrollHeight;")
            viewport_height = driver.execute_script("return window.innerHeight;")

            # Check bottom reached
            if current_y + viewport_height >= page_height - 100:
                print("⚠ Bottom reached. Checking for new content...")
                time.sleep(3)
                new_height = driver.execute_script("return document.body.scrollHeight;")
                if new_height == page_height:
                    print("✅ No new content. Stopping.")
                    # Save remaining images if any
                    if images:
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                        file_path = os.path.join(
                            screenshot_folder,
                            f"screenshot_{screenshot_count:04d}_{timestamp}_final.png"
                        )
                        stitch_vertical(images, output_path=file_path)
                        print(f"💾 Saved final stitched screenshot: {file_path}")
                    break

            # Scroll smoothly
            driver.execute_script(
                "window.scrollBy({top: arguments[0], behavior: 'smooth'});",
                viewport_height - 170
            )
            print(f"⬇ Scrolled {viewport_height - 160}px")
            time.sleep(scroll_delay)

        except Exception as e:
            print(f"Error during screenshot: {e}")
            time.sleep(1)
            continue

    print(f"\nDone! Total stitched screenshots: {screenshot_count}")

# ---------------------
# MAIN
# ---------------------

try:
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
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
    time.sleep(5)

    print("Logged in successfully!")

    # Navigate to group
    group_url = "https://www.facebook.com/groups/818281132525134"
    driver.get(group_url)
    time.sleep(10)

    # Start screenshot process
    screenshot_and_scroll(
        driver,
        screenshot_folder="facebook_screenshots",
        scroll_delay=2.0,
        max_screenshots=20,
    )

    # Save cookies
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
