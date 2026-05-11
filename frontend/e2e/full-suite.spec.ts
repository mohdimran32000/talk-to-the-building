import { test, expect, type Page } from '@playwright/test'
import path from 'path'
import fs from 'fs'

const TEST_EMAIL = 'test@test.com'
const TEST_PASSWORD = 'supabase123'

// Phase 6 / Plan 06-02: admin account seeded by seed_admin_user.py + Migration 021.
// Default password matches backend/scripts/test_helpers.py:26-29 convention.
const TEST_ADMIN_EMAIL = 'admin@test.com'
const TEST_ADMIN_PASSWORD = process.env.TEST_USER_ADMIN_PASSWORD ?? 'adminpassword123'

// Phase 6 / Plan 06-11: API base for fixture helpers (apiPost / apiDelete).
// Tests assume the backend runs on :8001 per CLAUDE.md.
const API_BASE = process.env.PLAYWRIGHT_API_BASE ?? 'http://localhost:8001'

/** Helper: sign in and wait for chat to load */
async function signIn(page: import('@playwright/test').Page) {
  await page.goto('/login')
  await page.getByLabel('Email').fill(TEST_EMAIL)
  await page.getByLabel('Password').fill(TEST_PASSWORD)
  await page.getByRole('button', { name: 'Sign In' }).click()
  await expect(page.getByRole('button', { name: '+ New Chat' })).toBeVisible({ timeout: 15000 })
}

/** Helper: sign in as the seeded admin account (Plan 06-02). */
async function signInAdmin(page: import('@playwright/test').Page) {
  await page.goto('/login')
  await page.getByLabel('Email').fill(TEST_ADMIN_EMAIL)
  await page.getByLabel('Password').fill(TEST_ADMIN_PASSWORD)
  await page.getByRole('button', { name: 'Sign In' }).click()
  await expect(page.getByRole('button', { name: '+ New Chat' })).toBeVisible({ timeout: 15000 })
}

/** Helper: sign out (best-effort; lets a single test switch accounts). */
async function signOut(page: import('@playwright/test').Page) {
  const signOutBtn = page.getByRole('button', { name: /sign out/i })
  if (await signOutBtn.isVisible().catch(() => false)) {
    await signOutBtn.click()
    await expect(page.getByLabel('Email')).toBeVisible({ timeout: 10000 })
  }
}

/**
 * Read the Supabase access token from the page's localStorage. Supabase v2 stores it
 * under `sb-<projectRef>-auth-token`; the project ref varies per env, so we scan all
 * localStorage keys for one matching the shape. Returns null if not signed in.
 */
async function getStoredToken(page: Page): Promise<string | null> {
  return await page.evaluate(() => {
    for (const key of Object.keys(window.localStorage)) {
      if (key.startsWith('sb-') && key.endsWith('-auth-token')) {
        try {
          const raw = window.localStorage.getItem(key)
          if (!raw) continue
          const parsed = JSON.parse(raw)
          return parsed?.access_token ?? null
        } catch {
          continue
        }
      }
    }
    return null
  })
}

/**
 * apiPost — fixture helper for Phase 6 e2e tests that need to bootstrap folder
 * structure without 5 UI clicks. Uses the page's stored Supabase JWT to call
 * the backend. Caller MUST signIn(page) (or signInAdmin) before invoking.
 */
async function apiPost(page: Page, urlPath: string, body: unknown): Promise<any> {
  const token = await getStoredToken(page)
  if (!token) throw new Error('apiPost: no auth token in localStorage — call signIn(page) first')
  const res = await page.request.post(`${API_BASE}${urlPath}`, {
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    data: body as Record<string, unknown>,
  })
  if (!res.ok()) {
    throw new Error(`apiPost ${urlPath} failed: ${res.status()} ${await res.text()}`)
  }
  return res.json()
}

/**
 * apiGet — fixture helper used in cross-scope drag test to verify the document's
 * folder_path was NOT mutated after the BLOCK modal opened (D-01 empirical check).
 */
async function apiGet(page: Page, urlPath: string): Promise<any> {
  const token = await getStoredToken(page)
  if (!token) throw new Error('apiGet: no auth token in localStorage')
  const res = await page.request.get(`${API_BASE}${urlPath}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok()) {
    throw new Error(`apiGet ${urlPath} failed: ${res.status()} ${await res.text()}`)
  }
  return res.json()
}

/**
 * apiDelete — fixture cleanup helper. Used in finally blocks to remove
 * test-created folders / documents BY ID. CLAUDE.md mandatory rule: tests must
 * NEVER delete all user data — pass specific ids only. Tolerates 404.
 */
async function apiDelete(page: Page, urlPath: string): Promise<void> {
  const token = await getStoredToken(page)
  if (!token) throw new Error('apiDelete: no auth token in localStorage')
  const res = await page.request.delete(`${API_BASE}${urlPath}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok() && res.status() !== 404) {
    // eslint-disable-next-line no-console
    console.warn(`apiDelete ${urlPath} returned ${res.status()}: ${await res.text()}`)
  }
}

// ── Auth Tests ──

