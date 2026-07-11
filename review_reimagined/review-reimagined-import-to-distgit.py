import os
import sys
import logging
import tempfile
import requests
from review_reimagined.distgit import DistGit, GitUser
from copr.v3 import Client
from pathlib import Path


def copr_build_results_urls(build_id: int, chroot: str) -> list[str]:
    """
    This function will live in python3-copr
    https://github.com/fedora-copr/copr/issues/4119
    """
    client = Client.create_from_config_file()
    build_chroot = client.build_chroot_proxy.get(build_id, chroot)
    results = client.build_proxy.get_built_packages(build_id)
    urls = []
    for nevra in results[chroot]["packages"]:
        filename = "{N}-{V}-{R}.{A}.rpm".format(
            N=nevra["name"],
            V=nevra["version"],
            R=nevra["release"],
            A=nevra["arch"],
        )
        url = build_chroot.result_url + filename
        urls.append(url)
    return urls


def copr_build_srpm_url(build_id: int, chroot: str) -> str:
    urls = copr_build_results_urls(build_id, chroot)
    for url in urls:
        if url.endswith(".src.rpm"):
            return url

def main():
    reponame = "ddd"
    branches = ["rawhide", "f44"]
    copr_build_id = 2925758
    copr_chroot = "fedora-rawhide-x86_64"

    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger(__name__)

    distgit = DistGit(log)
    distgit.create_repository(reponame, branches)
    distgit.update_description(reponame, "This is my custom description")

    srpm_url = copr_build_srpm_url(copr_build_id, copr_chroot)
    response = requests.get(srpm_url)
    if not response.ok:
        log.error("Failed to download: %s", srpm_url)
        sys.exit(1)

    with tempfile.TemporaryDirectory() as tmp:
        filename = srpm_url.split("/")[-1]
        srpm = Path(tmp) / filename
        srpm.write_bytes(response.content)

        distgit.import_package(
            reponame,
            branches,
            Path(srpm),
            GitUser("John Doe", "jdoe@email.ex"),
        )


if __name__ == "__main__":
    main()
