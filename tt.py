"""

camoufox>=0.4.11
requests>=2.31.0

"""

import asyncio
import re
import time
import random
import gc
import os
import string
import threading
import tempfile
import traceback
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime as dt

import requests
from bs4 import BeautifulSoup
from camoufox.async_api import AsyncCamoufox

os.environ["NO_PROXY"] = "localhost,127.0.0.1"
os.environ["no_proxy"] = "localhost,127.0.0.1"

SIGNUP_URL = "https://accounts.x.ai/sign-up?redirect=grok-com"

file_lock = threading.Lock()
timestamp = dt.now().strftime("%m%d%H%M")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNT_DIR = os.path.join(SCRIPT_DIR, "account")
GROK_DIR = os.path.join(ACCOUNT_DIR, "result_grok")
SSO_DIR = os.path.join(ACCOUNT_DIR, "result_sso")
DEBUG_DIR = os.path.join(tempfile.gettempdir(), "grok_debug_headless")
os.makedirs(ACCOUNT_DIR, exist_ok=True)
os.makedirs(GROK_DIR, exist_ok=True)
os.makedirs(SSO_DIR, exist_ok=True)
os.makedirs(DEBUG_DIR, exist_ok=True)

GROK_FILE = os.path.join(GROK_DIR, f"grok_hl_{timestamp}.txt")
SSO_FILE = os.path.join(SSO_DIR, f"sso_hl_{timestamp}.txt")

first_names = ["James", "John", "Robert", "Michael", "William",
               "David", "Richard", "Joseph", "Thomas", "Charles"]
last_names = ["Smith", "Johnson", "Williams", "Brown", "Jones",
              "Garcia", "Miller", "Davis", "Rodriguez", "Martinez"]

_MAIL_TM_ACCOUNTS = {}

_FALLBACK_API = "https://mail.chatgpt.org.uk"
_FALLBACK_KEY = "gpt-test"
_FALLBACK_WEB_HEADERS = {
    "Referer": "https://mail.chatgpt.org.uk/",
    "Origin": "https://mail.chatgpt.org.uk",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
}
_fallback_quota_exceeded = False
_fallback_use_web = False


def _fallback_headers():
    global _fallback_use_web
    if _fallback_use_web:
        return _FALLBACK_WEB_HEADERS
    return {"X-API-Key": _FALLBACK_KEY}


def create_primary_email():
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
        }
        resp = requests.get("https://api.mail.tm/domains", headers=headers, timeout=15)
        if resp.status_code != 200:
            return None, None

        data = resp.json()
        members = data.get("hydra:member", []) or data.get("member", [])
        if not members:
            return None, None

        domain = random.choice(members)["domain"]
        username = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
        email = f"{username}@{domain}"
        password = "TempPass123!"

        acc_data = {"address": email, "password": password}
        create_resp = requests.post("https://api.mail.tm/accounts", json=acc_data, headers=headers, timeout=15)
        if create_resp.status_code not in (201, 200):
            return None, None

        token_resp = requests.post("https://api.mail.tm/token", json=acc_data, headers=headers, timeout=15)
        token = token_resp.json()["token"]
        _MAIL_TM_ACCOUNTS[email] = token
        return email, token
    except Exception:
        return None, None


def fetch_primary_code(email, timeout=60):
    token = _MAIL_TM_ACCOUNTS.get(email)
    if not token:
        return None

    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    start_time = time.time()
    seen_ids = set()

    while time.time() - start_time < timeout:
        try:
            list_resp = requests.get("https://api.mail.tm/messages", headers=headers, timeout=10)
            if list_resp.status_code == 200:
                msgs = list_resp.json().get("hydra:member", []) or list_resp.json().get("member", [])
                for msg in msgs:
                    msg_id = msg.get("id")
                    if not msg_id or msg_id in seen_ids:
                        continue
                    seen_ids.add(msg_id)

                    read_resp = requests.get(f"https://api.mail.tm/messages/{msg_id}", headers=headers, timeout=10)
                    if read_resp.status_code == 200:
                        data = read_resp.json()
                        subject = data.get("subject", "") or ""
                        text = data.get("text", "") or ""
                        html = " ".join(data.get("html", [])) if isinstance(data.get("html"), list) else str(data.get("html", ""))
                        full_text = f"{subject} {text} {html}".lower()

                        subj_match = re.match(r"^([A-Za-z0-9\-]{5,10})\s+xAI\s+confirmation\s+code", subject, re.IGNORECASE)
                        if subj_match:
                            return subj_match.group(1)

                        for pat in [r"(?i)code[:\s-]*(\d{6,8})", r"(?i)verification[:\s-]*(\d{6,8})", r"(?<!\d)(\d{6,8})(?!\d)"]:
                            match = re.search(pat, full_text)
                            if match:
                                return match.group(1)
        except Exception:
            pass
        time.sleep(4)
    return None