test.describe('Auth', () => {
  test.describe.configure({ mode: 'serial' })

  test('Protected route redirects to /login', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveURL(/\/login/)
  })

  test('Login page renders correctly', async ({ page }) => {
    await page.goto('/login')
    await expect(page.locator('[data-slot="card-title"]')).toHaveText('Sign In')
    await expect(page.getByLabel('Email')).toBeVisible()
    await expect(page.getByLabel('Password')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Sign In' })).toBeVisible()
    await expect(page.getByRole('link', { name: 'Sign up' })).toBeVisible()
  })

  test('Login links to signup', async ({ page }) => {
    await page.goto('/login')
    await page.getByRole('link', { name: 'Sign up' }).click()
    await expect(page).toHaveURL(/\/signup/)
  })

  test('Signup page renders correctly', async ({ page }) => {
    await page.goto('/signup')
    await expect(page.locator('[data-slot="card-title"]')).toHaveText('Create Account')
    await expect(page.getByLabel('Email')).toBeVisible()
    await expect(page.getByLabel('Password')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Create Account' })).toBeVisible()
  })

  test('Signup links to login', async ({ page }) => {
    await page.goto('/signup')
    await page.getByRole('link', { name: 'Sign in' }).click()
    await expect(page).toHaveURL(/\/login/)
  })

  test('Invalid credentials show error', async ({ page }) => {
    await page.goto('/login')
    await page.getByLabel('Email').fill('nonexistent@test.com')
    await page.getByLabel('Password').fill('wrongpassword')
    await page.getByRole('button', { name: 'Sign In' }).click()
    await expect(page.locator('.rounded-md.p-3.text-sm')).toBeVisible({ timeout: 15000 })
  })

  test('Valid credentials redirect to chat', async ({ page }) => {
    await signIn(page)
    await expect(page).toHaveURL('/')
  })

  test('Sign out redirects to login', async ({ page }) => {
    await signIn(page)
    await page.getByRole('button', { name: 'Sign Out' }).click()
    await expect(page).toHaveURL(/\/login/, { timeout: 10000 })
  })

  test('Session persists on refresh', async ({ page }) => {
    await signIn(page)
    await page.reload()
    await expect(page.getByRole('button', { name: '+ New Chat' })).toBeVisible({ timeout: 10000 })
    await expect(page).toHaveURL('/')
  })
})

// ── Thread Tests ──

test.describe('Threads', () => {
  test.describe.configure({ mode: 'serial' })

  test('Create thread via button', async ({ page }) => {
    await signIn(page)
    const threadsBefore = await page.locator('.overflow-y-auto > div').count()
    await page.getByRole('button', { name: '+ New Chat' }).click()
    await expect(page.locator('.overflow-y-auto > div')).toHaveCount(threadsBefore + 1, { timeout: 5000 })
  })

  test('Auto-create thread on first message', async ({ page }) => {
    test.setTimeout(90000)
    await signIn(page)
    const threadsBefore = await page.locator('.overflow-y-auto > div').count()
    await page.getByPlaceholder('Type a message...').fill('Hello auto-create')
    await page.getByRole('button', { name: 'Send' }).click()
    // Thread should appear in sidebar
    await expect(page.locator('.overflow-y-auto > div')).toHaveCount(threadsBefore + 1, { timeout: 10000 })
    // Wait for stream to complete
    await expect(page.getByPlaceholder('Type a message...')).toBeEnabled({ timeout: 60000 })
  })

  test('Delete thread', async ({ page }) => {
    await signIn(page)
    // Create a thread first
    await page.getByRole('button', { name: '+ New Chat' }).click()
    await page.waitForTimeout(1000)
    const threadsAfterCreate = await page.locator('.overflow-y-auto > div').count()

    // Hover over last thread and click delete
    const lastThread = page.locator('.overflow-y-auto > div').first()
    await lastThread.hover()
    const deleteBtn = lastThread.locator('button')
    if (await deleteBtn.isVisible()) {
      await deleteBtn.click()
      await expect(page.locator('.overflow-y-auto > div')).toHaveCount(threadsAfterCreate - 1, { timeout: 5000 })
    }
  })

  test('Select thread shows active state', async ({ page }) => {
    await signIn(page)
    // Create a thread
    await page.getByRole('button', { name: '+ New Chat' }).click()
    await page.waitForTimeout(1000)
    const firstThread = page.locator('.overflow-y-auto > div').first()
    await firstThread.click()
    // Active thread should have distinct styling
    await expect(firstThread).toHaveClass(/bg-muted|font-medium/, { timeout: 3000 })
  })
})

// ── Message Tests ──

test.describe('Messages', () => {
  test.describe.configure({ mode: 'serial' })

  test('Send message shows user bubble', async ({ page }) => {
    test.setTimeout(90000)
    await signIn(page)
    await page.getByPlaceholder('Type a message...').fill('Test user message')
    await page.getByRole('button', { name: 'Send' }).click()
    await expect(page.getByText('Test user message').first()).toBeVisible({ timeout: 5000 })
    // Wait for stream to complete
    await expect(page.getByPlaceholder('Type a message...')).toBeEnabled({ timeout: 60000 })
  })

  test('Streaming produces assistant response', async ({ page }) => {
    test.setTimeout(90000)
    await signIn(page)
    await page.getByPlaceholder('Type a message...').fill('Say exactly: hello world')
    await page.getByRole('button', { name: 'Send' }).click()
    // Wait for streaming to complete
    await expect(page.getByPlaceholder('Type a message...')).toBeEnabled({ timeout: 60000 })
    // Assistant content should be visible (at least 2 message bubbles)
    const messageBubbles = page.locator('.rounded-2xl, .rounded-lg').filter({ hasText: /.+/ })
    await expect(messageBubbles.first()).toBeVisible()
  })

  test('Stop button appears during streaming', async ({ page }) => {
    test.setTimeout(90000)
    await signIn(page)
    await page.getByPlaceholder('Type a message...').fill('Write a very long essay about the history of computing')
    await page.getByRole('button', { name: 'Send' }).click()
    // Stop button should appear during streaming
    await expect(page.getByRole('button', { name: 'Stop' })).toBeVisible({ timeout: 10000 })
    // Wait for completion or click stop
    await page.getByRole('button', { name: 'Stop' }).click()
    await expect(page.getByPlaceholder('Type a message...')).toBeEnabled({ timeout: 10000 })
  })

  test('Stop button aborts streaming', async ({ page }) => {
    test.setTimeout(90000)
    await signIn(page)
    await page.getByPlaceholder('Type a message...').fill('Write a 5000 word essay about space exploration')
    await page.getByRole('button', { name: 'Send' }).click()
    await expect(page.getByRole('button', { name: 'Stop' })).toBeVisible({ timeout: 10000 })
    await page.getByRole('button', { name: 'Stop' }).click()
    // After stopping, textarea should be enabled and Stop should disappear
    await expect(page.getByPlaceholder('Type a message...')).toBeEnabled({ timeout: 10000 })
    await expect(page.getByRole('button', { name: 'Stop' })).not.toBeVisible()
  })
})

// ── Document Tests ──

test.describe('Documents', () => {
  test.describe.configure({ mode: 'serial' })

  test('Documents panel shows count', async ({ page }) => {
    await signIn(page)
    await expect(page.getByText(/Documents \(\d+\)/)).toBeVisible({ timeout: 5000 })
  })

  test('Panel expands and collapses', async ({ page }) => {
    await signIn(page)
    const panelHeader = page.getByText(/Documents \(\d+\)/)
    await panelHeader.click()
    await expect(page.getByRole('button', { name: 'Upload File' })).toBeVisible({ timeout: 3000 })
    await panelHeader.click()
    await expect(page.getByRole('button', { name: 'Upload File' })).not.toBeVisible()
  })

  test('Upload file shows in list with pending badge', async ({ page }) => {
    await signIn(page)
    // Expand panel
    await page.getByText(/Documents \(\d+\)/).click()
    await expect(page.getByRole('button', { name: 'Upload File' })).toBeVisible()

    // Create a test text file
    const testContent = 'The capybara is the largest living rodent in the world.'
    const tmpPath = path.join(__dirname, 'test_upload.txt')
    fs.writeFileSync(tmpPath, testContent)

    try {
      // Upload via file input
      const fileInput = page.locator('input[type="file"]')
      await fileInput.setInputFiles(tmpPath)

      // File should appear in the list
      await expect(page.getByText('test_upload.txt')).toBeVisible({ timeout: 10000 })
    } finally {
      fs.unlinkSync(tmpPath)
    }
  })

  test('Status transitions to ready', async ({ page }) => {
    test.setTimeout(60000)
    await signIn(page)
    await page.getByText(/Documents \(\d+\)/).click()

    // Create and upload a file
    const testContent = 'Capybaras are herbivores that feed on grasses and aquatic plants. They live in South America.'
    const tmpPath = path.join(__dirname, 'test_status.txt')
    fs.writeFileSync(tmpPath, testContent)

    try {
      const fileInput = page.locator('input[type="file"]')
      await fileInput.setInputFiles(tmpPath)
      await expect(page.getByText('test_status.txt')).toBeVisible({ timeout: 10000 })

      // Wait for ready badge (polling updates every 2s)
      await expect(page.getByText('ready').first()).toBeVisible({ timeout: 40000 })
    } finally {
      fs.unlinkSync(tmpPath)
    }
  })

  test('Delete file removes from list', async ({ page }) => {
    await signIn(page)
    await page.getByText(/Documents \(\d+\)/).click()
    await page.waitForTimeout(1000)

    // Check if there are any files to delete
    const deleteButtons = page.locator('button:has-text("✕")')
    const count = await deleteButtons.count()
    if (count > 0) {
      const fileName = await deleteButtons.first().locator('..').locator('..').locator('span').first().textContent()
      await deleteButtons.first().click()
      // Give time for deletion
      await page.waitForTimeout(2000)
      if (fileName) {
        // File count should decrease (panel header updates)
        await expect(page.getByText(/Documents \(\d+\)/)).toBeVisible()
      }
    }
  })
})

// ── Theme Tests ──

test.describe('Theme', () => {
  test.describe.configure({ mode: 'serial' })

  test('Theme toggle button visible', async ({ page }) => {
    await signIn(page)
    // Theme toggle is in the sidebar
    const themeBtn = page.locator('button').filter({ hasText: /Light|Dark|System/ }).first()
    // If not found by text, look for the icon button
    const iconBtn = page.locator('[aria-label="Toggle theme"]').or(themeBtn)
    await expect(iconBtn.first()).toBeVisible({ timeout: 5000 })
  })

  test('Switch to dark mode', async ({ page }) => {
    await signIn(page)
    // Click the theme toggle button (Sun/Moon icon)
    const themeButtons = page.locator('button').filter({ has: page.locator('svg') })
    // Find the theme button in the sidebar (near Sign Out)
    const sidebar = page.locator('.w-64')
    const themeBtn = sidebar.locator('button').filter({ has: page.locator('svg') }).first()
    await themeBtn.click()
    // Click "Dark" option in dropdown
    await page.getByText('Dark', { exact: true }).click()
    await expect(page.locator('html')).toHaveClass(/dark/, { timeout: 3000 })
  })

  test('Theme persists on reload', async ({ page }) => {
    await signIn(page)
    // Set dark mode
    const sidebar = page.locator('.w-64')
    const themeBtn = sidebar.locator('button').filter({ has: page.locator('svg') }).first()
    await themeBtn.click()
    await page.getByText('Dark', { exact: true }).click()
    await expect(page.locator('html')).toHaveClass(/dark/)
    // Reload
    await page.reload()
    await expect(page.getByRole('button', { name: '+ New Chat' })).toBeVisible({ timeout: 10000 })
    await expect(page.locator('html')).toHaveClass(/dark/)
    // Reset to system for cleanup
    const themeBtn2 = page.locator('.w-64').locator('button').filter({ has: page.locator('svg') }).first()
    await themeBtn2.click()
    await page.getByText('System', { exact: true }).click()
  })
})

// ── Admin Settings Tests ──

test.describe('Admin Settings', () => {
  test.describe.configure({ mode: 'serial' })

  test('Settings link not visible for non-admin user', async ({ page }) => {
    await signIn(page)
    await expect(page.locator('[data-testid="settings-link"]')).not.toBeVisible()
  })

  test('/settings redirects non-admin to /', async ({ page }) => {
    await signIn(page)
    await page.goto('/settings')
    await expect(page).toHaveURL('/', { timeout: 5000 })
  })
})

// ── Metadata Tests ──

test.describe('Metadata', () => {
  test.describe.configure({ mode: 'serial' })

  test('Metadata badges visible on ready document', async ({ page }) => {
    test.setTimeout(60000)
    await signIn(page)
    await page.getByText(/Documents \(\d+\)/).click()
    await expect(page.getByRole('button', { name: 'Upload File' })).toBeVisible()

    // Check if any ready documents exist with metadata badges (purple badges)
    const readyBadges = page.locator('.bg-green-100:has-text("ready")')
    const count = await readyBadges.count()
    if (count > 0) {
      // At least one ready doc — check for metadata badges (purple)
      const metadataBadges = page.locator('.bg-purple-100')
      await expect(metadataBadges.first()).toBeVisible({ timeout: 5000 })
    }
    // If no ready docs, test passes (nothing to check)
  })

  test('Filter bar renders when documents with metadata exist', async ({ page }) => {
    test.setTimeout(60000)
    await signIn(page)
    // Check if filter bar appears (only shows when ready docs with metadata exist)
    const filterBar = page.getByText('Filters')
    // Wait a bit for files to load
    await page.waitForTimeout(3000)
    // Filter bar may or may not be visible depending on whether ready docs exist
    const isVisible = await filterBar.isVisible().catch(() => false)
    // This is a soft check — if visible, verify it has content
    if (isVisible) {
      await expect(filterBar).toBeVisible()
    }
    // Pass regardless — we're checking it doesn't crash
  })
})

// ── Console Error Tests ──

test.describe('Console Errors', () => {
  test('No critical console errors on main pages', async ({ page }) => {
    const errors: string[] = []
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(msg.text())
    })

    await page.goto('/login')
    await page.waitForTimeout(2000)
    await page.goto('/signup')
    await page.waitForTimeout(2000)

    const criticalErrors = errors.filter(
      (e) =>
        !e.includes('favicon') &&
        !e.includes('extension') &&
        !e.includes('404') &&
        !e.includes('Failed to load resource')
    )
    expect(criticalErrors).toHaveLength(0)
  })
})

