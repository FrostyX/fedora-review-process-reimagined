import os
import sys
import subprocess
import argparse
import logging
import tempfile
import requests
from pathlib import Path
from contextlib import suppress
from urllib.parse import urljoin
from copr.v3 import Client, CoprRequestException, CoprNoResultException
from copr.v3.helpers import wait
from ogr.services.forgejo import ForgejoService, ForgejoPullRequest
from review_reimagined.settings import Settings


def get_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pull-request",
        type=int,
        required=True,
    )
    parser.add_argument(
        "--force",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    return parser


class Forgejo:
    def __init__(self, instance: str, token: str | None = None):
        self.instance = instance
        self.token = token

    def pull_request(
        self,
        namespace: str,
        repo: str,
        number: int,
    ) -> ForgejoPullRequest:
        service = ForgejoService(instance_url=self.instance, token=self.token)
        project = service.get_project(repo=repo, namespace=namespace)
        return project.get_pr(number)

    def changed_files_per_commit(
        self,
        namespace: str,
        repo: str,
        pull_request: ForgejoPullRequest,
    ) -> list[dict]:
        service = ForgejoService(instance_url=self.instance, token=self.token)
        result = []
        commits = reversed(list(pull_request.get_all_commits()))
        for sha in commits:
            commit = service.api.repository.repo_get_single_commit(
                owner=namespace,
                repo=repo,
                sha=sha,
            )
            result.append({
                "commit": sha,
                "files": [x.filename for x in commit.files],
            })
        return result

    def clone_url(self, pull_request: ForgejoPullRequest) -> str:
        return pull_request.source_project.get_git_urls()["git"]


def copr_wipe_project(client: Client, owner: str, project: str) -> None:
    """
    It would be nice to just delete the whole project but we are facing
    a race condition in Copr - https://github.com/fedora-copr/copr/issues/4184
    Therefore, we need to cancel and delete all the builds while preserving the
    project itself.
    """
    with suppress(CoprNoResultException):
        cancelable = ["running", "pending", "starting", "importing", "waiting"]
        builds = client.build_proxy.get_list(owner, project)
        for build in builds:
            if build.state in cancelable:
                client.build_proxy.cancel(build.id)
        client.build_proxy.delete_list([x.id for x in builds])


def copr_build_url(client: Client, build_id: int) -> str:
    """
    Return a predictable URL for a Copr build
    """
    return urljoin(client.config["copr_url"], f"/coprs/build/{build_id}")


def fedora_chroots_available_in_copr(client: Client):
    chroots = client.mock_chroot_proxy.get_list()
    return [
        x for x in chroots.keys()
        if x.startswith("fedora-")
        and x.endswith("-x86_64")
        and not x.startswith("fedora-eln")
    ]


def copr_create_or_update_project(
    client: Client,
    owner: str,
    project: str,
    chroots: list[str],
) -> None:
    try:
        client.project_proxy.add(
            owner,
            project,
            chroots=chroots,
            unlisted_on_hp=True,
        )
    except CoprRequestException as ex:
        if "already has a project named" not in str(ex):
            raise CoprRequestException(str(ex)) from ex
        client.project_proxy.edit(owner, project, chroots)


def copr_already_built_packages(
    client: Client,
    owner: str,
    project: str,
) -> list[str]:
    packages = client.package_proxy.get_list(
        owner,
        project,
        with_latest_succeeded_build=True,
    )
    return [
        x["name"] for x in packages
        if x["builds"]["latest_succeeded"] is not None
    ]


def copr_failed_in_rawhide(client, build_id) -> bool:
    try:
        chroot = client.build_chroot_proxy.get(build_id, "fedora-rawhide-x86_64")
        return chroot.state != "succeeded"
    except CoprNoResultException:
        return True


def copr_custom_script(clone_url, commit, directory):
    return (
        f"#! /bin/sh -xe\n"
        f"git clone {clone_url} package-review\n"
        f"cd package-review\n"
        f"git checkout {commit}\n"

        # There is no standardized way to define vendored sources in the spec
        # file and generate them on the fly when building a SRPM. There is a
        # RFE for this feature and several approaches are being discussed.
        # https://forge.fedoraproject.org/packaging/guidelines/issues/1132
        # Until we agree on the standard solution, I am implementing the
        # easiest possible solution. Users can put a `generate-sources.sh`
        # script next to their spec file and we will run it for them.
        f"if [ -f {directory}/generate-sources.sh ]; then\n"
        f"    chmod +x {directory}/generate-sources.sh\n"
        f"    (cd {directory} && ./generate-sources.sh)\n"
        f"fi\n"

        f"cp -r {directory}/* ..\n"
    )


def main():
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger(__name__)

    settings = Settings()
    parser = get_arg_parser()
    args = parser.parse_args()
    projectname = f"fedora-review-pr-{args.pull_request}"

    forgejo = Forgejo(settings.forge_instance, token=settings.forge_token)
    pull_request = forgejo.pull_request(
        settings.forge_namespace,
        settings.forge_repo,
        args.pull_request,
    )
    changes = forgejo.changed_files_per_commit(
        settings.forge_namespace,
        settings.forge_repo,
        pull_request,
    )

    copr = Client.create_from_config_file()
    with tempfile.TemporaryDirectory() as tmp:
        if args.force:
            copr_wipe_project(copr, settings.copr_owner, projectname)

        chroots = fedora_chroots_available_in_copr(copr)
        copr_create_or_update_project(
            copr,
            settings.copr_owner,
            projectname,
            chroots,
        )

        already_built = copr_already_built_packages(
            copr,
            settings.copr_owner,
            projectname,
        )

        builds = []
        for change in changes:
            for filename in change["files"]:
                filename = Path(filename)
                packagename = filename.parent.name
                if packagename in already_built:
                    log.info("Skipping package %s, already built", packagename)
                    continue

                if filename.suffix != ".spec":
                    continue

                if filename.stem != packagename:
                    raise SystemExit("Specfile must match the directory name")

                clone_url = forgejo.clone_url(pull_request)

                # We may not actually want to use the change["commit"] but
                # rather find out what was the latest commit that changed that
                # exact file and submit the build from that commit/file.
                commit = change["commit"]

                # Can we reasonably hardcode a list of dependencies or do we
                # need to allow users to define themselves? For example by
                # creating a `generate-sources-dependencies.txt`?
                # It could be standardized into the specfile
                # https://github.com/rpm-software-management/rpm/issues/2631
                # https://github.com/rpm-software-management/rpm/issues/2709
                script_builddeps = [
                    "git",
                    "rpmdevtools",
                    "python3-specfile",
                    "golang",
                    "go-vendor-tools",
                ]
                script = copr_custom_script(clone_url, commit, filename.parent)

                # TODO If we have two spec files added within one commit, we
                # should be able to build them in paralel, therefore
                # `with_build_id` instead of `after_build_id`.
                buildopts = {}
                if builds:
                    buildopts = {"after_build_id": builds[-1].id}

                build = copr.build_proxy.create_from_custom(
                    settings.copr_owner,
                    projectname,
                    script,
                    script_builddeps=script_builddeps,
                    buildopts=buildopts,
                )
                log.info("Copr build: %s", copr_build_url(copr, build.id))
                builds.append(build)

    log.info("Waiting for the Copr builds to finish")
    builds = wait(builds)

    if failed := [x.id for x in builds if copr_failed_in_rawhide(copr, x.id)]:
        log.error("Failed to build in Rawhide: %s", failed)
        sys.exit(1)
    log.info("All builds finished successfully")


if __name__ == "__main__":
    main()
