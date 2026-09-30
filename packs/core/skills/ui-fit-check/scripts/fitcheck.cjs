#!/usr/bin/env node
// Visual fit check: loads every route in a real browser and fails on anything a person would see
// as broken layout. Built 2026-09-28 after the NoRCON v3.1 review, where a green overlap check
// still shipped text spilling out of cards, table cells cut to "Blu…", a due date printed over
// the next column and a chart row hanging below its card.
//
// Usage:
//   node fitcheck.cjs --base http://localhost:5181 --routes routes.txt [--widths 1512,1280]
//        [--zooms 0.75,1] [--init '{"norcon-tenant":"anesthesia"}'] [--shots DIR] [--json out.json]
//        [--wait 1800] [--warn small,trunc]
// routes.txt: one path per line; `# comment` lines are skipped. A line may add a click to run
// before measuring:  /v3/work/records  >>  tbody tr:first-child
// Exit code: 0 when every page is clean, 1 when any finding remains, 2 on a setup error.
//
// Rules (each finding names the rule, the text and the numbers):
//   error        an uncaught page error
//   hscroll      the page, or a table's scroll box, scrolls sideways
//   overlap      two runs of text in different elements overlap by more than 2px each way
//   cell         text runs past the edge of its table cell (td, th, role=cell/gridcell)
//   box          text runs past the edge of its card or panel (border or glass, rounded)
//   cut          text is cut off by a clipping box without an ellipsis
//   trunc        an ellipsis hides too much: an id-like value, a label of 16 characters or
//                fewer, or less than half the text. Mark a deliberate one data-truncate-ok.
//   wrap         a column header, tab, chip or button breaks onto a second line
//   small        text renders under 10.5 screen pixels
//   spill        a fixed-height list ([data-fit]) holds more than it shows
const fs = require('fs')
const path = require('path')

function arg(name, dflt) {
  const i = process.argv.indexOf('--' + name)
  return i > 0 ? process.argv[i + 1] : dflt
}
function loadPlaywright() {
  const tries = ['playwright', '@playwright/test']
  for (const t of tries) { try { return require(t) } catch { /* next */ } }
  // The npx cache holds a copy on this machine; find the newest.
  const npx = path.join(process.env.HOME || '', '.npm/_npx')
  if (fs.existsSync(npx)) {
    for (const d of fs.readdirSync(npx)) {
      const p = path.join(npx, d, 'node_modules/playwright')
      if (fs.existsSync(p)) return require(p)
    }
  }
  console.error('fitcheck: playwright not found. Run `npx playwright --version` once, or install it.')
  process.exit(2)
}
function chromePath() {
  const root = path.join(process.env.HOME || '', 'Library/Caches/ms-playwright')
  if (!fs.existsSync(root)) return undefined
  const dirs = fs.readdirSync(root).filter((d) => d.startsWith('chromium-')).sort().reverse()
  for (const d of dirs) {
    const mac = path.join(root, d, 'chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing')
    if (fs.existsSync(mac)) return mac
  }
  return undefined
}

