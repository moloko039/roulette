"""Запуск e2e: python e2e/run_e2e.py [сценарий ...] [--list] [--repeat N] [--client-dir КОПИЯ_КЛИЕНТА]. Код возврата 0 (всё прошло или Chrome не найден) / 1 (падение).

Нужны: websockets (e2e/requirements.txt), зависимости сервера (bot/requirements.txt) и Chrome/Chromium. Скриншоты упавших сценариев
пишутся во временную папку (путь печатается), в репозиторий ничего не попадает."""
import asyncio
import importlib
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
GOLDEN_NET = os.path.join(HERE, "golden_net.json")
NET_SKIP = ("layout", "mines_layout", "crash_visibility")    # раскладка меняет размеры и открывает игры подряд; crash_visibility ждёт раунд по времени: число опросов состояния плавает. Запросы не предмет проверки
# Сценарии с опросом: число подряд идущих одинаковых запросов зависит от скорости (после оплаты Stars клиент опрашивает /api/cosmetics/mine до появления предмета),
# поэтому последовательность сравнивается со склейкой подряд идущих одинаковых запросов (порядок и набор разных запросов проверяются, число опросов нет).
POLL_COLLAPSE = ("wardrobe_buy_stars",)
SCENARIOS = ["lobby", "betpanel_keyboard", "roulette", "mines", "keno", "blackjack", "crash", "hilo", "resume", "accrual_tick",
             "transfers_ui", "layout", "mines_layout", "shared_core", "skin_vars", "skin_apply", "wardrobe_flow", "wardrobe_rating", "wardrobe_safety", "skin_contrast", "wardrobe_buy_chips", "wardrobe_buy_stars", "wardrobe_buy_safety", "skin_preview_isolation", "keno_hex_play", "best_wins_board", "best_wins_safety", "blackjack_auto_stand", "desktop_scroll", "crash_visibility", "post_body_abort", "style_tab"]


def collapse(seq):
    """Подряд идущие одинаковые запросы склеиваются в один (опрос)."""
    out = []
    for line in seq:
        if not out or out[-1] != line:
            out.append(line)
    return out


def net_diff(want, got):
    """Первое расхождение двух последовательностей запросов (для сообщения о падении)."""
    for i in range(max(len(want), len(got))):
        a = want[i] if i < len(want) else "<нет>"
        b = got[i] if i < len(got) else "<нет>"
        if a != b:
            return "запрос %d: ожидали «%s», получили «%s» (всего ожидали %d, получили %d)" % (i + 1, a, b, len(want), len(got))
    return "?"


def main(argv):
    if "--list" in argv:
        print("\n".join(SCENARIOS))
        return 0
    repeat = 1
    if "--repeat" in argv:
        i = argv.index("--repeat")
        repeat = int(argv[i + 1])
        del argv[i:i + 2]
    client_dir = None
    if "--client-dir" in argv:
        i = argv.index("--client-dir")
        client_dir = os.path.abspath(argv[i + 1])
        del argv[i:i + 2]
    record_net = "--record-net" in argv
    if record_net:
        argv.remove("--record-net")
    names = [a for a in argv if not a.startswith("-")] or SCENARIOS
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        print("Неизвестные сценарии: %s. Доступные: %s" % (", ".join(unknown), ", ".join(SCENARIOS)))
        return 1
    import harness
    if client_dir:
        harness.CLIENT_ROOT = client_dir    # копия клиента (например, с намеренной поломкой), файлы репозитория не трогаются
    chrome_path = harness.find_chrome()
    if chrome_path is None:
        print("Chrome не найден, e2e пропущены")
        return 0
    if harness.websockets is None:
        print("Не установлен websockets: pip install -r e2e/requirements.txt")
        return 1
    return asyncio.run(run_all(harness, chrome_path, names, repeat, record_net))


async def run_all(harness, chrome_path, names, repeat, record_net=False):
    tmp = tempfile.mkdtemp(prefix="e2e-chrome-")
    shots = tempfile.mkdtemp(prefix="e2e-shots-")
    chrome = harness.Chrome(chrome_path, tmp)
    failed = []
    results = []
    golden = json.load(open(GOLDEN_NET, encoding="utf-8")) if os.path.exists(GOLDEN_NET) and not record_net else {}
    recorded = {}
    try:
        for round_no in range(repeat):
            for name in names:
                mod = importlib.import_module("scenarios." + name)
                started = time.time()
                world = harness.World(chrome, users=getattr(mod, "USERS", None), viewport=getattr(mod, "VIEWPORT", (390, 700)),
                                      clock_mod=getattr(mod, "CLOCK_MOD", None))
                error = None
                try:
                    await world.start()
                    world.page.allowed.extend(getattr(mod, "ALLOW_CONSOLE", ()))
                    await mod.run(world)
                    if world.page.problems:
                        raise harness.E2EError("в консоли ошибки или предупреждения: " + " | ".join(world.page.problems[:3]))
                    net = harness.normalize_net(world.page.net)
                    if name in NET_SKIP:
                        pass
                    elif record_net:
                        recorded.setdefault(name, net)
                        if recorded[name] != net:
                            raise harness.E2EError("запись сетевого эталона неповторима: " + net_diff(recorded[name], net))
                    elif name in golden and (collapse(golden[name]) if name in POLL_COLLAPSE else golden[name]) != (collapse(net) if name in POLL_COLLAPSE else net):
                        want, got = (collapse(golden[name]), collapse(net)) if name in POLL_COLLAPSE else (golden[name], net)
                        raise harness.E2EError("последовательность запросов не совпала с golden_net.json: " + net_diff(want, got))
                except Exception as exc:  # noqa: BLE001
                    error = "%s: %s" % (type(exc).__name__, exc)
                    if world.page is not None:
                        try:
                            await world.page.screenshot(os.path.join(shots, "%s-%d.png" % (name, round_no)))
                        except Exception:  # noqa: BLE001
                            pass
                finally:
                    await world.stop()
                took = time.time() - started
                results.append((name, error, took))
                print("%s %-18s %5.1f с%s" % ("ОК    " if error is None else "ПАДЕНИЕ", name, took, "" if error is None else "\n        " + error), flush=True)
                if error is not None:
                    failed.append(name)
    finally:
        chrome.stop()
        shutil.rmtree(tmp, ignore_errors=True)
    if record_net and not failed:
        old = json.load(open(GOLDEN_NET, encoding="utf-8")) if os.path.exists(GOLDEN_NET) else {}
        old.update(recorded)
        with open(GOLDEN_NET, "w", encoding="utf-8") as f:
            json.dump(old, f, ensure_ascii=False, indent=0, sort_keys=True)
        print("Сетевой эталон записан: %d сценариев, %d запросов" % (len(old), sum(len(v) for v in old.values())))
    total = len(results)
    print("Итого: %d прогонов, упало %d" % (total, len(failed)))
    if failed:
        print("Скриншоты упавших: " + shots)
        return 1
    shutil.rmtree(shots, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
