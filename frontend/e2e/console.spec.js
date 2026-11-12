import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { randomUUID } from "node:crypto";

test("business CSV import exposes invalid rows and never applies partial data", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await login(page);
  await page.goto("/");
  await page
    .getByRole("button", { name: "业务数据与词典", exact: true })
    .click();
  await page.getByLabel("导入词典名称", { exact: true }).fill("CI批次错误检查");
  await page
    .getByLabel("导入数据文件", { exact: true })
    .setInputFiles({
      name: "invalid-terms.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("term,canonical\n测试别名,测试主词\n,测试主词\n"),
    });
  await page.getByRole("button", { name: "上传并校验", exact: true }).click();
  await expect(page.locator(".batch-detail")).toContainText(
    "整批未写入业务库",
    { timeout: 30000 },
  );
  await expect(page.locator(".batch-detail")).toContainText("错误 1");
  await expect(
    page.locator(".batch-detail").getByRole("link", { name: "下载错误明细" }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("dictionary import and bulk promotion create an immutable revised version", async ({
  page,
}) => {
  await login(page);
  await page.goto("/");
  await page
    .getByRole("button", { name: "业务数据与词典", exact: true })
    .click();
  await page
    .getByLabel("导入词典名称", { exact: true })
    .fill("CI批量主从词检查");
  await page
    .getByLabel("导入数据文件", { exact: true })
    .setInputFiles({
      name: "terms.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(
        "term,canonical,element_code,scope\n冻制,冷冻,STORAGE,02\n冷冻,冷冻,STORAGE,02\n",
      ),
    });
  await page.getByRole("button", { name: "上传并校验", exact: true }).click();
  await expect(page.locator(".batch-detail")).toContainText(
    "整批校验通过，已入库",
    { timeout: 30000 },
  );
  await page.getByRole("button", { name: "查看导入词典", exact: true }).click();
  await page
    .getByRole("checkbox", { name: "选择词条 冻制", exact: true })
    .check();
  await page
    .getByRole("combobox", { name: "词条批量操作", exact: true })
    .selectOption("promote");
  await page.getByLabel("批量调整依据", { exact: true }).fill("CI人工切换主词");
  await page.getByRole("button", { name: "保存批量调整", exact: true }).click();
  await expect(page.locator(".table-wrap").first()).toContainText(
    "规范词：冻制",
  );
  await expect(
    page.getByRole("button", { name: "审核发布此版本", exact: true }),
  ).toBeDisabled();
});

test("pilot report shows overall and reached denominators separately", async ({
  page,
}) => {
  await login(page);
  await page.route("**/v1/evaluations/status", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    const retention = { hits: 1, n: 2, value: 0.5 },
      reached = { hits: 1, n: 1, value: 1 };
    body.experiments = [
      {
        name: "synthetic-ui-pilot",
        pilot: true,
        suite: "agent",
        n_completed: 2,
        variant: "synthetic fixture",
        label_status: "synthetic-ui-only",
        metrics: {
          "heading_recall@40": { hits: 1, n: 1, value: 1 },
          top1_10digit: { hits: 1, n: 2, accuracy: 0.5 },
          top1_6digit: { hits: 1, n: 2, accuracy: 0.5 },
          failures: 0,
          model_requests: 0,
        },
        stage_metrics: {
          statuses: { awaiting_information: 1, awaiting_review: 1 },
          stages: {
            retrieved: {
              reference_heading_retention_all_cases: retention,
              reference_heading_retention_reached_cases: reached,
            },
          },
        },
      },
    ];
    await route.fulfill({ response, json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "评测中心", exact: true }).click();
  const card = page
    .locator("article.report")
    .filter({
      has: page.getByRole("heading", {
        name: "synthetic-ui-pilot",
        exact: true,
      }),
    });
  await expect(card).toContainText("十位Top1标签一致");
  await expect(card).toContainText("六位Top1标签一致");
  await expect(card).not.toContainText("品目 Recall@40");
  await expect(card).toContainText("1/2");
  await expect(card).toContainText("1/1");
  await expect(card).toContainText("完整Agent开发集及留出集尚未完成");
});

function credentials() {
  return JSON.parse(
    fs.readFileSync(
      path.resolve(
        process.env.E2E_CREDENTIAL_FILE ||
          "../data/private/bootstrap-admin.json",
      ),
      "utf8",
    ),
  );
}
async function login(page) {
  const response = await page.request.post("/auth/login", {
    data: credentials(),
  });
  expect(response.status()).toBe(200);
  return response.json();
}

test("login form rejects invalid credentials", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "登录工作空间" }),
  ).toBeVisible();
  await page
    .getByRole("textbox", { name: "账号", exact: true })
    .fill("invalid-user");
  await page.getByLabel("密码", { exact: true }).fill("not-a-real-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("用户名或密码错误");
});

