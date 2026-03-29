"""One-off script to scrape the 'How To Play' rules from IPL Fantasy."""

import re
from pathlib import Path

from ipl_fantasy.auth import create_browser, login, HOME_URL
from ipl_fantasy.config import Settings
from ipl_fantasy.notify import Telegram

# Suppress noisy logging from auth/notify
import logging
logging.basicConfig(level=logging.WARNING)

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "how-to-play.md"


def scrape_rules() -> str:
    """Open the 'How To Play' popup and scrape all rules text."""
    settings = Settings()
    telegram = Telegram(settings)

    pw, browser = create_browser()

    # Ensure we're logged in
    print("Logging in...")
    login(browser, settings, telegram)

    page = browser.new_page()

    try:
        print("Navigating to home page...")
        page.goto(HOME_URL, wait_until="networkidle")
        page.wait_for_timeout(3000)

        # Screenshot the homepage first to see what's available
        page.screenshot(path="/tmp/ipl_home.png", full_page=True)
        print("Homepage screenshot saved to /tmp/ipl_home.png")

        # Dump all visible link/button text to find the right label
        links = page.evaluate("""() => {
            const els = document.querySelectorAll('a, button, [role="button"], [onclick]');
            return Array.from(els).map(el => ({
                text: el.textContent.trim().substring(0, 80),
                tag: el.tagName,
                href: el.href || '',
                visible: el.offsetParent !== null,
            })).filter(e => e.text.length > 0);
        }""")
        print("Links/buttons on page:")
        for l in links:
            print(f"  [{l['tag']}] {l['text'][:60]} (visible={l['visible']})")

        # Try to find and click "How To Play" (case-insensitive partial match)
        print("\nClicking 'How To Play'...")
        how_to_play = page.locator("text=/how to play/i").first
        how_to_play.click(timeout=10000)
        page.wait_for_timeout(3000)
        page.screenshot(path="/tmp/ipl_how_to_play.png")
        print("Screenshot saved to /tmp/ipl_how_to_play.png")

        # Find the scrollable modal/popup container
        modal_info = page.evaluate("""() => {
            const candidates = document.querySelectorAll(
                '[class*="modal"], [class*="popup"], [class*="dialog"], '
                + '[class*="overlay"], [class*="drawer"], [role="dialog"]'
            );
            return Array.from(candidates).map(el => ({
                tag: el.tagName,
                className: el.className,
                id: el.id,
                scrollHeight: el.scrollHeight,
                clientHeight: el.clientHeight,
                scrollable: el.scrollHeight > el.clientHeight,
                textLength: el.textContent.length,
            }));
        }""")

        print(f"Found {len(modal_info)} modal candidates:")
        for m in modal_info:
            print(f"  {m['tag']}.{m['className'][:60]} scrollable={m['scrollable']} "
                  f"textLen={m['textLength']}")

        # Pick the best container — scrollable with most text
        scrollable = [m for m in modal_info if m['scrollable'] and m['textLength'] > 100]
        if not scrollable:
            scrollable = [m for m in modal_info if m['textLength'] > 100]
        if not scrollable:
            print("ERROR: Could not find the rules popup. Check /tmp/ipl_how_to_play.png")
            return ""

        target = max(scrollable, key=lambda m: m['textLength'])
        print(f"Using container: {target['tag']}.{target['className'][:60]}")

        selector = (f"#{target['id']}" if target['id']
                    else f".{target['className'].split()[0]}" if target['className']
                    else target['tag'])

        # Scroll through the popup incrementally to trigger lazy loading
        page.evaluate("""(sel) => {
            const el = document.querySelector(sel);
            if (!el) return;
            const step = el.clientHeight;
            let pos = 0;
            const id = setInterval(() => {
                pos += step;
                el.scrollTop = pos;
                if (pos >= el.scrollHeight) clearInterval(id);
            }, 200);
        }""", selector)
        page.wait_for_timeout(5000)

        # Ensure we're fully at the bottom
        page.evaluate("""(sel) => {
            const el = document.querySelector(sel);
            if (el) el.scrollTop = el.scrollHeight;
        }""", selector)
        page.wait_for_timeout(2000)

        # Extract structured text from the popup
        content = page.evaluate(r"""(sel) => {
            const el = document.querySelector(sel);
            if (!el) return '';

            function extract(node) {
                let result = '';
                for (const child of node.childNodes) {
                    if (child.nodeType === 3) {
                        const t = child.textContent.trim();
                        if (t) result += t + ' ';
                    } else if (child.nodeType === 1) {
                        const tag = child.tagName.toLowerCase();
                        const t = child.textContent.trim();
                        if (!t) continue;
                        if (/^h[1-6]$/.test(tag)) {
                            result += '\n' + '#'.repeat(+tag[1]) + ' ' + t + '\n\n';
                        } else if (tag === 'p') {
                            result += t + '\n\n';
                        } else if (tag === 'li') {
                            result += '- ' + t + '\n';
                        } else if (tag === 'ul' || tag === 'ol') {
                            result += extract(child) + '\n';
                        } else if (tag === 'br') {
                            result += '\n';
                        } else if (tag === 'table') {
                            for (const row of child.querySelectorAll('tr')) {
                                const cells = Array.from(row.querySelectorAll('td, th'))
                                    .map(c => c.textContent.trim());
                                result += '| ' + cells.join(' | ') + ' |\n';
                            }
                            result += '\n';
                        } else if (['div','section','span','strong','b','em','i','a'].includes(tag)) {
                            result += extract(child);
                        } else {
                            result += t + '\n';
                        }
                    }
                }
                return result;
            }
            return extract(el);
        }""", selector)

        page.screenshot(path="/tmp/ipl_how_to_play_final.png")
        print(f"Extracted {len(content)} characters")
        return content

    finally:
        page.close()
        browser.close()
        pw.stop()


def clean_markdown(raw: str) -> str:
    """Clean up raw extracted text into readable markdown."""
    text = re.sub(r'\n{3,}', '\n\n', raw.strip())
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'(#+) ', r'\n\1 ', text)
    return f"# IPL Fantasy - How To Play\n\n{text.strip()}\n"


def main():
    raw = scrape_rules()
    if not raw:
        print("No content scraped. Exiting.")
        return

    md = clean_markdown(raw)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(md, encoding="utf-8")
    print(f"\nRules saved to {OUTPUT_PATH}")
    print(f"Total length: {len(md)} characters")


if __name__ == "__main__":
    main()
