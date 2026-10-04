"""Запуск e2e: python e2e/run_e2e.py [сценарий ...] [--list] [--repeat N] [--client-dir КОПИЯ_КЛИЕНТА]. Код возврата 0 (всё прошло или Chrome не найден) / 1 (падение).

Нужны: websockets (e2e/requirements.txt), зависимости сервера (bot/requirements.txt) и Chrome/Chromium. Скриншоты упавших сценариев
пишутся во временную папку (путь печатается), в репозиторий ничего не попадает."""
import asyncio
import importlib
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SCENARIOS = ["lobby", "betpanel_keyboard", "roulette", "mines", "keno", "blackjack", "crash", "hilo", "resume", "accrual_tick",
             "transfers_ui", "layout", "mines_layout"]


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
    return asyncio.run(run_all(harness, chrome_path, names, repeat))


async def run_all(harness, chrome_path, names, repeat):
    tmp = tempfile.mkdtemp(prefix="e2e-chrome-")
    shots = tempfile.mkdtemp(prefix="e2e-shots-")
    chrome = harness.Chrome(chrome_path, tmp)
    failed = []
    results = []
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
    total = len(results)
    print("Итого: %d прогонов, упало %d" % (total, len(failed)))
    if failed:
        print("Скриншоты упавших: " + shots)
        return 1
    shutil.rmtree(shots, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
