// facebook_cookies_scraper.js
// Usage: node facebook_cookies_scraper.js "https://www.facebook.com/groups/YOUR_GROUP_ID" [cookies.json]
// If cookies.json doesn't exist the script will open a browser for manual login and save cookies.

const puppeteer = require('puppeteer-extra');
const fs = require('fs').promises;
const path = require('path');

const DEFAULT_COOKIES_FILE = process.argv[3] || 'cookies.json';
const GROUP_URL = process.argv[2] || 'https://www.facebook.com/groups/263561763818649';
const OUTPUT_FILE = 'facebook_posts_with_cookies.json';

async function fileExists(p) {
  try { await fs.access(p); return true; } catch { return false; }
}

async function saveCookies(page, cookiesPath) {
  const cookies = await page.cookies();
  await fs.writeFile(cookiesPath, JSON.stringify(cookies, null, 2), 'utf8');
  console.log(`Saved ${cookies.length} cookies to ${cookiesPath}`);
}

async function loadCookiesFromFile(cookiesPath) {
  const raw = await fs.readFile(cookiesPath, 'utf8');
  const cookies = JSON.parse(raw);
  return cookies;
}

/**
 * Main scraping flow:
 * - Launches Puppeteer
 * - Navigates to facebook.com
 * - If cookies file exists -> set cookies and reload
 * - If not logged in -> open interactive window for manual login, then save cookies
 * - Navigate to group URL and scrape posts
 */
