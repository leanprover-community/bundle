import * as assert from 'assert';
import * as path from 'path';

suite('Environment Variables', () => {
    test('PATH contains lean/bin', () => {
        const bundleRoot = process.env.BUNDLE_ROOT;
        assert.ok(bundleRoot, 'BUNDLE_ROOT not set');
        const leanBin = path.join(bundleRoot, 'lean', 'bin');
        const pathVar = process.env.PATH || '';
        assert.ok(
            pathVar.includes(leanBin),
            `PATH does not contain ${leanBin}\nPATH: ${pathVar}`
        );
    });

    test('ELAN_HOME is set correctly', () => {
        const bundleRoot = process.env.BUNDLE_ROOT;
        assert.ok(bundleRoot, 'BUNDLE_ROOT not set');
        const expected = path.join(bundleRoot, 'lean');
        assert.strictEqual(
            process.env.ELAN_HOME,
            expected,
            `ELAN_HOME is ${process.env.ELAN_HOME}, expected ${expected}`
        );
    });

    test('LEAN_PATH contains expected directories', () => {
        const bundleRoot = process.env.BUNDLE_ROOT;
        assert.ok(bundleRoot, 'BUNDLE_ROOT not set');
        const leanPath = process.env.LEAN_PATH || '';
        assert.ok(leanPath.length > 0, 'LEAN_PATH is empty');

        // Should contain the toolchain lib
        const toolchainLib = path.join(bundleRoot, 'lean', 'lib', 'lean');
        assert.ok(
            leanPath.includes(toolchainLib),
            `LEAN_PATH does not contain toolchain lib: ${toolchainLib}`
        );

        // Should contain the project build dir
        const projectBuild = path.join(bundleRoot, 'project', '.lake', 'build', 'lib', 'lean');
        assert.ok(
            leanPath.includes(projectBuild),
            `LEAN_PATH does not contain project build dir: ${projectBuild}`
        );
    });
});
