import { test, expect } from '@playwright/test'

// Existing test user created in Supabase
const TEST_EMAIL = 'test@test.com'
const TEST_PASSWORD = 'supabase123'

test.describe('Module 1: Auth & Chat E2E', () => {
  test.describe.configure({ mode: 'serial' })

  // --- Auth Page Rendering ---

  test('1. Protected route redirects to /login when unauthenticated', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveURL(/\/login/)
    await expect(page.locator('[data-slot="card-title"]')).toHaveText('Sign In')
  })

  test('2. Login page renders correctly', async ({ page }) => {
    await page.goto('/login')
    await expect(page.locator('[data-slot="card-title"]')).toHaveText('Sign In')
    await expect(page.getByText('Enter your credentials')).toBeVisible()
    await expect(page.getByLabel('Email')).toBeVisible()
    await expect(page.getByLabel('Password')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Sign In' })).toBeVisible()
    await expect(page.getByRole('link', { name: 'Sign up' })).toBeVisible()
  })

  test('3. Login page links to signup', async ({ page }) => {
    await page.goto('/login')
    await page.getByRole('link', { name: 'Sign up' }).click()
    await expect(page).toHaveURL(/\/signup/)
  })

  test('4. Signup page renders correctly', async ({ page }) => {
    await page.goto('/signup')
    await expect(page.locator('[data-slot="card-title"]')).toHaveText('Create Account')
    await expect(page.getByLabel('Email')).toBeVisible()
    await expect(page.getByLabel('Password')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Create Account' })).toBeVisible()
    await expect(page.getByRole('link', { name: 'Sign in' })).toBeVisible()
  })

  test('5. Signup page links to login', async ({ page }) => {
    await page.goto('/signup')
    await page.getByRole('link', { name: 'Sign in' }).click()
    await expect(page).toHaveURL(/\/login/)
  })

  // --- Auth Error Handling ---

  test('6. Login with invalid credentials shows error', async ({ page }) => {
    await page.goto('/login')
    await page.getByLabel('Email').fill('nonexistent@test.com')
    await page.getByLabel('Password').fill('wrongpassword')
    await page.getByRole('button', { name: 'Sign In' }).click()
    await expect(page.locator('.rounded-md.p-3.text-sm')).toBeVisible({ timeout: 15000 })
  })

  // --- Auth Flow with Existing User ---

  test('7. Login with valid credentials redirects to chat', async ({ page }) => {
    await page.goto('/login')
    await page.getByLabel('Email').fill(TEST_EMAIL)
    await page.getByLabel('Password').fill(TEST_PASSWORD)
    await page.getByRole('button', { name: 'Sign In' }).click()

    await expect(page).toHaveURL('/', { timeout: 15000 })
    await expect(page.getByRole('button', { name: '+ New Chat' })).toBeVisible({ timeout: 10000 })
  })

  test('8. Sign out redirects to login', async ({ page }) => {
    // Sign in first
    await page.goto('/login')
    await page.getByLabel('Email').fill(TEST_EMAIL)
    await page.getByLabel('Password').fill(TEST_PASSWORD)
    await page.getByRole('button', { name: 'Sign In' }).click()
    await expect(page.getByRole('button', { name: '+ New Chat' })).toBeVisible({ timeout: 15000 })

    // Sign out
    await page.getByRole('button', { name: 'Sign Out' }).click()
    await expect(page).toHaveURL(/\/login/, { timeout: 10000 })
  })

  test('9. Session persists on page refresh', async ({ page }) => {
    // Sign in
    await page.goto('/login')
    await page.getByLabel('Email').fill(TEST_EMAIL)
    await page.getByLabel('Password').fill(TEST_PASSWORD)
    await page.getByRole('button', { name: 'Sign In' }).click()
    await expect(page.getByRole('button', { name: '+ New Chat' })).toBeVisible({ timeout: 15000 })

    // Refresh page
    await page.reload()
    await expect(page.getByRole('button', { name: '+ New Chat' })).toBeVisible({ timeout: 10000 })
    await expect(page).toHaveURL('/')
  })

  // --- Chat Functionality ---

  test('10. Create new thread', async ({ page }) => {
    // Sign in
    await page.goto('/login')
    await page.getByLabel('Email').fill(TEST_EMAIL)
    await page.getByLabel('Password').fill(TEST_PASSWORD)
    await page.getByRole('button', { name: 'Sign In' }).click()
    await expect(page.getByRole('button', { name: '+ New Chat' })).toBeVisible({ timeout: 15000 })

    // Create thread
    // Count threads before
    const threadsBefore = await page.locator('.overflow-y-auto > div').count()

    await page.getByRole('button', { name: '+ New Chat' }).click()

    // Thread count should increase
    await expect(page.locator('.overflow-y-auto > div')).toHaveCount(threadsBefore + 1, { timeout: 5000 })
  })

  test('11. Send message and receive streaming response', async ({ page }) => {
    test.setTimeout(90000)
    // Sign in
    await page.goto('/login')
    await page.getByLabel('Email').fill(TEST_EMAIL)
    await page.getByLabel('Password').fill(TEST_PASSWORD)
    await page.getByRole('button', { name: 'Sign In' }).click()
    await expect(page.getByRole('button', { name: '+ New Chat' })).toBeVisible({ timeout: 15000 })

    // Type and send a message (auto-creates thread)
    await page.getByPlaceholder('Type a message...').fill('Say exactly: test ok')
    await page.getByRole('button', { name: 'Send' }).click()

    // User message should appear in the message area
    // (the text also appears in sidebar as thread title, so use nth(1) for the message bubble)
    await expect(page.getByText('Say exactly: test ok').nth(1)).toBeVisible({ timeout: 5000 })

    // Wait for assistant response to appear (the "Thinking..." placeholder gets replaced)
    // The textarea should become enabled again after streaming completes
    await expect(page.getByPlaceholder('Type a message...')).toBeEnabled({ timeout: 60000 })
  })

  // --- Console Errors ---

  test('12. No console errors on auth pages', async ({ page }) => {
    const errors: string[] = []
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        errors.push(msg.text())
      }
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
