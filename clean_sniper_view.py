"""DOM Cleanse — Strip WealthCharts to a High-Precision Sniper View.
Run after Chrome is started with --remote-debugging-port=9223."""
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
        print(json.dumps({"error": "No WealthCharts tab found"}))
        exit()

    page.bring_to_front()
    time.sleep(1)

    # ================================================================
    # PHASE 1: DOM CLEANSE — Remove all visual noise
    # ================================================================
    cleanse_result = page.evaluate("""() => {
        const report = {hidden: 0, modals_killed: 0, indicators_removed: 0};

        // --- 1. Hide News Feed, Chat, Social, Education sidebars ---
        const sidebarKeywords = /news|chat|social|education|learn|community|help|support|tour|guide|onboard/i;
        const sidebarSelectors = [
            '[class*="sidebar"]', '[class*="side-bar"]', '[class*="feed"]',
            '[class*="chat"]', '[class*="social"]', '[class*="education"]',
            '[class*="news"]', '[class*="learn"]', '[class*="community"]',
            '[class*="help-panel"]', '[class*="tour"]', '[class*="onboard"]',
            '[class*="sidebar"]', 'aside', '[role="complementary"]',
            '[class*="ribbon"]', '[class*="toolbar-left"]', '[class*="toolbar-right"]',
        ];
        for (const sel of sidebarSelectors) {
            document.querySelectorAll(sel).forEach(el => {
                const text = (el.className || '') + ' ' + (el.id || '');
                const rect = el.getBoundingClientRect();
                // Only hide sidebars (narrow vertical panels), not the main chart
                if (rect.width < 350 && rect.height > 100) {
                    el.style.setProperty('display', 'none', 'important');
                    report.hidden++;
                }
            });
        }

        // --- 2. Kill ALL popups, modals, overlays, notifications ---
        const popupSelectors = [
            '[role="dialog"]', '[class*="modal"]', '[class*="popup"]',
            '[class*="overlay"]', '[class*="toast"]', '[class*="notification"]',
            '[class*="banner"]', '[class*="alert-bar"]', '[class*="promo"]',
            '[class*="marketing"]', '[class*="upgrade"]', '[class*="trial"]',
            '[class*="welcome"]', '[class*="whats-new"]', '[class*="changelog"]',
            '[class*="survey"]', '[class*="feedback"]', '[class*="rating"]',
        ];
        for (const sel of popupSelectors) {
            document.querySelectorAll(sel).forEach(el => {
                const rect = el.getBoundingClientRect();
                // Don't hide tiny elements or the trade panel itself
                const text = (el.textContent || '').toLowerCase();
                if (text.includes('buy mkt') || text.includes('sell mkt')) return;
                if (rect.width > 50 && rect.height > 50) {
                    el.style.setProperty('display', 'none', 'important');
                    report.modals_killed++;
                }
            });
        }

        // --- 3. Remove chart indicators EXCEPT Volume Profile and Liquidity ---
        // WealthCharts stores indicators in a list; we find and remove excess
        const indicatorContainers = document.querySelectorAll(
            '[class*="indicator"], [class*="study"], [class*="overlay-list"], ' +
            '[class*="indicator-list"], [class*="studies"], [data-name*="indicator"]'
        );
        const keepPatterns = /volume.?profile|liquidity|fvg|order.?block|imbalance|poc|value.?area/i;
        for (const container of indicatorContainers) {
            const items = container.querySelectorAll('[class*="item"], [class*="row"], [class*="entry"], li, div');
            items.forEach(item => {
                const text = (item.textContent || '').toLowerCase();
                if (!keepPatterns.test(text) && text.length < 100 && text.length > 2) {
                    item.style.setProperty('display', 'none', 'important');
                    report.indicators_removed++;
                }
            });
        }

        // --- 4. Inject persistent CSS to keep Trade Panel pinned and block modals ---
        const style = document.createElement('style');
        style.id = 'sniper-view-css';
        style.textContent = `
            /* BLOCK all modals/popups permanently */
            [role="dialog"]:not([data-keep]),
            [class*="modal"]:not([data-keep]),
            [class*="popup"]:not([data-keep]),
            [class*="overlay"]:not([data-keep]),
            [class*="toast"]:not([data-keep]),
            [class*="notification"]:not([data-keep]),
            [class*="banner"]:not([data-keep]),
            [class*="promo"]:not([data-keep]),
            [class*="marketing"]:not([data-keep]),
            [class*="welcome"]:not([data-keep]),
            [class*="upgrade"]:not([data-keep]),
            [class*="trial"]:not([data-keep]),
            [class*="survey"]:not([data-keep]),
            [class*="feedback"]:not([data-keep]),
            [class*="whats-new"]:not([data-keep]),
            [class*="changelog"]:not([data-keep]),
            [class*="onboarding"]:not([data-keep]),
            [class*="tour"]:not([data-keep]) {
                display: none !important;
                visibility: hidden !important;
                opacity: 0 !important;
                pointer-events: none !important;
                z-index: -9999 !important;
            }

            /* PIN the trade panel — force visible */
            [class*="order-entry"],
            [class*="trade-panel"],
            [class*="trade-bar"],
            button[data-type="buy-mkt"],
            button[data-type="sell-mkt"],
            [class*="buy"][class*="mkt"],
            [class*="sell"][class*="mkt"] {
                display: flex !important;
                visibility: visible !important;
                opacity: 1 !important;
                z-index: 99999 !important;
            }

            /* Maximize chart canvas */
            [class*="chart-area"],
            [class*="chart-container"],
            canvas {
                width: 100% !important;
                height: 100% !important;
            }
        `;
        // Remove old sniper CSS if it exists
        const old = document.getElementById('sniper-view-css');
        if (old) old.remove();
        document.head.appendChild(style);

        // --- 5. Dismiss any "Got it" / "Dismiss" / "Close" buttons ---
        const dismissButtons = document.querySelectorAll('button, [role="button"]');
        for (const btn of dismissButtons) {
            const text = (btn.textContent || '').trim().toLowerCase();
            if (/^(got it|dismiss|close|no thanks|maybe later|ok|skip|continue|done|x)$/i.test(text)) {
                const rect = btn.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    try { btn.click(); } catch(e) {}
                }
            }
        }

        return report;
    }""")
    print(f"[CLEANSE] Phase 1 complete: {json.dumps(cleanse_result)}")
    time.sleep(0.5)

    # ================================================================
    # PHASE 2: Force-open trade panel if hidden
    # ================================================================
    panel_result = page.evaluate("""() => {
        // Click the $ icon to ensure trade panel is open
        const triggers = document.querySelectorAll('i.fa-dollar-sign, [class*="dollar"], [class*="trade-icon"]');
        for (const t of triggers) {
            const rect = t.getBoundingClientRect();
            if (rect.width > 0) {
                t.click();
                return {panel_opened: true, method: 'dollar-icon'};
            }
        }
        return {panel_opened: false};
    }""")
    print(f"[CLEANSE] Phase 2 (panel): {json.dumps(panel_result)}")
    time.sleep(1)

    # ================================================================
    # PHASE 3: Re-calculate button coordinates post-cleanse
    # ================================================================
    cdp = page.context.new_cdp_session(page)
    try:
        win = cdp.send("Browser.getWindowForTarget")
        bounds = win.get("bounds", {})
        win_x = bounds.get("left", 0)
        win_y = bounds.get("top", 0)
    except Exception:
        win_x, win_y = 0, 0

    chrome_toolbar = 85

    coords = page.evaluate("""() => {
        const results = {};
        const map = {
            'buy_btn': ['button[data-type="buy-mkt"]', '[data-testid="buy-mkt"]'],
            'sell_btn': ['button[data-type="sell-mkt"]', '[data-testid="sell-mkt"]'],
            'search_bar': ['[data-testid="symbol-search-input"]', 'input[placeholder*="symbol" i]', 'input[placeholder*="search" i]'],
            'account_dropdown': ['div.account-id-display', '[data-testid*="account"]']
        };

        for (const [key, sels] of Object.entries(map)) {
            for (const sel of sels) {
                try {
                    const el = document.querySelector(sel);
                    if (el) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            results[key] = {
                                dom_x: Math.round(rect.left + rect.width / 2),
                                dom_y: Math.round(rect.top + rect.height / 2),
                                w: Math.round(rect.width),
                                h: Math.round(rect.height),
                                sel: sel
                            };
                            break;
                        }
                    }
                } catch(e) {}
            }
        }

        // Text fallback
        if (!results.buy_btn || !results.sell_btn) {
            document.querySelectorAll('button, [role="button"], div').forEach(el => {
                const text = (el.textContent || '').trim().toUpperCase();
                const rect = el.getBoundingClientRect();
                if (rect.width < 10 || rect.height < 10 || rect.width > 500) return;
                if (!results.buy_btn && text.includes('BUY') && text.includes('MKT')) {
                    results.buy_btn = {dom_x: Math.round(rect.left+rect.width/2), dom_y: Math.round(rect.top+rect.height/2), w: Math.round(rect.width), h: Math.round(rect.height), sel: 'text'};
                }
                if (!results.sell_btn && text.includes('SELL') && text.includes('MKT')) {
                    results.sell_btn = {dom_x: Math.round(rect.left+rect.width/2), dom_y: Math.round(rect.top+rect.height/2), w: Math.round(rect.width), h: Math.round(rect.height), sel: 'text'};
                }
            });
        }
        return results;
    }""")

    # Build final output
    pyautogui_map = {}
    elements = {}
    for key, data in coords.items():
        sx = win_x + data["dom_x"]
        sy = win_y + chrome_toolbar + data["dom_y"]
        pyautogui_map[key] = [sx, sy]
        elements[key] = {
            "screen_x": sx, "screen_y": sy,
            "dom_x": data["dom_x"], "dom_y": data["dom_y"],
            "width": data["w"], "height": data["h"],
            "selector": data["sel"]
        }

    missing = [k for k in ("buy_btn", "sell_btn", "search_bar", "account_dropdown") if k not in elements]

    output = {
        "status": "ok" if not missing else "partial",
        "cleanse": cleanse_result,
        "panel": panel_result,
        "window": {"x": win_x, "y": win_y},
        "elements": elements,
        "pyautogui_click_targets": pyautogui_map,
        "missing": missing,
        "note": "Coordinates verified POST-CLEANSE. Modals are permanently blocked by injected CSS."
    }
    print("\n" + json.dumps(output, indent=2))

except Exception as e:
    print(json.dumps({"error": str(e)}))
finally:
    pw.stop()