async function scrapeWithCookies(groupUrl, cookiesPath) {
  const isHeadless = false; // set true for headless use (Colab/CI)
  const launchOptions = {
    headless: isHeadless,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-blink-features=AutomationControlled'
    ],
  };

  // If running on systems like Colab set executablePath (example)
  // launchOptions.executablePath = '/usr/bin/chromium-browser';

  const browser = await puppeteer.launch(launchOptions);
  const page = await browser.newPage();
  
  // Better anti-detection measures
  await page.setViewport({ width: 1920, height: 1080 });
  
  // Set more realistic headers
  await page.setExtraHTTPHeaders({
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1'
  });
  
  await page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36');
  
  // Remove webdriver flag
  await page.evaluateOnNewDocument(() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => false });
    
    // Mock chrome object
    window.chrome = {
      runtime: {}
    };
    
    // Mock permissions
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
      parameters.name === 'notifications' ?
        Promise.resolve({ state: Notification.permission }) :
        originalQuery(parameters)
    );
  });

  // Go to facebook root to set cookies for the domain
  await page.goto('https://www.facebook.com', { waitUntil: 'networkidle2', timeout: 60000 });

  // If cookies file exists, load them
  if (await fileExists(cookiesPath)) {
    console.log(`Loading cookies from ${cookiesPath}...`);
    try {
      const cookies = await loadCookiesFromFile(cookiesPath);
      await page.setCookie(...cookies);
      await page.reload({ waitUntil: 'networkidle2' });
    } catch (err) {
      console.warn('Failed to load cookies:', err.message);
    }
  }

  // Check login status
  const loggedIn = await page.evaluate(() => {
    return !!document.querySelector('[aria-label="Home"], [aria-label="Create a post"], [role="navigation"] a[href*="profile.php"]');
  });

  if (!loggedIn) {
    console.log('Not logged in with cookies. Opening browser for manual login.');
    console.log('Please log in to Facebook in the opened browser window.');
    console.log(`After login completes, press Enter in this terminal to continue and save cookies to ${cookiesPath}.\n`);

    await new Promise(resolve => {
      process.stdin.resume();
      process.stdin.once('data', () => {
        process.stdin.pause();
        resolve();
      });
    });

    await new Promise(resolve => setTimeout(resolve, 3000));
    await saveCookies(page, cookiesPath);
  } else {
    console.log('Login detected via cookies.');
  }

  // Navigate to the group URL
  console.log('Navigating to group URL:', groupUrl);
  await page.goto(groupUrl, { waitUntil: 'networkidle2', timeout: 60000 });
  
  // Check if we're on a redirect page
  await new Promise(resolve => setTimeout(resolve, 3000));
  
  const isRedirectPage = await page.evaluate(() => {
    return document.body.innerText.includes('These community chats are read-only') || 
           document.body.innerText.includes('go to the group');
  });
  
  if (isRedirectPage) {
    console.log('\n⚠️  Facebook is showing a redirect page.');
    console.log('This happens when Facebook detects automation.');
    console.log('\n📋 MANUAL STEPS REQUIRED:');
    console.log('1. In the opened browser window, click any link to enter the group');
    console.log('2. Wait for the group feed to load (you should see posts)');
    console.log('3. Come back to this terminal and press ENTER to continue scraping\n');
    
    // Take screenshot of redirect page
    await page.screenshot({ path: 'redirect_page.png', fullPage: false });
    console.log('Screenshot saved to redirect_page.png\n');
    
    // Wait for user to press Enter
    await new Promise(resolve => {
      process.stdin.resume();
      process.stdin.once('data', () => {
        process.stdin.pause();
        resolve();
      });
    });
    
    console.log('Continuing with scraping...');
  }
  
  // Wait for posts to load
  console.log('Waiting for posts to load...');
  try {
    await page.waitForSelector('[role="article"]', { timeout: 10000 });
    console.log('Posts found on page!');
  } catch (e) {
    console.log('⚠️  No [role="article"] found.');
    
    // Check what's actually on the page
    const pageText = await page.evaluate(() => document.body.innerText.substring(0, 500));
    console.log('Page content sample:', pageText);
    
    console.log('\n🔄 The page may still be loading or using a different structure.');
    console.log('Taking a screenshot for inspection...\n');
  }
  
  // Take a screenshot for debugging
  await page.screenshot({ path: 'debug_screenshot.png', fullPage: false });
  console.log('Screenshot saved to debug_screenshot.png');
  
  await new Promise(resolve => setTimeout(resolve, 5000));

  // auto-scroll helper
  await autoScroll(page);

  // Expand "See more" buttons
  await expandAllPosts(page);

  // DEBUG: Check what we're actually finding
  const debugInfo = await page.evaluate(() => {
    const articles = document.querySelectorAll('[role="article"]');
    const feeds = document.querySelectorAll('[role="feed"]');
    const mains = document.querySelectorAll('[role="main"]');
    
    // Try to find any divs that might contain posts
    const allDivs = Array.from(document.querySelectorAll('div'));
    const divsWithText = allDivs
      .filter(d => d.innerText && d.innerText.length > 50 && d.innerText.length < 2000)
      .slice(0, 5)
      .map(d => ({
        text: d.innerText.substring(0, 150),
        id: d.id,
        classes: d.className.substring(0, 100),
        role: d.getAttribute('role'),
        dataTestId: d.getAttribute('data-testid')
      }));
    
    return {
      articleCount: articles.length,
      feedCount: feeds.length,
      mainCount: mains.length,
      pageTitle: document.title,
      bodyHTML: document.body.innerHTML.substring(0, 3000),
      divsWithText: divsWithText,
      allRoles: Array.from(new Set(allDivs.map(d => d.getAttribute('role')).filter(r => r)))
    };
  });
  
  console.log('\n=== DEBUG INFO ===');
  console.log('Page Title:', debugInfo.pageTitle);
  console.log('Articles found:', debugInfo.articleCount);
  console.log('Feeds found:', debugInfo.feedCount);
  console.log('Main sections found:', debugInfo.mainCount);
  console.log('All roles on page:', debugInfo.allRoles);
  console.log('\nSample divs with text:');
  debugInfo.divsWithText.forEach((div, i) => {
    console.log(`\nDiv ${i + 1}:`);
    console.log('  Text:', div.text);
    console.log('  Role:', div.role);
    console.log('  Data-testid:', div.dataTestId);
  });
  console.log('\nBody HTML (first 1000 chars):', debugInfo.bodyHTML.substring(0, 1000));
  console.log('==================\n');

  // IMPROVED: Extract posts with better selectors and fallbacks
  const posts = await page.evaluate(() => {
    let postElements = document.querySelectorAll('[role="article"]');
    
    // If no articles found, try alternative selectors
    if (postElements.length === 0) {
      console.log('No [role="article"] found, trying alternatives...');
      
      // Try finding posts by other common patterns
      const alternatives = [
        '[data-pagelet*="FeedUnit"]',
        '[data-pagelet*="GroupFeed"]',
        '[data-testid*="post"]',
        'div[class*="userContentWrapper"]',
        'div[data-ad-preview]'
      ];
      
      for (const selector of alternatives) {
        postElements = document.querySelectorAll(selector);
        if (postElements.length > 0) {
          console.log(`Found ${postElements.length} posts using selector: ${selector}`);
          break;
        }
      }
    }
    
    const results = [];
    const debug = [];
    
    if (postElements.length === 0) {
      debug.push({ error: 'No post elements found with any selector' });
      return { posts: results, debug };
    }
    
    postElements.forEach((post, index) => {
      try {
        // Get ALL text content as fallback
        const allText = post.innerText || '';
        
        // IMPROVED: Better text extraction with multiple fallbacks
        let text = '';
        
        // Try multiple selectors for post content
        const textSelectors = [
          '[data-ad-comet-preview="message"]',
          '[data-ad-preview="message"]',
          'div[dir="auto"][style*="text-align"]',
          'div[data-ad-comet-preview] > span',
          'span[dir="auto"]',
          'div[dir="auto"]'
        ];
        
        for (const selector of textSelectors) {
          const elements = post.querySelectorAll(selector);
          if (elements.length > 0) {
            // Get the longest text (likely the main post content)
            const texts = Array.from(elements)
              .map(el => el.innerText?.trim() || '')
              .filter(t => t.length > 0)
              .sort((a, b) => b.length - a.length);
            
            if (texts[0] && texts[0].length > 10) {
              text = texts[0];
              break;
            }
          }
        }
        
        // If no text found with selectors, try to extract from all divs
        if (!text) {
          const allDivs = Array.from(post.querySelectorAll('div'));
          const divTexts = allDivs
            .map(d => d.innerText?.trim() || '')
            .filter(t => t.length > 50 && t.length < 5000)
            .sort((a, b) => b.length - a.length);
          
          if (divTexts[0]) {
            text = divTexts[0];
          }
        }

        // IMPROVED: Better author extraction
        let author = 'Unknown';
        
        // Try multiple selectors for author name
        const authorSelectors = [
          'h2 a[role="link"]',
          'h3 a[role="link"]',
          'h4 a[role="link"]',
          'h2 span a[role="link"]',
          'h3 span a[role="link"]',
          'h4 span a[role="link"]',
          'a[role="link"] > span > span',
          'strong > span',
          'a[role="link"] strong',
          'h2 a',
          'h3 a',
          'h4 a'
        ];
        
        for (const selector of authorSelectors) {
          const authorElement = post.querySelector(selector);
          if (authorElement && authorElement.innerText && authorElement.innerText.trim().length > 0) {
            author = authorElement.innerText.trim();
            // Filter out if it's just a single character or looks like UI text
            if (author.length > 1 && !author.match(/^[0-9]+$/)) {
              break;
            }
          }
        }

        // IMPROVED: Better timestamp extraction
        let timestamp = 'Unknown';
        
        // Try multiple selectors for timestamp
        const timeSelectors = [
          'a[href*="/posts/"]',
          'a[href*="comment_id"]',
          'a[aria-label*="ago"]',
          'span[id^="jsc_"]',
          'abbr'
        ];
        
        for (const selector of timeSelectors) {
          const timeElements = post.querySelectorAll(selector);
          for (const timeElement of timeElements) {
            const timeText = timeElement.innerText?.trim() || timeElement.getAttribute('title') || timeElement.getAttribute('aria-label') || '';
            // Check if it looks like a timestamp
            if (timeText && (timeText.includes('ago') || timeText.includes('h') || timeText.includes('m') || timeText.includes('d') || timeText.includes('w') || timeText.match(/\d+\s*(hour|minute|day|week|month|year|sec)/i))) {
              timestamp = timeText;
              break;
            }
          }
          if (timestamp !== 'Unknown') break;
        }

        // Extract links
        const links = Array.from(post.querySelectorAll('a[href]'))
          .map(a => a.href)
          .filter(href => href.includes('facebook.com/') || href.startsWith('http'));

        // Add debug info for first post
        if (index === 0) {
          debug.push({
            textFound: !!text,
            textLength: text.length,
            authorFound: author !== 'Unknown',
            timestampFound: timestamp !== 'Unknown',
            linksCount: links.length,
            sampleText: text.substring(0, 100)
          });
        }

        // ALWAYS add the post (even with Unknown values) for debugging
        results.push({
          index: index + 1,
          author,
          text: text || allText.substring(0, 500), // Use full text as fallback
          timestamp,
          links: links.slice(0, 5)
        });
      } catch (err) {
        results.push({
          index: index + 1,
          author: 'Error',
          text: 'Error parsing: ' + err.message,
          timestamp: 'Error',
          links: []
        });
      }
    });
    
    // Return both results and debug info
    return { posts: results, debug };
  });

  console.log('Debug info from extraction:', JSON.stringify(posts.debug, null, 2));

  const dataToSave = {
    groupUrl,
    scrapedAt: new Date().toISOString(),
    totalPosts: posts.posts.length,
    posts: posts.posts
  };

  await fs.writeFile(OUTPUT_FILE, JSON.stringify(dataToSave, null, 2), 'utf8');
  console.log(`Saved ${posts.posts.length} posts to ${OUTPUT_FILE}`);

  await browser.close();
  return posts.posts;
}

