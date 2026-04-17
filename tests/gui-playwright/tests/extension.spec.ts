/**
 * Tier 6: Lean 4 extension installation smoke test.
 *
 * Verifies that the lean4 extension is actually loaded by VSCodium
 * using portable-mode auto-discovery (no --extensions-dir flag).
 *
 * These tests run BEFORE infoview.spec.ts (alphabetical order) and
 * give clear, fast error messages like "lean4 extension not installed"
 * instead of letting downstream tests silently time out after 120s.
 */
import { test, expect } from '@playwright/test';
import { launchVSCodium, closeVSCodium, LaunchResult } from '../helpers/launch';
import * as fs from 'fs';

let result: LaunchResult;

test.beforeAll(async () => {
    fs.mkdirSync('test-results', { recursive: true });
    result = await launchVSCodium();
});

test.afterAll(async () => {
    if (result) {
        await closeVSCodium(result);
    }
});

test('lean4 extension is installed and activated', async () => {
    const page = result.page;
    await page.waitForSelector('.monaco-workbench', { timeout: 30_000 });

    // Open the Extensions sidebar via command palette.
    const mod = process.platform === 'darwin' ? 'Meta' : 'Control';
    await page.keyboard.press(`${mod}+Shift+KeyX`);

    // Wait for the Extensions view to appear.
    await page.waitForSelector('.extensions-list', { timeout: 15_000 });

    await page.screenshot({ path: 'test-results/extension-sidebar.png' });

    // Look for the lean4 extension in the installed list.
    // The extension item contains the extension ID or display name.
    const lean4Entry = page.locator('.extension-list-item', {
        hasText: /[Ll]ean/,
    });
    const count = await lean4Entry.count();
    expect(count, 'lean4 extension should appear in the Extensions sidebar').toBeGreaterThan(0);
});

test('lean file is recognized as lean4 language', async () => {
    const page = result.page;
    await page.waitForSelector('.monaco-workbench', { timeout: 30_000 });

    // Open fixture_goals.lean from the explorer.
    const fileItem = page.getByRole('treeitem', { name: 'fixture_goals.lean' });
    await fileItem.waitFor({ timeout: 10_000 });
    await fileItem.dblclick();

    await page.waitForSelector('.monaco-editor .view-lines', { timeout: 15_000 });

    // The lean4 extension registers the "lean4" language. If the extension
    // is not installed, .lean files fall back to plain text.
    // Check the language mode indicator in the status bar.
    const langIndicator = page.getByRole('button', { name: /lean4/i });
    await langIndicator.waitFor({ timeout: 30_000 }).catch(() => {});

    await page.screenshot({ path: 'test-results/extension-language-mode.png' });

    // Explicitly assert — don't just let waitFor timeout with a cryptic message.
    const langVisible = await langIndicator.isVisible().catch(() => false);
    expect(langVisible,
        'Language mode should be "lean4" — if this fails, the lean4 extension ' +
        'is not installed or not activating. Check that VSCodium portable mode ' +
        'auto-discovers data/extensions/ without --extensions-dir.',
    ).toBe(true);
});

test('Lean 4 Infoview panel opens', async () => {
    const page = result.page;

    // The infoview should auto-open when a .lean file is active and the
    // extension is loaded. Look for any webview frame whose URL contains
    // the lean4 extension ID.
    const deadline = Date.now() + 60_000;
    let found = false;

    while (Date.now() < deadline) {
        for (const ctx of result.browser.contexts()) {
            for (const p of ctx.pages()) {
                for (const frame of p.frames()) {
                    if (frame.url().includes('extensionId=leanprover.lean4')) {
                        found = true;
                        break;
                    }
                }
                if (found) break;
            }
            if (found) break;
        }
        if (found) break;
        await page.waitForTimeout(2000);
    }

    await page.screenshot({ path: 'test-results/extension-infoview-panel.png' });

    expect(found,
        'Lean 4 Infoview webview should open when a .lean file is active. ' +
        'No frame with extensionId=leanprover.lean4 was found — the extension ' +
        'is either not installed or failed to activate.',
    ).toBe(true);
});