// Runs inside the page. Returns the findings for the visible document.
function measure() {
  const zoom = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--app-zoom')) || 1
  const root = document.getElementById('main') || document.body
  const F = []
  const add = (rule, text) => { if (F.filter((f) => f.rule === rule).length < 8) F.push({ rule, text }) }
  const visible = (el) => {
    for (let e = el; e && e !== document.body; e = e.parentElement) {
      const cs = getComputedStyle(e)
      if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) < 0.05) return false
      if (e.getAttribute('aria-hidden') === 'true' && e.tagName !== 'svg') return false
      if (e.getAttribute('role') === 'tooltip' || e.hasAttribute('data-radix-popper-content-wrapper')) return false
      if (e.classList && (e.classList.contains('sr-only'))) return false
    }
    return true
  }
  const clips = (cs) => cs.overflowX !== 'visible' || cs.overflowY !== 'visible'
  const scrolls = (cs) => /auto|scroll/.test(cs.overflowX + cs.overflowY)
  const short = (t) => t.replace(/\s+/g, ' ').trim().slice(0, 32)

  if (document.documentElement.scrollWidth > innerWidth + 1) add('hscroll', `page ${document.documentElement.scrollWidth} > ${innerWidth}`)
  for (const t of root.querySelectorAll('table')) {
    if (!visible(t)) continue
    for (let e = t.parentElement; e && e !== root; e = e.parentElement) {
      const cs = getComputedStyle(e)
      if (/auto|scroll/.test(cs.overflowX) && e.scrollWidth > e.clientWidth + 2) { add('hscroll', `table box ${e.scrollWidth} > ${e.clientWidth}`); break }
    }
  }
  for (const el of root.querySelectorAll('[data-fit]')) {
    if (el.offsetParent && el.scrollHeight > el.clientHeight + 3) add('spill', `${el.clientHeight} < ${el.scrollHeight}`)
  }

  const runs = []
  let small = 0
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const t = n.textContent.trim(); if (t.length < 2) continue
    const el = n.parentElement; if (!el || !visible(el)) continue
    const rg = document.createRange(); rg.selectNodeContents(n)
    const raw = rg.getBoundingClientRect(); if (raw.width < 2 || raw.height < 2) continue
    if (raw.bottom < 0 || raw.top > innerHeight * 4) continue
    const lines = rg.getClientRects().length
    // What actually shows after every clipping ancestor, and whether it scrolls.
    let r = { l: raw.left, t: raw.top, r: raw.right, b: raw.bottom }
    let clipper = null, inScroll = false
    for (let e = el; e && e !== document.documentElement; e = e.parentElement) {
      const cs = getComputedStyle(e)
      if (clips(cs)) {
        if (scrolls(cs)) inScroll = true
        else if (!clipper) clipper = e
        const c = e.getBoundingClientRect()
        r = { l: Math.max(r.l, c.left), t: Math.max(r.t, c.top), r: Math.min(r.r, c.right), b: Math.min(r.b, c.bottom) }
      }
    }
    if (r.r - r.l < 2 || r.b - r.t < 2) continue
    const fsz = parseFloat(getComputedStyle(el).fontSize) * zoom
    if (fsz < 10.5) small++
    runs.push({ el, t: short(t), raw, r, lines, clipper, inScroll })
  }
  if (small) add('small', `${small} runs under 10.5px`)

  // overlap
  for (let i = 0; i < runs.length; i++) for (let j = i + 1; j < runs.length; j++) {
    const a = runs[i], c = runs[j]
    if (a.lines > 1 || c.lines > 1) continue
    const ix = Math.min(a.r.r, c.r.r) - Math.max(a.r.l, c.r.l), iy = Math.min(a.r.b, c.r.b) - Math.max(a.r.t, c.r.t)
    if (ix <= 2 || iy <= 2) continue
    if (a.el === c.el || a.el.contains(c.el) || c.el.contains(a.el)) continue
    add('overlap', `"${a.t}" over "${c.t}" (${Math.round(ix)}x${Math.round(iy)}px)`)
  }

  const TOL = 2
  for (const u of runs) {
    // cell: the text's own box against its cell
    const cell = u.el.closest('td,th,[role=cell],[role=gridcell],[role=columnheader]')
    if (cell && root.contains(cell)) {
      const c = cell.getBoundingClientRect()
      if (u.raw.right > c.right + TOL || u.raw.left < c.left - TOL || u.raw.bottom > c.bottom + TOL) {
        const clipped = u.r.r < u.raw.right - 1 || u.r.b < u.raw.bottom - 1
        if (!clipped || !getComputedStyle(u.el).textOverflow.includes('ellipsis')) add('cell', `"${u.t}" runs ${Math.round(Math.max(u.raw.right - c.right, u.raw.bottom - c.bottom, c.left - u.raw.left))}px past its cell`)
      }
    }
    // box: the text against the nearest card or panel
    let box = null
    for (let e = u.el.parentElement; e && e !== root; e = e.parentElement) {
      const cs = getComputedStyle(e)
      const rounded = parseFloat(cs.borderTopLeftRadius) >= 8
      const framed = parseFloat(cs.borderTopWidth) > 0 || e.classList.contains('glass-panel') || (cs.backgroundColor !== 'rgba(0, 0, 0, 0)' && cs.backgroundColor !== 'transparent')
      if (rounded && framed && e.getBoundingClientRect().width > 120) { box = e; break }
    }
    if (box && !u.inScroll) {
      const c = box.getBoundingClientRect()
      const past = Math.max(u.raw.bottom - c.bottom, u.raw.right - c.right, c.left - u.raw.left, c.top - u.raw.top)
      if (past > -3 && !getComputedStyle(u.el).textOverflow.includes('ellipsis')) add('box', past > TOL ? `"${u.t}" runs ${Math.round(past)}px outside its card` : `"${u.t}" touches the edge of its card`)
    }
  }

  // cut, trunc, wrap: per element
  for (const el of root.querySelectorAll('*')) {
    if (!el.firstChild || !visible(el)) continue
    const t = (el.textContent || '').replace(/\s+/g, ' ').trim(); if (t.length < 2) continue
    const cs = getComputedStyle(el)
    const ellipsis = cs.textOverflow.includes('ellipsis') || cs.webkitLineClamp !== 'none' && cs.webkitLineClamp !== ''
    if (el.children.length <= 2 && clips(cs) && !scrolls(cs) && el.clientWidth > 0) {
      const over = el.scrollWidth > el.clientWidth + 2
      if (over && !ellipsis && !el.hasAttribute('data-clip-ok')) add('cut', `"${short(t)}" ${el.scrollWidth}>${el.clientWidth}`)
      if (over && ellipsis && !el.closest('[data-truncate-ok]')) {
        const frac = el.clientWidth / el.scrollWidth
        const idLike = /^[A-Z]{2,5}-[\dA-Z-]{3,}/.test(t) || /^\d{5,}$/.test(t)
        if (idLike || t.length <= 16 || frac < 0.5) add('trunc', `"${short(t)}" shows ${Math.round(frac * 100)}%`)
      }
    }
    const isHead = el.matches('th,[role=columnheader],[role=tab],button,[role=radio]')
    if (isHead && el.getBoundingClientRect().width > 0 && t.length < 60) {
      // A single run of text inside the control that breaks onto a second line.
      const tw = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
      for (let n = tw.nextNode(); n; n = tw.nextNode()) {
        if (n.textContent.trim().length < 2) continue
        const rg = document.createRange(); rg.selectNodeContents(n)
        const tops = new Set([...rg.getClientRects()].filter((q) => q.width > 1).map((q) => Math.round(q.top)))
        if (tops.size > 1) { add('wrap', `"${short(t)}" breaks onto ${tops.size} lines`); break }
      }
    }
  }
  return F
}

