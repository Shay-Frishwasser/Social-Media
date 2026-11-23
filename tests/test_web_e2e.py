"""Opt-in browser walkthrough against a real loopback Uvicorn server."""

import io
import os
import socket
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Thread
from time import monotonic, sleep
from zipfile import ZipFile

import pytest
import uvicorn
from PIL import Image
from playwright.sync_api import expect, sync_playwright

from brandai import RuntimeSettings
from brandai.api import create_app
from brandai.api import services as onboarding_services
from brandai.publishing.email import MockEmailDelivery
from test_milestone7_research import FixtureCrawler, QUOTE, StaticSearch


@pytest.mark.skipif(os.getenv("BRANDAI_WEB_E2E") != "1", reason="build web and set BRANDAI_WEB_E2E=1")
def test_local_brand_workflow_in_chromium(tmp_path, monkeypatch):
    assert (Path(__file__).resolve().parents[1] / "web/dist/index.html").is_file()
    monkeypatch.setenv("BRANDAI_CONTENT_PROVIDER", "mock")
    monkeypatch.setenv("BRANDAI_TOPIC_RESEARCH", "live")
    monkeypatch.setenv("BRANDAI_AUTOMATED_ONBOARDING", "deterministic")
    monkeypatch.setattr(onboarding_services, "PublicTopicCrawler", FixtureCrawler)
    monkeypatch.setattr(onboarding_services, "DDGSSearchProvider", StaticSearch)
    mailbox = MockEmailDelivery()
    app = create_app(RuntimeSettings(tmp_path / "runtime"), topic_search=StaticSearch(),
                     topic_crawler=FixtureCrawler(), email_delivery=mailbox)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", ws="none"))
        runner = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        runner.start()
        try:
            deadline = monotonic() + 10
            while not server.started and runner.is_alive() and monotonic() < deadline:
                sleep(0.02)
            assert server.started
            _walkthrough(f"http://127.0.0.1:{port}", tmp_path, app.state.job_runner)
        finally:
            server.should_exit = True
            runner.join(10)


