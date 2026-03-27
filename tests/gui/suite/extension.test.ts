import * as assert from 'assert';
import * as path from 'path';
import * as vscode from 'vscode';

suite('Extension Activation', () => {
    test('lean4 extension is installed and can be found', () => {
        const ext = vscode.extensions.getExtension('leanprover.lean4');
        assert.notStrictEqual(ext, undefined, 'lean4 extension not found');
    });

    test('lean4 extension activates successfully', async () => {
        const ext = vscode.extensions.getExtension('leanprover.lean4')!;
        await ext.activate();
        assert.strictEqual(ext.isActive, true, 'lean4 extension did not activate');
    });
});

suite('Settings', () => {
    test('extensions.autoUpdate is false', () => {
        const config = vscode.workspace.getConfiguration('extensions');
        assert.strictEqual(config.get('autoUpdate'), false);
    });

    test('update.mode is none', () => {
        const config = vscode.workspace.getConfiguration('update');
        assert.strictEqual(config.get('mode'), 'none');
    });

    test('telemetry.telemetryLevel is off', () => {
        const config = vscode.workspace.getConfiguration('telemetry');
        assert.strictEqual(config.get('telemetryLevel'), 'off');
    });
});

suite('Workspace', () => {
    test('workspace folder is the project directory', () => {
        const folders = vscode.workspace.workspaceFolders;
        assert.ok(folders && folders.length > 0, 'No workspace folders open');

        const bundleRoot = process.env.BUNDLE_ROOT;
        assert.ok(bundleRoot, 'BUNDLE_ROOT not set');

        const expected = path.resolve(bundleRoot, 'project');
        const actual = path.resolve(folders[0].uri.fsPath);

        // Case-insensitive comparison for Windows
        assert.strictEqual(
            actual.toLowerCase(),
            expected.toLowerCase(),
            `Workspace folder is ${actual}, expected ${expected}`
        );
    });
});
