"""Wall detection for login/captcha/interstitial pages."""

import re

WALL_URL_PATTERNS = [
    r"login",
    r"signin",
    r"sign-in",
    r"/auth",
    r"challenge",
    r"captcha",
    r"verify",
]

WALL_TITLE_PATTERNS = [
    r"sign in",
    r"log in",
    r"verify you are human",
    r"just a moment",
    r"attention required",
    r"robot",
]

WALL_SELECTORS = [
    'iframe[src*="recaptcha"]',
    'iframe[title*="recaptcha" i]',
    "#cf-challenge-running",
    ".cf-browser-verification",
    "#challenge-form",
    'form[action*="login"]',
    'input[type="password"]',
    '[data-testid="login-form"]',
    ".g-recaptcha",
]


async def url_indicates_wall(url: str) -> bool:
    lower = url.lower()
    return any(re.search(pat, lower) for pat in WALL_URL_PATTERNS)


async def is_yc_logged_in(page) -> bool:
    try:
        if await page.locator("text=/My profile/i").count() > 0:
            return True
        if await page.locator('a[href*="/inbox"], text=/Inbox/i').count() > 0:
            return True
        if await page.locator('text=/Showing \\d+ matching startups/i').count() > 0:
            return True
    except Exception:
        pass
    return False


async def is_yc_listing_ready(page) -> bool:
    if not await is_yc_logged_in(page):
        return False
    try:
        if await page.locator('a:has-text("View job")').count() > 0:
            return True
        if await page.locator("text=/Showing \\d+ matching startups/i").count() > 0:
            return True
    except Exception:
        pass
    return False


async def detect_google_wall(page) -> bool:
    url = page.url.lower()
    if "/sorry/" in url or "google.com/sorry" in url:
        return True

    try:
        body_sample = (await page.inner_text("body"))[:2500].lower()
    except Exception:
        body_sample = ""

    if any(
        phrase in body_sample
        for phrase in (
            "unusual traffic",
            "not a robot",
            "verify you're not a robot",
            "verify you are not a robot",
        )
    ):
        return True

    try:
        if await page.locator('iframe[src*="recaptcha"], #recaptcha, form#captcha').count() > 0:
            return True
    except Exception:
        pass

    title = (await page.title()).lower()
    if "sorry" in title or "unusual traffic" in title:
        return True

    try:
        if await page.locator("#search, div#rso, div[data-async-context]").count() > 0:
            return False
    except Exception:
        pass

    return False


async def detect_crunchbase_wall(page) -> bool:
    url = page.url.lower()
    if "crunchbase.com" not in url:
        return False
    if any(token in url for token in ("/login", "/sign_in", "/sign-in", "/register")):
        return True

    try:
        body_sample = (await page.inner_text("body"))[:2000].lower()
    except Exception:
        body_sample = ""

    if "sign in to crunchbase" in body_sample or "log in to crunchbase" in body_sample:
        return True

    try:
        if await page.locator('input[type="password"], iframe[src*="recaptcha"]').count() > 0:
            if "sign in" in body_sample or "log in" in body_sample:
                return True
    except Exception:
        pass
    return False


async def detect_yc_wall(page) -> bool:
    url = page.url.lower()
    if "workatastartup.com" not in url:
        return False
    if await is_yc_logged_in(page):
        return False
    if any(token in url for token in ("/login", "/sign_in", "/sign-in", "/auth", "/users/sign_in")):
        return True
    try:
        if await page.locator('input[type="password"], form[action*="login"], form[action*="sign_in"]').count() > 0:
            return True
    except Exception:
        pass
    try:
        if await page.locator('a:has-text("Log in"), button:has-text("Log in"), a:has-text("Sign in")').count() > 0:
            return True
    except Exception:
        pass
    return False


async def detect_wall(page, source: str | None = None) -> bool:
    url = page.url.lower()
    if "google." in url:
        return await detect_google_wall(page)
    if "crunchbase.com" in url or source == "crunchbase":
        return await detect_crunchbase_wall(page)
    if "workatastartup.com" in url or source == "yc":
        return await detect_yc_wall(page)

    if await url_indicates_wall(page.url):
        return True

    title = (await page.title()).lower()
    if any(re.search(pat, title) for pat in WALL_TITLE_PATTERNS):
        return True

    for selector in WALL_SELECTORS:
        try:
            if await page.locator(selector).count() > 0:
                if selector == 'input[type="password"]':
                    sign_in_text = await page.locator(
                        'text=/sign in|log in|create account/i'
                    ).count()
                    if sign_in_text > 0:
                        return True
                else:
                    return True
        except Exception:
            continue
    return False


async def wall_cleared(page, source: str | None = None) -> bool:
    if source == "yc" and "workatastartup.com" in page.url.lower():
        return await is_yc_logged_in(page)
    return not await detect_wall(page, source=source)
