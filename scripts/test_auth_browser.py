"""Browser registration/login/logout smoke test against the configured MySQL.

Run after migration: python -m scripts.test_auth_browser
Creates a temporary test account and removes it on completion.
"""
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright
from sqlalchemy import delete

from backend.config import Settings
from backend.database import make_engine, make_session_factory
from backend.models import User


def main():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = {**os.environ, "APP_ORIGIN": base, "SESSION_COOKIE_SECURE": "false",
           "OPENROUTER_ALLOW_FINANCIAL_DATA": "false"}
    email = f"browser-{secrets.token_hex(10)}@example.com"
    password = secrets.token_urlsafe(24)
    root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
        cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    engine = make_engine(Settings.from_env().database_url)
    try:
        for _ in range(100):
            try:
                with urlopen(base + "/login", timeout=1):
                    break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError("Local server did not start")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge" if os.name == "nt" else "chromium", headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + "/dashboard")
            expect(page).to_have_url(base + "/login")
            page.locator("#register-tab").click()
            page.locator("#name").fill("Проверка браузера")
            page.locator("#email").fill(email)
            page.locator("#password").fill(password)
            page.locator("#password-confirmation").fill("different password")
            page.locator("#auth-submit").click()
            expect(page).to_have_url(base + "/register")
            assert page.locator("#password-confirmation").evaluate("el => !el.validity.valid")
            page.locator("#password-confirmation").fill(password)
            page.locator("#auth-submit").click()
            expect(page).to_have_url(base + "/dashboard")
            expect(page.locator("#account-name")).to_have_text("Проверка браузера")
            page.reload()
            expect(page.locator("#account-name")).to_have_text("Проверка браузера")
            page.locator('nav a[href="#upload"]').click()
            page.locator("#statement-file").set_input_files(root / "data/sample.csv")
            page.locator("#upload-submit").click()
            expect(page.locator("#upload-message")).to_contain_text("Обработка завершена", timeout=60000)
            page.locator("#account-logout").click()
            expect(page).to_have_url(base + "/login")
            assert context.request.get(base + "/api/transactions").status == 401
            page.locator("#email").fill(email)
            page.locator("#password").fill("incorrect password")
            page.locator("#auth-submit").click()
            expect(page.locator("#auth-message")).to_contain_text("Неверный email или пароль")
            page.locator("#password").fill(password)
            page.locator("#auth-submit").click()
            expect(page).to_have_url(base + "/dashboard")
            assert len(context.request.get(base + "/api/transactions").json()) > 0
            page.locator("#account-logout").click()
            page.set_viewport_size({"width": 390, "height": 844})
            page.goto(base + "/register")
            expect(page.locator("#auth-submit")).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            screenshot = root / ".local/auth-register-mobile.png"
            screenshot.parent.mkdir(exist_ok=True)
            page.screenshot(path=str(screenshot), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.screenshot(path=str(root / ".local/auth-register-desktop.png"), full_page=True)
            assert not errors, errors
            browser.close()
        print("Browser OK: validation, registration, session reload, upload, logout, login, route protection, mobile layout.")
    finally:
        process.terminate()
        process.wait(timeout=15)
        with make_session_factory(engine)() as db:
            db.execute(delete(User).where(User.email == email))
            db.commit()
        engine.dispose()


if __name__ == "__main__":
    main()
