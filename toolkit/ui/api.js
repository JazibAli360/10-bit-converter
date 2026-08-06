/* Local API boundary: authentication, JSON errors, and safe text escaping. */
(function () {
  const rawFetch = window.fetch.bind(window);
  window.fetch = (input, init = {}) => {
    const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
    headers.set("X-10bit-Token", API_TOKEN);
    return rawFetch(input, {...init, headers});
  };
  window.authQuery = () => `auth=${encodeURIComponent(API_TOKEN)}`;
  window.escHtml = value => String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  window.apiJson = async (url, options) => {
    const response = await fetch(API + url, options);
    const body = await response.json().catch(() => ({error: `Request failed (${response.status})`}));
    return response.ok ? body : {...body, error: body.error || `Request failed (${response.status})`};
  };
}());
