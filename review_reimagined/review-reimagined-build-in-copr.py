import logging
import subprocess
import tempfile
from pathlib import Path
from copr.v3 import Client


PACKAGE_REVIEW_REPO = "http://forgejo:3000/packaging/package-review"
COPR_OWNER = "@fedora-review"


def git_clone(url: str, dst: Path) -> None:
    cmd = ["git", "clone", url, str(dst)]
    subprocess.run(cmd, check=True)


def git_fetch(repo: Path, pull_request: int) -> str:
    local = f"pr-{pull_request}"
    cmd = ["git", "fetch", "origin", f"pull/{pull_request}/head:{local}"]
    subprocess.run(cmd, check=True, cwd=repo)
    return local


def git_switch(repo: Path, branch: str) -> None:
    cmd = ["git", "switch", branch]
    subprocess.run(cmd, check=True, cwd=repo)


def main():
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger(__name__)

    force = False
    pull_request = 1
    projectname = f"fedora-review-pr-{pull_request}"
    chroots = ["fedora-rawhide-x86_64", "fedora-44-x86_64"]

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        git_clone(PACKAGE_REVIEW_REPO, tmp)

        branch = git_fetch(tmp, pull_request)
        git_switch(tmp, branch)

        copr = Client.create_from_config_file()

        if force:
            # It would probably be better to just remove all existing builds
            # instead of the whole project
            copr.project_proxy.delete(COPR_OWNER, projectname)

        copr.project_proxy.add(COPR_OWNER, projectname, chroots, exist_ok=True)

        for spec in tmp.glob("*.spec"):
            build = copr.build_proxy.create_from_file(
                COPR_OWNER, projectname, spec
            )
            log.info("Copr build: %s", build.id)


if __name__ == "__main__":
    main()