;(async () => {
  const base = arg('base')
  const routesFile = arg('routes')
  if (!base || !routesFile) { console.error('usage: fitcheck.cjs --base URL --routes FILE [--widths 1512,1280] [--zooms 1] [--init JSON] [--shots DIR] [--json FILE]'); process.exit(2) }
  const widths = arg('widths', '1512').split(',').map(Number)
  const zooms = arg('zooms', '').split(',').filter(Boolean)
  const init = JSON.parse(arg('init', '{}'))
  const shots = arg('shots', '')
  const jsonOut = arg('json', '')
  const wait = Number(arg('wait', '1800'))
  // Rules reported but not counted as failures, e.g. --warn small when the app runs at a reduced zoom on purpose.
  const warnOnly = new Set(arg('warn', '').split(',').filter(Boolean))
  const height = Number(arg('height', '900'))
  const lines = fs.readFileSync(routesFile, 'utf8').split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('#'))
  const { chromium } = loadPlaywright()
  const b = await chromium.launch({ executablePath: chromePath() })
  if (shots) fs.mkdirSync(shots, { recursive: true })
  const report = []
  let bad = 0
  for (const w of widths) for (const z of (zooms.length ? zooms : [null])) {
    const ctx = await b.newContext({ viewport: { width: w, height } })
    const store = { ...init }; if (z) store['norcon-zoom'] = z
    await ctx.addInitScript((kv) => { try { for (const [k, v] of Object.entries(kv)) localStorage.setItem(k, v) } catch { /* private */ } }, store)
    const p = await ctx.newPage()
    const errs = []; p.on('pageerror', (e) => errs.push(String(e).slice(0, 160)))
    for (const line of lines) {
      const [route, click] = line.split('>>').map((s) => s.trim())
      errs.length = 0
      try { await p.goto(base + route, { waitUntil: 'load', timeout: 30000 }) } catch (e) { report.push({ route, w, z, findings: [{ rule: 'error', text: 'navigation ' + String(e).slice(0, 80) }] }); bad++; continue }
      await p.waitForFunction(() => (document.getElementById('root')?.innerHTML.length || document.body.innerHTML.length) > 3000, null, { timeout: 20000 }).catch(() => {})
      await p.waitForTimeout(wait)
      if (click) { try { await p.click(click, { timeout: 5000 }); await p.waitForTimeout(wait) } catch (e) { errs.push('click failed: ' + click) } }
      const findings = await p.evaluate(measure)
      for (const e of errs) findings.unshift({ rule: 'error', text: e })
      report.push({ route: line, w, z, findings })
      const tag = `${w}${z ? ' z' + z : ''}`
      const failing = findings.filter((f) => !warnOnly.has(f.rule))
      if (failing.length) { bad++; console.log(`✗ ${tag} ${line}`); for (const f of failing) console.log(`    ${f.rule.padEnd(7)} ${f.text}`) }
      else console.log(`✓ ${tag} ${line}${findings.length ? `  (warnings: ${findings.map((f) => f.rule).join(', ')})` : ''}`)
      if (shots) await p.screenshot({ path: path.join(shots, `${w}${z ? '-z' + z : ''}-${line.replace(/[^a-z0-9]+/gi, '_').slice(0, 80)}.png`), fullPage: false })
    }
    await ctx.close()
  }
  await b.close()
  if (jsonOut) fs.writeFileSync(jsonOut, JSON.stringify(report, null, 2))
  console.log(`\n${bad} of ${report.length} page views have findings`)
  process.exit(bad ? 1 : 0)
})().catch((e) => { console.error(e); process.exit(2) })
