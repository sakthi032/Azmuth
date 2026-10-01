"""Find out why the builtin plugin isn't loading.

Run:  py -m scripts.diagnose_plugins
"""

from pathlib import Path

from app.plugins import PluginLoader, parse_manifest


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    builtin_root = project_root / "app" / "plugins" / "builtin"

    print(f"project root:  {project_root}")
    print(f"builtin root:  {builtin_root}")
    print(f"exists:        {builtin_root.is_dir()}")
    print()

    if not builtin_root.is_dir():
        print("The builtin directory does not exist. Create it with the files below.")
        return

    print("Contents of builtin/:")
    entries = sorted(builtin_root.iterdir(), key=lambda p: p.name.lower())
    if not entries:
        print("  (empty)")
    for entry in entries:
        kind = "DIR " if entry.is_dir() else "FILE"
        print(f"  {kind} {entry.name}")
        if entry.is_dir():
            manifest_path = entry / "plugin.json"
            module_path = entry / "plugin.py"
            print(f"       plugin.json present: {manifest_path.is_file()}")
            print(f"       plugin.py   present: {module_path.is_file()}")
            if manifest_path.is_file():
                try:
                    manifest = parse_manifest(manifest_path)
                    print(f"       manifest parses: {manifest.name} v{manifest.version}")
                except Exception as exc:
                    print(f"       manifest FAILED: {exc}")

    print()
    print("Loader result:")
    report = PluginLoader([builtin_root]).load_all()
    print(f"  loaded:   {report.loaded_count}")
    print(f"  failures: {report.failure_count}")
    for lp in report.loaded:
        print(f"    loaded: {lp.metadata.name}")
    for failure in report.failures:
        print(f"    failure: {failure.directory}")
        print(f"      reason: {failure.reason}")


if __name__ == "__main__":
    main()