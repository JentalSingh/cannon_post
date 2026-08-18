#!/usr/bin/env python
# coding: utf-8

"""
Canon Community – Sign‑up Automation (with Cloudflare/Turnstile + reCAPTCHA Solver)
- Opens Canon forum thread URL
- Handles Cloudflare challenge (refresh + Turnstile + 2Captcha + manual fallback)
- Waits for page to fully load (document.readyState + header presence)
- Clicks "Sign In" using robust CSS selector (lia-component-users-action-login)
- Clicks "Register here" (searches in iframes + popups)
- Fills registration form with correct field names
- Uses HARDCORDED email: jiseh25704@ittiv.com
- Detects and solves reCAPTCHA using 2Captcha
- Clicks REGISTER
- Proxy rotation with Selenium Wire
- IP checker included
"""

import json
import os
import random
import re
import time
import traceback
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests
from seleniumwire import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException, JavascriptException, WebDriverException
from dotenv import load_dotenv
from faker import Faker

load_dotenv()

# ============================================================
# CONFIGURATION
# ============================================================
TARGET_URL = "https://community.usa.canon.com/t5/Desktop-Inkjet-Printers/My-Canon-Pixma-MG3600-printer-not-connecting-with-wifi/td-p/604861"
PROXY_FILE = Path("proxies.txt")
MAX_USERNAME_LENGTH = 14
HARDCODED_EMAIL = "jiseh25704@ittiv.com"  # <--- HARDCORDED EMAIL
TWO_CAPTCHA_API_KEY = os.getenv("TWO_CAPTCHA_API_KEY", "")
if TWO_CAPTCHA_API_KEY and len(TWO_CAPTCHA_API_KEY) == 32:
    print(f"🔑 2Captcha API Key loaded: {TWO_CAPTCHA_API_KEY[:4]}...{TWO_CAPTCHA_API_KEY[-4:]}")
else:
    print("⚠️ 2Captcha API Key is missing or invalid – CAPTCHA solving will fail.")

# ============================================================
# TURNSTILE INTERCEPT SCRIPT (CDP)
# ============================================================
TURNSTILE_INTERCEPT_SCRIPT = """
(() => {
  if (window.__tsInterceptorInstalled) return;
  window.__tsInterceptorInstalled = true;
  window.__tsParams = null;
  window.__tsCallback = null;
  console.clear = () => console.log("Console was cleared");
  const patch = () => {
    if (!window.turnstile || typeof window.turnstile.render !== "function" || window.turnstile.__codexPatched) return false;
    const originalRender = window.turnstile.render.bind(window.turnstile);
    window.turnstile.render = (container, options = {}) => {
      window.__tsParams = {
        sitekey: options.sitekey || null,
        pageurl: window.location.href,
        data: options.cData || null,
        pagedata: options.chlPageData || null,
        action: options.action || null,
        userAgent: navigator.userAgent,
        json: 1
      };
      window.cfCallback = typeof options.callback === "function" ? options.callback : null;
      console.log("intercepted-params:" + JSON.stringify(window.__tsParams));
      return originalRender(container, options);
    };
    window.turnstile.__codexPatched = true;
    return true;
  };
  const timer = setInterval(() => { if (patch()) clearInterval(timer); }, 50);
  setTimeout(() => clearInterval(timer), 20000);
})();
"""

