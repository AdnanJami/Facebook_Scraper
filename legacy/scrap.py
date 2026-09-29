import json
from time import sleep
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import random

def click_see_more_buttons(driver):
    """
    Finds and clicks 'See more' buttons only in posts, excluding comment sections.
    """
    try:
        # More specific selectors that target post content, not comments
        selectors = [
            # Target "See more" in post content area (not in comments)
            "//div[@data-ad-preview='message']//div[contains(text(), 'See more')]",
            "//div[contains(@class, 'userContent')]//div[contains(text(), 'See more')]",
            "//div[contains(@class, 'userContent')]//span[contains(text(), 'See more')]",
            # Generic but filtered approach
            "//div[contains(text(), 'See more')]",
            "//span[contains(text(), 'See more')]",
        ]
        
        clicked_count = 0
        processed_buttons = set()
        
        for selector in selectors:
            try:
                buttons = driver.find_elements(By.XPATH, selector)
                
                for button in buttons:
                    try:
                        # Skip if already processed
                        button_id = id(button)
                        if button_id in processed_buttons:
                            continue
                        
                        # Get the button's text to verify
                        button_text = button.text.strip().lower()
                        if 'see more' not in button_text:
                            continue
                        
                        # Check if button is in a comment section
                        # Comments are typically inside elements with specific attributes
                        parent_html = driver.execute_script(
                            "return arguments[0].closest('[role=\"article\"]')?.outerHTML || '';", 
                            button
                        )
                        
                        # Skip if it's in a comment section
                        # Comments often have specific markers
                        if any(keyword in parent_html.lower() for keyword in [
                            'comment', 
                            'aria-label="comment',
                            'data-commentid',
                            'comment_', 
                            'UFIComment'
                        ]):
                            # Additional check: see if "See more" is far down in the DOM
                            distance_from_top = driver.execute_script("""
                                var article = arguments[0].closest('[role="article"]');
                                if (!article) return 9999;
                                var rect = arguments[0].getBoundingClientRect();
                                var articleRect = article.getBoundingClientRect();
                                return rect.top - articleRect.top;
                            """, button)
                            
                            # If the button is more than 800px from top of article, likely a comment
                            if distance_from_top > 800:
                                continue
                        
                        # Check if button is visible and clickable
                        if button.is_displayed() and button.is_enabled():
                            # Scroll the button into view slowly
                            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", button)
                            time.sleep(random.uniform(1.0, 2.0))  # Increased wait time
                            
                            # Click the button
                            try:
                                button.click()
                            except:
                                # Try JavaScript click if regular click fails
                                driver.execute_script("arguments[0].click();", button)
                            
                            clicked_count += 1
                            processed_buttons.add(button_id)
                            print(f"Clicked 'See more' in post #{clicked_count}")
                            
                            # Longer delay after clicking
                            time.sleep(random.uniform(2.0, 4.0))  # Increased from 0.5-1.0
                    except Exception as e:
                        # Button might have become stale or unclickable
                        continue
            except Exception as e:
                continue
        
        if clicked_count > 0:
            print(f"Total post 'See more' buttons clicked: {clicked_count}")
        
        return clicked_count
    
    except Exception as e:
        print(f"Error clicking 'See more' buttons: {e}")
        return 0

def smooth_scroll_with_expansion(driver, scroll_pause_range=(1.5, 3.0), scroll_increment_range=(80, 200), max_scroll_time=300):
    """
    Smoothly scrolls down the page and expands 'See more' buttons in posts only.
    Much slower and more human-like behavior.
    """
    start_time = time.time()
    last_height = driver.execute_script("return document.body.scrollHeight")
    scroll_count = 0
    
    print("Starting SLOW smooth scrolling with post expansion (ignoring comments)...")

    while True:
        # Get current scroll position
        current_position = driver.execute_script("return window.pageYOffset;")
        
        # Smaller random scroll increment for slower, more natural behavior
        scroll_by = random.randint(*scroll_increment_range)
        
        # Smooth scroll using scrollBy with behavior
        driver.execute_script(f"window.scrollBy({{top: {scroll_by}, behavior: 'smooth'}});")
        
        # Longer pause for human-like behavior
        time.sleep(random.uniform(*scroll_pause_range))
        
        scroll_count += 1
        
        # Every 2-4 scrolls, try to click 'See more' buttons in posts
        if scroll_count % random.randint(2, 4) == 0:
            print("\nChecking for 'See more' buttons in posts...")
            click_see_more_buttons(driver)
            time.sleep(random.uniform(2.0, 4.0))  # Longer wait after checking
        
        # More frequent longer pauses (simulating reading)
        if random.random() < 0.3:  # 30% chance (increased from 15%)
            pause_time = random.uniform(3.0, 6.0)  # Much longer pauses
            print(f"Pause for {pause_time:.1f}s (simulating reading)...")
            time.sleep(pause_time)

        # Check new height after scrolling
        new_height = driver.execute_script("return document.body.scrollHeight")
        current_position = driver.execute_script("return window.pageYOffset;")
        
        # Check if we're near the bottom
        viewport_height = driver.execute_script("return window.innerHeight;")
        if current_position + viewport_height >= new_height - 100:
            # Wait longer for lazy loading
            time.sleep(5)
            
            # Try clicking any remaining 'See more' buttons in posts
            print("\nFinal check for 'See more' buttons in posts...")
            click_see_more_buttons(driver)
            
            final_height = driver.execute_script("return document.body.scrollHeight")
            
            if final_height == new_height:  # No new content loaded
                print("Reached end of page.")
                break
            else:
                last_height = final_height

        # Stop if max time exceeded
        if (time.time() - start_time) > max_scroll_time:
            print("Max scroll time reached.")
            break

    print("Smooth scrolling finished.")

try:
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.get("https://www.facebook.com/")
    print("Opened Facebook")

    # 🧠 Load cookies
    with open("cookies.json", "r") as f:
        cookies = json.load(f)

    for cookie in cookies:
        cookie.pop("sameSite", None)
        driver.add_cookie(cookie)

    print("Cookies loaded successfully!")

    # Refresh to apply login session
    driver.refresh()
    sleep(8)  # Increased wait time

    print("Logged in using cookies!")

    # 🧭 Go to group
    group_url = "https://www.facebook.com/groups/263561763818649"
    driver.get(group_url)
    sleep(15)  # Increased initial wait time

    # 🔽 MUCH SLOWER smooth scroll with post expansion (ignoring comments)
    smooth_scroll_with_expansion(
        driver, 
        scroll_pause_range=(1.5, 3.0),      # Increased from (0.15, 0.4)
        scroll_increment_range=(80, 200),    # Decreased from (150, 400)
        max_scroll_time=60                  # Increased from 120
    )

    # 💾 Update cookies
    with open("cookies.json", "w") as f:
        json.dump(driver.get_cookies(), f)
    print("Cookies updated and saved.")

except Exception as e:
    print("The error raised is:", e)
finally:
    if 'driver' in locals():
        driver.quit()