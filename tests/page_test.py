"""End-to-end page test on the Mac: real Chrome (separate throwaway profile), fake microphone fed from a WAV.
Usage: .venv/bin/python tests/page_test.py testclips/clip.wav out.png
"""
import json
import os
import sys
import tempfile

from playwright.sync_api import sync_playwright

clip = os.path.abspath(sys.argv[1])
shot = os.path.abspath(sys.argv[2])
url = os.environ.get("BARK_URL", "http://127.0.0.1:8099/")
import soundfile as sf
dur = sf.info(clip).duration

with sync_playwright() as p, tempfile.TemporaryDirectory() as prof:
    ctx = p.chromium.launch_persistent_context(
        prof, channel="chrome", headless=True,
        args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
              f"--use-file-for-fake-audio-capture={clip}%noloop"],
        viewport={"width": 1100, "height": 1250},
        permissions=["microphone"],
    )
    page = ctx.new_page()
    page.goto(url)
    page.wait_for_timeout(800)
    talk = page.locator("#talk")

    def hold(label):
        n0 = page.locator("#list > *").count()
        box = talk.bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.wait_for_timeout(int(dur * 1000) + 700)
        page.mouse.up()
        page.wait_for_function(f"document.querySelectorAll('#list > *').length > {n0}", timeout=60000)
        page.wait_for_timeout(300)
        print(label, "->", page.locator("#list > *").first.inner_text().replace("\n", " | "))

    page.click('[data-variant="regular"]')
    hold("MIC regular")
    page.click('[data-variant="clean"]')
    hold("MIC clean")
    page.check("#both")
    n0 = page.locator("#list > *").count()
    page.set_input_files("#file", clip)
    page.wait_for_function(f"document.querySelectorAll('#list > *').length > {n0}", timeout=60000)
    page.wait_for_timeout(300)
    print("FILE both ->", page.locator("#list > *").first.inner_text().replace("\n", " | "))
    page.uncheck("#both")
    page.screenshot(path=shot, full_page=True)
    ctx.close()
print("screenshot", shot)
