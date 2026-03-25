import * as path from 'path';
import * as fs from 'fs';
import { runTests } from '@vscode/test-electron';

function buildLeanPath(bundleRoot: string): string {
    const sep = process.platform === 'win32' ? ';' : ':';
    const parts: string[] = [
        path.join(bundleRoot, 'lean', 'lib', 'lean'),
        path.join(bundleRoot, 'project', '.lake', 'build', 'lib', 'lean'),
    ];

    // Add each package's build dir, matching start_lean.cmd logic
    const packagesDir = path.join(bundleRoot, 'project', '.lake', 'packages');
    if (fs.existsSync(packagesDir)) {
        for (const pkg of fs.readdirSync(packagesDir)) {
            const buildDir = path.join(packagesDir, pkg, '.lake', 'build', 'lib', 'lean');
            if (fs.existsSync(buildDir)) {
                parts.push(buildDir);
            }
        }
    }

    return parts.join(sep);
}

async function main() {
    const bundleRoot = process.env.BUNDLE_ROOT;
    if (!bundleRoot) {
        console.error('BUNDLE_ROOT environment variable not set.');
        console.error('Usage: BUNDLE_ROOT=/path/to/bundle node out/run-tests.js');
        process.exit(1);
    }

    // Determine VSCodium executable path
    // On Windows, the .exe is in the root. On Linux, use the bin/ wrapper script
    // which sets up the correct Electron environment.
    const isWindows = process.platform === 'win32';
    const vscodiumExe = isWindows
        ? path.join(bundleRoot, 'vscodium', 'VSCodium.exe')
        : path.join(bundleRoot, 'vscodium', 'bin', 'codium');

    if (!fs.existsSync(vscodiumExe)) {
        console.error(`VSCodium not found at: ${vscodiumExe}`);
        process.exit(1);
    }

    const projectPath = path.join(bundleRoot, 'project');

    // Copy fixture.lean into the project directory so the language server
    // can find it in the workspace
    const fixtureSrc = path.resolve(__dirname, '..', 'fixture.lean');
    const fixtureDst = path.join(projectPath, 'fixture.lean');
    fs.copyFileSync(fixtureSrc, fixtureDst);

    // Set environment variables matching start_lean.cmd / start_lean.sh
    const leanBin = path.join(bundleRoot, 'lean', 'bin');
    const pathSep = isWindows ? ';' : ':';
    process.env.PATH = leanBin + pathSep + (process.env.PATH || '');
    process.env.ELAN_HOME = path.join(bundleRoot, 'lean');
    process.env.LEAN_PATH = buildLeanPath(bundleRoot);
    process.env.BUNDLE_ROOT = bundleRoot;

    console.log('=== Tier 5: VSCodium integration smoke test ===');
    console.log(`  VSCodium: ${vscodiumExe}`);
    console.log(`  Project:  ${projectPath}`);
    console.log(`  ELAN_HOME: ${process.env.ELAN_HOME}`);
    console.log(`  LEAN_PATH entries: ${process.env.LEAN_PATH.split(pathSep).length}`);

    try {
        await runTests({
            vscodeExecutablePath: vscodiumExe,
            extensionDevelopmentPath: path.resolve(__dirname, '..', 'stub-extension'),
            extensionTestsPath: path.resolve(__dirname, 'suite', 'index'),
            launchArgs: [
                projectPath,
                '--extensions-dir', path.join(bundleRoot, 'vscodium', 'data', 'extensions'),
                '--user-data-dir', path.join(bundleRoot, 'vscodium', 'data', 'user-data'),
                '--disable-gpu',
                '--no-sandbox',
                '--disable-gpu-sandbox',
                '--skip-welcome',
                '--disable-updates',
                '--new-window',
            ],
        });
        console.log('=== All Tier 5 tests passed ===');
    } catch (err) {
        console.error('=== Tier 5 tests FAILED ===');
        console.error(err);
        process.exit(1);
    } finally {
        // Clean up the fixture file we copied
        try { fs.unlinkSync(fixtureDst); } catch { /* ignore */ }
    }
}

main();
