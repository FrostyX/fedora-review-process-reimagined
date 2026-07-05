import logging
from review_reimagined.distgit import DistGit, GitUser
from pathlib import Path


def main():
    reponame = "ddd"
    branches = ["rawhide", "f44"]

    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger(__name__)

    distgit = DistGit(log)
    distgit.create_repository(reponame, branches)
    distgit.update_description(reponame, "This is my custom description")

    # https://kojipkgs.fedoraproject.org//packages/hello/2.12.3/1.fc44/src/hello-2.12.3-1.fc44.src.rpm
    srpm = "/opt/hello-2.12.3-1.fc44.src.rpm"
    distgit.import_package(
        reponame,
        branches,
        Path(srpm),
        GitUser("John Doe", "jdoe@email.ex"),
    )


if __name__ == "__main__":
    main()
