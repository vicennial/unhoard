"""Playwright check of the card page (run by test_browser.py with a Python that has Playwright, not by unittest).

Usage: browser_check.py BASE_URL SCREENSHOT_DIR [REAL_URL [GO_URL]]
First-run checklist: BASE_URL serves only the fictional demo cards, so the checklist is open, tells the visitor they
are looking at sample cards, has a Copy button per command, can be hidden (and stays hidden after a reload) and
reopened from the Setup lamp; REAL_URL (optional) serves one real enriched bookmark, so the checklist is hidden there
while the Setup lamp is still present.
Checks: the station of a card whose brief holds a URL lists it under "Links in this task", every station shows its
brief (there is no toggle), only https URLs are links (noopener noreferrer), an opened waiting row shows the links
with the scheme as written, hidden/bidi characters are marked and make the URL plain text, and "+N more" shows
when the server left URLs out.
"""
import json
import sys

from playwright.sync_api import sync_playwright

base, shots = sys.argv[1], sys.argv[2]
real = sys.argv[3] if len(sys.argv) > 3 else None
go_url = sys.argv[4] if len(sys.argv) > 4 else None  # a server whose Go starts something (Omnigent found)
failures = []


def check(ok, message):
    if not ok:
        failures.append(message)


with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context(viewport={"width": 1280, "height": 900}, permissions=["clipboard-read", "clipboard-write"])
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/")
    page.wait_for_selector(".station")
    stations = page.query_selector_all(".station")
    with_links = [s for s in stations if s.query_selector(".urls")]
    check(len(with_links) >= 1, "no station shows the demo card's relate URL")
    for s in stations:
        check(s.query_selector(".brief") is not None, "a station does not show its brief")
        check(s.query_selector(".disclose") is None, "there should be no Brief and note toggle")
    for s in with_links:
        check("links in this task" in s.inner_text().lower(), "the field label is missing")
        for a in s.query_selector_all(".urls a"):
            check(a.get_attribute("href").startswith("https://"), "a non-https link is clickable")
            check(a.get_attribute("rel") == "noopener noreferrer", "link rel is wrong")
            check(a.inner_text() == a.get_attribute("href"), "the link text differs from its target")
    page.screenshot(path=shots + "/stations.png")
    # The first lamp says what it does: nothing is started with the print target, so it reads "Show brief".
    labels = [b.text_content() for b in page.query_selector_all(".station .lamp.go")]
    check(labels and set(labels) == {"Show brief"}, f"the print target should say Show brief: {labels}")
    if go_url:
        gp = context.new_page()
        gp.goto(go_url + "/")
        gp.wait_for_selector(".station")
        go_labels = [b.text_content() for b in gp.query_selector_all(".station .lamp.go")]
        check(go_labels and set(go_labels) == {"Go"}, f"a target that starts something should say Go: {go_labels}")
        gp.close()

    # First-run checklist, demo state: open, says so, one Copy button per command, commands are plain text.
    page.wait_for_selector("#setup .pf-item")
    check(page.is_visible("#setup"), "the checklist should show while only demo cards exist")
    check(len(page.query_selector_all("#setup .pf-item")) == 6, "the checklist should list six items")
    check("looking at sample cards" in page.inner_text("#setup"), "the demo notice is missing")
    codes = [c.inner_text() for c in page.query_selector_all("#setup .pf-code")]
    copies = page.query_selector_all("#setup .pf-copy")
    check(codes and len(codes) == len(copies), f"every command needs a Copy button: {codes}")
    check("unhoard login x" in codes, f"the login command is missing: {codes}")
    check(page.is_visible("#setup-lamp") and "/6" in page.inner_text("#setup-lamp"), "the Setup lamp is missing")
    copies[0].click()
    page.wait_for_selector("#setup .pf-copy >> text=Copied")
    check(page.text_content("#setup .pf-copy") == "Copied", "the Copy button did not report a copy")
    check(page.evaluate("navigator.clipboard.readText()") == codes[0], "the clipboard does not hold the command")
    page.screenshot(path=shots + "/setup-demo.png")
    page.click("#setup-hide")
    check(not page.is_visible("#setup"), "Hide did not close the checklist")
    page.reload()
    page.wait_for_selector(".station")
    page.wait_for_selector("#setup-lamp:not([hidden])")
    check(not page.is_visible("#setup"), "a hidden checklist came back after a reload")
    page.click("#setup-lamp")
    check(page.is_visible("#setup") and page.get_attribute("#setup-lamp", "aria-expanded") == "true",
          "the Setup lamp did not reopen the checklist")
    page.click("#setup-lamp")
    check(not page.is_visible("#setup"), "the Setup lamp did not close the checklist")

    if real:
        rp = context.new_page()
        rp.goto(real + "/")
        rp.wait_for_selector(".station")
        rp.wait_for_selector("#setup-lamp:not([hidden])")
        check(not rp.is_visible("#setup"), "the checklist should be hidden once a real card exists")
        rp.click("#setup-lamp")
        check(rp.is_visible("#setup"), "the Setup lamp should open the checklist even with real cards")
        rp.close()

    # Phone width: no sideways scroll with the checklist open.
    phone = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
    phone.goto(base + "/")
    phone.wait_for_selector("#setup .pf-item")
    check(phone.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"),
          "the page scrolls sideways at phone width")
    phone.close()

    # An opened waiting row and hostile / odd URLs, with the API answered locally.
    def route(r):
        resp = r.fetch()
        data = json.loads(resp.text())
        card = dict(data["picks"][0]) if data["picks"] else None
        if card:
            card["action_id"] = "BMK-FFFFFFFF"
            card["relate_urls"] = ["http://plain.example.invalid/x", "https://ok.example.invalid/a?b=1",
                                   "javascript:alert(1)", "www.example.invalid/no-scheme", "ftp://files.example.invalid/x",
                                   "file:///etc/hosts", "git@host.example.invalid:org/repo.git", "example.invalid.com",
                                   "https://ok.example.invalid/\u202egpj.exe", "https://zw.example.invalid/a\u200bb",
                                   "https://tag.example.invalid/a\U000E0041b"]
            card["relate_urls_more"] = 3
            data["waiting"] = [card] + data["waiting"]
        r.fulfill(response=resp, body=json.dumps(data))

    page.route("**/api/cards*", route)
    page.goto(base + "/")
    page.wait_for_selector("#waiting .row")
    page.click("#waiting .row")
    page.wait_for_selector(".row-open .urls")
    opened = page.query_selector(".row-open")
    links = opened.query_selector_all(".urls a")
    check([a.get_attribute("href") for a in links] == ["https://ok.example.invalid/a?b=1"],
          "only a clean https URL may be a link")
    texts = [li.inner_text() for li in opened.query_selector_all(".urls li")]
    check(texts[:8] == ["http://plain.example.invalid/x", "https://ok.example.invalid/a?b=1", "javascript:alert(1)",
                        "www.example.invalid/no-scheme", "ftp://files.example.invalid/x", "file:///etc/hosts",
                        "git@host.example.invalid:org/repo.git", "example.invalid.com"], f"URL text not printed in full: {texts}")
    check(texts[8] == "https://ok.example.invalid/<U+202E>gpj.exe" and texts[9] == "https://zw.example.invalid/a<U+200B>b",
          f"hidden characters not marked: {texts[8:10]}")
    check(texts[10] == "https://tag.example.invalid/a<U+E0041>b", f"tag character not marked: {texts[10]}")
    check(texts[11] == "+3 more not shown", f"the truncation note is missing: {texts[11:]}")
    check("\u202e" not in "".join(texts) and "\U000E0041" not in "".join(texts), "a hidden character reached the page")
    check([a.get_attribute("href") for a in links] == ["https://ok.example.invalid/a?b=1"], "a marked URL became a link")
    page.screenshot(path=shots + "/opened.png")
    # Show brief: a 409 "target changed" starts nothing and keeps the card; a real answer shows the brief as plain text.
    sb = context.new_page()
    sb.route("**/api/cards/*/decide", lambda r: r.fulfill(status=409, content_type="application/json", body=json.dumps(
        {"ok": False, "reason": "target_changed", "message": "launch target changed", "launch": {}})))
    sb.goto(base + "/")
    sb.wait_for_selector(".station")
    count = len(sb.query_selector_all(".station"))
    sb.fill(".station textarea", "keep this draft")
    sb.click(".station .lamp.go")
    sb.click(".station .lamp.go")
    sb.wait_for_selector("#notice:has-text('nothing was started')")
    check(len(sb.query_selector_all(".station")) == count, "a refused Go must leave the cards where they are")
    check(sb.input_value(".station textarea") == "keep this draft", "a refused Go lost the typed note")
    check(sb.is_hidden("#brief-sect"), "no brief should show after a refused Go")
    sb.unroute("**/api/cards/*/decide")
    sb.click(".station .lamp.go")
    sb.wait_for_selector("#brief-sect:not([hidden])")
    check(sb.text_content("#brief-text") == "BRIEF <b>not html</b> TEXT", "the brief is not shown as plain text")
    check(sb.query_selector("#brief-text b") is None, "the brief text was parsed as HTML")
    check("Brief ready" in sb.text_content("#notice"), "the notice should come from the server's answer")
    check(sb.is_visible("#brief-copy"), "the brief needs a Copy button")
    sb.click("#brief-copy")
    sb.wait_for_selector("#brief-copy:has-text('Copied'), #brief-copy:has-text('Press Ctrl+C')")  # the clipboard is async
    # the announcement is short, and it lives where assistive tech can reach it (not inside the hidden setup panel)
    live = sb.text_content("#copy-live")
    check(live in ("Copied", "Could not copy. The command is selected: press Ctrl+C."), f"copy announcement: {live!r}")
    check(sb.evaluate("document.getElementById('copy-live').closest('[hidden]') === null"),
          "the copy announcement sits inside a hidden panel")
    sb.click("#brief-close")
    check(sb.is_hidden("#brief-sect"), "Close should hide the brief")
    sb.close()

    # At 390 px: thumb-sized buttons (44 px), the brief panel fits, nothing scrolls sideways.
    ph = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3, is_mobile=True,
                             has_touch=True).new_page()
    ph.goto(base + "/")
    ph.wait_for_selector(".station, button.plain")
    if ph.query_selector(".station") is None:  # today's one card was answered above: poll one more
        ph.click("button.plain")
        ph.wait_for_selector(".station")
    for lamp in ph.query_selector_all(".station .lamp"):
        box = lamp.bounding_box()
        check(box["height"] >= 44, f"a Go/Later/Skip button is only {box['height']:.0f} px tall at 390 px")
    check(ph.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"),
          "the cards scroll sideways at 390 px")
    ph.click(".station .lamp.go")
    ph.wait_for_selector("#brief-sect:not([hidden])")
    for ident in ("#brief-copy", "#brief-close"):
        check(ph.query_selector(ident).bounding_box()["height"] >= 44, f"{ident} is under 44 px tall at 390 px")
    panel = ph.query_selector("#brief-sect").bounding_box()
    check(panel["x"] >= 0 and panel["x"] + panel["width"] <= 390, f"the brief panel does not fit: {panel}")
    check(ph.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"),
          "the brief scrolls the page sideways at 390 px")
    ph.close()
    check(not errors, f"page errors: {errors}")
    browser.close()

if failures:
    print("FAIL:", *failures, sep="\n  ")
    sys.exit(1)
print("OK")
