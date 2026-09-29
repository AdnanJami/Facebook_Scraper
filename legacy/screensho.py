from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from PIL import Image
import io
from time import sleep
import json
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By

# Configuration
MAX_SCROLLS = 50  # Maximum number of scrolls to prevent infinite loops
MAX_NO_CHANGE = 3  # Stop if height doesn't change after this many scrolls
SCROLL_PAUSE = 1.5  # Seconds to wait after each scroll for content to load

try:
    # --- Setup driver ---
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    # --- Open Facebook and load cookies ---
    print("Loading Facebook...")
    driver.get("https://www.facebook.com/")
    
    try:
        with open("cookies.json", "r") as f:
            cookies = json.load(f)
        
        cookie_count = 0
        for cookie in cookies:
            cookie.pop("sameSite", None)
            try:
                driver.add_cookie(cookie)
                cookie_count += 1
            except Exception as e:
                print(f"Could not add cookie: {e}")
        
        print(f"✅ Loaded {cookie_count} cookies")
    except FileNotFoundError:
        print("⚠️ cookies.json not found - continuing without cookies")

    driver.refresh()
    sleep(8)

    # --- Go to group ---
    group_url = "https://www.facebook.com/groups/263561763818649"
    print(f"Navigating to group: {group_url}")
    driver.get(group_url)
    sleep(5)

    # --- Hide navbar for clean screenshot ---
    driver.execute_script("""
    var header = document.querySelector('div[role="banner"]'); 
    if (header) { header.style.display = 'none'; }
    """)
    sleep(0.5)

    # --- Find the middle content section ---
    print("Finding main content section...")
    try:
        middle_section = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//div[@role='main']"))
        )
        print("✅ Found main section")
    except:
        try:
            middle_section = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "[role='feed']"))
            )
            print("✅ Found feed section")
        except:
            raise Exception("Could not find main content section")

    # --- Scroll through the middle section and capture screenshots ---
    viewport_height = driver.execute_script("return window.innerHeight")
    total_height = driver.execute_script("return arguments[0].scrollHeight", middle_section)
    print(f"Initial section height: {total_height}px")
    print(f"Viewport height: {viewport_height}px")

    screenshots = []
    current_scroll = 0
    scroll_count = 0
    no_change_count = 0
    last_height = total_height

    print("\nStarting scroll and capture...")
    while current_scroll < total_height and scroll_count < MAX_SCROLLS:
        # Scroll to current position
        driver.execute_script("arguments[0].scrollTop = arguments[1];", middle_section, current_scroll)
        sleep(SCROLL_PAUSE)
        
        # Check if new content loaded
        new_height = driver.execute_script("return arguments[0].scrollHeight", middle_section)
        
        if new_height > last_height:
            total_height = new_height
            last_height = new_height
            no_change_count = 0
            print(f"📈 New content loaded. Height: {new_height}px")
        else:
            no_change_count += 1
            if no_change_count >= MAX_NO_CHANGE:
                print(f"✅ Reached end of feed (no new content after {MAX_NO_CHANGE} scrolls)")
                break
        
        # Capture screenshot
        png = middle_section.screenshot_as_png
        img = Image.open(io.BytesIO(png))
        screenshots.append(img)
        
        current_scroll += viewport_height
        scroll_count += 1
        print(f"📸 Screenshot {scroll_count} captured (scroll position: {current_scroll}px)")

    if not screenshots:
        raise Exception("No screenshots were captured!")

    print(f"\n✅ Captured {len(screenshots)} screenshots")

    # --- Stitch screenshots vertically ---
    print("Stitching images together...")
    width = screenshots[0].width
    height = sum(img.height for img in screenshots)
    
    stitched_image = Image.new('RGB', (width, height))
    y_offset = 0
    
    for i, img in enumerate(screenshots):
        stitched_image.paste(img, (0, y_offset))
        y_offset += img.height
        if (i + 1) % 10 == 0:
            print(f"Processed {i + 1}/{len(screenshots)} images...")

    # Save the final image
    output_file = "middle_section_screenshot.png"
    stitched_image.save(output_file)
    print(f"\n✅ Screenshot saved as '{output_file}'")
    print(f"Final image size: {width}x{height}px")

except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()

finally:
    # Always close the driver
    if 'driver' in locals():
        driver.quit()
        print("\n🔒 Browser closed")