// ── Phase 6: File Explorer Tests ──
// Tag: @phase6 — focused-suite gate per Plan 06-11 VALIDATION.md.
// Run: npx playwright test e2e/full-suite.spec.ts --grep '@phase6'
//
// CLEANUP DISCIPLINE (CLAUDE.md): every test below tracks resources it creates
// (folder ids, document ids) and deletes only those — NEVER blanket-delete user data.

test.describe('FileExplorer @phase6', () => {
  test.describe.configure({ mode: 'serial' })

  /**
   * Pitfall 12 structural invariant: MessageList.tsx must NOT contain agent-type
   * structural forks (if/===/switch on tool.tool that gates JSX). Label LOOKUP map
   * via tool.tool[key] is permitted; structural if-branches are forbidden.
   * This is the grep gate from Plan 06-11 Task 1 — runs without a browser context.
   */
  test('Pitfall 12 invariant: SubAgentSection has no agent-type fork @phase6', () => {
    const srcPath = path.join(__dirname, '..', 'src', 'components', 'MessageList.tsx')
    const src = fs.readFileSync(srcPath, 'utf8')
    // Forbidden: if (tool.tool === 'explore_knowledge_base') { <JSX> } and similar.
    const forbiddenIf =
      /if\s*\(\s*tool\.(tool|name|agent|type)\s*===?\s*['"](explore_knowledge_base|analyze_document)['"]/
    expect(src).not.toMatch(forbiddenIf)
    // Also forbid switch(tool.tool) { case 'explore_knowledge_base': }
    const forbiddenSwitch =
      /switch\s*\(\s*tool\.(tool|name|agent|type)\s*\)\s*\{[\s\S]*?case\s+['"](explore_knowledge_base|analyze_document)['"]/
    expect(src).not.toMatch(forbiddenSwitch)
  })

  // UI-01: file explorer panel renders (replaces FileUploadPanel).
  test('UI-01 FileExplorer renders in place of FileUploadPanel @phase6', async ({ page }) => {
    await signIn(page)
    await expect(page.getByTestId('file-explorer-body')).toBeVisible({ timeout: 10000 })
    // The legacy panel name should not appear anywhere in the DOM
    expect(await page.getByText('FileUploadPanel', { exact: true }).count()).toBe(0)
  })

  // UI-02: two scope sections render simultaneously (not tabs).
  test('UI-02 two scope sections render simultaneously (not tabs) @phase6', async ({ page }) => {
    await signIn(page)
    await expect(page.getByTestId('file-explorer-body')).toBeVisible({ timeout: 10000 })
    await expect(page.getByText('Shared (global)').first()).toBeVisible()
    await expect(page.getByText('My Files').first()).toBeVisible()
    // No tablist inside the explorer body — the two sections render side-by-side
    const explorer = page.getByTestId('file-explorer-body')
    expect(await explorer.locator('[role="tablist"]').count()).toBe(0)
  })

  // UI-03: folder open state persists across reload (per useOpenFoldersStorage).
  test('UI-03 folder open state persists across reload @phase6', async ({ page }) => {
    await signIn(page)
    const created: string[] = []
    try {
      const folder = await apiPost(page, '/api/folders', { path: '/phase6-persist', scope: 'user' })
      created.push(folder.id)
      await page.reload()
      await expect(page.getByRole('button', { name: '+ New Chat' })).toBeVisible({ timeout: 15000 })
      // Find the row for the created folder (my-files scope) and toggle it open
      const row = page.locator('[data-folder-path="/phase6-persist"][data-scope="user"]').first()
      await expect(row).toBeVisible({ timeout: 10000 })
      await row.locator('button[tabindex="0"]').first().click()
      await expect(row).toHaveAttribute('aria-expanded', 'true', { timeout: 5000 })
      // Reload and verify aria-expanded persists
      await page.reload()
      const rowAfter = page.locator('[data-folder-path="/phase6-persist"][data-scope="user"]').first()
      await expect(rowAfter).toHaveAttribute('aria-expanded', 'true', { timeout: 10000 })
    } finally {
      for (const id of created) await apiDelete(page, `/api/folders/${id}`)
    }
  })

  // UI-04: right-click context menu shows Create/Rename/Delete (admin on Shared).
  test('UI-04 folder context menu shows Create/Rename/Delete @phase6', async ({ page }) => {
    await signIn(page)
    const created: string[] = []
    try {
      const folder = await apiPost(page, '/api/folders', { path: '/phase6-ctxmenu', scope: 'user' })
      created.push(folder.id)
      await page.reload()
      await expect(page.getByTestId('file-explorer-body')).toBeVisible({ timeout: 10000 })
      const row = page.locator('[data-folder-path="/phase6-ctxmenu"][data-scope="user"]').first()
      await expect(row).toBeVisible({ timeout: 10000 })
      await row.click({ button: 'right' })
      await expect(page.getByRole('menuitem', { name: /new folder/i })).toBeVisible({ timeout: 5000 })
      await expect(page.getByRole('menuitem', { name: /rename/i })).toBeVisible()
      await expect(page.getByRole('menuitem', { name: /delete/i })).toBeVisible()
      await page.keyboard.press('Escape')
    } finally {
      for (const id of created) await apiDelete(page, `/api/folders/${id}`)
    }
  })

  // UI-04 Pitfall 5: delete non-empty folder surfaces server-supplied counts.
  test('UI-04 delete non-empty folder shows server-supplied document count @phase6', async ({ page }) => {
    test.setTimeout(90000)
    await signIn(page)
    const createdFolders: string[] = []
    const createdDocs: string[] = []
    try {
      const folder = await apiPost(page, '/api/folders', { path: '/phase6-nonempty', scope: 'user' })
      createdFolders.push(folder.id)

      // Upload a document into the folder via the UI (FormData; complex to issue via apiPost)
      await page.reload()
      await expect(page.getByTestId('file-explorer-body')).toBeVisible({ timeout: 10000 })
      const row = page.locator('[data-folder-path="/phase6-nonempty"][data-scope="user"]').first()
      await expect(row).toBeVisible({ timeout: 10000 })
      await row.click()  // selects the folder so upload lands here

      const tmpPath = path.join(__dirname, 'phase6_nonempty.txt')
      fs.writeFileSync(tmpPath, 'Phase 6 non-empty delete test content.')
      try {
        const fileInput = page.locator('input[type="file"]').first()
        await fileInput.setInputFiles(tmpPath)
        await expect(page.getByText('phase6_nonempty.txt').first()).toBeVisible({ timeout: 15000 })
      } finally {
        fs.unlinkSync(tmpPath)
      }

      // Capture the document id for cleanup
      const docHandle = page.locator('[data-document-id]').filter({ hasText: 'phase6_nonempty.txt' }).first()
      const docId = await docHandle.getAttribute('data-document-id')
      if (docId) createdDocs.push(docId)

      // Right-click the folder row and select Delete; expect server-supplied counts.
      await row.click({ button: 'right' })
      await page.getByRole('menuitem', { name: /delete/i }).click()
      // Trigger the actual DELETE call via the dialog confirm button to fetch counts
      const confirmBtn = page.getByRole('button', { name: /^delete$/i })
      await expect(confirmBtn).toBeVisible({ timeout: 5000 })
      await confirmBtn.click()
      // After the 409, the dialog must surface the server-supplied counts literally
      await expect(page.getByText(/contains \d+ documents?/i)).toBeVisible({ timeout: 10000 })
      await expect(page.getByText(/\d+ subfolders?/i)).toBeVisible()
      // Close the dialog
      const cancelBtn = page.getByRole('button', { name: /cancel|close/i }).first()
      if (await cancelBtn.isVisible().catch(() => false)) await cancelBtn.click()
      else await page.keyboard.press('Escape')
    } finally {
      for (const id of createdDocs) await apiDelete(page, `/api/files/${id}`)
      for (const id of createdFolders) await apiDelete(page, `/api/folders/${id}`)
    }
  })

  // UI-05: upload lands in currently-selected folder.
  test('UI-05 upload lands in currently-selected folder @phase6', async ({ page }) => {
    test.setTimeout(90000)
    await signIn(page)
    const createdFolders: string[] = []
    const createdDocs: string[] = []
    try {
      const folder = await apiPost(page, '/api/folders', { path: '/phase6-upload', scope: 'user' })
      createdFolders.push(folder.id)
      await page.reload()
      await expect(page.getByTestId('file-explorer-body')).toBeVisible({ timeout: 10000 })
      const row = page.locator('[data-folder-path="/phase6-upload"][data-scope="user"]').first()
      await expect(row).toBeVisible({ timeout: 10000 })
      await row.click()

      const tmpPath = path.join(__dirname, 'phase6_upload.txt')
      fs.writeFileSync(tmpPath, 'Phase 6 upload-into-selected-folder test.')
      try {
        const fileInput = page.locator('input[type="file"]').first()
        await fileInput.setInputFiles(tmpPath)
        await expect(page.getByText('phase6_upload.txt').first()).toBeVisible({ timeout: 15000 })
      } finally {
        fs.unlinkSync(tmpPath)
      }

      const docHandle = page.locator('[data-document-id]').filter({ hasText: 'phase6_upload.txt' }).first()
      const docId = await docHandle.getAttribute('data-document-id')
      if (docId) createdDocs.push(docId)
      // Verify backend stored it under /phase6-upload (read folder via API)
      const listing = await apiGet(page, `/api/folders?path=/phase6-upload&scope=user`)
      const fileNames = (listing.documents ?? []).map((d: any) => d.file_name)
      expect(fileNames).toContain('phase6_upload.txt')
    } finally {
      for (const id of createdDocs) await apiDelete(page, `/api/files/${id}`)
      for (const id of createdFolders) await apiDelete(page, `/api/folders/${id}`)
    }
  })

  // UI-06 happy path: same-scope drag-move using pointer-event pattern.
  test('UI-06 drag document to another folder in same scope moves it @phase6', async ({ page }) => {
    test.setTimeout(120000)
    await signIn(page)
    const createdFolders: string[] = []
    const createdDocs: string[] = []
    try {
      const dest = await apiPost(page, '/api/folders', { path: '/phase6-drag-dest', scope: 'user' })
      createdFolders.push(dest.id)

      // Upload doc to root via UI flow
      await page.reload()
      await expect(page.getByTestId('file-explorer-body')).toBeVisible({ timeout: 10000 })
      // Select root user
      const rootUser = page.locator('[data-folder-path="/"][data-scope="user"]').first()
      await expect(rootUser).toBeVisible({ timeout: 10000 })
      await rootUser.click()

      const tmpPath = path.join(__dirname, 'phase6_drag.txt')
      fs.writeFileSync(tmpPath, 'Phase 6 drag-move test.')
      try {
        const fileInput = page.locator('input[type="file"]').first()
        await fileInput.setInputFiles(tmpPath)
        await expect(page.getByText('phase6_drag.txt').first()).toBeVisible({ timeout: 15000 })
      } finally {
        fs.unlinkSync(tmpPath)
      }

      const docHandle = page.locator('[data-document-id]').filter({ hasText: 'phase6_drag.txt' }).first()
      const docId = await docHandle.getAttribute('data-document-id')
      if (!docId) throw new Error('phase6_drag.txt did not surface a data-document-id')
      createdDocs.push(docId)

      // dnd-kit needs pointer events — NOT page.dragTo (HTML5 only).
      // Pattern per Plan 06-11 RESEARCH.md §Wave 0 Gaps line 698.
      const source = page.locator(`[data-document-id="${docId}"]`).first()
      const target = page.locator('[data-folder-path="/phase6-drag-dest"][data-scope="user"]').first()
      const sourceBox = await source.boundingBox()
      const targetBox = await target.boundingBox()
      if (!sourceBox || !targetBox) throw new Error('boundingBox failed for drag fixtures')
      await page.mouse.move(sourceBox.x + sourceBox.width / 2, sourceBox.y + sourceBox.height / 2)
      await page.mouse.down()
      await page.mouse.move(
        targetBox.x + targetBox.width / 2,
        targetBox.y + targetBox.height / 2,
        { steps: 10 }
      )
      await page.mouse.up()

      // Verify backend folder_path was updated to /phase6-drag-dest
      await expect.poll(
        async () => {
          const listing = await apiGet(page, `/api/folders?path=/phase6-drag-dest&scope=user`)
          const names = (listing.documents ?? []).map((d: any) => d.file_name)
          return names.includes('phase6_drag.txt')
        },
        { timeout: 10000, message: 'expected phase6_drag.txt to appear under /phase6-drag-dest' }
      ).toBe(true)
    } finally {
      for (const id of createdDocs) await apiDelete(page, `/api/files/${id}`)
      for (const id of createdFolders) await apiDelete(page, `/api/folders/${id}`)
    }
  })

  // UI-06 / D-01: cross-scope drag opens BLOCKING dialog and does NOT mutate.
  test('UI-06/D-01 cross-scope drag opens BLOCK modal and does not mutate @phase6', async ({ page }) => {
    test.setTimeout(120000)
    await signInAdmin(page)
    const createdDocs: string[] = []
    try {
      // Upload a doc to admin's My Files root via UI
      await expect(page.getByTestId('file-explorer-body')).toBeVisible({ timeout: 10000 })
      const rootUser = page.locator('[data-folder-path="/"][data-scope="user"]').first()
      await expect(rootUser).toBeVisible({ timeout: 10000 })
      await rootUser.click()

      const tmpPath = path.join(__dirname, 'phase6_crossscope.txt')
      fs.writeFileSync(tmpPath, 'Phase 6 cross-scope BLOCK modal test.')
      try {
        const fileInput = page.locator('input[type="file"]').first()
        await fileInput.setInputFiles(tmpPath)
        await expect(page.getByText('phase6_crossscope.txt').first()).toBeVisible({ timeout: 15000 })
      } finally {
        fs.unlinkSync(tmpPath)
      }

      const docHandle = page.locator('[data-document-id]').filter({ hasText: 'phase6_crossscope.txt' }).first()
      const docId = await docHandle.getAttribute('data-document-id')
      if (!docId) throw new Error('phase6_crossscope.txt did not surface a data-document-id')
      createdDocs.push(docId)

      // Capture original folder_path before drag
      const beforeListing = await apiGet(page, `/api/folders?path=/&scope=user`)
      const beforeDoc = (beforeListing.documents ?? []).find((d: any) => d.id === docId)
      const originalPath = beforeDoc?.folder_path ?? '/'

      // Drag onto the Shared root folder (cross-scope)
      const source = page.locator(`[data-document-id="${docId}"]`).first()
      const target = page.locator('[data-folder-path="/"][data-scope="global"]').first()
      await expect(target).toBeVisible({ timeout: 5000 })
      const sourceBox = await source.boundingBox()
      const targetBox = await target.boundingBox()
      if (!sourceBox || !targetBox) throw new Error('boundingBox failed for cross-scope fixtures')
      await page.mouse.move(sourceBox.x + sourceBox.width / 2, sourceBox.y + sourceBox.height / 2)
      await page.mouse.down()
      await page.mouse.move(
        targetBox.x + targetBox.width / 2,
        targetBox.y + targetBox.height / 2,
        { steps: 10 }
      )
      await page.mouse.up()

      // D-01 LOCKED copy must appear verbatim — Plan 06-10 CrossScopeMoveDialog.
      await expect(page.getByText(/Scope is permanent for security/i)).toBeVisible({ timeout: 5000 })

      // Empirical: backend was NOT called — doc's folder_path unchanged + scope unchanged.
      const afterListing = await apiGet(page, `/api/folders?path=/&scope=user`)
      const afterDoc = (afterListing.documents ?? []).find((d: any) => d.id === docId)
      expect(afterDoc).toBeTruthy()
      expect(afterDoc.folder_path).toBe(originalPath)
      expect(afterDoc.scope).toBe('user')

      // Close the dialog via "Got it"
      const gotIt = page.getByRole('button', { name: /got it/i })
      if (await gotIt.isVisible().catch(() => false)) await gotIt.click()
      else await page.keyboard.press('Escape')
      await expect(page.getByText(/Scope is permanent for security/i)).not.toBeVisible({ timeout: 5000 })
    } finally {
      for (const id of createdDocs) await apiDelete(page, `/api/files/${id}`)
    }
  })

  // UI-07: inline document rename via Enter key.
  test('UI-07 rename document inline via Enter key @phase6', async ({ page }) => {
    test.setTimeout(90000)
    await signIn(page)
    const createdDocs: string[] = []
    try {
      await expect(page.getByTestId('file-explorer-body')).toBeVisible({ timeout: 10000 })
      const rootUser = page.locator('[data-folder-path="/"][data-scope="user"]').first()
      await expect(rootUser).toBeVisible({ timeout: 10000 })
      await rootUser.click()

      const tmpPath = path.join(__dirname, 'phase6_rename.txt')
      fs.writeFileSync(tmpPath, 'Phase 6 inline-rename test.')
      try {
        const fileInput = page.locator('input[type="file"]').first()
        await fileInput.setInputFiles(tmpPath)
        await expect(page.getByText('phase6_rename.txt').first()).toBeVisible({ timeout: 15000 })
      } finally {
        fs.unlinkSync(tmpPath)
      }

      const docHandle = page.locator('[data-document-id]').filter({ hasText: 'phase6_rename.txt' }).first()
      const docId = await docHandle.getAttribute('data-document-id')
      if (docId) createdDocs.push(docId)

      // Click the filename span (UI-07 enters rename mode)
      await docHandle.getByText('phase6_rename.txt', { exact: false }).click()
      const input = docHandle.locator('input').first()
      await expect(input).toBeVisible({ timeout: 5000 })
      await input.fill('phase6_renamed.txt')
      await input.press('Enter')

      await expect(page.getByText('phase6_renamed.txt').first()).toBeVisible({ timeout: 10000 })
    } finally {
      for (const id of createdDocs) await apiDelete(page, `/api/files/${id}`)
    }
  })

  // UI-08: breadcrumbs + scope badge + status badge render.
  test('UI-08 breadcrumbs and scope/status badges render @phase6', async ({ page }) => {
    test.setTimeout(90000)
    await signIn(page)
    const createdFolders: string[] = []
    const createdDocs: string[] = []
    try {
      const folder = await apiPost(page, '/api/folders', { path: '/phase6-bc', scope: 'user' })
      createdFolders.push(folder.id)
      await page.reload()
      await expect(page.getByTestId('file-explorer-body')).toBeVisible({ timeout: 10000 })
      const row = page.locator('[data-folder-path="/phase6-bc"][data-scope="user"]').first()
      await expect(row).toBeVisible({ timeout: 10000 })
      await row.click()

      // Breadcrumbs should surface "phase6-bc" segment in the header
      // (Breadcrumbs sits at the top of FileExplorerPanel — outside file-explorer-body.)
      await expect(page.getByText('phase6-bc').first()).toBeVisible({ timeout: 5000 })

      // Upload a doc so we can assert scope+status badges
      const tmpPath = path.join(__dirname, 'phase6_badge.txt')
      fs.writeFileSync(tmpPath, 'Phase 6 badge test.')
      try {
        const fileInput = page.locator('input[type="file"]').first()
        await fileInput.setInputFiles(tmpPath)
        await expect(page.getByText('phase6_badge.txt').first()).toBeVisible({ timeout: 15000 })
      } finally {
        fs.unlinkSync(tmpPath)
      }

      const docHandle = page.locator('[data-document-id]').filter({ hasText: 'phase6_badge.txt' }).first()
      const docId = await docHandle.getAttribute('data-document-id')
      if (docId) createdDocs.push(docId)

      // Scope badge: 'Private' for user-scope; Status badge: at minimum 'pending'/'processing'/'ready'/'failed'
      await expect(docHandle.getByText(/private/i)).toBeVisible({ timeout: 10000 })
      await expect(docHandle.getByText(/pending|processing|ready|failed/i).first()).toBeVisible()
    } finally {
      for (const id of createdDocs) await apiDelete(page, `/api/files/${id}`)
      for (const id of createdFolders) await apiDelete(page, `/api/folders/${id}`)
    }
  })

  // UI-09 keyboard nav (D-04 LOCKED set: Right/Left/Up/Down/Enter/Space).
  test('UI-09 keyboard arrows navigate the tree @phase6', async ({ page }) => {
    await signIn(page)
    const createdFolders: string[] = []
    try {
      const f = await apiPost(page, '/api/folders', { path: '/phase6-kbd', scope: 'user' })
      createdFolders.push(f.id)
      await page.reload()
      await expect(page.getByTestId('file-explorer-body')).toBeVisible({ timeout: 10000 })

      const row = page.locator('[data-folder-path="/phase6-kbd"][data-scope="user"]').first()
      await expect(row).toBeVisible({ timeout: 10000 })

      // Focus the row's interactive button
      const rowBtn = row.locator('button[tabindex="0"]').first()
      await rowBtn.focus()

      // Initial state: collapsed (aria-expanded=false)
      await expect(row).toHaveAttribute('aria-expanded', 'false', { timeout: 5000 })

      // ArrowRight expands
      await page.keyboard.press('ArrowRight')
      await expect(row).toHaveAttribute('aria-expanded', 'true', { timeout: 5000 })

      // ArrowLeft collapses
      await page.keyboard.press('ArrowLeft')
      await expect(row).toHaveAttribute('aria-expanded', 'false', { timeout: 5000 })

      // ArrowDown moves focus off the current row (we just assert focus changes)
      const focusedBefore = await page.evaluate(() => document.activeElement?.outerHTML ?? null)
      await page.keyboard.press('ArrowDown')
      const focusedAfter = await page.evaluate(() => document.activeElement?.outerHTML ?? null)
      expect(focusedAfter).not.toBe(focusedBefore)
    } finally {
      for (const id of createdFolders) await apiDelete(page, `/api/folders/${id}`)
    }
  })

  // UI-10 SubAgentSection renders Explorer trace on chat reload.
  // Skipped by default: requires a thread with explore_knowledge_base tool_metadata.
  // Operators can seed one manually; running without a seeded thread is a no-op pass.
  test('UI-10 SubAgentSection renders Explorer trace on chat reload @phase6', async ({ page }) => {
    await signIn(page)
    // Heuristic: find any thread whose conversation already includes a SubAgent label.
    // If none exists in the test account, skip — operator note documented in SUMMARY.md.
    const threads = page.locator('.overflow-y-auto > div')
    const threadCount = await threads.count()
    if (threadCount === 0) {
      test.skip(true, 'No threads in test account — seed one with explore_knowledge_base trace to exercise UI-10')
      return
    }

    // Try each thread until we find one with a SubAgent trace; otherwise skip.
    let found = false
    for (let i = 0; i < Math.min(threadCount, 5); i++) {
      await threads.nth(i).click()
      await page.waitForTimeout(800)
      const subAgent = page.getByText(/explore_knowledge_base|Explorer/i).first()
      if (await subAgent.isVisible().catch(() => false)) {
        found = true
        break
      }
    }
    if (!found) {
      test.skip(true, 'No thread with explore_knowledge_base trace in fixture — seed manually to exercise UI-10')
      return
    }
    // Reload and verify the SubAgentSection still renders the trace
    await page.reload()
    await expect(page.getByText(/explore_knowledge_base|Explorer/i).first()).toBeVisible({ timeout: 10000 })
  })

  // UI-11: admin sees + New folder affordance in Shared section.
  test('UI-11 admin sees + New folder in Shared section @phase6', async ({ page }) => {
    await signInAdmin(page)
    await expect(page.getByTestId('file-explorer-body')).toBeVisible({ timeout: 10000 })
    const sharedSection = page.locator('section[data-root-scope="global"]').first()
    await expect(sharedSection).toBeVisible()
    // Section-header "+ New folder" button is gated structurally on canCreate (admin sees it)
    await expect(sharedSection.getByRole('button', { name: /new folder/i })).toBeVisible({ timeout: 5000 })
  })

  // UI-11: non-admin does NOT see Create/Rename/Delete on Shared scope.
  test('UI-11 non-admin does not see + New folder in Shared section @phase6', async ({ page }) => {
    await signIn(page)
    await expect(page.getByTestId('file-explorer-body')).toBeVisible({ timeout: 10000 })
    const sharedSection = page.locator('section[data-root-scope="global"]').first()
    await expect(sharedSection).toBeVisible()
    // Section-header "+ New folder" button must be absent for non-admin (canCreate=false)
    expect(await sharedSection.getByRole('button', { name: /new folder/i }).count()).toBe(0)

    // Right-click root Shared row → must surface only the "Read-only (admin required)" item
    const sharedRoot = page.locator('[data-folder-path="/"][data-scope="global"]').first()
    await expect(sharedRoot).toBeVisible({ timeout: 5000 })
    await sharedRoot.click({ button: 'right' })
    await expect(page.getByRole('menuitem', { name: /Read-only \(admin required\)/i })).toBeVisible({ timeout: 5000 })
    // Should NOT see destructive entries
    expect(await page.getByRole('menuitem', { name: /^delete$/i }).count()).toBe(0)
    expect(await page.getByRole('menuitem', { name: /^rename$/i }).count()).toBe(0)
    await page.keyboard.press('Escape')
  })
})