// Helper functions
async function autoScroll(page) {
  console.log('Starting auto-scroll...');
  await page.evaluate(async () => {
    await new Promise((resolve) => {
      let totalHeight = 0;
      const distance = 300;
      const maxScrolls = 50; // Increased from 30
      let scrolls = 0;
      let lastHeight = 0;
      let sameHeightCount = 0;
      
      const timer = setInterval(() => {
        const currentHeight = document.body.scrollHeight;
        window.scrollBy(0, distance);
        totalHeight += distance;
        scrolls++;
        
        // Check if page height hasn't changed (reached bottom)
        if (currentHeight === lastHeight) {
          sameHeightCount++;
          if (sameHeightCount >= 3) {
            clearInterval(timer);
            resolve();
            return;
          }
        } else {
          sameHeightCount = 0;
        }
        lastHeight = currentHeight;
        
        if (scrolls >= maxScrolls) {
          clearInterval(timer);
          resolve();
        }
      }, 500); // Increased wait time from 300ms to 500ms
    });
  });
  console.log('Auto-scroll completed');
  
  // Wait for content to load after scrolling
  await new Promise(resolve => setTimeout(resolve, 2000));
}

async function expandAllPosts(page) {
  try {
    await page.evaluate(() => {
      const buttons = Array.from(document.querySelectorAll('div[role="button"], a[role="button"], span'))
        .filter(el => {
          const t = el.innerText || '';
          const short = t.trim().slice(0, 20);
          return /see more/i.test(short) || /আরও দেখুন/.test(short) || (t.length > 0 && t.length < 20 && /more|continue|full story|show more/i.test(t));
        });

      buttons.forEach(b => {
        try { b.click(); } catch (e) {}
      });
    });
    // Use setTimeout instead of waitForTimeout
    await new Promise(resolve => setTimeout(resolve, 1000));
    console.log('Attempted to expand posts');
  } catch (err) {
    console.warn('expandAllPosts error', err.message);
  }
}

// Run
scrapeWithCookies(GROUP_URL, DEFAULT_COOKIES_FILE)
  .then(posts => {
    console.log('Done. Posts count:', posts.length);
    process.exit(0);
  })
  .catch(err => {
    console.error('Fatal error:', err);
    process.exit(1);
  });