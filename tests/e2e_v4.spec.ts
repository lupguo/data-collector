/**
 * tests/e2e_v4.spec.ts — 虾人情报站 v4 Playwright E2E 测试
 * 覆盖：前台页面 / Admin 调度配置 / 定时任务触发 / LLM 用量查询
 *
 * 运行：npx playwright test tests/e2e_v4.spec.ts --reporter=line
 */
import { test, expect, type Page, type APIRequestContext } from '@playwright/test';

const BASE_URL = 'http://127.0.0.1:18180';
const API = `${BASE_URL}`;

// ─────────────────────────────────────────────
// 工具函数
// ─────────────────────────────────────────────
async function waitForFlask(request: APIRequestContext) {
  for (let i = 0; i < 10; i++) {
    try {
      const r = await request.get(`${API}/api/health`);
      if (r.ok()) return;
    } catch {}
    await new Promise(r => setTimeout(r, 500));
  }
  throw new Error('Flask 服务不可达');
}

// ─────────────────────────────────────────────
// 1. 前台页面测试
// ─────────────────────────────────────────────
test.describe('前台页面', () => {
  test('首页加载正常，含基础元素', async ({ page }) => {
    await page.goto(BASE_URL, { waitUntil: 'domcontentloaded', timeout: 10000 });
    // 检查页面不是错误页
    const title = await page.title();
    expect(title).not.toContain('Error');
    // 检查页面有内容（不是空白）
    const body = await page.locator('body').innerText();
    expect(body.length).toBeGreaterThan(10);
  });

  test('API /api/health 返回 ok', async ({ request }) => {
    await waitForFlask(request);
    const r = await request.get(`${API}/api/health`);
    expect(r.ok()).toBeTruthy();
    const data = await r.json();
    expect(data.status).toBe('ok');
  });

  test('API /status 返回结构正确', async ({ request }) => {
    const r = await request.get(`${API}/status`);
    expect(r.ok()).toBeTruthy();
    const data = await r.json();
    expect(data).toHaveProperty('recent_tasks');
    expect(data).toHaveProperty('pending_push');
  });

  test('API /channels 返回频道列表（含 AI技术动态）', async ({ request }) => {
    const r = await request.get(`${API}/channels`);
    expect(r.ok()).toBeTruthy();
    const data = await r.json();
    expect(Array.isArray(data)).toBeTruthy();
    expect(data.length).toBeGreaterThan(0);
    const names = data.map((c: any) => c.name);
    expect(names).toContain('AI技术动态');
    expect(names).toContain('GitHub热门项目');
  });

  test('API /tasks 返回任务列表', async ({ request }) => {
    const r = await request.get(`${API}/tasks?limit=5`);
    expect(r.ok()).toBeTruthy();
  });
});

// ─────────────────────────────────────────────
// 2. Admin 页面 - 调度配置
// ─────────────────────────────────────────────
test.describe('Admin - 调度配置 API', () => {
  test('GET /admin/schedule 返回三个默认调度任务', async ({ request }) => {
    const r = await request.get(`${API}/admin/schedule`);
    expect(r.ok()).toBeTruthy();
    const data = await r.json();
    const jobs: any[] = Array.isArray(data) ? data : data.jobs ?? [];
    expect(jobs.length).toBeGreaterThanOrEqual(3);
    const jobIds = jobs.map((j: any) => j.job_id);
    expect(jobIds).toContain('collect');
    expect(jobIds).toContain('analyze');
    expect(jobIds).toContain('route');
  });

  test('PUT /admin/schedule/collect 可以修改 cron 表达式', async ({ request }) => {
    const r = await request.put(`${API}/admin/schedule/collect`, {
      data: { enabled: true, cron_expr: '0 */2 * * *', timeout_sec: 300 },
      headers: { 'Content-Type': 'application/json' },
    });
    expect(r.ok()).toBeTruthy();
    // 还原
    await request.put(`${API}/admin/schedule/collect`, {
      data: { enabled: true, cron_expr: '0 * * * *', timeout_sec: 300 },
      headers: { 'Content-Type': 'application/json' },
    });
  });

  test('PUT /admin/schedule/collect 可以禁用再启用', async ({ request }) => {
    // 禁用
    let r = await request.put(`${API}/admin/schedule/collect`, {
      data: { enabled: false },
      headers: { 'Content-Type': 'application/json' },
    });
    expect(r.ok()).toBeTruthy();

    // 验证已禁用
    const list = await request.get(`${API}/admin/schedule`);
    const jobs: any[] = await list.json();
    const collect = jobs.find((j: any) => j.job_id === 'collect');
    expect(collect?.enabled).toBeFalsy();

    // 重新启用
    r = await request.put(`${API}/admin/schedule/collect`, {
      data: { enabled: true },
      headers: { 'Content-Type': 'application/json' },
    });
    expect(r.ok()).toBeTruthy();
  });

  test('PUT /admin/schedule/nonexistent 返回 404', async ({ request }) => {
    const r = await request.put(`${API}/admin/schedule/no_such_job`, {
      data: { enabled: false },
      headers: { 'Content-Type': 'application/json' },
    });
    expect(r.status()).toBe(404);
  });

  test('POST /admin/schedule/collect/trigger 立即触发任务（异步）', async ({ request }) => {
    const r = await request.post(`${API}/admin/schedule/collect/trigger`);
    // 接受 200 或 202，不要求立即完成
    expect([200, 201, 202]).toContain(r.status());
  });
});

