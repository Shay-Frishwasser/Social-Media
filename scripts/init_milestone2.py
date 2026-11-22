from pathlib import Path

from brandai import BrandAIStore, RuntimeSettings
from brandai.playbooks import seed_playbooks


def main() -> None:
    workspace = Path(__file__).resolve().parents[1]
    settings = RuntimeSettings.from_env(workspace / ".env")
    store = BrandAIStore(settings)
    seeded = seed_playbooks(store, workspace / "playbooks")
    print(f"Initialized global registry: {settings.system_db}")
    print(f"Initialized brand root: {settings.brands_dir}")
    for path in seeded:
        print(f"Registered playbook: {path}")


if __name__ == "__main__":
    main()
