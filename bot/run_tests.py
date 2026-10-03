"""Запускает все bot/test_*.py по очереди и печатает итог. Код выхода 1, если что-то упало."""
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    passed, failed = [], []
    for path in sorted(glob.glob(os.path.join(HERE, "test_*.py"))):
        name = os.path.basename(path)
        result = subprocess.run([sys.executable, path], cwd=HERE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, universal_newlines=True)
        if result.returncode == 0:
            passed.append(name)
            print("ПРОШЁЛ  " + name)
        else:
            failed.append(name)
            print("УПАЛ    " + name)
            print("\n".join(result.stdout.splitlines()[-15:]))
    print("\nИтого: прошло %d, упало %d" % (len(passed), len(failed)))
    if failed:
        print("Упали: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