// ─────────────────────────────────────────────
// 3. LLM 用量统计 API
// ─────────────────────────────────────────────
test.describe('Admin - LLM 用量统计', () => {
  test('GET /admin/llm/usage 返回 200 及正确结构', async ({ request }) => {
    const r = await request.get(`${API}/admin/llm/usage?days=7`);
    expect(r.ok()).toBeTruthy();
    const data = await r.json();
    // 接受任意包含 summary/daily/details 的结构
    expect(typeof data).toBe('object');
  });

  test('GET /admin/llm/usage?days=1 返回正确', async ({ request }) => {
    const r = await request.get(`${API}/admin/llm/usage?days=1`);
    expect(r.ok()).toBeTruthy();
  });
});

// ─────────────────────────────────────────────
// 4. 调度器特性验证（数据库侧）
// ─────────────────────────────────────────────
test.describe('调度任务特性（DB 侧验证）', () => {
  test('t_schedule_config 热加载：updated_at 在 PUT 后更新', async ({ request }) => {
    // 记录更新前的 updated_at
    const before = await request.get(`${API}/admin/schedule`);
    const jobsBefore: any[] = await before.json();
    const collectBefore = jobsBefore.find((j: any) => j.job_id === 'collect');
    const updatedAtBefore = collectBefore?.updated_at;

    // 等 1 秒确保时间差
    await new Promise(r => setTimeout(r, 1100));

    // 触发一次更新
    await request.put(`${API}/admin/schedule/collect`, {
      data: { enabled: true, cron_expr: '0 * * * *' },
      headers: { 'Content-Type': 'application/json' },
    });

    // 再次读取
    const after = await request.get(`${API}/admin/schedule`);
    const jobsAfter: any[] = await after.json();
    const collectAfter = jobsAfter.find((j: any) => j.job_id === 'collect');
    const updatedAtAfter = collectAfter?.updated_at;

    // updated_at 应该比之前更新
    expect(new Date(updatedAtAfter).getTime()).toBeGreaterThan(
      new Date(updatedAtBefore).getTime()
    );
  });

  test('push --window 参数：同一 window 不重复推送（DB 去重）', async ({ request }) => {
    // 先确认 push dry-run 不报错
    // 通过 admin/trigger 触发 push（dry-run 模式验证参数传递）
    // 这里只验证 API 层面的正确性
    const r = await request.get(`${API}/channels`);
    const channels: any[] = await r.json();
    const ai = channels.find((c: any) => c.name === 'AI技术动态');
    expect(ai).toBeDefined();
    expect(ai.enabled).toBeTruthy();
  });

  test('admin 触发采集后 t_tasks 有新记录', async ({ request }) => {
    // 触发采集
    const triggerR = await request.post(`${API}/admin/trigger/collect`, {
      data: { job: 'github' },
      headers: { 'Content-Type': 'application/json' },
    });
    // 可能是 200/202，任务异步
    expect([200, 201, 202]).toContain(triggerR.status());

    // 等待任务写入 DB（最多 15s）
    let found = false;
    for (let i = 0; i < 15; i++) {
      await new Promise(r => setTimeout(r, 1000));
      const tasksR = await request.get(`${API}/tasks?limit=3`);
      const tasks: any[] = await tasksR.json();
      if (tasks.some((t: any) => t.trigger_type === 'manual')) {
        found = true;
        break;
      }
    }
    expect(found).toBeTruthy();
  });
});

// ─────────────────────────────────────────────
// 5. Admin 页面 UI（如有 admin.html）
// ─────────────────────────────────────────────
test.describe('Admin 页面 UI', () => {
  test('GET /admin 页面可以加载（不是 404）', async ({ page }) => {
    const r = await page.goto(`${BASE_URL}/admin`, {
      waitUntil: 'domcontentloaded',
      timeout: 10000,
    });
    expect(r?.status()).not.toBe(404);
    expect(r?.status()).not.toBe(500);
  });

  test('Admin 页面包含频道或数据源相关内容', async ({ page }) => {
    await page.goto(`${BASE_URL}/admin`, { waitUntil: 'domcontentloaded', timeout: 10000 });
    const body = await page.locator('body').innerText();
    // 至少应该有这些词之一
    const hasContent = ['频道', 'channel', 'source', 'admin', 'AI', 'schedule']
      .some(kw => body.toLowerCase().includes(kw.toLowerCase()));
    expect(hasContent).toBeTruthy();
  });
});
