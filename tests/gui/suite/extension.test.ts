import * as assert from 'assert';
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
    test('lean4.automaticallyBuildDependencies is false', () => {
        const config = vscode.workspace.getConfiguration('lean4');
        assert.strictEqual(config.get('automaticallyBuildDependencies'), false);
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
