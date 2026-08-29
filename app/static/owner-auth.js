(() => {
  const TOKEN_KEY = "dx.ownerToken";
  const origFetch = window.fetch.bind(window);
  const OrigES = window.EventSource;

  let pending = null;

  function pathnameOf(url) {
    try {
      return new URL(String(url), location.origin).pathname;
    } catch {
      return "";
    }
  }

  function isPublic(url) {
    const p = pathnameOf(url);
    if (!p) return false;
    if (p === "/health" || p === "/capabilities" || p === "/" || p === "/favicon.ico") return true;
    if (p === "/api/v1/auth/bootstrap") return true;
    if (p.startsWith("/static/") || p.startsWith("/admin/static/")) return true;
    if (p.startsWith("/docs") || p.startsWith("/redoc") || p === "/openapi.json") return true;
    if (p.startsWith("/apps/")) return true;
    if (
      p === "/admin" || p === "/admin/" ||
      p === "/admin/daedalus" || p === "/admin/daedalus/" ||
      p === "/admin/daedalus/hub" ||
      p === "/admin/daedalus/playground" ||
      p === "/admin/daedalus/projects" ||
      p === "/admin/daedalus/game-studio" ||
      p === "/admin/daedalus/projects/game-studio" ||
      p === "/admin/daedalus/agent" ||
      p === "/admin/daedalus/review" ||
      p === "/admin/daedalus/settings" ||
      p === "/admin/daedalus/connectors" ||
      p === "/admin/daedalus/proposals" ||
      p === "/admin/daedalus/skills" ||
      p === "/admin/daedalus/bundles" ||
      p === "/admin/llm-router"
    ) return true;
    return false;
  }

  function stored() {
    try {
      return localStorage.getItem(TOKEN_KEY) || "";
    } catch {
      return "";
    }
  }

  function save(token) {
    try {
      localStorage.setItem(TOKEN_KEY, token);
    } catch {
      /* private mode */
    }
  }

  async function ensureToken() {
    const have = stored();
    if (have) return have;
    if (pending) return pending;
    pending = (async () => {
      try {
        const res = await origFetch("/api/v1/auth/bootstrap");
        if (res.ok) {
          const data = await res.json();
          if (data && data.token) {
            save(data.token);
            return data.token;
          }
        }
      } catch {
        /* remote / offline */
      }
      if (typeof window.prompt === "function") {
        const typed = window.prompt(
          "Собственически токен (DAEDALUS_TOKEN). На 127.0.0.1 Studio го взима сам."
        );
        if (typed && typed.trim()) {
          save(typed.trim());
          return typed.trim();
        }
      }
      return "";
    })();
    try {
      return await pending;
    } finally {
      pending = null;
    }
  }

  function withToken(url, token) {
    const u = new URL(String(url), location.origin);
    if (token && !u.searchParams.get("daedalus_token")) {
      u.searchParams.set("daedalus_token", token);
    }
    if (String(url).startsWith("http://") || String(url).startsWith("https://")) {
      return u.toString();
    }
    return u.pathname + u.search + u.hash;
  }

  window.fetch = async function (input, init) {
    const url = typeof input === "string" ? input : (input && input.url) || "";
    if (isPublic(url)) {
      return origFetch(input, init);
    }
    const token = await ensureToken();
    if (!token) {
      return origFetch(input, init);
    }
    if (typeof Request !== "undefined" && input instanceof Request) {
      const headers = new Headers(input.headers);
      if (!headers.has("Authorization") && !headers.has("X-Daedalus-Token")) {
        headers.set("Authorization", "Bearer " + token);
      }
      return origFetch(new Request(input, { headers }), init);
    }
    const next = init ? Object.assign({}, init) : {};
    const headers = new Headers(next.headers || {});
    if (!headers.has("Authorization") && !headers.has("X-Daedalus-Token")) {
      headers.set("Authorization", "Bearer " + token);
    }
    next.headers = headers;
    return origFetch(input, next);
  };

  if (OrigES) {
    function WrappedEventSource(url, esInit) {
      const token = stored();
      return new OrigES(token ? withToken(url, token) : String(url), esInit);
    }
    WrappedEventSource.prototype = OrigES.prototype;
    WrappedEventSource.CONNECTING = OrigES.CONNECTING;
    WrappedEventSource.OPEN = OrigES.OPEN;
    WrappedEventSource.CLOSED = OrigES.CLOSED;
    window.EventSource = WrappedEventSource;
  }

  window.DaedalusAuth = {
    ensureToken,
    stored,
    url: (href) => withToken(href, stored()),
  };

  ensureToken();
})();
