"""History UI check in real Chrome (throwaway profile, fake mic from a WAV).
Usage: .venv/bin/python tests/history_test.py testclips/clip.wav out.png   (server must be running)
"""
import os, sys, tempfile
from playwright.sync_api import sync_playwright
import soundfile as sf

clip, shot = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
url = os.environ.get("BARK_URL", "http://127.0.0.1:8099/")
dur = sf.info(clip).duration
rows = "#history .row"

with sync_playwright() as p, tempfile.TemporaryDirectory() as prof:
    ctx = p.chromium.launch_persistent_context(prof, channel="chrome", headless=True,
        args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
              f"--use-file-for-fake-audio-capture={clip}%noloop"],
        viewport={"width": 1180, "height": 780}, permissions=["microphone"])
    page = ctx.new_page()
    page.on("dialog", lambda d: (print("DIALOG", d.message), d.accept()))
    page.goto(url); page.wait_for_timeout(1000)
    n0 = page.locator(rows).count(); print("rows at start", n0)
    page.click('#rec-switch [data-variant="regular"]')
    b = page.locator("#talk").bounding_box()
    page.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2); page.mouse.down()
    page.wait_for_function("document.querySelector('#talk').textContent.includes('Listening')", timeout=15000)
    page.wait_for_timeout(int(dur * 1000) + 700); page.mouse.up()
    page.wait_for_function(f"document.querySelectorAll('{rows}').length > {n0}", timeout=60000)
    page.wait_for_timeout(800)
    print("after mic:", page.locator(rows).count(), "| first row:", page.locator(rows).first.inner_text().replace("\n", " | "))
    print("detail:", page.locator("#rec-name").inner_text(), "|", page.locator("#transcript").inner_text()[:90], "|", page.locator("#timing").inner_text())
    page.click('#detail-switch [data-variant="clean"]')
    page.wait_for_function("!document.querySelector('#transcript').className.includes('wait') && document.querySelector('#timing').textContent.includes('Clean')", timeout=60000)
    print("clean:", page.locator("#transcript").inner_text()[:90], "|", page.locator("#timing").inner_text())
    page.click("#rename"); page.wait_for_timeout(200)
    page.keyboard.press("Meta+A"); page.keyboard.type("Thursday budget note"); page.keyboard.press("Enter")
    page.wait_for_timeout(800)
    print("renamed:", page.locator("#rec-name").inner_text(), "| row:", page.locator(rows).first.inner_text().split("\n")[0])
    print("audio src:", page.eval_on_selector("#player", "e => e.currentSrc || e.src"))
    page.wait_for_timeout(500)
    print("audio duration:", page.eval_on_selector("#player", "e => e.duration"))
    page.screenshot(path=shot)
    ctx.close()
print("screenshot", shot)
