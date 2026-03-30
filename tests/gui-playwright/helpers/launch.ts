import * as path from 'path';
import * as fs from 'fs';
import * as os from 'os';
import * as childProcess from 'child_process';
import { chromium, Browser, Page } from 'playwright';

function buildLeanPath(bundleRoot: string): string {
    const sep = process.platform === 'win32' ? ';' : ':';
    const parts: string[] = [
        path.join(bundleRoot, 'lean', 'lib', 'lean'),
        path.join(bundleRoot, 'project', '.lake', 'build', 'lib', 'lean'),
    ];

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

function getVSCodiumLauncher(bundleRoot: string): string {
    const isWindows = process.platform === 'win32';
    const isMac = process.platform === 'darwin';
    return isWindows
        ? path.join(bundleRoot, 'vscodium', 'bin', 'codium.cmd')
        : isMac
            ? path.join(bundleRoot, 'vscodium', 'bin', 'codium')
            : path.join(bundleRoot, 'vscodium', 'bin', 'codium');
}

export interface LaunchResult {
    browser: Browser;
    page: Page;
    process: childProcess.ChildProcess;
    workspacePath: string;
    userDataDir: string;
    copiedFixtures: string[];
}

async function waitForCDP(port: number, timeoutMs: number): Promise<void> {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        try {
            const resp = await fetch(`http://127.0.0.1:${port}/json/version`);
            if (resp.ok) return;
        } catch { /* not ready */ }
        await new Promise(r => setTimeout(r, 500));
    }
    throw new Error(`CDP not available on port ${port} after ${timeoutMs}ms`);
}

function randomPort(): number {
    return 9200 + Math.floor(Math.random() * 800);
}