def create_fallback_email():
    global _fallback_quota_exceeded, _fallback_use_web
    if _fallback_quota_exceeded and not _fallback_use_web:
        _fallback_use_web = True

    try:
        r = requests.get(
            f"{_FALLBACK_API}/api/generate-email",
            headers=_fallback_headers(),
            timeout=30,
        )
        if r.status_code == 200 and r.json().get("success"):
            return r.json()["data"]["email"]

        if r.status_code in (429, 401):
            _fallback_quota_exceeded = True
            if not _fallback_use_web:
                _fallback_use_web = True
                return create_fallback_email()
            return None
    except Exception:
        pass
    return None


def fetch_fallback_code(email, timeout=60):
    start = time.time()
    seen_ids = set()
    while time.time() - start < timeout:
        try:
            r = requests.get(
                f"{_FALLBACK_API}/api/emails",
                params={"email": email},
                headers=_fallback_headers(),
                timeout=10,
            )
            if r.status_code == 200:
                mail_list = r.json().get("data", {}).get("emails", [])
                for mail in mail_list:
                    mail_id = mail.get("id", "")
                    if mail_id in seen_ids:
                        continue
                    seen_ids.add(mail_id)

                    subject = mail.get("subject", "") or ""
                    subj_match = re.match(
                        r"^([A-Za-z0-9\-]{5,10})\s+xAI\s+confirmation\s+code",
                        subject, re.IGNORECASE,
                    )
                    if subj_match:
                        return subj_match.group(1)

                    html = mail.get("html_content") or mail.get("content", "")
                    content = mail.get("content", "") or ""
                    full_text = f"{subject} {content} {html}"

                    soup = BeautifulSoup(html, "html.parser")
                    span = soup.find("span", class_="verification-code")
                    if span:
                        code = span.get_text().strip()
                        if 5 <= len(code) <= 10:
                            return code

                    for pat in [
                        r"(?i)code[:\s-]*(\d{6,8})",
                        r"(?i)verification[:\s-]*(\d{6,8})",
                        r"(?<!\d)(\d{6})(?!\d)",
                    ]:
                        m = re.search(pat, full_text)
                        if m:
                            return m.group(1)
        except Exception:
            pass
        time.sleep(3)
    return None


def generate_password(length=14):
    upper = random.choice(string.ascii_uppercase)
    lower = random.choice(string.ascii_lowercase)
    digit = random.choice(string.digits)
    special = random.choice("!@#$%&*")
    rest = random.choices(
        string.ascii_letters + string.digits + "!@#$%&*",
        k=length - 4,
    )
    chars = list(upper + lower + digit + special + "".join(rest))
    random.shuffle(chars)
    return "".join(chars)