test("dashboard renders real data and has no browser errors", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await login(page);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "工作概览", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("两项前置工具 + 六阶段归类")).toBeVisible();
  await expect(page.locator(".source-summary .version-text")).not.toHaveText(
    "语料未就绪",
  );
  expect(errors).toEqual([]);
  await page.screenshot({
    path: "test-results/console-overview.png",
    fullPage: true,
  });
});

test("tax source annotates raw declaration and returns enterprise feedback", async ({
  page,
}) => {
  await login(page);
  await page.goto("/");
  await page.getByRole("button", { name: "规范申报", exact: true }).click();
  await page
    .getByRole("combobox", { name: "词典环境" })
    .selectOption("sandbox");
  await page
    .getByRole("textbox", { name: "申报原文", exact: true })
    .fill("税号：0202300090；品名：变质牛肉");
  await page.getByRole("button", { name: "提交风险校验", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "税智源 · 退回企业整改" }),
  ).toBeVisible({ timeout: 30000 });
  await expect(page.locator("mark")).toHaveText("变质");
  await expect(
    page.getByRole("link", { name: "下载企业反馈报告" }),
  ).toBeVisible();
});

test("knowledge path keeps structural intermediate nodes", async ({ page }) => {
  await login(page);
  await page.goto("/");
  await page.getByRole("button", { name: "知识与来源", exact: true }).click();
  await page
    .getByRole("textbox", { name: "查询编码", exact: true })
    .fill("010121");
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await expect(page.locator(".code-path")).toContainText("01012");
  await expect(page.locator(".source-label")).toContainText("来源");
});

test("synthetic task shows results without allowing publication", async ({
  page,
}) => {
  const actor = await login(page);
  const response = await page.request.post("/v1/governance/terms/jobs", {
    headers: { "X-CSRF-Token": actor.csrf, "Idempotency-Key": randomUUID() },
    data: { synthetic: true, known: { 测试主词: ["测试别名"] }, pending: [] },
  });
  expect(response.status()).toBe(202);
  const task = await response.json();
  await page.goto("/");
  await page.getByRole("button", { name: "任务中心", exact: true }).click();
  await page.getByText(task.id.slice(0, 12), { exact: false }).first().click();
  await expect(page.getByRole("heading", { name: "同义词分组" })).toBeVisible({
    timeout: 30000,
  });
  await expect(page.getByText("联调样例不能发布到正式术语库。")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "确认并发布词簇" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "关闭任务详情" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("mobile workspace does not overflow horizontally", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "工作概览", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".source-summary .version-text")).not.toHaveText(
    "语料未就绪",
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/console-mobile.png",
    fullPage: true,
  });
});

test("diagnostic mode is opt-in and does not submit a paid request", async ({
  page,
}) => {
  await login(page);
  await page.goto("/");
  await expect(page.locator(".source-summary .version-text")).not.toHaveText(
    "语料未就绪",
  );
  await page.getByRole("button", { name: "商品归类", exact: true }).click();
  const mode = page.getByRole("combobox", { name: "运行模式" });
  await expect(mode).toHaveValue("hybrid");
  await page
    .getByRole("textbox", { name: "申报原文", exact: true })
    .fill("冷冻去骨牛肉，供人食用");
  await expect(page.getByRole("button", { name: "开始归类" })).toBeEnabled();
  await mode.selectOption("bm25_diagnostic");
  await expect(
    page.getByText(
      "此模式用于验证工作流，保留诊断标记，不能作为混合检索效果评测。",
    ),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "开始归类" })).toBeEnabled();
});

