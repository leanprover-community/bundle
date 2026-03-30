/**
 * Tier 6: Lean 4 bundle GUI smoke tests.
 *
 * Verifies that the infoview webview renders proof goals and that
 * typing in the editor produces visible diagnostic feedback.
 *
 * Uses a single VSCodium instance shared across all tests.
 */
import { test, expect } from '@playwright/test';
import { launchVSCodium, closeVSCodium, LaunchResult } from '../helpers/launch';
import { findInfoviewFrame, waitForInfoviewText } from '../helpers/frames';
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

test('infoview renders goal state for a proof with sorry', async () => {
    const page = result.page;

    await page.waitForSelector('.monaco-workbench', { timeout: 30_000 });

    // Open fixture_goals.lean from the explorer
    const fileItem = page.getByRole('treeitem', { name: 'fixture_goals.lean' });
    await fileItem.waitFor({ timeout: 10_000 });
    await fileItem.dblclick();

    await page.waitForSelector('.monaco-editor .view-lines', { timeout: 15_000 });

    // Wait for lean4 extension
    const langIndicator = page.getByRole('button', { name: 'lean4' });
    await langIndicator.waitFor({ timeout: 60_000 });

    // Find the lean4 infoview specifically (identified by extension ID + React root)
    const infoviewFrame = await findInfoviewFrame(result.browser, 120_000);

    // Place cursor on sorry line to trigger goal display
    const mod = process.platform === 'darwin' ? 'Meta' : 'Control';
    await page.keyboard.press(`${mod}+KeyG`);
    await page.waitForSelector('.quick-input-widget', { timeout: 5_000 });
    await page.keyboard.type('4');
    await page.keyboard.press('Enter');

    // Wait for the turnstile symbol — proves the infoview rendered a goal state
    await waitForInfoviewText(infoviewFrame, '⊢', 60_000);

    await page.screenshot({ path: 'test-results/infoview-goals.png' });

    // Assert fixture-specific goal content:
    // The fixture is: example (a b : Nat) : a + b = b + a := by sorry
    // The infoview should show the conclusion from this specific proof.
    const text = await infoviewFrame.evaluate(() =>
        document.body?.innerText || ''
    );
    expect(text).toContain('a + b = b + a');
});

test('typing an error produces visible diagnostic feedback', async () => {
    const page = result.page;

    // Open fixture_edit.lean
    const fileItem = page.getByRole('treeitem', { name: 'fixture_edit.lean' });
    await fileItem.waitFor({ timeout: 10_000 });
    await fileItem.dblclick();

    await page.waitForSelector('.monaco-editor .view-lines', { timeout: 15_000 });

    // Move to end and type a deliberate error
    const mod = process.platform === 'darwin' ? 'Meta' : 'Control';
    await page.keyboard.press(`${mod}+End`);
    await page.keyboard.press('Enter');
    await page.keyboard.type('#check Nat.nonexistent_name', { delay: 50 });

    // Wait for the error/warning status bar item to show a non-zero error count.
    // VS Code's "No Problems" button changes to show error/warning counts.
    // We check for this specific status item, not arbitrary digits in the status bar.
    const deadline = Date.now() + 120_000;
    let errorCount = '';
    while (Date.now() < deadline) {
        // The Problems status item contains error/warning counts with codicon markers.
        // When there are errors, the accessible name changes from "No Problems" to
        // something like "2 Errors, 0 Warnings" or the text content includes digits
        // next to the error codicon.
        const problemsText = await page.evaluate(() => {
            // Find the status bar item that shows problems/errors
            // It contains codicon-error and codicon-warning icons
            const items = document.querySelectorAll('[id*="status.problems"], [aria-label*="Error"], [aria-label*="Problem"]');
            if (items.length > 0) {
                return Array.from(items).map(e => e.textContent?.trim()).join(' ');
            }
            // Fallback: find the element with error codicon
            const errorIcon = document.querySelector('.codicon-error');
            if (errorIcon?.parentElement) {
                return errorIcon.parentElement.textContent?.trim() || '';
            }
            return '';
        });

        // Check if we found a non-zero error count
        const match = problemsText.match(/(\d+)/);
        if (match && parseInt(match[1]) > 0) {
            errorCount = problemsText;
            break;
        }
        await page.waitForTimeout(2000);
    }

    await page.screenshot({ path: 'test-results/interaction-error.png' });
    expect(errorCount).not.toBe('');
});

// Pinned to MDD154 commit 1f32d8e.
// f01_egalites.lean is a student exercise file from the project.
// Update this if the project repo or CI default changes.
test('project file activates infoview', async () => {
    const page = result.page;

    // Open f01_egalites.lean via Quick Open
    const mod = process.platform === 'darwin' ? 'Meta' : 'Control';
    await page.keyboard.press(`${mod}+KeyP`);
    await page.waitForSelector('.quick-input-widget', { timeout: 5_000 });
    await page.keyboard.type('f01_egalites', { delay: 30 });
    await page.waitForTimeout(1500);
    await page.keyboard.press('Enter');

    await page.waitForSelector('.monaco-editor .view-lines', { timeout: 15_000 });

    // The infoview should already be open from previous tests.
    // Wait for it to show content for the project file (either goals or messages).
    // The infoview text includes the filename when it processes a file.
    const infoviewFrame = await findInfoviewFrame(result.browser, 30_000);
    await waitForInfoviewText(infoviewFrame, 'f01_egalites', 120_000);

    await page.screenshot({ path: 'test-results/project-exercise.png' });
});
