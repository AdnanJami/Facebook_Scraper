// Usage:
// node fb_group_scrap.js "https://www.facebook.com/groups/<group-id>" cookies.json 50

import fs from "fs/promises";
import playwright from "playwright";

async function loadCookies(context, cookieFile) {
  try {
    const cookies = JSON.parse(await fs.readFile(cookieFile, "utf8"));
    // Fix invalid sameSite values
    const validCookies = cookies.map(c => ({
      ...c,
      sameSite:
        c.sameSite === "no_restriction"
          ? "None"
          : c.sameSite?.charAt(0).toUpperCase() + c.sameSite?.slice(1).toLowerCase(),
    }));
    await context.addCookies(validCookies);
    console.log("✅ Cookies loaded successfully");
  } catch (err) {
    console.error("⚠️ Could not load cookies:", err.message);
  }
}

async function scrollAndCollect(page, maxPosts = 50) {
  const collected = new Set();
  const posts = [];

  while (posts.length < maxPosts) {
    const newPosts = await page.$$eval('div[role="article"]', async (articles) => {
      const results = [];

      for (const a of articles) {
        // Skip if this article is likely a comment (has comment_id in link)
        const linkElem = Array.from(a.querySelectorAll("a[href*='/posts/']"))
          .map(el => el.href)
          .find(href => !href.includes("comment_id"));
        if (!linkElem) continue;

        // Click "See more" to expand long posts
        const seeMore = a.querySelector("div[role='button'][aria-label*='See more']");
        if (seeMore) seeMore.click();

        // Get main content (longest text block inside the article)
        const possibleTextBlocks = a.querySelectorAll("div[dir='auto']");
        let contentElem = "";
        let maxLength = 0;
        for (const block of possibleTextBlocks) {
          if (block.innerText && block.innerText.length > maxLength) {
            contentElem = block.innerText.trim();
            maxLength = block.innerText.length;
          }
        }

        if (!contentElem) continue;

        // Poster name
        const posterName = a.querySelector("h2 strong a")?.innerText || "";

        // Post date
        const postDate = a.querySelector("abbr")?.getAttribute("title") || "";

        results.push({
          content: contentElem,
          link: linkElem,
          poster: posterName,
          date: postDate
        });
      }

      return results;
    });

    for (const post of newPosts) {
      if (!collected.has(post.link)) {
        collected.add(post.link);
        posts.push(post);
      }
    }

    console.log(`📜 Collected ${posts.length} posts...`);

    if (posts.length >= maxPosts) break;

    await page.evaluate(() => window.scrollBy(0, window.innerHeight));
    await page.waitForTimeout(2500);
  }

  return posts.slice(0, maxPosts);
}

(async () => {
  const groupUrl = process.argv[2];
  const cookieFile = process.argv[3];
  const maxPosts = parseInt(process.argv[4]) || 50;

  if (!groupUrl) {
    console.error("❌ Please provide a Facebook group URL.");
    process.exit(1);
  }

  const browser = await playwright.chromium.launch({ headless: false, slowMo: 100 });
  const context = await browser.newContext();
  await loadCookies(context, cookieFile);
  const page = await context.newPage();

  console.log("🌐 Navigating to group page...");
  await page.goto(groupUrl, { waitUntil: "domcontentloaded", timeout: 120000 });

  console.log("🔍 Collecting posts...");
  const posts = await scrollAndCollect(page, maxPosts);

  console.log(`✅ Scraped ${posts.length} posts.`);
  await fs.writeFile("posts.json", JSON.stringify(posts, null, 2));
  console.log("💾 Saved to posts.json");

  await browser.close();
})();
