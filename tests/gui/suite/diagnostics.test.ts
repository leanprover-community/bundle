import * as assert from 'assert';
import * as path from 'path';
import * as vscode from 'vscode';

suite('Language Server & Diagnostics', () => {
    let fixtureUri: vscode.Uri;

    suiteSetup(() => {
        const bundleRoot = process.env.BUNDLE_ROOT;
        assert.ok(bundleRoot, 'BUNDLE_ROOT not set');
        fixtureUri = vscode.Uri.file(path.join(bundleRoot, 'project', 'fixture.lean'));
    });

    test('language server starts and produces diagnostics', async function () {
        // Open the fixture file
        const doc = await vscode.workspace.openTextDocument(fixtureUri);
        await vscode.window.showTextDocument(doc);

        // Wait for diagnostics to arrive
        const diagnostics = await waitForDiagnostics(fixtureUri, 120_000);
        assert.ok(diagnostics !== null, 'No diagnostics received within timeout');
    });

    test('no error diagnostics in fixture file', async function () {
        // Diagnostics should already be available from the previous test.
        // Wait a moment for any additional diagnostic updates to settle.
        const diagnostics = await waitForDiagnostics(fixtureUri, 30_000);
        assert.ok(diagnostics !== null, 'No diagnostics available');

        const errors = diagnostics!.filter(
            (d) => d.severity === vscode.DiagnosticSeverity.Error
        );
        assert.strictEqual(
            errors.length,
            0,
            `Expected no errors but got ${errors.length}: ${errors.map((e) => e.message).join('; ')}`
        );
    });
});

/**
 * Wait for diagnostics to appear for the given URI.
 * Returns the diagnostics array, or null if timeout is reached.
 */
function waitForDiagnostics(
    uri: vscode.Uri,
    timeoutMs: number
): Promise<vscode.Diagnostic[] | null> {
    return new Promise((resolve) => {
        // Check if diagnostics are already present
        const existing = vscode.languages.getDiagnostics(uri);
        if (existing.length > 0) {
            resolve(existing);
            return;
        }

        const timer = setTimeout(() => {
            disposable.dispose();
            // One final check
            const final = vscode.languages.getDiagnostics(uri);
            resolve(final.length > 0 ? final : null);
        }, timeoutMs);

        const disposable = vscode.languages.onDidChangeDiagnostics((e) => {
            if (e.uris.some((u) => u.toString() === uri.toString())) {
                const diags = vscode.languages.getDiagnostics(uri);
                if (diags.length > 0) {
                    clearTimeout(timer);
                    disposable.dispose();
                    resolve(diags);
                }
            }
        });
    });
}
