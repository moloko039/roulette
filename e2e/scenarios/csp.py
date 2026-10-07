"""CSP страницы: политика задана, без 'unsafe-inline', 'unsafe-eval' и подстановочных источников; внешнее разрешено только скрипту Telegram и серверу игры;
внедрённый inline-скрипт и inline-стиль не выполняются (браузер сообщает о нарушении)."""
import json

from harness import check

NAME = "csp"
USERS = {"me": {"rate": 0}}
CLOCK_MOD = 5
ALLOW_CONSOLE = (r"Content Security Policy", r"Refused to")     # намеренное нарушение: проба внедрения


async def run(w):
    p = w.page
    policy = await p.ev("(document.querySelector('meta[http-equiv=\"Content-Security-Policy\"]') || {}).content || ''")
    check("CSP задана", bool(policy), True)
    parts = {d.split(" ")[0]: d.split(" ")[1:] for d in (x.strip() for x in policy.split(";")) if d}
    check("по умолчанию всё запрещено", parts.get("default-src"), ["'none'"])
    check("скрипты: свои и Telegram", parts.get("script-src"), ["'self'", "https://telegram.org"])
    check("стили: только свои", parts.get("style-src"), ["'self'"])
    check("нет unsafe-inline, unsafe-eval и звёздочек в скриптах и стилях", [any(t in ("'unsafe-inline'", "'unsafe-eval'", "*", "data:", "https:") for t in parts[k]) for k in ("script-src", "style-src")], [False, False])
    check("запросы только к серверу игры", len(parts.get("connect-src", [])), 1)
    check("base-uri, form-action, object-src закрыты", [parts.get(k) for k in ("base-uri", "form-action", "object-src")], [["'none'"]] * 3)
    probe = await p.ev("""(async () => {
      let violations = [];
      document.addEventListener('securitypolicyviolation', (e) => violations.push(e.violatedDirective));
      const s = document.createElement('script'); s.textContent = 'window.__csp_ran = 1'; document.body.appendChild(s);
      const el = document.createElement('div'); el.setAttribute('style', 'width: 123px'); document.body.appendChild(el);
      await new Promise((r) => setTimeout(r, 100));
      return { ran: !!window.__csp_ran, violations };
    })()""")
    check("inline-скрипт не выполнился", probe["ran"], False)
    check("нарушения зафиксированы для script-src и style-src", sorted({v.split("-elem")[0].split("-attr")[0] for v in probe["violations"]}), ["script-src", "style-src"])
