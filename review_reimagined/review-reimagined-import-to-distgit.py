import os
import sys
import rpm
import logging
import tempfile
import requests
from fedora_distro_aliases import get_distro_aliases
from review_reimagined.distgit import DistGit, GitUser
from review_reimagined.settings import Settings
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


def get_srpm_name(path: Path) -> str:
    ts = rpm.TransactionSet()
    ts.setVSFlags(rpm._RPMVSF_NOSIGNATURES)
    with open(path, "rb") as fp:
        hdr = ts.hdrFromFdno(fp.fileno())
        return hdr["name"]


def package_builds_successfully_in(copr_owner, copr_project) -> dict:
    SUCCEEDED = 1
    aliases = get_distro_aliases()
    distros = aliases["fedora-all"]

    client = Client.create_from_config_file()
    monitor = client.monitor_proxy.monitor(copr_owner, copr_project)

    result = {}
    for package in monitor["packages"]:
        packagename = package["name"]
        result[packagename] = {
            "distros": [],
            "copr_build_id": None,
        }
        for distro in distros:
            chroot = f"{distro.namever}-x86_64"
            if package["chroots"][chroot]["status"] == SUCCEEDED:
                result[packagename]["distros"].append(distro)

            if distro.version == "rawhide":
                result[packagename]["copr_build_id"] = \
                    package["chroots"][chroot]["build_id"]
    return result


def main():
    settings = Settings()
    pull_request = 1
    projectname = f"fedora-review-pr-{pull_request}"

    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger(__name__)

    successfully_builds_in = package_builds_successfully_in(
        settings.copr_owner,
        projectname,
    )
    with tempfile.TemporaryDirectory() as tmp:
        for package, successfully_builds_in in successfully_builds_in.items():
            srpm_url = copr_build_srpm_url(
                successfully_builds_in["copr_build_id"],
                "fedora-rawhide-x86_64",
            )
            response = requests.get(srpm_url)
            if not response.ok:
                log.error("Failed to download: %s", srpm_url)
                sys.exit(1)

            filename = srpm_url.split("/")[-1]
            srpm = Path(tmp) / filename
            srpm.write_bytes(response.content)
            reponame = get_srpm_name(srpm)

            branches = [x.branch for x in successfully_builds_in["distros"]]
            distgit = DistGit(log)
            distgit.create_repository(reponame, branches)
            distgit.update_description(reponame, "This is my custom description")
            distgit.import_package(
                reponame,
                branches,
                Path(srpm),
                GitUser("John Doe", "jdoe@email.ex"),
            )


if __name__ == "__main__":
    main()
