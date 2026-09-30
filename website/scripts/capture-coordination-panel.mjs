/**
 * Screenshot harness for the chat side panel's Coordination view.
 *
 * The panel renders the group snapshot from GET /api/projects/{id}/panel in the
 * chosen order — header → Members → Work → Collisions — so a still image is the
 * only way to confirm the section order, the coordinator badge, the work state
 * pills, and the collision footer actually lay out as designed rather than just
 * mount (which the vitest suite proves).
 *
 * Runs the REAL built SPA (website/dist) against a static file server with every
 * /api/** call answered from fixtures by Playwright. No gateway, no dashboard
 * token, nothing written outside the out dir. The client code under test is
 * unmodified — only the network and the localStorage seed are stubbed. The slot
 * row carries `project_group_id`, which is what ChatPage threads down to the
 * panel, so the fixture must set it or the panel renders the untagged empty
 * state.
 *
 * Usage: node scripts/capture-coordination-panel.mjs <baseUrl> <outDir>
 */
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const BASE = process.argv[2] || 'http://127.0.0.1:6811'
const OUT = process.argv[3] || '../temp-screenshots/coordination-panel'
const SLOT = 'chat-coordination'
const GROUP = 'grp-aidlc'
const PROJECT = '/home/user/workspace/KiroCrew'

mkdirSync(OUT, { recursive: true })

const slots = [{
  key: SLOT,
  title: 'Coordinate the aidlc migration',
  running: false,
  last_message: 'Panel wired.',
  messages: 2,
  agent: 'kirocrew',
  memory_mode: 'persistent',
  project: PROJECT,
  project_group_id: GROUP,
  modified: Math.floor(Date.now() / 1000),
  source_links: [],
  source_links_total: 0,
}]

const detail = {
  running: false, has_more: false, total: 1, queue: [], project: PROJECT,
  project_group_id: GROUP,
  messages: [{ role: 'user', ts: Date.now() / 1000 - 60, content: 'Show the coordination panel.' }],
}

/** A populated group: a coordinator + two workers, two work items (one merged
 *  with a PR, one open), and a same-worktree collision between the workers. */
const panelPopulated = {
  project: { id: GROUP, name: 'aidlc-migration' },
  sessions: [
    { session: 'sess-conductor', title: 'Conductor', agent: 'kirocrew', branch: 'feature/plan', is_coordinator: true },
    { session: 'sess-a', title: 'Worker A', agent: 'kirocrew', branch: 'feature/api' },
    { session: 'sess-b', title: 'Worker B', agent: 'kirocrew', branch: 'feature/ui' },
  ],
  work: [
    { coordinator: 'sess-conductor', item_id: 'w1', title: 'Wire the API layer', state: 'accepted', status: 'done', summary: '', pr: 42, round: 1, worker: 'sess-a' },
    { coordinator: 'sess-conductor', item_id: 'w2', title: 'Build the coordination panel UI', state: 'open', status: 'in progress', summary: '', pr: null, round: 1, worker: 'sess-b' },
  ],
  collisions: [
    { signal: 'same-worktree', sessions: ['sess-a', 'sess-b'] },
  ],
}

/** A tagged-but-quiet group: the empty state a solo tagged session sees. */
const panelEmpty = { project: { id: GROUP, name: 'aidlc-migration' }, sessions: [], work: [], collisions: [] }

const json = (route, body, status = 200) => route.fulfill({
  status, contentType: 'application/json', body: JSON.stringify(body),
})

const scene = { theme: 'light', panel: panelPopulated }

async function main() {
  const browser = await chromium.launch()
  const context = await browser.newContext({
    viewport: { width: 1500, height: 950 },
    deviceScaleFactor: 2,
  })
  const page = await context.newPage()

  await page.routeWebSocket(/\/api\/ws/, () => {})

  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url())
    const path = url.pathname
    if (/\/api\/projects\/[^/]+\/panel$/.test(path)) return json(route, scene.panel)
    if (path === '/api/chat/slots') return json(route, slots)
    if (path === '/api/artifacts/session-docs') return json(route, { docs: [] })
    if (path === '/api/artifacts') return json(route, { artifacts: [] })
    if (path.startsWith('/api/chat/slots/')) return json(route, detail)
    if (path.startsWith('/api/instances')) return json(route, { instances: [], active: '' })
    if (path.startsWith('/api/kiro-prerequisite')) {
      return json(route, {
        platform: 'linux', installed: true, authenticated: true, ready: true,
        initial_setup_complete: true, can_auto_install: false, can_login: false,
        repair_required: false, docs_url: '', setup_allowed: false,
        operation: { status: 'idle', message: '' },
      })
    }
    const shell = {
      '/api/status': () => ({ sessions: 1, crons: 0, lessons: 0, uptime: 120, version: 'dev' }),
      '/api/notifications': () => ({ notifications: [], unread: 0 }),
      '/api/auth/me': () => ({ user: 'owner', app: '' }),
      '/api/models': () => ({ models: [], default: 'auto' }),
      '/api/themes': () => ({ themes: [], installed: [] }),
      '/api/theme/boot': () => ({ mode: scene.theme, theme: '' }),
      '/api/dashboard/branding': () => ({ bot_name: 'Kiro', avatar: '' }),
      '/api/recent-projects': () => ({ dirs: [PROJECT] }),
      '/api/chat/nav/resolve-links': () => ({ summaries: [] }),
    }
    if (shell[path]) return json(route, shell[path]())
    if (/(config|tips|voice|autonudge|branding|status|usage-summary)/.test(path)) return json(route, {})
    return json(route, [])
  })

  page.on('pageerror', err => console.log('PAGEERROR:', String(err).slice(0, 300)))
  page.on('console', msg => {
    if (msg.type() === 'error') console.log('CONSOLE:', msg.text().slice(0, 300))
  })

  async function load(theme, panel) {
    scene.theme = theme
    scene.panel = panel
    await page.addInitScript(({ t, slot }) => {
      localStorage.clear()
      localStorage.setItem('mc-theme', t)
      localStorage.setItem('mc-onboarded', '1')
      localStorage.setItem('mc-active-slot', slot)
      localStorage.setItem('mc-activity-open:' + slot, 'true')
      localStorage.setItem('mc-privacy-notice-v1', '1')
      localStorage.setItem('mc-panel-tabs:' + slot, JSON.stringify({
        tabs: [{ id: 'coordination', kind: 'coordination', title: 'Coordination' }],
        activeId: 'coordination',
      }))
    }, { t: theme, slot: SLOT })
    await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(2600)
  }

  /** Crop to the coordination panel: the custom header down through its body. */
  async function shot(name) {
    // The panel header carries the project name; wait for it, then crop the
    // panel column (right ~460px band) so the shot is the surface under test.
    await page.getByText('aidlc-migration').first().waitFor({ timeout: 8000 })
    await page.waitForTimeout(500)
    const x = 1500 - 470
    await page.screenshot({
      path: `${OUT}/${name}.png`,
      clip: { x, y: 40, width: 470, height: 900 - 40 },
    })
    console.log('wrote', `${OUT}/${name}.png`)
  }

  for (const theme of ['light', 'dark']) {
    await load(theme, panelPopulated)
    await shot(`01-populated-${theme}`)
  }
  await load('light', panelEmpty)
  await shot('02-empty-light')

  await browser.close()
}

main().catch(err => { console.error(err); process.exit(1) })