# ============================================================
# PROXY LOADING & PARSING
# ============================================================
def load_proxies():
    proxies = []
    if PROXY_FILE.exists():
        with PROXY_FILE.open("r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    proxies.append(line)
    return proxies

def build_proxy_config(proxy_value):
    if not proxy_value:
        return None
    if "://" not in proxy_value and ":" in proxy_value:
        parts = proxy_value.split(":")
        if len(parts) == 2:
            host, port = parts
            return {"host": host, "port": int(port), "username": "", "password": "", "label": f"{host}:{port}"}
        elif len(parts) == 4:
            host, port, username, password = parts
            return {"host": host, "port": int(port), "username": username, "password": password, "label": f"{host}:{port}"}
    parsed = urlparse(proxy_value if "://" in proxy_value else f"http://{proxy_value}")
    if not parsed.hostname or not parsed.port:
        return None
    return {
        "host": parsed.hostname,
        "port": parsed.port,
        "username": parsed.username or "",
        "password": parsed.password or "",
        "label": f"{parsed.hostname}:{parsed.port}",
    }

def get_proxy_candidates(limit=20):
    proxies = load_proxies()
    if not proxies:
        print("⚠️ No proxies found – using direct connection.")
        return [None]
    random.shuffle(proxies)
    candidates = []
    for p in proxies[:limit]:
        cfg = build_proxy_config(p)
        if cfg:
            candidates.append(cfg)
    if not candidates:
        candidates = [None]
    return candidates

# ============================================================
# IP CHECKER
# ============================================================
def check_browser_ip(driver):
    print("🌐 Checking browser public IP...")
    try:
        driver.get("https://api.ipify.org?format=json")
        time.sleep(2)
        body = driver.find_element(By.TAG_NAME, "body").text.strip()
        data = json.loads(body)
        ip = data.get("ip", "unknown")
        print(f"🌐 Browser public IP: {ip}")
        return ip
    except Exception as e:
        print(f"⚠️ IP check failed: {e}")
        return None
    finally:
        driver.get("about:blank")
        time.sleep(1)

# ============================================================
# DRIVER CREATION
# ============================================================
def create_driver(proxy_config):
    chrome_options = webdriver.ChromeOptions()
    chrome_options.page_load_strategy = "none"
    chrome_options.add_argument("--no-first-run")
    chrome_options.add_argument("--no-default-browser-check")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--disable-infobars")
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    chrome_options.set_capability("goog:loggingPrefs", {"browser": "ALL"})

    seleniumwire_options = {}
    if proxy_config:
        host = proxy_config["host"]
        port = proxy_config["port"]
        username = proxy_config.get("username")
        password = proxy_config.get("password")
        proxy_url = f"http://{host}:{port}"
        if username and password:
            proxy_url = f"http://{username}:{password}@{host}:{port}"
        elif username:
            proxy_url = f"http://{username}@{host}:{port}"
        seleniumwire_options = {
            "proxy": {
                "http": proxy_url,
                "https": proxy_url,
                "no_proxy": "localhost,127.0.0.1"
            },
            "verify_ssl": False,
        }
        print(f"✅ Proxy configured: {host}:{port}")

    driver = webdriver.Chrome(
        options=chrome_options,
        seleniumwire_options=seleniumwire_options
    )
    driver.implicitly_wait(10)
    driver.set_page_load_timeout(30)

    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": TURNSTILE_INTERCEPT_SCRIPT})
    return driver

# ============================================================
# CLOUDFLARE / TURNSTILE FUNCTIONS
# ============================================================
def is_cloudflare_challenge(driver):
    try:
        title = (driver.title or "").lower()
    except:
        title = ""
    try:
        body = driver.find_element(By.TAG_NAME, "body").text.lower()
    except:
        body = ""
    markers = [
        "just a moment",
        "performing security verification",
        "checking your browser",
        "verify you are human",
        "this website uses a security service",
        "ray id:",
        "performance and security by cloudflare",
    ]
    return any(m in title for m in markers) or any(m in body for m in markers)

def drain_browser_logs(driver):
    intercepted = None
    try:
        entries = driver.get_log("browser")
    except Exception:
        return None
    for entry in entries:
        message = entry.get("message", "")
        if "intercepted-params:" in message:
            try:
                log_entry = message.encode("utf-8").decode("unicode_escape")
            except Exception:
                log_entry = message
            match = re.search(r'intercepted-params:({.*?})', log_entry)
            if match:
                try:
                    intercepted = json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass
        if "turnstile" in message.lower() or "cloudflare" in message.lower() or "403" in message:
            print("Browser console:", message)
    return intercepted

def extract_turnstile_from_page(driver):
    try:
        params = driver.execute_script("return window.__tsParams;")
        if params and params.get("sitekey"):
            return params
    except (JavascriptException, WebDriverException):
        pass
    try:
        element = driver.find_element(By.CSS_SELECTOR, ".cf-turnstile,[data-sitekey]")
        sitekey = element.get_attribute("data-sitekey")
        if sitekey:
            return {
                "sitekey": sitekey,
                "pageurl": driver.current_url,
                "data": element.get_attribute("data-cdata"),
                "pagedata": None,
                "action": element.get_attribute("data-action"),
                "userAgent": driver.execute_script("return navigator.userAgent;"),
                "json": 1,
            }
    except Exception:
        pass
    try:
        iframes = driver.find_elements(By.CSS_SELECTOR, "iframe[src*='turnstile']")
    except Exception:
        iframes = []
    for iframe in iframes:
        src = iframe.get_attribute("src") or ""
        query = parse_qs(urlparse(src).query)
        sitekey = (query.get("sitekey") or query.get("k") or [None])[0]
        if sitekey:
            return {
                "sitekey": sitekey,
                "pageurl": driver.current_url,
                "data": None,
                "pagedata": None,
                "action": None,
                "userAgent": driver.execute_script("return navigator.userAgent;"),
                "json": 1,
            }
    return None

def wait_for_turnstile_params(driver, timeout_seconds=30):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        intercepted = drain_browser_logs(driver)
        if intercepted and intercepted.get("sitekey"):
            print("Captured Turnstile params from browser logs")
            return intercepted
        params = extract_turnstile_from_page(driver)
        if params and params.get("sitekey"):
            print("Captured Turnstile params from page state")
            return params
        time.sleep(1)
    return None

def solve_turnstile_2captcha(params):
    if not TWO_CAPTCHA_API_KEY or len(TWO_CAPTCHA_API_KEY) != 32:
        raise RuntimeError("TWO_CAPTCHA_API_KEY invalid")
    payload = {
        "key": TWO_CAPTCHA_API_KEY,
        "method": "turnstile",
        "sitekey": params["sitekey"],
        "pageurl": params["pageurl"],
        "json": 1,
    }
    if params.get("action"):
        payload["action"] = params["action"]
    if params.get("data"):
        payload["data"] = params["data"]
    if params.get("pagedata"):
        payload["pagedata"] = params["pagedata"]
    if params.get("userAgent"):
        payload["useragent"] = params["userAgent"]
    
    print(f"🔄 Submitting Turnstile to 2Captcha for {params['pageurl']}")
    response = requests.post("https://2captcha.com/in.php", data=payload, timeout=60)
    response.raise_for_status()
    data = response.json()
    if data.get("status") != 1:
        raise RuntimeError(f"2Captcha submit failed: {data}")
    captcha_id = data["request"]
    print(f"✅ 2Captcha accepted request id: {captcha_id}")
    
    for attempt in range(1, 31):
        time.sleep(5)
        poll = requests.get(
            "https://2captcha.com/res.php",
            params={"key": TWO_CAPTCHA_API_KEY, "action": "get", "id": captcha_id, "json": 1},
            timeout=60,
        )
        poll.raise_for_status()
        result = poll.json()
        if result.get("status") == 1:
            token = result.get("request")
            if token:
                print(f"✅ Received 2Captcha token on attempt {attempt}")
                return token
        elif result.get("request") == "CAPCHA_NOT_READY":
            print(f"⏳ 2Captcha still solving ({attempt}/30)")
        else:
            raise RuntimeError(f"2Captcha poll failed: {result}")
    raise TimeoutError("2Captcha timeout")

def apply_turnstile_token(driver, token):
    print("🔄 Applying Turnstile token")
    result = driver.execute_script(
        """
        const solveToken = arguments[0];
        if (typeof window.cfCallback === "function") {
            window.cfCallback(solveToken);
            return "callback";
        }
        let applied = false;
        document.querySelectorAll('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]').forEach((el) => {
            el.value = solveToken;
            el.dispatchEvent(new Event("input", { bubbles: true }));
            el.dispatchEvent(new Event("change", { bubbles: true }));
            applied = true;
        });
        return applied ? "input" : "none";
        """,
        token,
    )
    print(f"✅ Applied Turnstile token via '{result}' mode")

def wait_for_challenge_clear(driver, timeout_seconds=20):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            current_url = driver.current_url
            title = (driver.title or "").strip().lower()
            page_source = (driver.page_source or "").lower()
        except:
            time.sleep(0.5)
            continue
        challenge_markers = (
            "__cf_chl_rt_tk=" in current_url
            or "just a moment" in title
            or "cf-challenge-running" in page_source
            or "challenge-form" in page_source
            or "why_captcha" in page_source
        )
        if not challenge_markers:
            print("✅ Challenge cleared.")
            return True
        time.sleep(1)
    print("❌ Challenge may not have cleared within timeout.")
    return False

def manual_captcha_wait():
    print("\n🔴 Please solve the CAPTCHA manually in the browser.")
    input("🟢 Press ENTER after solving...")
    print("✅ Continuing.")

def handle_cloudflare_challenge(driver):
    if not is_cloudflare_challenge(driver):
        print("✅ No Cloudflare challenge detected initially.")
        return True
    
    print("🛡️ Cloudflare challenge page detected.")
    
    MAX_CF_REFRESHES = 2
    for refresh_attempt in range(MAX_CF_REFRESHES + 1):
        if not is_cloudflare_challenge(driver):
            print("✅ Cloudflare challenge cleared after refresh.")
            return True
        if refresh_attempt >= MAX_CF_REFRESHES:
            print("❌ Cloudflare challenge still active after refresh retries.")
            break
        print(f"🔄 Refreshing page ({refresh_attempt + 1}/{MAX_CF_REFRESHES})...")
        driver.refresh()
        try:
            WebDriverWait(driver, 15).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
        except Exception:
            pass
        time.sleep(3)
    
    if is_cloudflare_challenge(driver):
        print("🛡️ Attempting to solve with Turnstile + 2Captcha...")
        params = wait_for_turnstile_params(driver, timeout_seconds=30)
        if params:
            print("✅ Turnstile params captured. Solving with 2Captcha...")
            try:
                token = solve_turnstile_2captcha(params)
                apply_turnstile_token(driver, token)
                if wait_for_challenge_clear(driver, timeout_seconds=30):
                    print("✅ Challenge cleared after solving.")
                    time.sleep(3)
                    return True
                else:
                    print("❌ Challenge did not clear after applying token.")
                    manual_captcha_wait()
                    if not is_cloudflare_challenge(driver):
                        print("✅ Manual intervention cleared the challenge.")
                        return True
                    else:
                        return False
            except Exception as e:
                print(f"⚠️ Turnstile solving failed: {e}")
                traceback.print_exc()
        else:
            print("ℹ️ No Turnstile params found.")
    
    print("⚠️ Automated solving failed or unavailable. Falling back to manual...")
    manual_captcha_wait()
    if not is_cloudflare_challenge(driver):
        print("✅ Manual intervention cleared the challenge.")
        return True
    else:
        print("❌ Challenge still present after manual wait.")
        return False

# ============================================================
# DATA GENERATION (with hardcoded email)
# ============================================================
def generate_user_data():
    fake = Faker()
    first = fake.first_name().lower()
    last = fake.last_name().lower()
    number = str(random.randint(10, 9999))
    username_raw = first + last + number
    username = username_raw[:MAX_USERNAME_LENGTH]
    while len(username) < 4:
        username += str(random.randint(0, 9))
    username = username[:MAX_USERNAME_LENGTH]

    password = fake.password(length=12, special_chars=True, digits=True, upper_case=True, lower_case=True)
    # Use hardcoded email
    email = HARDCODED_EMAIL
    first_name = fake.first_name()
    last_name = fake.last_name()
    return {
        "username": username,
        "password": password,
        "email": email,
        "first_name": first_name,
        "last_name": last_name,
    }

# ============================================================
# ROBUST FIELD FILLER
# ============================================================
def robust_fill_field(driver, by, selector, value, field_name="field", timeout=20):
    try:
        print(f"🔎 Looking for {field_name}...")
        element = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((by, selector))
        )
        WebDriverWait(driver, timeout).until(
            EC.visibility_of(element)
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
        time.sleep(0.5)

        print(
            f"   tag={element.tag_name}, "
            f"type={element.get_attribute('type')}, "
            f"displayed={element.is_displayed()}, "
            f"enabled={element.is_enabled()}"
        )

        try:
            element.click()
            element.send_keys(Keys.CONTROL, "a")
            element.send_keys(Keys.BACKSPACE)
            element.send_keys(value)
            actual = element.get_attribute("value")
            if actual == value:
                print(f"✅ Filled {field_name}")
                return True
            print(f"⚠️ send_keys executed but value mismatch: {actual!r}")
        except Exception as e:
            print(f"⚠️ Normal fill failed: {e}")

        try:
            driver.execute_script("""
                const el = arguments[0];
                const value = arguments[1];
                const setter = Object.getOwnPropertyDescriptor(
                    HTMLInputElement.prototype,
                    'value'
                ).set;
                setter.call(el, value);
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new Event('blur', {bubbles: true}));
            """, element, value)
            actual = element.get_attribute("value")
            if actual == value:
                print(f"✅ Filled {field_name} via JavaScript")
                return True
            print(f"❌ JS value mismatch: {actual!r}")
        except Exception as e:
            print(f"❌ JavaScript fill failed: {e}")
        return False

    except Exception as e:
        print(f"❌ Could not locate/fill {field_name}: {e}")
        return False

def robust_check_checkbox(driver, by, selector, field_name="checkbox", timeout=20):
    try:
        element = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((by, selector))
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
        time.sleep(0.3)
        if element.is_selected():
            print(f"✅ {field_name} already checked")
            return True
        try:
            element.click()
        except Exception:
            driver.execute_script("arguments[0].click();", element)
        time.sleep(0.5)
        if element.is_selected():
            print(f"✅ Checked {field_name}")
            return True
        print(f"❌ Click happened but {field_name} is not checked")
        return False
    except Exception as e:
        print(f"❌ Could not check {field_name}: {e}")
        return False

# ============================================================
# RECAPTCHA SOLVER
# ============================================================
def extract_recaptcha_sitekey(driver):
    try:
        el = driver.find_element(By.CSS_SELECTOR, ".g-recaptcha[data-sitekey]")
        return el.get_attribute("data-sitekey")
    except:
        pass
    try:
        iframe = driver.find_element(By.CSS_SELECTOR, "iframe[src*='recaptcha']")
        src = iframe.get_attribute("src")
        m = re.search(r'k=([^&]+)', src)
        if m:
            return m.group(1)
    except:
        pass
    return None

def solve_recaptcha_2captcha(sitekey, pageurl):
    if not TWO_CAPTCHA_API_KEY or len(TWO_CAPTCHA_API_KEY) != 32:
        raise RuntimeError("TWO_CAPTCHA_API_KEY invalid")
    payload = {
        "key": TWO_CAPTCHA_API_KEY,
        "method": "userrecaptcha",
        "googlekey": sitekey,
        "pageurl": pageurl,
        "json": 1,
    }
    resp = requests.post("https://2captcha.com/in.php", data=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != 1:
        raise RuntimeError(f"2Captcha submit failed: {data}")
    captcha_id = data["request"]
    for _ in range(30):
        time.sleep(5)
        poll = requests.get(
            "https://2captcha.com/res.php",
            params={"key": TWO_CAPTCHA_API_KEY, "action": "get", "id": captcha_id, "json": 1},
            timeout=60,
        )
        poll.raise_for_status()
        result = poll.json()
        if result.get("status") == 1:
            token = result.get("request")
            if token:
                print(f"✅ reCAPTCHA token received. Length: {len(token)} chars")
                return token
        elif result.get("request") != "CAPCHA_NOT_READY":
            raise RuntimeError(f"2Captcha poll failed: {result}")
    raise TimeoutError("2Captcha timeout")

def inject_recaptcha_token(driver, token):
    try:
        driver.execute_script("""
            const token = arguments[0];
            document.querySelectorAll('textarea[name="g-recaptcha-response"]').forEach(el => {
                el.value = token;
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
            });
            document.querySelectorAll('input[name="g-recaptcha-hidden"]').forEach(el => {
                el.value = token;
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
            });
            if (typeof grecaptcha !== 'undefined') {
                try {
                    if (grecaptcha.execute) {
                        grecaptcha.execute(token);
                    }
                    grecaptcha.getResponse = function(widgetId) {
                        return token;
                    };
                } catch(e) {}
            }
        """, token)
        print("✅ reCAPTCHA token injected.")
        return True
    except Exception as e:
        print(f"❌ Injection failed: {e}")
        traceback.print_exc()
        return False

def verify_recaptcha_injection(driver):
    result = driver.execute_script("""
        return {
            textarea_len: document.querySelector('textarea[name="g-recaptcha-response"]')?.value?.length || 0,
            hidden_len: document.querySelector('input[name="g-recaptcha-hidden"]')?.value?.length || 0,
            grecaptcha_response: typeof grecaptcha !== 'undefined' ? grecaptcha.getResponse() || '' : ''
        };
    """)
    print(f"🔍 Verification: {result}")
    if result['grecaptcha_response'] and len(result['grecaptcha_response']) > 100:
        print("✅ grecaptcha.getResponse() returns a valid token.")
        return True
    else:
        print("⚠️ grecaptcha.getResponse() is empty or invalid.")
        return False

# ============================================================
# CLICK "Sign In" – IMPROVED WITH PAGE LOAD WAITING
# ============================================================
def click_sign_in(driver):
    print("⏳ Waiting for page to fully load...")
    wait = WebDriverWait(driver, 60)

    # 1. Wait for document.readyState to be "complete"
    wait.until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )
    print("✅ Document readyState = complete")

    # Optional debugging: check readyState over time (if needed)
    # for i in range(30):
    #     state = driver.execute_script("return document.readyState")
    #     print(f"{i:02d} readyState={state}, url={driver.current_url}")
    #     time.sleep(1)

    # 2. Wait for the header to be present (ensures AJAX-loaded content)
    try:
        wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "div.custom-community-header-user-navigation")
            )
        )
        print("✅ Header navigation loaded")
    except TimeoutException:
        print("⚠️ Header not found, but continuing anyway...")

    # 3. Wait for Sign In link to be visible and clickable
    try:
        signin = wait.until(
            EC.visibility_of_element_located(
                (By.CSS_SELECTOR, "a.lia-component-users-action-login")
            )
        )
        print("✅ Sign In link found and visible")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", signin)
        time.sleep(0.5)
        driver.execute_script("arguments[0].click();", signin)
        print("✅ Sign In clicked")
        time.sleep(3)
        return True
    except TimeoutException as e:
        print(f"⚠️ Sign In link not found after wait: {e}")
        # Fallback: try XPath by class
        try:
            signin = driver.find_element(By.XPATH, "//a[contains(@class, 'lia-component-users-action-login')]")
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", signin)
            time.sleep(0.5)
            driver.execute_script("arguments[0].click();", signin)
            print("✅ Sign In clicked (XPath fallback)")
            time.sleep(3)
            return True
        except Exception as e2:
            print(f"❌ Fallback also failed: {e2}")
            return False

