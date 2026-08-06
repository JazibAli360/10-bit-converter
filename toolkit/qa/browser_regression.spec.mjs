/*
 * Browser-level regression suite. Run against a local app server with:
 *   npx playwright test toolkit/qa/browser_regression.spec.mjs
 *
 * It uses route fixtures, never real customer media or output folders.
 */
import { test, expect } from '@playwright/test';

const source = {
  path: '/fixtures/gradient.mp4', name: 'gradient.mp4', width: 1920, height: 1080,
  codec: 'h264', bits: 8, fps: 30, dur: 10, kbps: 8000, size: 10_000_000,
  status: 'Queued', pct: '', loading: false,
};
const settings = {dest_mode:'same',dest_dir:'',suffix:'_10bit',on_exists:'skip',crf:18,preset:'slow',deband_range:16,deband_blur:true,dither:2,thr_custom:'0.03',target_mbps:12,deflicker:false,max_quality:false,audio:'copy',denoise:'off',two_pass:false,dual_export:false,live_preview:false,engine:'ffmpeg-deband-v1'};

async function fixtureApi(page) {
  let report = {time:'2026-07-14 12:00:00',done:1,failed:1,skipped:0,cancelled:false,total_in_size:'10 MB',total_out_size:'12 MB',elapsed_sec:4,items:[{source:source.path,output:'/fixtures/gradient_10bit.mp4'}]};
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const json = value => route.fulfill({contentType:'application/json', body:JSON.stringify(value)});
    if (url.pathname === '/api/settings') return json(settings);
    if (url.pathname === '/api/presets') return json([]);
    if (url.pathname === '/api/watch') return json({enabled:false,folder:'',processed:0});
    if (url.pathname === '/api/report') return json(report);
    if (url.pathname === '/api/history') return json({reports:[]});
    if (url.pathname === '/api/preflight') return json({ready:true,on_exists:'skip',collisions:[],blocking:[],warnings:[],total_estimate:10_000_000,disks:[{folder:'/fixtures',free:100_000_000_000,needed:10_000_000}],items:[{name:source.name,format:'HEVC',out:'/fixtures/gradient_10bit.mp4'}]});
    if (url.pathname === '/api/filmstrip') return json({token:'film',times:[]});
    if (url.pathname === '/api/scopes') return json({token:'scope',errors:{},t:1,duration:10});
    if (url.pathname === '/api/compare') return json({token:'compare',duration:10,t:1});
    if (url.pathname === '/api/convert') return json({ok:true});
    if (url.pathname === '/api/status') return json({running:false,items:[],now:{file:'—',pct:0,eta:'--:--'},batch:{total:0}});
    return json({ok:true});
  });
  return {setReport: value => { report = value; }};
}

async function seedQueue(page, items=[source]) {
  await page.evaluate(items => { queue=items.map(item=>({...item})); queueReady=true; selectedRowIndex=null; renderQueue(queue); refreshConvert(); }, items);
}

test.describe('10-bit Converter interaction regression', () => {
  test.beforeEach(async ({page}) => {
    await fixtureApi(page);
    await page.goto(process.env.TENBIT_TEST_URL || 'http://127.0.0.1:8779/');
  });

  test('queue selection, duplicate, and remove keep counts in sync', async ({page}) => {
    await seedQueue(page);
    await page.locator('#queue input[type=checkbox]').check();
    await expect(page.locator('#bulkCount')).toHaveText('1 selected');
    await page.locator('[data-row-actions="0"]').click();
    await page.locator('[data-duplicate="0"]').click();
    await expect(page.locator('.row')).toHaveCount(2);
    await page.locator('[data-row-actions="0"]').click();
    await page.locator('[data-remove="0"]').click();
    await expect(page.locator('.row')).toHaveCount(1);
    await expect(page.locator('#estimate')).toContainText('1 file');
  });

  test('per-video settings show Custom and can reset to global', async ({page}) => {
    await seedQueue(page);
    await page.locator('[data-clip="0"]').click();
    await page.locator('#clipUseDefaults').selectOption('false');
    await page.locator('#clipStrength').selectOption({label:'High'});
    await page.getByRole('button', {name:'Save custom settings'}).click();
    await expect(page.locator('.profile-badge.custom')).toHaveText('CUSTOM');
    await page.locator('[data-resetclip="0"]').click();
    await expect(page.locator('.profile-badge.global')).toHaveText('GLOBAL');
  });

  test('preflight shows an exact output path before conversion', async ({page}) => {
    await seedQueue(page);
    await page.getByRole('button', {name:'Convert'}).click();
    await expect(page.locator('#mPreflight')).toBeVisible();
    await expect(page.locator('#preflightBody')).toContainText('/fixtures/gradient_10bit.mp4');
  });

  test('scopes and comparison open from row actions', async ({page}) => {
    await seedQueue(page, [{...source,status:'Done',out:'/fixtures/gradient_10bit.mp4'}]);
    await page.locator('[data-row-actions="0"]').click();
    await page.locator('[data-scopes="0"]').click();
    await expect(page.locator('#mScopes')).toBeVisible();
    await page.locator('#mScopes .ghost').click();
    await page.locator('[data-row-actions="0"]').click();
    await page.locator('[data-cmp="0"]').click();
    await expect(page.locator('#mCompare')).toBeVisible();
  });

  test('completion result offers reveal/retry controls', async ({page}) => {
    await page.evaluate(() => showBatchSummary());
    await expect(page.locator('#completionPanel')).toBeVisible();
    await expect(page.locator('#completionStats')).toContainText('1 completed');
    await expect(page.locator('#completionRetry')).toBeVisible();
  });

  test('680 px layout has no horizontal overflow and keeps Convert reachable', async ({page}) => {
    await page.setViewportSize({width:680,height:900});
    await expect(page.locator('#btnConvert')).toBeVisible();
    await expect(page.locator('html')).toHaveJSProperty('scrollWidth', 680);
  });
});