async def async_run_job(thread_id, task_id, timeout_sec=300):
    prefix = f"[T{thread_id}-#{task_id}]"
    job_start = time.time()

    def log(msg):
        elapsed = time.time() - job_start
        print(f"{prefix} [{elapsed:5.1f}s] {msg}")

    def check_timeout():
        if time.time() - job_start > timeout_sec:
            raise TimeoutError("Timeout")

    camoufox_obj = None
    browser = None
    context = None
    page = None

    try:
        log("[步骤1] 创建临时邮箱...")
        email_address = None
        email_provider = None

        for _retry in range(5):
            try:
                addr, _token = await asyncio.to_thread(create_primary_email)
                if addr:
                    email_address = addr
                    email_provider = "primary"
                    break
            except Exception:
                pass

            log("[步骤1] 主邮箱失败，尝试备用...")
            try:
                addr = await asyncio.to_thread(create_fallback_email)
                if addr:
                    email_address = addr
                    email_provider = "fallback"
                    break
            except Exception:
                pass

            log(f"[步骤1] 第{_retry+1}次全部失败，等待5s...")
            await asyncio.sleep(5)

        if not email_address:
            log("[步骤1] ❌ 邮箱创建失败(5次重试)")
            return False
        log(f"[步骤1] ✓ 邮箱: {email_address} ({email_provider})")

        grok_password = generate_password()
        fname = random.choice(first_names)
        lname = random.choice(last_names)

        check_timeout()
        log("[步骤2] 启动 Camoufox (headless)...")
        selected_os = random.choice(["windows", "macos", "linux"])

        camoufox_obj = AsyncCamoufox(
            headless=True,
            os=selected_os,
            locale="en-US",
            humanize=False,
            geoip=False,
            i_know_what_im_doing=True,
            block_webrtc=True,
            disable_coop=True,
        )
        browser = await camoufox_obj.__aenter__()
        context = await browser.new_context()
        page = await context.new_page()

        ua = await page.evaluate("navigator.userAgent")
        log(f"[步骤2] ✓ Camoufox 就绪 (OS={selected_os}, UA={ua[:50]}...)")

        check_timeout()
        log("[步骤3] 打开注册页面...")
        await page.goto(SIGNUP_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        title = await page.title()
        url = page.url
        log(f"[步骤3] Title: {title}")
        log(f"[步骤3] URL: {url}")

        for cf_wait in range(30):
            has_signup = await page.locator(
                "button:has-text('Sign up with email'), "
                "button:has-text('Sign up with X'), "
                "input[name='email']"
            ).count()
            if has_signup > 0:
                log(f"[步骤3] ✓ 注册页面已加载 ({cf_wait}s)")
                break
            if cf_wait == 0:
                log("[步骤3] 等待页面内容加载...")
            if cf_wait % 5 == 4:
                title = await page.title()
                log(f"[步骤3] 等待中... Title={title}")
            await asyncio.sleep(1)
        else:
            log("[步骤3] ⚠ 30s内未检测到注册页面元素")
            await page.screenshot(path=os.path.join(DEBUG_DIR, f"cf_T{thread_id}.png"))
            title = await page.title()
            log(f"[诊断] Title={title}, URL={page.url}")

        await page.screenshot(path=os.path.join(DEBUG_DIR, f"step3_T{thread_id}.png"))

        try:
            btn = page.locator("button:has-text('Sign up with email')")
            await btn.click(timeout=5000)
            log("[步骤3] 点击 'Sign up with email'")
            await asyncio.sleep(1)
        except Exception:
            log("[步骤3] 'Sign up with email' 未找到，继续")

        check_timeout()
        log("[步骤3] 填入邮箱...")
        email_input = page.locator('input[name="email"]')
        await email_input.wait_for(timeout=10000)
        await email_input.click()
        await email_input.type(email_address, delay=random.randint(30, 70))
        await asyncio.sleep(0.5)
        await email_input.press("Enter")
        log("[步骤3] 邮箱已提交")
        await asyncio.sleep(2)

        check_timeout()
        log("[步骤4] 等待验证码...")

        code_filled = False
        for attempt in range(40):
            check_timeout()

            if await page.locator('input[name="password"]').count() > 0:
                log("[步骤4] ✓ 已到密码页面")
                break

            if not code_filled:
                visible_inputs = page.locator("input:visible")
                input_count = await visible_inputs.count()
                if input_count > 0:
                    code = None
                    if email_provider == "primary":
                        for poll in range(20):
                            code = await asyncio.to_thread(
                                fetch_primary_code, email_address
                            )
                            if code:
                                break
                            if poll % 5 == 4:
                                log(f"[步骤4] 第{poll+1}次轮询...")
                            await asyncio.sleep(1)
                    else:
                        code = await asyncio.to_thread(
                            fetch_fallback_code, email_address, 40
                        )

                    if code:
                        log(f"[步骤4] ✓ 验证码: {code}")
                        first_input = visible_inputs.first
                        await first_input.click()
                        await first_input.type(str(code), delay=80)
                        code_filled = True
                        await asyncio.sleep(2)
                    else:
                        log("[步骤4] ❌ 未获取到验证码")
                        return False

            if code_filled:
                break
            await asyncio.sleep(1)

        check_timeout()
        log("[步骤5] 填写个人信息...")

        try:
            await page.locator('input[name="password"]').wait_for(timeout=15000)
        except Exception:
            log("[步骤5] ❌ 密码页面未出现")
            await page.screenshot(path=os.path.join(DEBUG_DIR, f"no_pw_T{thread_id}.png"))
            title = await page.title()
            log(f"[诊断] Title={title}, URL={page.url}")
            return False

        await page.locator('input[name="givenName"]').fill(fname)
        await asyncio.sleep(0.3)
        await page.locator('input[name="familyName"]').fill(lname)
        await asyncio.sleep(0.3)
        await page.locator('input[name="password"]').fill(grok_password)
        log(f"[步骤5] ✓ {fname} {lname}, 密码长度={len(grok_password)}")

        check_timeout()
        log("[步骤6] 等待 Turnstile 验证...")
        await asyncio.sleep(3)

        async def find_cf_frame():
            for frame in page.frames:
                if "challenges.cloudflare" in frame.url:
                    return frame
            return None

        async def get_turnstile_box():
            cf_frame = await find_cf_frame()
            if not cf_frame:
                log(f"[步骤6] 未找到 CF frame (共 {len(page.frames)} frames)")
                return None
            try:
                frame_el = await cf_frame.frame_element()
                box = await frame_el.bounding_box()
                if box and box["width"] > 0:
                    return box
                log("[步骤6] iframe bounding_box 为空或宽度0")
                return None
            except Exception as e:
                log(f"[步骤6] 获取 iframe box 异常: {e}")
                return None

        async def click_turnstile_mouse():
            try:
                box = await get_turnstile_box()
                if not box:
                    return False

                jitter_x = random.uniform(-2, 4)
                jitter_y = random.uniform(-3, 3)
                click_x = box["x"] + 28 + jitter_x
                click_y = box["y"] + box["height"] / 2 + jitter_y
                log(f"[步骤6] iframe box: ({box['x']:.0f},{box['y']:.0f}) "
                    f"{box['width']:.0f}x{box['height']:.0f}")
                log(f"[步骤6] mouse.click → ({click_x:.1f}, {click_y:.1f})")

                mid_x = click_x + random.uniform(40, 100)
                mid_y = click_y + random.uniform(-40, 40)
                await page.mouse.move(mid_x, mid_y)
                await asyncio.sleep(random.uniform(0.08, 0.2))
                await page.mouse.move(click_x, click_y)
                await asyncio.sleep(random.uniform(0.03, 0.1))
                await page.mouse.click(click_x, click_y)
                log("[步骤6] ✓ mouse.click 已执行")
                return True
            except Exception as e:
                log(f"[步骤6] mouse.click 异常: {e}")
                return False

        ts_final = "unknown"
        click_attempt = 0
        for wait_sec in range(90):
            ts = await page.evaluate('''() => {
                var inp = document.querySelector('input[name="cf-turnstile-response"]');
                if (!inp) return "no_input";
                if (!inp.value) return "empty";
                if (inp.value.length < 10) return "short";
                return "passed:" + inp.value.length;
            }''')
            ts_final = ts
            if ts.startswith("passed"):
                log(f"[步骤6] ✓ Turnstile 已通过! (等了 {wait_sec + 3}s)")
                break

            if ts == "no_input" and wait_sec >= 10:
                log("[步骤6] Turnstile input 不存在 (可能无需验证), 跳过等待")
                ts_final = "skipped"
                break

            if wait_sec in (2, 5, 9, 14, 20, 28, 38, 50, 65, 80):
                log(f"[步骤6] 第{click_attempt+1}次点击尝试 (Turnstile={ts})...")
                await click_turnstile_mouse()
                click_attempt += 1
                await asyncio.sleep(3)
                ts_after = await page.evaluate('''() => {
                    var inp = document.querySelector('input[name="cf-turnstile-response"]');
                    return inp ? (inp.value ? "passed:" + inp.value.length : "empty") : "no_input";
                }''')
                if ts_after.startswith("passed"):
                    log(f"[步骤6] ✓ 点击后 Turnstile 已通过!")
                    ts_final = ts_after
                    break
                continue

            if wait_sec % 10 == 9:
                log(f"[步骤6] {wait_sec+1}s Turnstile: {ts}")
            await asyncio.sleep(1)
        else:
            log(f"[步骤6] ⚠ Turnstile 最终: {ts_final}, 仍尝试提交")

        await page.screenshot(path=os.path.join(DEBUG_DIR, f"step6_T{thread_id}.png"))

        log("[步骤6] 点击 'Complete sign up'...")
        try:
            submit = page.locator("button:has-text('Complete sign up')")
            await submit.click(timeout=5000)
        except Exception:
            log("[步骤6] 按钮点击失败，JS 提交...")
            await page.evaluate('''() => {
                var btns = document.querySelectorAll("button");
                var btn = Array.from(btns).find(
                    b => b.textContent.includes("Complete") || b.type === "submit"
                );
                if (btn) btn.click();
            }''')
        log("[步骤6] ✓ 已提交")

        log("[步骤7] 等待注册结果...")
        await asyncio.sleep(3)

        sso_val = None
        sso_rw_val = ""
        for i in range(30):
            cookies = await context.cookies()
            cdict = {c["name"]: c["value"] for c in cookies}
            if "sso" in cdict:
                sso_val = cdict["sso"]
                sso_rw_val = cdict.get("sso-rw", "")
                break
            if i % 5 == 4:
                log(f"[步骤7] {i+1}s URL: {page.url}")
            await asyncio.sleep(1)

        if not sso_val:
            log("[步骤7] ❌ 未获取 SSO, 收集诊断...")
            await page.screenshot(path=os.path.join(DEBUG_DIR, f"fail_T{thread_id}.png"))
            title = await page.title()
            log(f"[诊断] Title={title}")
            log(f"[诊断] URL={page.url}")
            try:
                body = await page.evaluate(
                    'document.body ? document.body.innerText.substring(0, 500) : "NO_BODY"'
                )
                log(f"[诊断] 页面: {body[:300]}")
            except Exception:
                pass
            cookies = await context.cookies()
            log(f"[诊断] cookies: {[c['name'] for c in cookies]}")
            return False

        log(f"[步骤7] 🎉 注册成功! SSO={sso_val[:30]}...")

        with file_lock:
            with open(GROK_FILE, "a", encoding="utf-8") as f:
                f.write(f"Email: {email_address}\n")
                f.write(f"Password: {grok_password}\n")
                f.write(f"SSO: {sso_val}\n")
                if sso_rw_val:
                    f.write(f"SSO-RW: {sso_rw_val}\n")
                f.write("-" * 40 + "\n")
            with open(SSO_FILE, "a", encoding="utf-8") as f:
                f.write(f"{sso_val}\n")

        return True

    except TimeoutError:
        log(f"TIMEOUT ({time.time() - job_start:.1f}s)")
        if page:
            try:
                await page.screenshot(
                    path=os.path.join(DEBUG_DIR, f"timeout_T{thread_id}.png")
                )
            except Exception:
                pass
        return False
    except Exception as e:
        log(f"异常: {type(e).__name__}: {e}")
        traceback.print_exc()
        if page:
            try:
                await page.screenshot(
                    path=os.path.join(DEBUG_DIR, f"error_T{thread_id}.png")
                )
            except Exception:
                pass
        return False
    finally:
        try:
            if page:
                await page.close()
        except Exception:
            pass
        try:
            if context:
                await context.close()
        except Exception:
            pass
        try:
            if camoufox_obj:
                await camoufox_obj.__aexit__(None, None, None)
        except Exception:
            pass
        gc.collect()


def run_job(thread_id, task_id, timeout_sec=300):
    return asyncio.run(async_run_job(thread_id, task_id, timeout_sec))


def worker(thread_id, count):
    prefix = f"[Thread-{thread_id}]"
    print(f"{prefix} 启动，目标: {count} 个账号")

    success_count = 0
    fail_streak = 0
    while success_count < count:
        current_task = success_count + 1
        success = run_job(thread_id, current_task, timeout_sec=300)
        if success:
            print(
                f"{prefix} 任务 {current_task} 完成! "
                f"(成功 {success_count + 1}/{count})"
            )
            success_count += 1
            fail_streak = 0
            time.sleep(3)
        else:
            fail_streak += 1
            print(
                f"{prefix} 任务 {current_task} 失败 "
                f"(连续失败 {fail_streak} 次)"
            )
            if fail_streak >= 5:
                print(f"{prefix} 连续失败 {fail_streak} 次，等待 10s")
                time.sleep(10)
                fail_streak = 0
            else:
                time.sleep(2)

    print(f"{prefix} 全部完成!")


def main():
    parser = argparse.ArgumentParser(description="Grok 注册助手")
    parser.add_argument("--count", type=int, default=1, help="每个线程注册次数")
    parser.add_argument("--threads", type=int, default=1, help="线程数")
    parser.add_argument("--once", action="store_true", help="只运行一次，不重复询问")
    args = parser.parse_args()

    print("=" * 60)
    print("     Grok 注册助手 [v12.0 - Camoufox headless]")
    print("=" * 60)
    print(f"  浏览器: Camoufox (隐蔽 Firefox, 无头模式)")
    print(f"  调试目录: {DEBUG_DIR}")
    print(f"  结果目录: {GROK_DIR}")

    total_count = args.count
    thread_count = args.threads

    print(f"\n{thread_count} 线程，每线程 {total_count} 次")
    print(f"输出: {GROK_FILE}")
    print("=" * 60)

    with ThreadPoolExecutor(max_workers=thread_count) as executor:
        futures = []
        for i in range(thread_count):
            futures.append(executor.submit(worker, i, total_count))
            time.sleep(2)
        for f in futures:
            f.result()

    print("=" * 60)
    print("所有任务结束!")
    print(f"结果: {GROK_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    main()