# ============================================================
# CLICK "Register here" – with iframe + popup search
# ============================================================
def click_register_here(driver):
    print("🔘 Looking for 'Register here' link...")

    # ---- FIRST: Try in default content ----
    try:
        register = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//a[contains(normalize-space(.), 'Register here')] | //a[contains(normalize-space(.), 'New User? Register here.')]")
            )
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", register)
        time.sleep(0.5)
        driver.execute_script("arguments[0].click();", register)
        print("✅ Register here clicked (default content)")
        time.sleep(2)
        return True
    except Exception as e:
        print(f"⚠️ Not found in default content: {e}")

    # ---- SECOND: Check all iframes ----
    print("🔍 Searching inside iframes...")
    iframes = driver.find_elements(By.TAG_NAME, "iframe")
    print(f"Total iframes found: {len(iframes)}")
    for i, frame in enumerate(iframes):
        try:
            src = frame.get_attribute("src")
            print(f"  iframe {i}: {src}")
            driver.switch_to.frame(frame)
            try:
                register = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//a[contains(normalize-space(.), 'Register here')] | //a[contains(normalize-space(.), 'New User? Register here.')]")
                    )
                )
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", register)
                time.sleep(0.5)
                driver.execute_script("arguments[0].click();", register)
                print(f"✅ Register here clicked inside iframe {i}")
                driver.switch_to.default_content()
                time.sleep(2)
                return True
            except:
                driver.switch_to.default_content()
                continue
        except:
            driver.switch_to.default_content()
            continue

    # ---- THIRD: Try to find any link containing 'Register' inside popups ----
    print("🔍 Searching for any 'Register' link in popups/modals...")
    try:
        register = driver.find_element(By.XPATH, "//*[contains(normalize-space(.), 'Register') and (self::a or self::button)]")
        if register.is_displayed():
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", register)
            time.sleep(0.5)
            driver.execute_script("arguments[0].click();", register)
            print("✅ Register clicked (fallback)")
            time.sleep(2)
            return True
    except:
        pass

    # ---- DIAGNOSTICS ----
    print("❌ Register link not found. Dumping debug info...")
    print("\n========== PAGE SOURCE SNIPPET ==========")
    try:
        html = driver.page_source[:3000]
        print(html)
    except:
        print("Could not get page source")

    print("\n========== VISIBLE LINKS ==========")
    try:
        links = driver.find_elements(By.TAG_NAME, "a")
        for i, a in enumerate(links[:30]):
            try:
                text = a.text.strip() or " "
                href = a.get_attribute("href")
                print(f"[{i}] text={text!r}, href={href}")
            except:
                pass
    except Exception as e:
        print(f"Link dump failed: {e}")

    return False

