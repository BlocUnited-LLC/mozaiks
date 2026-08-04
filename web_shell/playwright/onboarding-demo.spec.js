import { expect, test } from '@playwright/test';

test('chat-ui onboarding demo shows the shared tour and advances locally', async ({ page }) => {
  await page.goto('/demo/onboarding');

  const dialog1 = page.getByRole('dialog', { name: /Onboarding step 1 of 3/i });
  await expect(dialog1).toBeVisible({ timeout: 5000 });
  await expect(dialog1).toContainText('Create your first app');
  await expect(dialog1).toContainText('1 / 3');

  await dialog1.getByRole('button', { name: 'Next' }).click();

  const dialog2 = page.getByRole('dialog', { name: /Onboarding step 2 of 3/i });
  await expect(dialog2).toBeVisible({ timeout: 3000 });
  await expect(dialog2).toContainText('Explore the marketplace');
  await expect(dialog2).toContainText('2 / 3');

  await dialog2.getByRole('button', { name: 'Skip tour' }).click();

  await expect(page.getByRole('dialog', { name: /Onboarding step/i })).toHaveCount(0, { timeout: 3000 });
});
