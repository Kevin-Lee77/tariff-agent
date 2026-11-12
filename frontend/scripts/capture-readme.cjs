/* Capture only the local synthetic fixture; credentials never enter images or stdout. */
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium, expect} = require('@playwright/test');
const root = path.resolve(__dirname, '../..');
const images = path.join(root, 'docs/images');
const base = 'http://127.0.0.1:18001';

(async () => {
  fs.mkdirSync(images, {recursive: true});
  const runtime = path.join(root, '.runtime');
  fs.mkdirSync(runtime, {recursive: true});
  const log = fs.openSync(path.join(runtime, 'readme-capture.log'), 'w');
  const python = process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python';
  const child = spawn(path.join(root, python), ['scripts/serve_e2e.py'], {
    cwd: root, env: {...process.env, PYTHONPATH: path.join(root, 'src')},
    stdio: ['ignore', log, log],
  });
  const browser = await chromium.launch({channel: 'chromium', headless: true});
  const page = await browser.newPage({viewport: {width: 1536, height: 1080}, deviceScaleFactor: 1, locale: 'zh-CN', timezoneId: 'Asia/Shanghai'});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    let ready = false;
    for (let i = 0; i < 100; i++) {
      if (child.exitCode !== null) throw new Error('Synthetic server exited; check the private local log');
      try {
        const response = await page.request.get(base + '/health/live', {timeout: 500});
        if (response.ok()) { ready = true; break; }
      } catch {}
      await new Promise(resolve => setTimeout(resolve, 200));
    }
    if (!ready) throw new Error('Synthetic fixture did not start');
    const credential = JSON.parse(fs.readFileSync(path.join(runtime, 'e2e/credentials.json'), 'utf8'));
    expect((await page.request.post(base + '/auth/login', {data: credential})).status()).toBe(200);
    const knowledge = await (await page.request.get(base + '/v1/knowledge/summary')).json();
    expect(knowledge.version).toBe('synthetic-browser-test');
    expect(knowledge.test_only).toBe(true);
    await page.goto(base + '/');
    await page.addStyleTag({content: '.toast { visibility: hidden !important; }'});
    await expect(page.getByRole('heading', {name: '工作概览', exact: true})).toBeVisible();
    await page.screenshot({path: path.join(images, 'console-overview.png'), fullPage: true});

    await page.getByRole('button', {name: '规范申报', exact: true}).click();
    await page.getByRole('combobox', {name: '词典环境'}).selectOption('sandbox');
    await page.getByRole('textbox', {name: '申报原文', exact: true}).fill('税号：0202300090；品名：变质牛肉；批次：SYNTHETIC-001');
    await page.getByRole('button', {name: '提交风险校验', exact: true}).click();
    await expect(page.getByRole('heading', {name: '税智源 · 退回企业整改'})).toBeVisible({timeout: 30000});
    await expect(page.locator('mark')).toHaveText('变质');
    await page.screenshot({path: path.join(images, 'risk-review.png')});
    await page.getByRole('button', {name: '关闭任务详情'}).click();

    await page.getByRole('button', {name: '术语治理', exact: true}).click();
    await expect(page.getByRole('heading', {name: '税智语 · 申报文本处理'})).toBeVisible();
    await page.getByRole('combobox', {name: '词典环境'}).selectOption('sandbox');
    await page.getByRole('textbox', {name: '申报原文', exact: true}).fill('冻制剔骨牛肉，不含添加剂；批次：SYNTHETIC-002');
    await page.getByRole('button', {name: '处理申报文本', exact: true}).click();
    await expect(page.getByRole('dialog')).toBeVisible({timeout: 30000});
    await expect(page.getByRole('dialog')).toContainText('冷冻');
    await page.screenshot({path: path.join(images, 'terminology.png')});
    await page.getByRole('button', {name: '关闭任务详情'}).click();

    await page.getByRole('button', {name: '评测中心', exact: true}).click();
    await expect(page.getByRole('heading', {name: '数据隔离与实验'})).toBeVisible();
    await page.screenshot({path: path.join(images, 'evaluation.png'), fullPage: true});
    await page.getByRole('button', {name: '业务数据与词典', exact: true}).click();
    await page.getByLabel('导入词典名称', {exact: true}).fill('公开展示合成词典');
    await page.getByLabel('导入数据文件', {exact: true}).setInputFiles({name: 'synthetic-terms.csv', mimeType: 'text/csv', buffer: Buffer.from('term,canonical,element_code,scope\n冻制,冷冻,STORAGE,02\n冷冻,冷冻,STORAGE,02\n')});
    await page.getByRole('button', {name: '上传并校验', exact: true}).click();
    await expect(page.locator('.batch-detail')).toContainText('整批校验通过，已入库', {timeout: 30000});
    await page.locator('section').filter({has: page.getByRole('heading', {name: '批量导入与导出', exact: true})}).screenshot({path: path.join(images, 'business-batches.png')});
    await page.setViewportSize({width: 1440, height: 1200});
    for (const name of ['architecture', 'workflow']) {
      await page.goto('file://' + path.join(images, name + '.svg'));
      const svg = page.locator('svg');
      await svg.screenshot({path: path.join(runtime, name + '-preview.png')});
    }
    expect(errors).toEqual([]);
    console.log(JSON.stringify({screenshots: 5, diagram_previews: 2, synthetic_only: true, frontend_errors: 0}));
  } finally {
    await browser.close();
    child.kill('SIGTERM');
    await new Promise(resolve => child.exitCode === null ? child.once('exit', resolve) : resolve());
    fs.closeSync(log);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