def _walkthrough(origin, tmp_path, job_runner):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900}, accept_downloads=True)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.on("requestfailed", lambda request: errors.append(request.url) if request.failure != "net::ERR_ABORTED" else None)
        page.on("response", lambda response: errors.append(f"{response.status} {response.url}") if response.status >= 400 else None)

        def route(key, title):
            page.locator(f'nav a[href="#{key}"]').click()
            expect(page.get_by_role("heading", level=1)).to_have_text(title)

        def confirm(title, trigger=None):
            page.get_by_role("button", name=trigger or title, exact=True).click()
            dialog = page.get_by_role("dialog", name=title)
            expect(dialog).to_be_visible()
            dialog.get_by_role("button", name="אישור", exact=True).click()
            expect(dialog).not_to_be_visible()

        page.goto(origin)
        expect(page.locator("html")).to_have_attribute("dir", "rtl")
        expect(page.locator("html")).to_have_attribute("lang", "he")
        assert page.locator(".rail").bounding_box()["x"] > page.locator("main").bounding_box()["x"]
        page.get_by_role("button", name="יצירת מותג ראשון").click()
        page.get_by_label("שם המותג", exact=True).fill("Cedar Studio")
        page.get_by_role("button", name="יצירת מותג", exact=True).click()
        expect(page.get_by_role("heading", level=1)).to_have_text("הקמת מותג")
        page.get_by_role("button", name="הזנת עובדות ידנית").click()
        page.get_by_label("מה המותג מציע?").fill("Cedar Studio offers small seasonal dinner menus.")
        page.get_by_label("למי הוא מיועד?").fill("The studio hosts local diners at a shared table.")
        page.get_by_label("מה מייחד אותו?").fill("The kitchen changes its menu with the seasons.")
        page.get_by_role("button", name="שמירת פרופיל לבדיקה").click()
        expect(page.get_by_role("heading", level=1)).to_have_text("זהות ואימות עובדות")
        page.get_by_role("button", name="תיקון", exact=True).first.click()
        expect(page.get_by_role("dialog")).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.get_by_role("dialog")).not_to_be_visible()
        expect(page.get_by_role("button", name="תיקון", exact=True).first).to_be_focused()
        confirm("אישור פרופיל המותג")

        route("design", "שפה חזותית")
        expect(page.get_by_text("טיפוגרפיה", exact=True)).to_be_visible()
        page.get_by_label("גופן בעברית").select_option("Assistant")
        assert page.evaluate("document.fonts.ready.then(() => document.fonts.check('16px Assistant'))")
        confirm("אישור השפה החזותית")

        route("strategy", "אסטרטגיה וכללי פלטפורמה")
        page.get_by_role("button", name="כללים לפי ערוץ").click()
        expect(page.get_by_role("dialog").get_by_text("כללים אפקטיביים")).to_be_visible()
        page.keyboard.press("Escape")
        page.get_by_role("button", name="יצירת אסטרטגיה").click()
        expect(page.get_by_role("slider").first).to_be_visible()
        confirm("אישור אסטרטגיה")

        route("calendar", "לוח תוכן")
        page.get_by_role("button", name="יצירת תוכנית חדשה").click()
        expect(page.locator(".slot").first).to_be_visible()
        confirm("אישור ושריון משבצות")
        page.get_by_role("button", name="חודש", exact=True).click()
        expect(page.locator(".day")).to_have_count(42)
        page.get_by_role("button", name="שבוע", exact=True).click()
        page.locator(".slot").first.click()
        expect(page.get_by_role("dialog").get_by_role("button", name="יצירת תוכן מהמשבצת")).to_be_visible()
        page.keyboard.press("Escape")

        route("photo", "סטודיו לפוסט תמונה")
        png = io.BytesIO()
        Image.new("RGB", (72, 64), "#5a714a").save(png, format="PNG")
        page.locator('input[type="file"]').set_input_files({"name": "dish.png", "mimeType": "image/png", "buffer": png.getvalue()})
        page.get_by_label("מה קורה, מתי, ומה חשוב לציין?").fill("A seasonal dinner is on the menu this Thursday.")
        page.get_by_role("radio", name="סטודיו Wow", exact=False).check()
        page.get_by_role("button", name="שמירת בריף צילום").click()
        expect(page.get_by_role("button", name="יצירת תוכן מהבריף")).to_be_visible()
        page.get_by_role("button", name="יצירת תוכן מהבריף").click()

        expect(page.get_by_role("heading", level=1)).to_have_text("סטודיו תוכן")
        page.get_by_label("נושא ציבורי למחקר").fill("Kitchen energy research")
        page.get_by_role("button", name="חיפוש ואימות ציטוטים").click()
        expect(page.get_by_text(QUOTE)).to_be_visible()
        page.get_by_role("button", name="שימוש במחקר ביצירת התוכן").click()
        page.get_by_role("button", name="יצירת תוכן", exact=True).click()
        expect(page.get_by_role("heading", name="עורך תוכן")).to_be_visible()
        original = page.get_by_label("גוף התוכן").input_value()
        assert QUOTE in original
        page.get_by_label("גוף התוכן").fill(original + "\n\nAn unsupported claim of 98 percent.")
        expect(page.get_by_text("ungrounded_numeric_assertion", exact=False)).to_be_visible()
        item_id = page.url.rsplit("/", 1)[1]
        brand_id = page.get_by_role("combobox", name="מותג פעיל").input_value()
        saved = page.request.get(f"{origin}/api/brands/{brand_id}/content/{item_id}").json()
        assert saved["revision"]["version"] == 1 and saved["revision"]["body"] == original
        page.get_by_role("button", name="שמירת גרסה ובדיקה מחדש").click()
        expect(page.locator(".panel", has=page.get_by_role("heading", name="עורך תוכן"))
               .get_by_text("גרסה 2", exact=False)).to_be_visible()
        expect(page.get_by_text("נדרש תיקון", exact=True).first).to_be_visible()
        page.get_by_label("גוף התוכן").fill(original)
        page.get_by_role("button", name="שמירת גרסה ובדיקה מחדש").click()
        expect(page.get_by_role("button", name="אישור הגרסה הנוכחית")).to_be_enabled()
        confirm("אישור הגרסה הנוכחית")
        route("publishing", "פרסום ומסירה")
        expect(page.get_by_role("button", name="הורדת חבילת ZIP")).to_be_disabled()
        expect(page.get_by_text("נדרש תוצר חזותי לגרסה הזו.", exact=False)).to_be_visible()
        route("content", "סטודיו תוכן")
        page.get_by_role("button", name="תוצר חזותי", exact=True).click()
        page.get_by_role("checkbox", name="קרוסלה מרובת עמודים").check()
        page.get_by_role("button", name="הפקת תוצר חזותי").click()
        expect(page.get_by_role("img", name="תוצר חזותי · עמוד 1")).to_be_visible(timeout=25000)
        page.get_by_label("עמוד בקרוסלה").select_option("2")
        expect(page.get_by_role("img", name="תוצר חזותי · עמוד 2")).to_be_visible()
        page.get_by_role("button", name="תצוגת קרוסלת PDF").click()
        expect(page.locator('iframe[title="תצוגת קרוסלת PDF"]')).to_be_visible()
        page.get_by_role("button", name="סגירת תצוגת PDF").click()
        with page.expect_download() as pdf:
            page.get_by_role("button", name="הורדת קרוסלת PDF").click()
        assert Path(pdf.value.path()).read_bytes().startswith(b"%PDF")

        route("publishing", "פרסום ומסירה")
        expect(page.get_by_role("button", name="הורדת חבילת ZIP")).to_be_enabled()
        with page.expect_download() as archive:
            page.get_by_role("button", name="הורדת חבילת ZIP").click()
        with ZipFile(archive.value.path()) as bundle:
            assert any(name.endswith("manifest.json") for name in bundle.namelist())
            assert any(name.endswith(".pdf") for name in bundle.namelist())

        page.screenshot(path=tmp_path / "publishing.png")
        page.get_by_role("button", name="שליחת חבילה במייל").click()
        email = page.get_by_role("dialog", name="אישור שליחה במייל")
        email.get_by_label("כתובת הנמען").fill("ops@example.org")
        email.get_by_role("button", name="אישור שליחה").click()
        expect(email).not_to_be_visible()
        brand_id = page.get_by_role("combobox", name="מותג פעיל").input_value()
        result = job_runner.run_next(brand_id)
        assert result is not None and result.status == "SUCCEEDED"
        expect(page.get_by_text("נמסר", exact=True)).to_be_visible(timeout=15000)
        page.get_by_role("button", name="עיבוד מקומי").click()
        expect(page.get_by_role("dialog").get_by_text("מסירה לפרסום")).to_be_visible()
        page.keyboard.press("Escape")

        page.get_by_role("button", name="מותג חדש").click()
        page.get_by_label("שם המותג", exact=True).fill("Kitchen Research")
        page.get_by_role("button", name="יצירת מותג", exact=True).click()
        page.get_by_label("אתר ציבורי").fill("https://research.example.org/studies/energy")
        page.get_by_label("שמות משתמש ברשתות (אופציונלי)").fill("@cedar")
        page.get_by_role("button", name="התחלת מחקר").click()
        expect(page.get_by_role("progressbar")).to_be_visible(timeout=15000)
        research_brand = page.get_by_role("combobox", name="מותג פעיל").input_value()
        assert research_brand != brand_id
        with ThreadPoolExecutor() as worker:
            researched = worker.submit(job_runner.run_next, research_brand).result(timeout=20)
        assert researched is not None and researched.status == "SUCCEEDED", researched
        ready = page.get_by_role("button", name="לבדיקת זהות המותג")
        expect(ready).to_be_visible(timeout=15000)
        ready.click()
        page.get_by_label("סינון עובדות").select_option("observed")
        page.locator(".claim-row", has_text=QUOTE).get_by_role("button", name="ציטוט ומקור").click()
        source = page.get_by_role("dialog", name="המקור והציטוט המדויק")
        expect(source.locator("blockquote")).to_have_text(QUOTE)
        expect(source.get_by_role("link")).to_have_attribute("href", "https://research.example.org/studies/energy")
        page.keyboard.press("Escape")

        page.set_viewport_size({"width": 390, "height": 844})
        page.emulate_media(reduced_motion="reduce")
        page.get_by_role("button", name="תפריט", exact=True).click()
        nav = page.get_by_role("dialog", name="סביבת עבודה")
        expect(nav).to_be_visible()
        assert nav.evaluate("(element) => parseFloat(getComputedStyle(element).animationDuration) < 0.005")
        nav.get_by_role("link", name="הקמת מותג").click()
        expect(page.get_by_role("heading", level=1)).to_have_text("הקמת מותג")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors, errors
        page.screenshot(path=tmp_path / "mobile.png")
        browser.close()
