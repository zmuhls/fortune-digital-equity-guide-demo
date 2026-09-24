import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { firefox, webkit } from 'playwright';

for (const [name, engine] of Object.entries({ firefox, webkit })) {
  test(`${name}: shared prompt refresh, stale recovery, concurrent save and reload`, { timeout: 60000 }, async () => {
    const browser = await engine.launch();
    try {
      const page = await browser.newPage();
      await page.clock.install();
      page.setDefaultTimeout(10000);
      page.on('dialog', dialog => dialog.accept());
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      let draft = { body: 'You are the Digital Equity guide. Your nickname is Deb.',
        version: 7, edit_number: 39, release_number: 1, display_version: 'v1.39', active: true,
        revisions: [], updated_by_name: 'Maria' };
      let writes = 0;
      const lab = () => ({ shared_draft: draft, compiled_prompt: draft.body,
        deployed: { display_version: draft.display_version, behavior_release: 'test' }, proposals: [] });
      await page.route('http://localhost/**', async route => {
        const path = new URL(route.request().url()).pathname;
        if (path.startsWith('/api/')) {
          let data = {};
          if (path.endsWith('/session')) data = { account: { slot_key: 'editor-1', display_name: 'Tester' }, csrf_token: 'fixture' };
          if (path.endsWith('/prompt-lab')) data = { prompt_lab: lab() };
          if (path.endsWith('/prompt-draft')) {
            writes++;
            const body = route.request().postDataJSON();
            if (body.expected_version !== draft.version) {
              await route.fulfill({ status: 409, json: { error: 'Newer saved prompt', current: draft } });
              return;
            }
            draft = { ...draft, body: body.body, version: draft.version + 1, edit_number: draft.edit_number + 1 };
            data = { shared_draft: draft };
          }
          await route.fulfill({ json: data });
          return;
        }
        const file = path === '/evaluation' ? 'evaluation.html' : path.split('/').at(-1);
        try {
          await route.fulfill({ body: await readFile(new URL(`../${file}`, import.meta.url)),
            contentType: file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html' });
        } catch { await route.fulfill({ status: 404, body: '' }); }
      });
      await page.addInitScript(() => {
        if (!sessionStorage.getItem('seeded')) {
          sessionStorage.setItem('seeded', '1');
          sessionStorage.setItem('fs-evaluation-draft:editor-1:prompt', JSON.stringify({ body: 'Earlier unsaved wording', changeNote: 'Keep my work', version: 6 }));
        }
      });
      await page.goto('http://localhost/evaluation');
      await page.locator('#prompt-lab-tab').click();
      const editor = page.locator('#shared-prompt-body');
      await editor.waitFor({ state: 'visible' });
      assert.equal(await editor.inputValue(), draft.body);
      assert.match(await page.locator('.prompt-draft-recovery').textContent(), /Earlier unsaved wording/);
      assert.equal(writes, 0);
      if (process.env.PROMPT_SYNC_SCREENSHOT_DIR) {
        await page.screenshot({ path: `${process.env.PROMPT_SYNC_SCREENSHOT_DIR}/prompt-sync-${name}.png`, fullPage: true });
      }
      await page.locator('.prompt-draft-recovery summary').click();
      await page.getByRole('button', { name: 'Restore draft 1 for editing' }).click();
      assert.equal(await editor.inputValue(), 'Earlier unsaved wording');
      await page.reload();
      await editor.waitFor({ state: 'visible' });
      assert.equal(await editor.inputValue(), 'Earlier unsaved wording');
      // Same-version refresh must retain genuine edits and selection.
      await editor.fill('My new edit');
      await page.locator('#conversations-tab').click();
      await page.locator('#prompt-lab-tab').click();
      assert.equal(await editor.inputValue(), 'My new edit');
      // A new save by another evaluator refreshes without any write from this tab.
      draft = { ...draft, body: 'Latest shared wording from another evaluator', version: 8 };
      await page.locator('#conversations-tab').click();
      await page.locator('#prompt-lab-tab').click();
      await page.waitForFunction(() => document.querySelector('#shared-prompt-body').value === 'Latest shared wording from another evaluator');
      assert.match(await page.locator('.prompt-draft-recovery').textContent(), /My new edit/);
      assert.equal(writes, 0);
      await editor.fill('Final intended prompt');
      await page.locator('#shared-prompt-change-note').fill('Intentional save');
      await page.getByRole('button', { name: 'Save & apply' }).click();
      await page.waitForFunction(() => document.querySelector('#shared-prompt-status').textContent.startsWith('Saved edit'));
      assert.equal(draft.body, 'Final intended prompt');
      await page.reload();
      await editor.waitFor({ state: 'visible' });
      assert.equal(await editor.inputValue(), 'Final intended prompt');
      // A save races with another evaluator; the backend rejects it, the UI
      // shows the winner and keeps the rejected text recoverable.
      await editor.fill('Concurrent unsaved work');
      await page.locator('#shared-prompt-change-note').fill('Concurrent save');
      draft = { ...draft, body: 'Winner of concurrent save', version: draft.version + 1 };
      await page.getByRole('button', { name: 'Save & apply' }).click();
      await page.waitForFunction(() => document.querySelector('#shared-prompt-body').value === 'Winner of concurrent save');
      assert.match(await page.locator('.prompt-draft-recovery').textContent(), /Concurrent unsaved work/);
      draft = { ...draft, body: 'Periodic refresh latest', version: draft.version + 1 };
      await page.clock.fastForward(11000);
      await page.waitForFunction(() => document.querySelector('#shared-prompt-body').value === 'Periodic refresh latest');
      assert.equal(errors.length, 0, errors.join('\n'));
    } finally { await browser.close(); }
  });
}
