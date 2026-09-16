/**
 * The build tail: what happens AFTER the concept is approved.
 *
 * An earlier run proved idea -> interview -> concept -> theme -> design docs
 * works against real infrastructure. It then reported "build quiet (1158
 * chars)" four times and timed out. 1158 chars is SMALLER than the interview
 * transcript that preceded it, so the UI did not stall mid-conversation - it
 * moved to a nearly empty screen and stayed there.
 *
 * That earlier run only logged the LENGTH of the page, which is why the
 * failure was unreadable. This one logs what the screen actually says, what
 * the backend thinks the session is doing, and which websocket frames arrive,
 * on every tick. The goal is a diagnosis, not a pass.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { test } from '@playwright/test';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(__dirname, '..', 'live-tail');
const LOG = path.join(OUT, 'tail-report.md');

const IDEA =
  'A simple habit tracker where users create habits, check them off daily, ' +
  'and see a streak count. Include a dashboard page listing habits with streaks.';

const REPLIES = [
  'Keep v1 simple, in-app only. You decide the rest.',
  'Sounds good, go with your recommendation.',
  'Yes, proceed.',
  'Agreed.',
  'Yes.',
];

// Buttons that advance the journey. Ordered: approvals first, then generic.
const ADVANCE = [
  /^approve/i,
  /^continue$/i,
  // A founder with no engineering background takes the fastest path. Matching it
  // first also answers the question this run exists to ask: does an unattended
  // build actually reach a generated app?
  // The whole option card is the button, and its accessible name is composed
  // from the eyebrow plus the title ("FASTEST PATH FASTEST PATH Autonomous"),
  // so this cannot be anchored. A founder with no engineering background takes
  // the fastest path; matching it also answers the question this run exists to
  // ask - does an unattended build actually reach a generated app?
  // PARTICIPATION=guided runs the same idea down the interviewed path, which is
  // the only way to tell whether a failure past the interview was caused by
  // skipping it or was simply never reached before.
  ...(process.env.PARTICIPATION === 'guided'
    ? [/build it with me/i, /review checkpoints/i]
    : [/autonomous/i, /build it for me/i]),
  // Other gates mark one option RECOMMENDED. When the platform already knows
  // the answer, taking it is what an unattended build would do.
  /recommended/i,
  /^confirm/i,
  /^(start|begin) build/i,
  /^proceed/i,
  /^next$/i,
  /^generate/i,
  /^looks good/i,
  /^choose /i,
  /^(yes|ok|got it|done)$/i,
];

const lines = [];
function say(s) {
  console.log(s);
  lines.push(s);
}

// Answers for stages after concept approval. A founder who chose "build it for
// me" defers; these say so without ever asking the agent a question back.
const LATE_REPLIES = [
  'Looks good - you decide the details and keep going.',
  'Yes, proceed.',
  'Go ahead with your recommendation.',
];

// The transcript only grows, so testing the whole body means that once the
// agent has ever asked what to build, the phrase is present forever and the
// idea gets resent on every tick. Look at the tail only, and cap it: if two
// restatements have not registered, resending a third will not help either.
let ideaRestatements = 0;
const asksWhatToBuild = (text) => {
  if (ideaRestatements >= 2) return false;
  const tail = String(text || '').slice(-700);
  return /what (do you want|type of app)|what problem|need to know what you want/i.test(tail);
};

let step = 0;
async function snap(page, name) {
  fs.mkdirSync(OUT, { recursive: true });
  step += 1;
  const file = path.join(OUT, `${String(step).padStart(2, '0')}-${name}.png`);
  await page.screenshot({ path: file, fullPage: true }).catch(() => {});
  return path.basename(file);
}

async function bodyText(page) {
  return (await page.locator('body').innerText().catch(() => '')) || '';
}

// Ask the backend what it believes the session is doing. This is the
// decisive signal: a quiet UI over a busy backend is a reporting bug, a
// quiet UI over an idle backend is a dead loop.
async function sessionState(page) {
  return page
    .evaluate(async () => {
      const out = {};
      try {
        const r = await fetch('/api/session/state');
        out.status = r.status;
        out.body = (await r.text()).slice(0, 1500);
      } catch (e) {
        out.error = String(e);
      }
      return out;
    })
    .catch((e) => ({ error: String(e) }));
}

// Some gates hold their action button behind an attestation checkbox. Tick
// anything unticked before trying to advance, or the click silently does
// nothing and the run looks stalled.
async function tickAttestations(page) {
  const boxes = page.locator('input[type=checkbox]:not(:checked)');
  const n = await boxes.count().catch(() => 0);
  let ticked = 0;
  for (let i = 0; i < Math.min(n, 6); i += 1) {
    const b = boxes.nth(i);
    if (await b.isVisible().catch(() => false)) {
      await b.check({ force: true }).catch(() => {});
      ticked += 1;
    }
  }
  return ticked;
}

async function clickAdvance(page) {
  const ticked = await tickAttestations(page);
  if (ticked) say(`  ticked ${ticked} attestation checkbox(es)`);
  for (const rx of ADVANCE) {
    const b = page.getByRole('button', { name: rx }).first();
    if (await b.isVisible().catch(() => false)) {
      const label = (await b.innerText().catch(() => '')).trim().slice(0, 40);
      await b.click().catch(() => {});
      return label || String(rx);
    }
  }
  return null;
}

// Any option card the journey puts up. Prefer one marked recommended: the
// platform already knows the answer when it labels one.
async function chooseOption(page) {
  const rec = page.locator('[data-recommended="true"]').first();
  if (await rec.isVisible().catch(() => false)) {
    await rec.click().catch(() => {});
    return 'recommended option';
  }
  return null;
}

test('the build tail: what the user sees after approving the concept', async ({ page }) => {
  fs.mkdirSync(OUT, { recursive: true });
  const apiErrors = [];
  const wsTypes = new Map();
  const wsSamples = [];

  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) {
      apiErrors.push(`${r.status()} ${r.url().replace('http://localhost:3100', '').slice(0, 140)}`);
    }
  });
  page.on('websocket', (ws) => {
    say(`  ws open: ${ws.url().slice(0, 100)}`);
    ws.on('framereceived', (f) => {
      const p = typeof f.payload === 'string' ? f.payload : '';
      const m = /"type"\s*:\s*"([^"]+)"/.exec(p);
      const t = m ? m[1] : '(untyped)';
      wsTypes.set(t, (wsTypes.get(t) || 0) + 1);
      if (wsSamples.length < 25) wsSamples.push(p.slice(0, 260));
    });
  });

  // -- enter -------------------------------------------------------------
  await page.goto('/create', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3500);
  await page.getByRole('button', { name: /start build/i }).first().click().catch(() => {});
  await page.waitForTimeout(6000);
  say(`\n  entered build at ${page.url()}`);

  const input = page.locator('textarea, input[type=text], [contenteditable=true]').first();
  await input.waitFor({ state: 'visible', timeout: 60000 });
  await input.fill(IDEA);
  await input.press('Enter');
  say('  idea sent\n');
  // The composer renders before the session can accept input, and a message
  // typed into that window is dropped with no error. Every wrong concept this
  // session traces back to exactly that moment, so confirm it landed.
  let landed = false;
  for (let attempt = 1; attempt <= 4 && !landed; attempt += 1) {
    await page.waitForTimeout(6000);
    landed = /habit/i.test(await bodyText(page));
    if (!landed) {
      say(`  idea not visible in transcript (attempt ${attempt}) - resending`);
      const box = page.locator('textarea, input[type=text], [contenteditable=true]').first();
      if (await box.isVisible().catch(() => false)) {
        await box.fill(IDEA).catch(() => {});
        await box.press('Enter').catch(() => {});
      }
    }
  }
  say(landed ? '  idea confirmed' : '  WARNING: idea never appeared in transcript');

  // -- drive the whole journey as far as it will go ----------------------
  const deadline = Date.now() + 78 * 60 * 1000;
  let lastLen = 0;
  let replyIndex = 0;
  let undelivered = 0;
  let lastUrl = page.url();
  let approved = false;
  let quiet = 0;
  let tick = 0;
  let lastReplyTick = -99;
  let dumpedQuiet = false;

  while (Date.now() < deadline) {
    await page.waitForTimeout(15000);
    tick += 1;
    const text = await bodyText(page);
    const url = page.url();

    if (url !== lastUrl) {
      say(`  [t${tick}] URL CHANGED -> ${url}`);
      lastUrl = url;
      await snap(page, `url-change-${tick}`);
    }

    // Advance any gate the journey puts up.
    const clicked = (await clickAdvance(page)) || (await chooseOption(page));
    if (clicked) {
      say(`  [t${tick}] advanced via "${clicked}"  (${await snap(page, `advance-${tick}`)})`);
      if (/approve/i.test(clicked)) approved = true;
      lastLen = 0;
      quiet = 0;
      continue;
    }

    if (text.length > lastLen + 40) {
      say(`  [t${tick}] +${text.length - lastLen} chars (total ${text.length})`);
      lastLen = text.length;
      quiet = 0;
      // Keep answering for the whole journey, not just the opening interview.
      // AppGenerator declares human_in_the_loop with an InterviewAgent of its
      // own, so a harness that goes silent after concept approval stalls there
      // and the run dies without ever reaching a generated app. Rate-limited so
      // a long streaming stage does not get peppered with replies.
      if (tick - lastReplyTick >= 2) {
        const box = page.locator('textarea, input[type=text], [contenteditable=true]').first();
        if (await box.isVisible().catch(() => false)) {
          // Answering "Agreed." to "what do you want to build?" is how a run
          // ends up designing something nobody asked for.
          const restate = asksWhatToBuild(text);
          if (restate) ideaRestatements += 1;
          const reply = restate
            ? IDEA
            : approved
              ? LATE_REPLIES[replyIndex % LATE_REPLIES.length]
              : REPLIES[Math.min(replyIndex, REPLIES.length - 1)];
          await box.fill(reply).catch(() => {});
          await box.press('Enter').catch(() => {});
          say(`  [t${tick}] replied: "${reply}"`);
          replyIndex += 1;
          lastReplyTick = tick;
          lastLen = (await bodyText(page)).length;
        }
      }
      continue;
    }

    // -- quiet: this is the state the previous run died in ---------------
    quiet += 1;
    const ws = [...wsTypes.entries()].map(([k, v]) => `${k}x${v}`).join(' ') || 'none';
    say(`  [t${tick}] QUIET #${quiet} (${text.length} chars) ws: ${ws}`);

    // A still screen with a live composer means the agent already spoke and is
    // waiting on us. Growth-triggered replies alone deadlock here: the rate
    // limit can swallow the one reply the growth tick was owed, and nothing
    // ever grows again because the agent is waiting.
    if (quiet >= 2 && tick - lastReplyTick >= 2) {
      const box = page.locator('textarea, input[type=text], [contenteditable=true]').first();
      if (await box.isVisible().catch(() => false)) {
        const restateNudge = asksWhatToBuild(text);
        if (restateNudge) ideaRestatements += 1;
        const reply = restateNudge
          ? IDEA
          : approved
            ? LATE_REPLIES[replyIndex % LATE_REPLIES.length]
            : REPLIES[Math.min(replyIndex, REPLIES.length - 1)];
        const acted = await box
          .fill(reply)
          .then(() => box.press('Enter'))
          .then(() => true)
          .catch(() => false);
        // Playwright resolves fill/press against a composer that is disabled,
        // readonly, or detached without anything being sent, so a resolved
        // promise is not delivery. Reply text still sitting in the box is proof
        // it was not: the app clears the composer only once it accepts a
        // message. A run logged four nudges while chat.input_ack stayed frozen
        // at 10 - four turns the harness reported and never took - and the
        // stall underneath them read as steady progress for six ticks.
        //
        // Only text that is still *identical* to the reply counts as failure.
        // An empty box is delivery; anything else is some state this check does
        // not understand, and guessing there would trade a false success for a
        // false alarm.
        const left = await box
          .inputValue()
          .catch(() => box.innerText())
          .catch(() => '');
        if (!acted || left.trim() === reply.trim()) {
          undelivered += 1;
          say(`  [t${tick}] nudge NOT delivered, composer kept it: "${reply}"`);
          if (undelivered >= 3) {
            say(`  [t${tick}] STOPPING: ${undelivered} nudges in a row never left the page.`);
            say('  The composer is visible but not accepting input - the run is stalled, not slow.');
            break;
          }
        } else {
          undelivered = 0;
          say(`  [t${tick}] nudged a waiting agent: "${reply}"`);
        }
        replyIndex += 1;
        lastReplyTick = tick;
        quiet = 0;
        lastLen = (await bodyText(page)).length;
        continue;
      }
    }

    if (quiet === 2 && !dumpedQuiet) {
      dumpedQuiet = true;
      say('\n  ===== WHAT THE QUIET SCREEN ACTUALLY SAYS =====');
      say(`  url: ${url}`);
      say('  --- full visible text ---');
      say(text.slice(0, 3000).replace(/\n{2,}/g, '\n'));
      say('  --- end visible text ---');
      const st = await sessionState(page);
      say(`  backend /api/session/state -> ${JSON.stringify(st).slice(0, 1800)}`);
      const btns = await page.getByRole('button').all();
      const labels = [];
      for (const b of btns.slice(0, 30)) {
        const t = (await b.innerText().catch(() => '')).trim().replace(/\s+/g, ' ');
        if (t) labels.push(t.slice(0, 36));
      }
      say(`  clickable buttons on screen: ${JSON.stringify([...new Set(labels)])}`);
      say(`  (${await snap(page, 'the-quiet-screen')})`);
      say('  ================================================\n');
    }

    // Re-check the backend every few quiet ticks: is it working or idle?
    if (quiet % 4 === 0) {
      const st = await sessionState(page);
      say(`  [t${tick}] backend session state: ${JSON.stringify(st).slice(0, 700)}`);
    }
  }

  await snap(page, 'final');

  // -- report ------------------------------------------------------------
  say('\n  ================ TAIL REPORT ================');
  say(`  final url: ${page.url()}`);
  say(`  concept approved: ${approved}`);
  say(`  ws frame types: ${JSON.stringify([...wsTypes.entries()])}`);
  say(`  api errors (${apiErrors.length}): ${JSON.stringify([...new Set(apiErrors)].slice(0, 12))}`);
  if (wsSamples.length) {
    say('  ws samples:');
    wsSamples.slice(0, 12).forEach((s) => say(`    ${s}`));
  }
  const finalText = await bodyText(page);
  say('  --- final screen text ---');
  say(finalText.slice(0, 2500));
  say('  ============================================');

  fs.writeFileSync(LOG, lines.join('\n'), 'utf8');
  console.log(`\n  report written -> ${LOG}`);
});