export async function launchVSCodium(options?: {
    fixtures?: string[];
    openFile?: string;
}): Promise<LaunchResult> {
    const bundleRoot = process.env.BUNDLE_ROOT;
    if (!bundleRoot) {
        throw new Error('BUNDLE_ROOT environment variable not set');
    }

    const launcher = getVSCodiumLauncher(bundleRoot);
    if (!fs.existsSync(launcher)) {
        throw new Error(`VSCodium launcher not found at: ${launcher}`);
    }

    // Use the bundle's project directory as the workspace.
    // The lean4 extension needs .lake/packages present for the LS to work.
    const workspacePath = path.join(bundleRoot, 'project');

    // Copy fixture files into the project dir (cleaned up after test).
    const fixtureDir = path.resolve(__dirname, '..', 'fixtures');
    const defaultFixtures = [
        path.join(fixtureDir, 'fixture_goals.lean'),
        path.join(fixtureDir, 'fixture_edit.lean'),
    ];
    const fixturesToUse = options?.fixtures ?? defaultFixtures;
    const copiedFixtures: string[] = [];
    for (const fixture of fixturesToUse) {
        const dest = path.join(workspacePath, path.basename(fixture));
        fs.copyFileSync(fixture, dest);
        copiedFixtures.push(dest);
    }

    // Kill any stale VSCodium processes to prevent "Sending env to running instance"
    try { childProcess.execSync('pkill -9 -f codium 2>/dev/null || true'); } catch { /* */ }

    // Use the bundle's own user-data-dir (has extension registry).
    // Clear user-state to prevent stale window restoration.
    const userDataDir = path.join(bundleRoot, 'vscodium', 'data', 'user-data');
    const userStateDir = path.join(bundleRoot, 'vscodium', 'data', 'user-state');
    try { fs.rmSync(userStateDir, { recursive: true, force: true }); } catch { /* ignore */ }

    const leanBin = path.join(bundleRoot, 'lean', 'bin');
    const gitCmd = path.join(bundleRoot, 'git', 'cmd');
    const pathSep = process.platform === 'win32' ? ';' : ':';
    const extraPaths = fs.existsSync(gitCmd)
        ? leanBin + pathSep + gitCmd
        : leanBin;

    const extensionsDir = path.join(bundleRoot, 'vscodium', 'data', 'extensions');

    const env: Record<string, string> = {
        ...process.env as Record<string, string>,
        PATH: extraPaths + pathSep + (process.env.PATH || ''),
        ELAN_HOME: path.join(bundleRoot, 'lean'),
        LEAN_PATH: buildLeanPath(bundleRoot),
        BUNDLE_ROOT: bundleRoot,
        DONT_PROMPT_WSL_INSTALL: '1',
    };

    // Patch settings.json to disable workspace trust and window restore.
    // Save the original so we can restore it in closeVSCodium.
    const settingsDir = path.join(userDataDir, 'User');
    fs.mkdirSync(settingsDir, { recursive: true });
    const settingsFile = path.join(settingsDir, 'settings.json');
    const settingsBackup = settingsFile + '.tier6-backup';
    let settings: Record<string, unknown> = {};
    if (fs.existsSync(settingsFile)) {
        fs.copyFileSync(settingsFile, settingsBackup);
        try { settings = JSON.parse(fs.readFileSync(settingsFile, 'utf-8')); } catch { /* */ }
    }
    settings['security.workspace.trust.enabled'] = false;
    settings['window.restoreWindows'] = 'none';
    fs.writeFileSync(settingsFile, JSON.stringify(settings, null, 2));

    const cdpPort = randomPort();

    console.log('=== Tier 6: Playwright UI automation test ===');
    console.log(`  Launcher: ${launcher}`);
    console.log(`  Workspace: ${workspacePath}`);
    console.log(`  User data: ${userDataDir}`);
    console.log(`  LEAN_PATH entries: ${env.LEAN_PATH.split(pathSep).length}`);

    const args = [
        workspacePath,
        `--user-data-dir=${userDataDir}`,
        `--extensions-dir=${extensionsDir}`,
        `--remote-debugging-port=${cdpPort}`,
        '--disable-gpu',
        '--no-sandbox',
        '--disable-gpu-sandbox',
        '--skip-welcome',
        '--disable-updates',
        '--new-window',
    ];

    // To open a specific file within the workspace, use --goto which opens
    // the file in the same window as the workspace folder.
    if (options?.openFile) {
        args.push('--goto', path.join(workspacePath, options.openFile));
    }

    const proc = childProcess.spawn(launcher, args, {
        env,
        stdio: 'pipe',
        detached: false,
    });

    proc.stdout?.on('data', (data: Buffer) => {
        const msg = data.toString().trim();
        if (msg) console.log(`  [vscodium stdout] ${msg}`);
    });
    proc.stderr?.on('data', (data: Buffer) => {
        const msg = data.toString().trim();
        if (msg && !msg.includes('which: no codium')) {
            if (msg.includes('extension') || msg.includes('Extension') ||
                msg.includes('lean') || msg.includes('activat') ||
                msg.includes('ERR')) {
                console.log(`  [vscodium] ${msg}`);
            }
        }
    });

    console.log('  Waiting for CDP...');
    await waitForCDP(cdpPort, 30_000);
    console.log('  CDP available');

    const browser = await chromium.connectOverCDP(`http://127.0.0.1:${cdpPort}`);

    // Wait a moment for pages to initialize, then find the right one
    await new Promise(r => setTimeout(r, 3000));
    let page: Page | undefined;
    for (const ctx of browser.contexts()) {
        for (const p of ctx.pages()) {
            const title = await p.title().catch(() => '');
            console.log(`  Found page: "${title}"`);
            if (title.includes('project')) {
                page = p;
            }
        }
    }
    // Fall back to last page if none matched
    if (!page) {
        const allPages = browser.contexts().flatMap(c => c.pages());
        page = allPages[allPages.length - 1];
    }
    if (!page) {
        throw new Error('No page found after CDP connection');
    }
    console.log(`  Using page: "${await page.title()}"`);

    return { browser, page, process: proc, workspacePath, userDataDir, copiedFixtures };
}

export async function closeVSCodium(result: LaunchResult) {
    try { await result.browser.close(); } catch { /* ignore */ }
    try {
        result.process.kill();
        if (result.process.pid) {
            try { process.kill(-result.process.pid, 'SIGTERM'); } catch { /* ignore */ }
        }
    } catch { /* ignore */ }
    for (const f of result.copiedFixtures) {
        try { fs.unlinkSync(f); } catch { /* ignore */ }
    }
    // Restore original settings.json
    const settingsFile = path.join(result.userDataDir, 'User', 'settings.json');
    const settingsBackup = settingsFile + '.tier6-backup';
    if (fs.existsSync(settingsBackup)) {
        try { fs.renameSync(settingsBackup, settingsFile); } catch { /* ignore */ }
    }
}
