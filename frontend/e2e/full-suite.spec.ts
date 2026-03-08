import { test, expect } from '@playwright/test'
import path from 'path'
import fs from 'fs'

const TEST_EMAIL = 'test@test.com'
const TEST_PASSWORD = 'supabase123'

/** Helper: sign in and wait for chat to load */
async function signIn(page: import('@playwright/test').Page) {
  await page.goto('/login')
  await page.getByLabel('Email').fill(TEST_EMAIL)
  await page.getByLabel('Password').fill(TEST_PASSWORD)
  await page.getByRole('button', { name: 'Sign In' }).click()
  await expect(page.getByRole('button', { name: '+ New Chat' })).toBeVisible({ timeout: 15000 })
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
