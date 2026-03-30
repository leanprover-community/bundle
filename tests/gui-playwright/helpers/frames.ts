import { Frame, Browser } from 'playwright';

/**
 * Find the Lean 4 infoview webview frame across all pages/contexts.
 *
 * Identifies the infoview specifically by checking that the frame's URL
 * contains the lean4 extension ID (extensionId=leanprover.lean4) AND that
 * the frame contains the React root element used by the infoview app.
 */
export async function findInfoviewFrame(
    browser: Browser,
    timeoutMs: number = 180_000,
): Promise<Frame> {
    const deadline = Date.now() + timeoutMs;

    while (Date.now() < deadline) {
        for (const ctx of browser.contexts()) {
            for (const page of ctx.pages()) {
                for (const frame of page.frames()) {
                    const url = frame.url();
                    // The lean4 infoview webview URL contains the extension ID
                    if (!url.includes('extensionId=leanprover.lean4')) continue;

                    try {
                        // Check this frame and its children for the infoview React root
                        for (const f of [frame, ...frame.childFrames()]) {
                            const hasInfoview = await f.evaluate(() => {
                                const root = document.getElementById('react_root');
                                return root !== null && root.innerText.length > 0;
                            }).catch(() => false);

                            if (hasInfoview) return f;
                        }
                    } catch {
                        // Frame may have been detached
                    }
                }
            }
        }

        await new Promise(r => setTimeout(r, 2000));
    }

    throw new Error(`Lean 4 infoview frame not found within ${timeoutMs}ms`);
}

/**
 * Wait for specific text content to appear in the infoview frame.
 */
export async function waitForInfoviewText(
    frame: Frame,
    text: string,
    timeoutMs: number = 60_000,
): Promise<void> {
    const deadline = Date.now() + timeoutMs;

    while (Date.now() < deadline) {
        try {
            const content = await frame.evaluate(() =>
                document.body?.innerText || ''
            );
            if (content.includes(text)) return;
        } catch {
            // Frame may have been detached/recreated
        }
        await new Promise(r => setTimeout(r, 1000));
    }

    throw new Error(`Text "${text}" not found in infoview within ${timeoutMs}ms`);
}
