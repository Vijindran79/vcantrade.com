"""Visual Laminate Mapper — maps WealthCharts UI elements to screen coordinates."""
from playwright.sync_api import sync_playwright
import json
import time

pw = sync_playwright().start()
try:
    browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9223")
    page = None
    for ctx in browser.contexts:
        for pg in ctx.pages:
            if "wealthcharts" in (pg.url or "").lower():
                page = pg
                break
        if page:
            break

    if not page:
        print(json.dumps({"error": "No WealthCharts tab found on localhost:9223"}))
    else:
        page.bring_to_front()
        time.sleep(1)

        cdp = page.context.new_cdp_session(page)

        # Get window bounds
        try:
            win = cdp.send("Browser.getWindowForTarget")
            bounds = win.get("bounds", {})
            win_x = bounds.get("left", 0)
            win_y = bounds.get("top", 0)
        except Exception:
            win_x, win_y = 0, 0

        # Chrome toolbar height (address bar + tabs) — typical Windows Chrome
        chrome_toolbar = 85

        js_code = """() => {
            const results = {};

            // Strategy 1: Try data-testid / data-type selectors
            const hardSelectors = {
                'buy_btn': ['button[data-type="buy-mkt"]', '[data-testid="buy-mkt"]'],
                'sell_btn': ['button[data-type="sell-mkt"]', '[data-testid="sell-mkt"]'],
                'search_bar': ['[data-testid="symbol-search-input"]', 'input[placeholder*="symbol" i]', 'input[placeholder*="search" i]'],
                'account_dropdown': ['div.account-id-display', '[data-testid*="account"]']
            };

            for (const [key, sels] of Object.entries(hardSelectors)) {
                for (const sel of sels) {
                    try {
                        const el = document.querySelector(sel);
                        if (el) {
                            const rect = el.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) {
                                results[key] = {
                                    x: Math.round(rect.left + rect.width / 2),
                                    y: Math.round(rect.top + rect.height / 2),
                                    w: Math.round(rect.width),
                                    h: Math.round(rect.height),
                                    method: 'selector: ' + sel,
                                    text: (el.textContent || el.value || '').trim().substring(0, 40)
                                };
                                break;
                            }
                        }
                    } catch(e) {}
                }
            }

            // Strategy 2: Text-based fallback for buttons
            const allEls = document.querySelectorAll('button, [role="button"], div, span, a, input');
            for (const el of allEls) {
                const text = (el.textContent || el.value || '').trim().toUpperCase();
                const rect = el.getBoundingClientRect();
                if (rect.width < 10 || rect.height < 10 || rect.width > 500) continue;

                if (!results.buy_btn && (text === 'BUY MKT' || text === 'BUY MARKET' || text === 'BUY')) {
                    results.buy_btn = {
                        x: Math.round(rect.left + rect.width / 2),
                        y: Math.round(rect.top + rect.height / 2),
                        w: Math.round(rect.width),
                        h: Math.round(rect.height),
                        method: 'text-match',
                        text: text.substring(0, 40)
                    };
                }
                if (!results.sell_btn && (text === 'SELL MKT' || text === 'SELL MARKET' || text === 'SELL')) {
                    results.sell_btn = {
                        x: Math.round(rect.left + rect.width / 2),
                        y: Math.round(rect.top + rect.height / 2),
                        w: Math.round(rect.width),
                        h: Math.round(rect.height),
                        method: 'text-match',
                        text: text.substring(0, 40)
                    };
                }
                if (!results.account_dropdown && text.includes('314327')) {
                    results.account_dropdown = {
                        x: Math.round(rect.left + rect.width / 2),
                        y: Math.round(rect.top + rect.height / 2),
                        w: Math.round(rect.width),
                        h: Math.round(rect.height),
                        method: 'text-match',
                        text: text.substring(0, 40)
                    };
                }
            }

            // Strategy 3: CSS class-based fallback for search input
            if (!results.search_bar) {
                const inputs = document.querySelectorAll('input');
                for (const inp of inputs) {
                    const cls = (inp.className || '').toLowerCase();
                    const ph = (inp.placeholder || '').toLowerCase();
                    const rect = inp.getBoundingClientRect();
                    if (rect.width < 30) continue;
                    if (cls.includes('search') || cls.includes('symbol') || ph.includes('search') || ph.includes('symbol')) {
                        results.search_bar = {
                            x: Math.round(rect.left + rect.width / 2),
                            y: Math.round(rect.top + rect.height / 2),
                            w: Math.round(rect.width),
                            h: Math.round(rect.height),
                            method: 'css-class-fallback',
                            text: (inp.value || inp.placeholder || '').substring(0, 40)
                        };
                        break;
                    }
                }
            }

            return results;
        }"""

        dom_coords = page.evaluate(js_code)

        # Convert DOM -> Screen coordinates
        screen = {}
        pyautogui_map = {}
        for key, data in dom_coords.items():
            sx = win_x + data["x"]
            sy = win_y + chrome_toolbar + data["y"]
            screen[key] = {
                "screen_x": sx,
                "screen_y": sy,
                "dom_x": data["x"],
                "dom_y": data["y"],
                "width": data["w"],
                "height": data["h"],
                "method": data["method"],
                "text": data["text"],
            }
            pyautogui_map[key] = [sx, sy]

        # Check which elements are missing
        missing = [k for k in ("buy_btn", "sell_btn", "search_bar", "account_dropdown") if k not in screen]

        output = {
            "status": "ok" if not missing else "partial",
            "page_url": page.url[:100],
            "window_position": {"x": win_x, "y": win_y},
            "chrome_toolbar_px": chrome_toolbar,
            "found": len(screen),
            "missing": missing,
            "elements": screen,
            "pyautogui_click_targets": pyautogui_map,
            "usage_example": "import pyautogui; pyautogui.click(pyautogui_map['buy_btn'][0], pyautogui_map['buy_btn'][1])"
        }
        print(json.dumps(output, indent=2))

except Exception as e:
    print(json.dumps({"error": str(e)}))
finally:
    pw.stop()