test("classification stops at source risk gate without language or model stages", async ({
  page,
}) => {
  await login(page);
  await page.goto("/");
  await page.getByRole("button", { name: "商品归类", exact: true }).click();
  await page
    .getByRole("combobox", { name: "词典环境" })
    .selectOption("sandbox");
  await page
    .getByRole("textbox", { name: "申报原文", exact: true })
    .fill("税号：0202300090；品名：变质牛肉");
  await page.getByRole("button", { name: "开始归类" }).click();
  await expect(
    page.getByRole("heading", { name: "税智源 · 退回企业整改" }),
  ).toBeVisible({ timeout: 30000 });
  await expect(
    page.getByRole("heading", { name: "税智语 · 申报文本归一化" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "确认编码", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "执行记录", exact: true }).click();
  await expect(page.locator(".trace-item")).toHaveCount(2);
});

test("tax language independently normalizes synonyms and preserves negation", async ({
  page,
}) => {
  await login(page);
  await page.goto("/");
  await page.getByRole("button", { name: "术语治理", exact: true }).click();
  await page
    .getByRole("combobox", { name: "词典环境" })
    .selectOption("sandbox");
  await page
    .getByRole("textbox", { name: "申报原文", exact: true })
    .fill("冻制剔骨牛肉，不含添加剂");
  await page.getByRole("button", { name: "处理申报文本", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "税智语 · 申报文本归一化" }),
  ).toBeVisible({ timeout: 30000 });
  await expect(
    page.getByText("冷冻去骨牛肉，不含添加剂", { exact: true }),
  ).toBeVisible();
});

test("agent memory page and risk-blocked task show isolated working state", async ({
  page,
}) => {
  await login(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Agent记忆", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "任务记忆与专家经验", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("尚无已发布的专家记忆。在已确认任务详情中可由专家发布。"),
  ).toBeVisible();
  await page.getByRole("button", { name: "商品归类", exact: true }).click();
  await page
    .getByRole("combobox", { name: "词典环境" })
    .selectOption("sandbox");
  await page
    .getByRole("textbox", { name: "申报原文", exact: true })
    .fill("税号：0202300090；品名：变质牛肉；备注：记忆隔离回归");
  await page.getByRole("button", { name: "开始归类" }).click();
  await expect(
    page.getByRole("heading", { name: "税智源 · 退回企业整改" }),
  ).toBeVisible({ timeout: 30000 });
  await page.getByRole("button", { name: "记忆与恢复", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "任务记忆与恢复", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("最近阶段：settled", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "调用链路", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "发布为专家案例记忆" }),
  ).toHaveCount(0);
});

test("evaluation center shows explicit index readiness without invented scores", async ({
  page,
}) => {
  await login(page);
  await page.goto("/");
  await page.getByRole("button", { name: "评测中心", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "数据隔离与实验", exact: true }),
  ).toBeVisible({ timeout: 20000 });
  await expect(
    page.getByRole("heading", { name: "Qdrant 检索索引", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "实验结果", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Badcase 回归", exact: true }),
  ).toBeVisible();
  if (process.env.E2E_FIXTURES) {
    await page.getByRole("button", { name: "查看前20条", exact: true }).click();
    await expect(
      page.getByText("合成未调味冻牛肉（仅CI）", { exact: true }),
    ).toBeVisible();
  }
  await page.screenshot({
    path: "test-results/evaluation-center.png",
    fullPage: true,
  });
});

test("business asset workbench filters bill and negative and opens dictionary entries", async ({
  page,
}) => {
  await login(page);
  await page.goto("/");
  await page
    .getByRole("button", { name: "业务数据与词典", exact: true })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "业务数据与词典",
      exact: true,
      level: 1,
    }),
  ).toBeVisible();
  await page
    .getByRole("combobox", { name: "数据环境" })
    .selectOption("sandbox");
  await expect(
    page.getByRole("button", { name: "查看要素" }).first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "查看要素" }).first().click();
  await expect(
    page.getByRole("heading", { name: /bill要素明细/ }),
  ).toBeVisible();
  await page
    .getByRole("combobox", { name: "数据类型" })
    .selectOption("negative");
  await page.getByRole("textbox", { name: "数据检索" }).fill("变质");
  await expect(
    page.locator(".table-wrap").first().locator("tbody"),
  ).toContainText("变质");
  await page.getByRole("textbox", { name: "数据检索" }).fill("");
  await page
    .getByRole("combobox", { name: "数据类型" })
    .selectOption("dictionaries");
  await page.getByRole("textbox", { name: "数据检索" }).fill("合成synonym词典");
  await expect(
    page.locator(".table-wrap").first().locator("tbody tr"),
  ).toHaveCount(1);
  await expect(
    page.locator(".table-wrap").first().locator("tbody"),
  ).toContainText("合成synonym词典");
  await page.getByRole("button", { name: "查看词条" }).click();
  await expect(
    page.locator(".table-wrap").first().locator("tbody"),
  ).toContainText("冻制");
  await expect(
    page.getByRole("button", { name: "审核发布此版本" }),
  ).toBeDisabled();
});
