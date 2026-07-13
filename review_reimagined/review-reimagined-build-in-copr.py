import sys
import argparse
import logging
import tempfile
import requests
from pathlib import Path
from contextlib import suppress
from copr.v3 import Client, CoprRequestException, CoprNoResultException
from copr.v3.helpers import wait
from ogr.services.forgejo import ForgejoService
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


def forgejo_changed_files_per_commit(
    instance: str,
    namespace: str,
    repo: str,
    pull_request: int,
    token: str | None = None
) -> list[dict]:
    service = ForgejoService(instance_url=instance, token=token)
    project = service.get_project(repo=repo, namespace=namespace)
    pr = project.get_pr(pull_request)

    result = []
    commits = reversed(list(pr.get_all_commits()))
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


def forgejo_file_url(
    instance: str,
    namespace: str,
    repo: str,
    commit: str,
    filename: str,
):
    return f"{instance}/{namespace}/{repo}/raw/commit/{commit}/{filename}"


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


def main():
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger(__name__)

    settings = Settings()
    parser = get_arg_parser()
    args = parser.parse_args()
    projectname = f"fedora-review-pr-{args.pull_request}"

    changes = forgejo_changed_files_per_commit(
        settings.forgejo_instance,
        settings.forgejo_namespace,
        settings.forgejo_repo,
        args.pull_request,
        token=settings.forgejo_token,
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
                packagename = filename.removesuffix(".spec")
                if packagename in already_built:
                    log.info("Skipping package %s, already built", packagename)
                    continue

                # We may not actually want to use the change["commit"] but
                # rather find out what was the latest commit that changed that
                # exact file and submit the build from that commit/file.
                url = forgejo_file_url(
                    settings.forgejo_instance,
                    settings.forgejo_namespace,
                    settings.forgejo_repo,
                    change["commit"],
                    filename,
                )
                response = requests.get(url)
                response.raise_for_status()
                spec = Path(tmp) / filename
                spec.write_bytes(response.content)

                # TODO If we have two spec files added within one commit, we should
                # be able to build them in paralel, therefore `with_build_id`
                # instead of `after_build_id`.
                buildopts = {}
                if builds:
                    buildopts = {"after_build_id": builds[-1].id}

                build = copr.build_proxy.create_from_file(
                    settings.copr_owner,
                    projectname,
                    spec,
                    buildopts=buildopts,
                )
                log.info("Copr build: %s", build.id)
                builds.append(build)

    log.info("Waiting for the Copr builds to finish")
    builds = wait(builds)

    if failed := [x.id for x in builds if copr_failed_in_rawhide(copr, x.id)]:
        log.error("Failed to build in Rawhide: %s", failed)
        sys.exit(1)
    log.info("All builds finished successfully")


if __name__ == "__main__":
    main()
