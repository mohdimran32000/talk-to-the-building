import { defineConfig } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

// Load test credentials (e.g. TEST_USER_ADMIN_PASSWORD) from backend/.env so the
// e2e suite uses the real dev credentials without publishing them in the repo.
for (const candidate of ['../backend/.env', 'backend/.env']) {
  const envFile = path.resolve(process.cwd(), candidate)
  if (!fs.existsSync(envFile)) continue
  for (const line of fs.readFileSync(envFile, 'utf-8').split(/\r?\n/)) {
    const m = line.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/)
    if (m && process.env[m[1]] === undefined) process.env[m[1]] = m[2]
  }
  break
}

export default defineConfig({
  testDir: './e2e',
  timeout: 30000,
  expect: { timeout: 10000 },
  fullyParallel: false,
  retries: 0,
  use: {
    baseURL: 'http://localhost:5173',
    headless: false,
    screenshot: 'only-on-failure',
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { browserName: 'chromium' },
    },
  ],
})
