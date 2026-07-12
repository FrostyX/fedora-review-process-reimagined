import argparse
import logging
from pathlib import Path
from git import Repo
from review_reimagined.settings import Settings


def get_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--push",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    return parser


def cleanup_commit_message(spec: Path) -> str:
    package = spec.stem
    return (
        f"Package {package} was imported to DistGit\n"
        f"\n"
        f"https://src.fedoraproject.org/rpms/{package}\n"
    )


def main():
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger(__name__)

    settings = Settings()
    parser = get_arg_parser()
    args = parser.parse_args()
    repo = Repo(args.repo)

    for spec in args.repo.rglob("*.spec"):
        message = cleanup_commit_message(spec)
        repo.index.remove(spec)
        repo.index.commit(message)
        spec.unlink()
        log.info("Removed: %s", spec)

    if args.push:
        repo.remote("origin").push()


if __name__ == "__main__":
    main()