# ============================================================
# SELECT RANDOM TIMEZONE (kept but NOT called)
# ============================================================
def select_random_timezone(driver):
    try:
        select_element = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "timezone"))
        )
        select = Select(select_element)
        options = [opt for opt in select.options if opt.text and not opt.text.startswith("Select")]
        if options:
            chosen = random.choice(options)
            select.select_by_visible_text(chosen.text)
            print(f"✅ Selected Timezone: {chosen.text}")
            return True
        else:
            if len(select.options) > 1:
                select.select_by_index(1)
                print(f"✅ Selected Timezone: {select.options[1].text}")
                return True
            else:
                print("⚠️ No timezone options available, skipping")
                return True
    except Exception as e:
        print(f"⚠️ Could not select Timezone: {e}")
        return False

# ============================================================
# CLICK REGISTER BUTTON (with reCAPTCHA)
# ============================================================
def click_register_button_with_recaptcha(driver):
    print("🔘 Looking for REGISTER button...")
    register_btn = None
    selectors = [
        "//input[@type='submit' and contains(@value, 'Register')]",
        "//input[@value='REGISTER']",
        "//button[contains(text(), 'Register')]",
        "//input[@type='submit']",
    ]
    for selector in selectors:
        try:
            if selector.startswith("//"):
                elements = driver.find_elements(By.XPATH, selector)
            else:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
            for el in elements:
                if el.is_displayed() and el.is_enabled():
                    register_btn = el
                    break
            if register_btn:
                break
        except Exception as e:
            print(f"⚠️ Selector failed: {selector} - {e}")

    if not register_btn:
        print("❌ REGISTER button not found.")
        return False

    print("🔍 Checking for reCAPTCHA...")
    sitekey = extract_recaptcha_sitekey(driver)
    print(f"🔑 Extracted sitekey: {sitekey}")

    if sitekey:
        print("🔒 Solving reCAPTCHA...")
        try:
            token = solve_recaptcha_2captcha(sitekey, driver.current_url)
            if token:
                print(f"✅ Token length: {len(token)}")
                inject_recaptcha_token(driver, token)
                verify_recaptcha_injection(driver)
            else:
                print("⚠️ No token received. Falling back to manual.")
                manual_captcha_wait()
                token = driver.execute_script("return document.querySelector('textarea[name=\"g-recaptcha-response\"]')?.value;")
                if token:
                    inject_recaptcha_token(driver, token)
        except Exception as e:
            print(f"⚠️ 2Captcha error: {e}")
            traceback.print_exc()
            manual_captcha_wait()
            token = driver.execute_script("return document.querySelector('textarea[name=\"g-recaptcha-response\"]')?.value;")
            if token:
                inject_recaptcha_token(driver, token)
    else:
        print("ℹ️ No reCAPTCHA found.")

    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", register_btn)
    time.sleep(0.5)
    try:
        register_btn.click()
    except:
        driver.execute_script("arguments[0].click();", register_btn)
    print("✅ REGISTER button clicked")
    return True

