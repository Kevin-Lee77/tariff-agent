let csrf = "";
export function setCsrf(value) {
  csrf = value || "";
}
export async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(csrf ? { "X-CSRF-Token": csrf } : {}),
      ...options.headers,
    },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message =
      typeof data.detail === "string"
        ? data.detail
        : "输入格式不正确，请检查后重试";
    const error = new Error(message);
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}
export function post(path, body, idempotent = false) {
  return api(path, {
    method: "POST",
    body: JSON.stringify(body),
    headers: idempotent ? { "Idempotency-Key": crypto.randomUUID() } : {},
  });
}
