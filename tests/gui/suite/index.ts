import * as path from 'path';
import * as fs from 'fs';
import Mocha from 'mocha';

export function run(): Promise<void> {
    const mocha = new Mocha({
        ui: 'tdd',
        color: true,
        timeout: 120_000,  // 120s per test -- language server startup can be slow
    });

    const testsRoot = path.resolve(__dirname);

    // Find all *.test.js files in the suite directory
    const testFiles = fs.readdirSync(testsRoot).filter((f) => f.endsWith('.test.js'));
    for (const f of testFiles) {
        mocha.addFile(path.resolve(testsRoot, f));
    }

    return new Promise((resolve, reject) => {
        mocha.run((failures) => {
            if (failures > 0) {
                reject(new Error(`${failures} test(s) failed.`));
            } else {
                resolve();
            }
        });
    });
}