# ============================================================
# MAIN AUTOMATION
# ============================================================
def run_automation(proxy_config):
    driver = create_driver(proxy_config)
    try:
        # ---- CHECK BROWSER IP ----
        check_browser_ip(driver)

        # ---- GENERATE USER DATA ----
        user_data = generate_user_data()
        username = user_data["username"]
        password = user_data["password"]
        email = user_data["email"]  # hardcoded
        first_name = user_data["first_name"]
        last_name = user_data["last_name"]

        username = username[:MAX_USERNAME_LENGTH]
        print(f"👤 Username: {username} ({len(username)} chars)")

        print("\n🧑 Generated user data:")
        print(f"   Username: {username}")
        print(f"   Password: {password}")
        print(f"   Email: {email} (hardcoded)")
        print(f"   First Name: {first_name}")
        print(f"   Last Name: {last_name}")

        # ---- OPEN TARGET URL ----
        print(f"\n🌐 Opening thread URL: {TARGET_URL}")
        driver.get(TARGET_URL)
        time.sleep(5)

        # ---- HANDLE CLOUDFLARE ----
        if not handle_cloudflare_challenge(driver):
            print("❌ Cloudflare challenge could not be resolved. Aborting.")
            driver.save_screenshot("canon_cloudflare_failed.png")
            return False

        # ---- 1. CLICK SIGN IN (IMPROVED) ----
        if not click_sign_in(driver):
            print("❌ Could not click Sign In. Exiting.")
            return False

        # ---- 2. CLICK REGISTER HERE (WITH IFRAME SEARCH) ----
        if not click_register_here(driver):
            print("❌ Could not click Register here. Exiting.")
            return False

        # ---- 3. FILL REGISTRATION FORM ----
        print("✍️ Filling registration form...")
        robust_fill_field(driver, By.NAME, "login", username, "Username")
        robust_fill_field(driver, By.NAME, "password", password, "Password")
        robust_fill_field(driver, By.NAME, "passwordConfirm", password, "Confirm Password")
        robust_fill_field(driver, By.NAME, "email", email, "Email")
        robust_fill_field(driver, By.NAME, "emailConfirm", email, "Confirm Email")
        robust_check_checkbox(driver, By.NAME, "rememberPassword", "Keep me signed in")
        robust_fill_field(driver, By.NAME, "profileFirstName", first_name, "First Name")
        robust_fill_field(driver, By.NAME, "profileLastName", last_name, "Last Name")
        # Timezone SKIPPED
        robust_check_checkbox(driver, By.NAME, "userAcceptsTermsOfService", "Terms of Service")

        # ---- 4. CLICK REGISTER (with reCAPTCHA) ----
        if not click_register_button_with_recaptcha(driver):
            print("❌ Could not click REGISTER. Exiting.")
            return False

        # ---- 5. WAIT FOR RESULT ----
        time.sleep(5)

        page_source = driver.page_source.lower()
        if "thank you" in page_source or "welcome" in page_source or "registered" in page_source:
            print("\n✅ Registration appears successful.")
            driver.save_screenshot("canon_registration_success.png")
            print("📸 Screenshot saved: canon_registration_success.png")
            return True
        else:
            if "username already exists" in page_source or "email already exists" in page_source:
                print("\n⚠️ Registration failed: Username or email already taken.")
                driver.save_screenshot("canon_registration_failed.png")
                print("📸 Screenshot saved: canon_registration_failed.png")
            else:
                print("\n⚠️ Registration may have failed. Check screenshot.")
                driver.save_screenshot("canon_registration_unknown.png")
                print("📸 Screenshot saved: canon_registration_unknown.png")
            return False

    except Exception as e:
        print(f"❌ Automation failed: {e}")
        traceback.print_exc()
        driver.save_screenshot("error_screenshot.png")
        return False
    finally:
        input("\n⏸️ Press ENTER to close browser...")
        driver.quit()

# ============================================================
# MAIN LOOP – PROXY ROTATION
# ============================================================
def main():
    print("\n" + "="*70)
    print("🚀 CANON COMMUNITY SIGN-UP (Hardcoded Email + Cloudflare + reCAPTCHA)")
    print("="*70)
    print(f"🌐 Target URL: {TARGET_URL}")
    print(f"📧 Hardcoded Email: {HARDCODED_EMAIL}")

    candidates = get_proxy_candidates(limit=20)
    for i, proxy_config in enumerate(candidates, 1):
        print(f"\n🔁 Attempt {i} using {proxy_config['label'] if proxy_config else 'Direct connection'}")
        success = run_automation(proxy_config)
        if success:
            print("\n✅ SUCCESS! Sign-up completed.")
            return
        else:
            print("\n❌ This attempt failed. Trying next proxy...")
    print("\n❌ All attempts failed.")

if __name__ == "__main__":
    main()