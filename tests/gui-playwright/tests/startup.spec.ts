/**
 * Tier 6: First-launch experience smoke tests.
 *
 * Verifies that when a student launches the bundle for the first time:
 *   - No Welcome tab is shown (workbench.startupEditor = none)
 *   - No notification popups appear (git parent repo, trust, etc.)
 *
 * Uses its own VSCodium instance with NO fixture files injected, so
 * the startup behaviour matches the real student experience.
 */
import { test, expect } from '@playwright/test';
import { launchVSCodium, closeVSCodium, LaunchResult } from '../helpers/launch';
import * as fs from 'fs';

let result: LaunchResult;

test.beforeAll(async () => {
    fs.mkdirSync('test-results', { recursive: true });
    // Launch with NO fixtures — we want to see exactly what a student sees.
    result = await launchVSCodium({ fixtures: [] });
});

test.afterAll(async () => {
    if (result) {
        await closeVSCodium(result);
    }
});

test('no Welcome tab on first launch', async () => {
    const page = result.page;
    await page.waitForSelector('.monaco-workbench', { timeout: 30_000 });

    // Give VS Code a moment to settle and open any startup tabs.
    await page.waitForTimeout(5_000);

    await page.screenshot({ path: 'test-results/startup-no-welcome.png' });

    // Check that no tab with "Welcome" or "Get Started" text is visible.
    const welcomeTab = page.locator('.tab', { hasText: /Welcome|Get Started/i });
    const count = await welcomeTab.count();
    expect(count, 'Welcome tab should not appear on startup').toBe(0);
});

test('no notification popups on startup', async () => {
    const page = result.page;
    await page.waitForSelector('.monaco-workbench', { timeout: 30_000 });

    // Wait for notifications to potentially appear.
    await page.waitForTimeout(10_000);

    await page.screenshot({ path: 'test-results/startup-no-notifications.png' });

    // Check that no notification toasts are visible.
    const toasts = page.locator('.notifications-toasts .notification-toast');
    const count = await toasts.count();

    if (count > 0) {
        // Log which notifications appeared (for debugging CI failures).
        for (let i = 0; i < count; i++) {
            const text = await toasts.nth(i).innerText().catch(() => '<unreadable>');
            console.log(`  Unexpected notification: ${text}`);
        }
    }

    expect(count, 'No notification popups should appear on startup').toBe(0);
});
